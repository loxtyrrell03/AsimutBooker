"""Prepared, journalled transfers at the moving room horizon.

Planning starts early. Releasing fallback coverage never starts before the
replacement's conservative live opening boundary. A failed known step restores
the previous completed state; uncertain writes keep the parent pending.
"""
import copy
import json
import time
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from booking_strategy import load_booking_strategy
from booking_quotas import QuotaWait
from date_time_preferences import resolve_time_preferences
from mutation_receipts import record_pending, load_journal
from progressive_browser import (PreparedSeed, prepare_seed, save_seed,
                                 preflight_transfer_destination, prepare_transfer_destination)
from progressive_planner import plan_progressive_transfer, ordered_adjustments
from progressive_state import (load_state, update_state, fingerprint, protected_event_ids,
    remember_opportunities, forget_plan, remember_denial, denial_key)
from progressive_transactions import (TransferEdit, record, reservation, transaction_payload,
                                     validate_transfer, transfer_summary)
from room_upgrades import (Reservation, RoomUpgrade, RoomConsolidation, local_instant,
                          time_text, clock_minutes)
from upgrade_validation import RoomPermissionRefusal


def allowed_scope(args):
    return not any(getattr(args, name, False) for name in
        ('check_only', 'agenda_only', 'plan_only', 'horizon_only', 'extensions_only', 'upgrade_dry_run'))


def in_scope(args, originals, room, day):
    return (not getattr(args, 'only_date', None) or args.only_date == day.isoformat()) and (
        not getattr(args, 'only_room', None) or args.only_room == room) and (
        getattr(args, 'upgrade_event_id', None) is None
        or args.upgrade_event_id in {r.event_id for r in originals})


def current_reservations(events):
    result = {}
    for event in events:
        if event.get('isReservation') is not True:
            continue
        r = Reservation.from_event(event)
        if r.event_id in result:
            raise ValueError('Duplicate reservation identity in complete agenda')
        result[r.event_id] = r
    return result


def missing_time_extension_has_priority(ctx, boundary):
    """Do not wait on a room upgrade ahead of an imminent held target extension."""
    e = ctx.engine
    extensions = e.load_extendable_bookings()
    if not extensions:
        return False
    now = datetime.now().astimezone()
    _, _, held = e.calculate_extension_capacity_holds(extensions, ctx.tracker, ctx.practice_plan,
        e.load_disabled_dates(ctx.settings), time_prefs=e.load_time_preferences(ctx.settings),
        now=now.replace(tzinfo=None))
    for item in held:
        timing = e.plan_horizon_extension(item['room'], item['date'], clock_minutes(item['startTime'])/60,
            clock_minutes(item['endTime'])/60, clock_minutes(item['target_end'])/60,
            now=now.replace(tzinfo=None))
        if timing.can_extend:
            return True
        if timing.next_unlock_at is not None:
            edge = timing.next_unlock_at.astimezone()
            if edge <= boundary and edge <= now + timedelta(seconds=180):
                return True
    return False


def preserves_capacity(ctx, plan):
    from progressive_capacity import preserves_transfer_capacity
    e = ctx.engine
    return preserves_transfer_capacity(e, plan, events=ctx.arguments(seed=plan.seed)['events'], gaps=ctx.gaps,
        settings=ctx.settings, practice_plan=ctx.practice_plan, policy=ctx.policy,
        now=datetime.now().astimezone(), extensions=e.load_extendable_bookings())


class LiveContext:
    """Fresh observations and exact writes; all calls run beneath the runtime lock."""
    def __init__(self, engine, page, settings, practice_plan):
        self.engine, self.page, self.settings, self.practice_plan = engine, page, settings, practice_plan
        self.policy = engine.require_live_room_policy()
        self.ignored = engine.load_ignored_events(settings)
        self.blackouts = engine.load_rebooking_blackouts(settings)
        self.tracker = None
        self.gaps = None
        self.day = None
        self.actions = 0

    def scan(self, day, *, grid=True):
        e = self.engine
        proof = self.page.context.new_page()
        try:
            today = datetime.now().date()
            if grid:
                self.policy = e.refresh_live_room_policy(proof, e.load_room_preferences(self.settings), today=today)
            tracker = e.BookingTracker()
            e.scan_agenda(proof, tracker, today, ignored_events=self.ignored,
                window_dates=self.policy.booking_dates(today), snapshot_path=e.AGENDA_SNAPSHOT_FILE)
            e.apply_rebooking_blackouts(tracker, self.blackouts)
            self.tracker, self.day = tracker, day
            if grid:
                e.open_practice_room_overview(proof, today)
                if day != today:
                    e.navigate_to_day(proof, (day - today).days, 0, base_date=today)
                e.wait_for_practice_room_grid(proof, day)
                self.gaps = e.get_available_slots(proof)
            return current_reservations(tracker.agenda_events)
        finally:
            proof.close()

    def prove(self, r):
        e = self.engine
        proof = self.page.context.new_page()
        try:
            e.safe_goto(proof, r.event_url)
            e.verify_persisted_booking_page(proof, r.room, r.day, time_text(r.start), time_text(r.end))
        finally:
            proof.close()

    def arguments(self, *, seed=None):
        from room_upgrade_runtime import planning_events
        e, day = self.engine, self.day
        events, ignored_ids = planning_events(e, self.tracker, self.ignored)
        blocked = [(a*60, b*60) for a,b in e.blackout_conflict_ranges(self.blackouts).get(day.isoformat(), ())]
        return dict(events=events, available_data=self.gaps, policy=self.policy,
            now=datetime.now().astimezone(), time_preferences=resolve_time_preferences(e.load_time_preferences(self.settings), day),
            planning=load_booking_strategy(self.settings).daily_planning, seed=seed,
            peak_start=int(e.PEAK_START*60), peak_end=int(e.PEAK_END*60),
            peak_limit=int(e.MAX_PEAK_HOURS*60),
            free_horizon_overrides_peak=getattr(e, "FREE_HORIZON_OVERRIDES_PEAK", False) is True,
            free_horizon_minutes=e.FREE_HORIZON_MINUTES, same_room_gap=e.SAME_ROOM_GAP_MINUTES,
            freeze_minutes=load_booking_strategy(self.settings).daily_planning.upgrade_freeze_hours*60,
            blocked_intervals=blocked, ignored_event_ids=ignored_ids, protected_extensions=e.load_extendable_bookings())

    def write_edit(self, receipt, old, new, *, restoring=False):
        e = self.engine
        step = TransferEdit(old, new)
        def check():
            # Exact live editor validation remains mandatory. This check keeps
            # the paired writes close together instead of rescanning the whole
            # week during the short interval of released fallback coverage.
            if e.list_pending_mutation_receipts() != [receipt]:
                return False
            if not restoring and datetime.now(timezone.utc) < datetime.fromisoformat(receipt['transfer']['opens_at']):
                return False
            return True
        result = e.edit_reservation_room_time(self.page, step, revalidate=check,
            freeze_minutes=0 if restoring else load_booking_strategy(self.settings).daily_planning.upgrade_freeze_hours*60,
            transaction_receipt=receipt)
        if result:
            self.actions += 1
        return result

    def write_cancel(self, receipt, r):
        e = self.engine
        def check():
            return (e.list_pending_mutation_receipts() == [receipt]
                and datetime.now(timezone.utc) >= datetime.fromisoformat(receipt['transfer']['opens_at']))
        records = [{**r.as_booking(), 'isReservation': True}]
        e.cancel_reservation_exact(self.page, records, event_id=r.event_id, room=r.room,
            date_str=r.day.isoformat(), start_time=time_text(r.start), end_time=time_text(r.end),
            today=datetime.now().date(), live_dates=self.policy.booking_dates(datetime.now().date()),
            ignored_events=self.ignored, transfer_receipt=receipt, transfer_revalidate=check)
        self.actions += 1


def _children(receipt):
    return [r for r in load_journal()['receipts'].values() if r.get('parent_id') == receipt['id']]


def _created_record(engine, receipt, role, actual):
    children = [r for r in _children(receipt) if r['transfer_role'] == role]
    if any(r['status'] == 'pending' for r in children):
        raise engine.BookingVerificationError('Transfer creation still needs independent reconciliation')
    verified = [r for r in children if r['status'] == 'verified']
    if len(verified) > 1:
        raise engine.BookingVerificationError('Transfer has duplicate successful creation receipts')
    if not verified:
        return None
    child = verified[0]
    event_id = engine.parse_confirmed_event_id(child.get('event_url', ''))
    r = actual.get(event_id)
    if r is None or any(record(r)[k] != child[k] for k in ('room', 'date', 'start', 'end')):
        raise engine.BookingVerificationError('Verified transfer creation no longer matches its exact booking')
    return r


def require_recovery_preferences(ctx, originals):
    """Compensation cannot override the user's currently protected intervals."""
    from booking_blackouts import load_rebooking_blackouts, interval_overlaps_blackout
    from event_identity import is_v2_event_identity_key
    e, settings = ctx.engine, ctx.settings
    disabled = e.load_disabled_dates(settings)
    ignored = e.load_ignored_events(settings)
    blackouts = load_rebooking_blackouts(settings)
    time_preferences = e.load_time_preferences(settings)
    for r in originals:
        reason = None
        if e.is_date_disabled(r.day, disabled):
            reason = 'the date is now disabled'
        elif ctx.practice_plan.enabled and ctx.practice_plan.target_for(r.day) <= 0:
            reason = 'the daily practice target is now zero'
        elif interval_overlaps_blackout(blackouts, r.day, r.start, r.end):
            reason = 'the interval is now protected by a cancellation blackout'
        elif not e.interval_is_strictly_preferred(r.start / 60, r.end / 60,
                                                resolve_time_preferences(time_preferences, r.day)):
            reason = 'the interval is outside the current strict time window'
        else:
            # Historical titles are deliberately absent from the transaction.
            # Matching a saved reservation's exact tuple is used only as a
            # conservative veto, never to authorize an edit or ignore a clash.
            for key in ignored:
                if key == f'{r.day.isoformat()}_{time_text(r.start)}_{time_text(r.end)}':
                    reason = 'the reservation is now ignored'
                    break
                if is_v2_event_identity_key(key):
                    value = json.loads(key[3:])
                    if (value.get('isReservation') is True and value.get('date') == r.day.isoformat()
                            and value.get('room') == r.room.upper()
                            and value.get('start') == time_text(r.start) and value.get('end') == time_text(r.end)):
                        reason = 'the reservation is now ignored'
                        break
        if reason:
            raise e.BookingVerificationError(
                f'Transfer recovery requires user attention: {r.day} {time_text(r.start)}-{time_text(r.end)} '
                f'cannot be restored because {reason}. The transaction remains pending; no preference is overridden.')


def recover_transfer(ctx, receipt):
    """Classify actual progress; finalize a secured prefix or restore its before-state."""
    e, t = ctx.engine, validate_transfer(receipt['transfer'])
    old = tuple(reservation(r) for r in t['originals'])
    retained = {r['event_id']: reservation(r) for r in t['remaining']}
    prefix = reservation(t['replacement'])
    seed_before = reservation(t['seed_before']) if t['seed_before'] else None
    actual = ctx.scan(prefix.day)
    if any(r.get('parent_id') == receipt['id'] for r in e.list_pending_mutation_receipts() if r != receipt):
        raise e.BookingVerificationError('Transfer child remains uncertain; no restoration or repeat Save is allowed')
    seed = actual.get(seed_before.event_id) if seed_before else _created_record(e, receipt, 'seed', actual)
    if seed_before and seed not in (seed_before, prefix):
        raise e.BookingVerificationError('Transfer anchor matches neither exact saved state')
    secured = seed is not None and (seed.room, seed.day, seed.start, seed.end) == (prefix.room, prefix.day, prefix.start, prefix.end)
    if secured and 'destination' not in t['started_steps']:
        raise e.BookingVerificationError('An unattempted destination changed outside the recorded transfer')
    for r in old:
        observed = actual.get(r.event_id)
        if observed != r and f'source:{r.event_id}' not in t['started_steps']:
            raise e.BookingVerificationError('An unattempted fallback changed externally; no automatic rebooking is allowed')
        if observed not in (r, retained.get(r.event_id)):
            raise e.BookingVerificationError('Fallback changed outside its exact recorded transfer states')
    if secured:
        if any(actual.get(r.event_id) != retained.get(r.event_id) for r in old):
            raise e.BookingVerificationError('Secured prefix has an unexpected fallback state; retaining transaction')
        for r in (*retained.values(), seed):
            ctx.prove(r)
        def completed(data):
            data['plans'] = [p for p in data['plans'] if p['id'] != t['plan_id']]
            if retained:
                target = reservation(t['target'])
                next_end = min(target.end, seed.end + 15)
                if target.end - next_end < t['minimum_minutes']:
                    next_end = target.end
                edge = local_instant(seed.day, next_end) - timedelta(
                    minutes=ctx.policy.horizon_minutes_for(seed.room), seconds=ctx.policy.site_clock_offset_bounds[0])
                data['plans'].append(dict(id=t['plan_id'], fingerprint=fingerprint(ctx.settings),
                    originals=[record(r) for r in retained.values()], seed=record(seed), target=record(replace(target,event_id=seed.event_id)),
                    next_at=edge.isoformat(), observed_at=datetime.now(timezone.utc).isoformat()))
        # Persist continuation before clearing the parent, including after crashes.
        update_state(completed)
        e.verify_mutation_receipt(receipt['id'], event_url=seed.event_url)
        print(f'PROGRESSIVE UPGRADE VERIFIED: {seed.room} {time_text(seed.start)}-{time_text(seed.end)}; '
              f'{sum(r.duration for r in retained.values())} fallback minutes retained')
        return True

    # Seed is absent/unchanged. Restore in reverse order, checking each exact
    # original. A failed restoration stays pending and visibly blocks new work.
    restore_order = [next(r for r in old if r.event_id == event_id)
                     for event_id in reversed(t['adjustment_order'])]
    restore_order += [r for r in old if r.event_id not in t['adjustment_order']]
    restoring = [r for r in restore_order if actual.get(r.event_id) != r
                 and (r.event_id in actual or _created_record(e, receipt, f'restore:{r.event_id}', actual) is None)]
    # Check every planned restoration before the first write. An ignored
    # retained fragment also protects its original from being enlarged.
    require_recovery_preferences(ctx, [*restoring, *(actual[r.event_id] for r in restoring if r.event_id in actual)])
    for r in restore_order:
        current = actual.get(r.event_id)
        if current == r:
            ctx.prove(r)
            continue
        if current is not None:
            if not ctx.write_edit(receipt, current, r, restoring=True):
                raise e.BookingVerificationError('Fallback restoration was refused; transfer remains pending')
            actual[r.event_id] = r
        else:
            restored = _created_record(e, receipt, f'restore:{r.event_id}', actual)
            if restored is None:
                prepared_page = ctx.page.context.new_page()
                try:
                    prepared = prepare_seed(e, prepared_page, r)
                    restored = save_seed(e, prepared, receipt, role=f'restore:{r.event_id}') if isinstance(prepared, PreparedSeed) else False
                    if not restored:
                        raise e.BookingVerificationError('Released fallback could not be rebooked; recovery remains pending')
                    ctx.actions += 1
                finally:
                    prepared_page.close()
                actual[restored.event_id] = restored
            ctx.prove(restored)
    if seed_before:
        ctx.prove(seed_before)
    forget_plan(t['plan_id'])
    e.resolve_mutation_receipt(receipt['id'], resolution='Progressive transfer not completed; every original interval restored and verified')
    print('PROGRESSIVE TRANSFER RESTORED: previous complete practice coverage verified')
    ctx.scan(prefix.day)
    return False


def execute_transfer(ctx, plan, saved, args):
    e = ctx.engine
    def capacity(candidate):
        return preserves_capacity(ctx, candidate)
    if not capacity(plan):
        return False
    adjustments = ordered_adjustments(plan, same_room_gap=e.SAME_ROOM_GAP_MINUTES,
                                      peak_start=int(e.PEAK_START*60), peak_end=int(e.PEAK_END*60))
    if adjustments is None:
        return False
    # Reserve forward AND restoration writes before starting a paired action.
    worst_case = 2 * (len(adjustments) + 1)
    run_started = getattr(e, '_booker_run_started_monotonic', None)
    def enough_runtime():
        if not getattr(args, 'scheduled', False) or not isinstance(run_started, (int, float)):
            return True
        # The existing task has a 14-minute hard stop. Count authentication and
        # lock waiting too, leave a one-minute margin, and reserve time for both
        # forward changes and rollback before releasing any booked minutes.
        remaining = 780 - (time.monotonic() - run_started)
        allowance = max(0, (plan.opens_at - datetime.now(timezone.utc)).total_seconds()) + 60 * worst_case
        if remaining < allowance:
            print('UPGRADE DEFERRED: insufficient scheduled runtime for a complete transfer and recovery')
            return False
        return True
    if not enough_runtime():
        return False
    limit = getattr(args, 'max_actions', None)
    if limit is not None and ctx.actions + worst_case > limit:
        return False
    if getattr(args, 'max_action_minutes', None) is not None and (
            plan.replacement.duration - (plan.seed.duration if plan.seed else 0) > args.max_action_minutes):
        return False
    prepared_page = ctx.page.context.new_page()
    prepared = None
    parent = None
    try:
        if prepared_page is not None:
            prepared = prepare_transfer_destination(e, prepared_page, plan)
            if not isinstance(prepared, PreparedSeed):
                if isinstance(prepared, RoomPermissionRefusal):
                    remember_denial(plan.replacement.room, plan.target.day, now=datetime.now(timezone.utc))
                return False
        wait = (plan.opens_at - datetime.now(timezone.utc)).total_seconds()
        if wait > 180:
            return False
        actual = ctx.scan(plan.target.day)
        opportunity = _opportunity(saved)
        fresh = plan_progressive_transfer(opportunity, acceptable_layout=capacity, **ctx.arguments(seed=plan.seed))
        if fresh is None:
            return False
        # The prefilled prefix must match the freshly chosen step; do not edit
        # sources for a different stale form after a slow queue/scan.
        if (fresh.originals, fresh.remaining, fresh.replacement, fresh.seed, fresh.target) != (
                plan.originals, plan.remaining, plan.replacement, plan.seed, plan.target):
            return False
        if not capacity(fresh):
            return False
        plan = fresh
        if (plan.opens_at - datetime.now(timezone.utc)).total_seconds() > 180:
            return False
        for r in (*plan.originals, *((plan.seed,) if plan.seed else ())):
            if actual.get(r.event_id) != r:
                return False
            ctx.prove(r)
        if datetime.now(timezone.utc) < plan.opens_at:
            # Complete slow agenda/grid work BEFORE the boundary. At the edge
            # each exact edit/seed receives a new server-side check, without a
            # whole-week scan between releasing and reacquiring its minutes.
            e.wait_until_datetime(plan.opens_at.astimezone().replace(tzinfo=None), ctx.page,
                                  label='UPGRADE TRANSFER', settle_seconds=0)
        if datetime.now(timezone.utc) < plan.opens_at:
            return False
        if prepared is not None:
            preflight = preflight_transfer_destination(e, prepared, plan)
            if preflight is not True:
                if isinstance(preflight, RoomPermissionRefusal):
                    remember_denial(plan.replacement.room, plan.target.day, now=datetime.now(timezone.utc))
                return False
        if not enough_runtime():
            return False
        t = transaction_payload(plan, plan_id=saved['id'], baseline_ids=ctx.tracker.agenda_active_event_ids,
                                minimum_minutes=ctx.policy.minimum_block_minutes, seed=plan.seed,
                                adjustment_order=[r.event_id for r, _ in adjustments])
        parent = record_pending('transfer', room=plan.replacement.room, booking_date=plan.target.day.isoformat(),
            start=time_text(plan.replacement.start), end=time_text(plan.replacement.end), transfer=t)
        for old, new in adjustments:
            if new is None:
                ctx.write_cancel(parent, old)
            elif not ctx.write_edit(parent, old, new):
                recover_transfer(ctx, parent)
                return False
        if plan.seed is None:
            result = save_seed(e, prepared, parent)
            if result:
                ctx.actions += 1
        else:
            result = ctx.write_edit(parent, plan.seed, plan.replacement)
        if isinstance(result, RoomPermissionRefusal):
            remember_denial(plan.replacement.room, plan.target.day, now=datetime.now(timezone.utc))
        # Even a known rejection is classified from the actual complete agenda;
        # a throwing/uncertain Save propagates with the parent intact.
        return recover_transfer(ctx, parent)
    except QuotaWait as exc:
        if parent is not None:
            recover_transfer(ctx, parent)
        # Recovery can itself use verified edits. Preserve their action cost
        # when an ordinary run continues with fresh daily/advance planning.
        exc.completed_actions = ctx.actions
        raise
    finally:
        if prepared_page is not None:
            prepared_page.close()


def _opportunity(saved):
    originals = tuple(reservation(r) for r in saved['originals'])
    seed = reservation(saved['seed']) if saved['seed'] else None
    target = reservation(saved['target'])
    group = (*originals, *((seed,) if seed else ()))
    if len(group) == 1:
        change = RoomUpgrade(group[0], target)
    else:
        change = RoomConsolidation(group, target)
    return SimpleNamespace(change=change)


def process_progressive_upgrades(engine, page, settings, practice_plan, args, tracker,
                                 total_actions, booking_details):
    if not allowed_scope(args):
        return total_actions, tracker
    pending = engine.list_pending_mutation_receipts()
    parents = [r for r in pending if r['kind'] == 'transfer']
    if pending and not parents:
        return total_actions, tracker
    planning = load_booking_strategy(settings).daily_planning
    ctx = LiveContext(engine, page, settings, practice_plan)
    ctx.actions = total_actions
    if parents:
        if len(parents) != 1 or len(pending) != 1:
            raise engine.BookingVerificationError('Uncertain transfer or unrelated mutations require reconciliation first')
        parent = parents[0]
        t = parent['transfer']
        group = tuple(reservation(r) for r in t['originals'])
        scope_group = (*group, *((reservation(t['seed_before']),) if t['seed_before'] else ()))
        limit = getattr(args, 'max_actions', None)
        if (not in_scope(args, scope_group, parent['room'], group[0].day)
                or limit is not None and total_actions + 2 * (len(group) + 1) > limit):
            raise engine.BookingVerificationError('Transfer recovery remains pending outside this run\'s scope')
        if recover_transfer(ctx, parent):
            booking_details.append(transfer_summary(parent))
        tracker = ctx.tracker
    if not planning.upgrade_rooms:
        return ctx.actions, tracker
    state = load_state()
    started = time.monotonic()
    now = datetime.now(timezone.utc)
    enabled = engine.load_disabled_dates(settings)
    current = current_reservations(tracker.agenda_events)
    candidates = []
    for saved in state['plans']:
        original = [reservation(r) for r in saved['originals']]
        seed = reservation(saved['seed']) if saved['seed'] else None
        target = reservation(saved['target'])
        if (saved['fingerprint'] != fingerprint(settings)
                or any(current.get(r.event_id) != r for r in [*original, *((seed,) if seed else ())])
                or target.room not in ctx.policy.room_order
                or engine.is_date_disabled(target.day, enabled)
                or practice_plan.enabled and practice_plan.target_for(target.day) <= 0
                or local_instant(target.day, min([target.start, *[r.start for r in original]])) <= now + timedelta(hours=planning.upgrade_freeze_hours)):
            forget_plan(saved['id'])
            continue
        if not in_scope(args, [*original, *([seed] if seed else [])], target.room, target.day):
            continue
        denied = state['denials'].get(denial_key(target.room, target.day))
        if denied and datetime.fromisoformat(denied) > now:
            continue
        earliest_end = (min(seed.end + 15, target.end) if seed else
                        target.start + ctx.policy.minimum_block_minutes)
        current_edge = local_instant(target.day, earliest_end) - timedelta(
            minutes=ctx.policy.horizon_minutes_for(target.room), seconds=ctx.policy.site_clock_offset_bounds[0])
        if current_edge > now + timedelta(seconds=180):
            continue
        candidates.append(saved)
    candidates.sort(key=lambda p: (p['next_at'], ctx.policy.room_order.index(p['target']['room'])))
    for saved in candidates:
        # Leave room beneath the existing 14-minute task lifetime for a complete
        # transfer or rollback; the next scheduled run can try further prospects.
        if time.monotonic() - started > 480:
            break
        target = reservation(saved['target'])
        seed = reservation(saved['seed']) if saved['seed'] else None
        # An earlier alternative in this same sweep may just have proved a
        # room/date permission denial. Do not revisit all its other starts
        # using the precomputed candidate list before trying another room.
        latest_denial = load_state()['denials'].get(denial_key(target.room, target.day))
        if latest_denial and datetime.fromisoformat(latest_denial) > datetime.now(timezone.utc):
            continue
        # Whole-session upgrades retain their established path when already
        # entirely bookable. An active partial transfer still needs completion.
        ctx.scan(target.day)
        plan = plan_progressive_transfer(_opportunity(saved), acceptable_layout=lambda p: preserves_capacity(ctx, p),
                                         **ctx.arguments(seed=seed))
        if plan is None:
            forget_plan(saved['id'])
            continue
        if plan.opens_at > datetime.now(timezone.utc) + timedelta(seconds=180):
            continue
        if missing_time_extension_has_priority(ctx, plan.opens_at):
            print('UPGRADE DEFERRED: an imminent extension is needed to fill the daily target')
            return ctx.actions, ctx.tracker or tracker
        if time.monotonic() - started > 360:
            break
        changed = execute_transfer(ctx, plan, saved, args)
        tracker = ctx.tracker or tracker
        if changed:
            booking_details.append(f'UPGRADED PART: {target.day} {target.room} '
                f'{time_text(plan.replacement.start)}-{time_text(plan.replacement.end)}; '
                f'{sum(r.duration for r in plan.remaining)} fallback minutes retained')
            # One bounded transfer per run; never wait another quarter-hour.
            break
    return ctx.actions, tracker
