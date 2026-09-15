"""Safe intermediate reservations when Asimut forbids overlapping originals.

Every intermediate edit retains one exact reservation and its full duration.
Only the final, freshly approved enlarged booking permits donor retirement.
"""
from dataclasses import dataclass, replace
from datetime import datetime

from daily_planner import interval_overlap_minutes, soft_time_value
from room_upgrades import (Reservation, RoomUpgrade, RoomConsolidation, clock_minutes,
                          find_room_upgrades, apply_upgrade_to_events, availability_after_upgrade)


def staging_arguments(change, kwargs):
    """Soft hours may be used temporarily; strict dated/global hours never relax."""
    args = dict(kwargs)
    prefs = args['time_preferences']
    if not prefs.get('strict_mode'):
        args['time_preferences'] = {**prefs, 'enabled': False}
    args['planning'] = replace(args['planning'], enabled=False)
    new = change.replacement
    # The final session must remain clear of every intermediate reservation.
    args['blocked_intervals'] = (*args.get('blocked_intervals', ()), (new.start, new.end))
    return args


def prepare_consolidation(change, *, excluded_steps=(), **kwargs):
    """Find a collision-free sequence or leave every original untouched.

    Intermediate rooms must themselves be superior to the moved donor. Thus a
    failed restoration still retains all minutes in a better room, with its
    exact intermediate time visible in the pending transaction.
    """
    if not isinstance(change, RoomConsolidation) or change.bridges:
        raise ValueError('Staging requires an unstaged exact consolidation')
    new = change.replacement
    same_gap = kwargs.get('same_room_gap', 60)
    peak_start, peak_end = kwargs.get('peak_start', 540), kwargs.get('peak_end', 960)
    peak_limit = kwargs.get('peak_limit', 120)
    def overlaps(r, gap=0):
        return new.start < r.end + gap and new.end > r.start - gap
    donors = change.retired
    required = {r.event_id for r in donors if overlaps(r, same_gap if r.room == new.room else 0)}
    # Retained peak bookings still count during the enlarged anchor Save.
    interim_peak = sum(interval_overlap_minutes(clock_minutes(e['startTime']), clock_minutes(e['endTime']),
                                                 peak_start, peak_end) for e in kwargs['events']
                       if e.get('isReservation') is True and e['date'] == str(new.day)
                       and e['eventId'] != change.original.event_id)
    interim_peak += interval_overlap_minutes(new.start, new.end, peak_start, peak_end)
    if new.day.weekday() < 5 and interim_peak > peak_limit:
        required.update(r.event_id for r in donors if interval_overlap_minutes(r.start, r.end, peak_start, peak_end))
    if not required:
        return change
    args = staging_arguments(change, kwargs)
    prefs = kwargs['time_preferences']
    window = ((round(prefs['start_hour'] * 60), round(prefs['end_hour'] * 60)) if prefs.get('enabled') else None)
    pending = tuple(r for r in donors if r.event_id in required)
    def search(left, events, gaps, bridges):
        if not left:
            staged = replace(change, bridges=bridges)
            peak = sum(interval_overlap_minutes(clock_minutes(e['startTime']), clock_minutes(e['endTime']),
                                                 peak_start, peak_end) for e in events
                       if e.get('isReservation') is True and e['date'] == str(new.day)
                       and e['eventId'] != change.original.event_id)
            if new.day.weekday() < 5 and peak + interval_overlap_minutes(new.start, new.end, peak_start, peak_end) > peak_limit:
                return None
            return staged
        # Try each donor order: an earlier move can free a useful staging gap.
        for donor in left:
            options = find_room_upgrades(donor, **{**args, 'events': events, 'available_data': gaps})
            options = [b for b in options if b.replacement.room != new.room
                       and (b.original, b.replacement) not in excluded_steps]
            options.sort(key=lambda b: (-soft_time_value(b.replacement.start, b.replacement.duration, window),
                                       abs(b.replacement.start - donor.start), b.rank))
            for bridge in options:
                found = search(tuple(r for r in left if r != donor), apply_upgrade_to_events(events, bridge),
                               availability_after_upgrade(gaps, bridge), (*bridges, bridge))
                if found:
                    return found
        return None
    return search(pending, kwargs['events'], kwargs['available_data'], ())


def transaction_allows_step(receipt, step):
    """The durable parent authorizes only its recorded forward/reverse edits."""
    from room_upgrades import consolidation_from_receipt
    group = consolidation_from_receipt(receipt)
    if isinstance(step, RoomConsolidation):
        return step.originals == group.staged_originals and step.replacement == group.replacement and not step.bridges
    if isinstance(step, RoomUpgrade):
        return any((step.original == b.original and step.replacement == b.replacement)
                   or (step.original == b.replacement and step.replacement == b.original) for b in group.bridges)
    return False


def fresh_staging_arguments(engine, page, day, *, verified_tracker=None):
    from booking_strategy import load_booking_strategy
    from date_time_preferences import resolve_time_preferences
    from room_upgrade_runtime import planning_events
    settings = engine.load_settings_document(engine.settings_file)
    planning = load_booking_strategy(settings).daily_planning
    ignored = engine.load_ignored_events(settings)
    blackouts = engine.load_rebooking_blackouts(settings)
    policy = engine.require_live_room_policy()
    tracker = verified_tracker if verified_tracker is not None else engine.BookingTracker()
    now = datetime.now().astimezone()
    if verified_tracker is None:
        engine.scan_agenda(page, tracker, now.date(), ignored_events=ignored,
                          window_dates=policy.booking_dates(now.date()), snapshot_path=engine.AGENDA_SNAPSHOT_FILE)
    events, ignored_ids = planning_events(engine, tracker, ignored)
    engine.open_practice_room_overview(page, now.date())
    if day != now.date():
        engine.navigate_to_day(page, (day - now.date()).days, 0, base_date=now.date())
    engine.wait_for_practice_room_grid(page, day)
    gaps = engine.get_available_slots(page)
    return dict(events=events, available_data=gaps, policy=policy, now=datetime.now().astimezone(),
        time_preferences=resolve_time_preferences(engine.load_time_preferences(settings), day), planning=planning,
        peak_start=int(engine.PEAK_START * 60), peak_end=int(engine.PEAK_END * 60),
        peak_limit=int(engine.MAX_PEAK_HOURS * 60), same_room_gap=engine.SAME_ROOM_GAP_MINUTES,
        freeze_minutes=planning.upgrade_freeze_hours * 60,
        blocked_intervals=[(a * 60, b * 60) for a, b in engine.blackout_conflict_ranges(blackouts).get(str(day), ())],
        protected_extensions=engine.load_extendable_bookings(), ignored_event_ids=ignored_ids)


def revalidate_staging_step(engine, page, receipt, step, *, restoring=False):
    from room_upgrades import consolidation_from_receipt
    if not transaction_allows_step(receipt, step):
        return False
    check = page.context.new_page()
    try:
        outcome, verified_tracker = engine.verify_consolidation_state(check, receipt)
        if outcome not in {'untouched', 'staged'}:
            return False
        group = consolidation_from_receipt(receipt)
        args = fresh_staging_arguments(engine, check, step.original.day, verified_tracker=verified_tracker)
        if isinstance(step, RoomConsolidation):
            possible = find_room_upgrades(step.original, _originals=step.originals, **args)
            # The planner accounts for the final state; ensure that the actual
            # intermediate donors also permit Save without a personal clash.
            if prepare_consolidation(step, **args) != step:
                return False
        else:
            if restoring:
                args['planning'] = replace(args['planning'], enabled=False)
                # Restoration preserves the original saved times, but honours
                # a subsequently changed strict window and every live veto.
                if not args['time_preferences'].get('strict_mode'):
                    args['time_preferences'] = {**args['time_preferences'], 'enabled': False}
                possible = find_room_upgrades(step.original, _allow_room_downgrade=True, **args)
            else:
                possible = find_room_upgrades(step.original, **staging_arguments(group, args))
        return any(c.original == step.original and c.replacement == step.replacement for c in possible)
    finally:
        check.close()


def restore_staged_consolidation(engine, page, receipt):
    """Recovery before anchor Save restores exact originals; it never cancels."""
    from room_upgrades import consolidation_from_receipt
    group = consolidation_from_receipt(receipt)
    outcome, tracker = engine.verify_consolidation_state(page, receipt)
    if outcome not in {'untouched', 'staged'}:
        raise engine.BookingVerificationError('Restoration requires a proved unchanged anchor')
    for bridge in reversed(group.bridges):
        actual = next((Reservation.from_event(e) for e in tracker.agenda_events
                       if e.get('eventId') == bridge.original.event_id), None)
        if actual == bridge.original:
            continue
        reverse = RoomUpgrade(bridge.replacement, bridge.original)
        changed = engine.edit_reservation_room_time(page, reverse, transaction_receipt=receipt,
            revalidate=lambda: revalidate_staging_step(engine, page, receipt, reverse, restoring=True))
        if not changed:
            raise engine.BookingVerificationError('Original slot could not be restored; full intermediate booking remains secured')
        outcome, tracker = engine.verify_consolidation_state(page, receipt)
    if outcome != 'untouched':
        raise engine.BookingVerificationError('Staging restoration is incomplete; all further changes remain blocked')
    engine.resolve_mutation_receipt(receipt['id'], resolution='Consolidation not completed; every original restored and verified')
    print('CONSOLIDATION RESTORED: every original reservation independently verified; no booking cancelled')
    return False


@dataclass(frozen=True)
class RestoredStaging:
    actions_used: int
    failed_step: RoomUpgrade | RoomConsolidation

    def __bool__(self):
        return False


def execute_staged_consolidation(engine, page, change, *, revalidate, dry_run, freeze_minutes):
    """Journal, move exact donors, secure the anchor, then use normal retirement."""
    if not change.bridges:
        raise ValueError('No intermediate reservations to execute')
    if revalidate() is not True:
        return False
    if dry_run:
        # These checks do not prove the final anchor can Save: donors have not
        # moved. The real run must freshly check it after every bridge succeeds.
        for bridge in change.bridges:
            def preview_check():
                check = page.context.new_page()
                try:
                    args = fresh_staging_arguments(engine, check, bridge.original.day)
                    options = find_room_upgrades(bridge.original, **staging_arguments(change, args))
                    return any(c.original == bridge.original and c.replacement == bridge.replacement for c in options)
                finally:
                    check.close()
            approved = engine.edit_reservation_room_time(page, bridge, revalidate=preview_check,
                                                         dry_run=True, freeze_minutes=freeze_minutes)
            if not getattr(approved, 'preview_ready', False):
                return RestoredStaging(0, bridge)
        print('CONSOLIDATION STAGING READY (not saved): final anchor still requires a fresh check after staging')
        return engine.UpgradePreview()
    def record(r):
        return dict(event_id=r.event_id, room=r.room, date=str(r.day),
                    start=r.as_booking()['startTime'], end=r.as_booking()['endTime'])
    with engine.booking_save_boundary():
        if engine.list_pending_mutation_receipts():
            raise engine.BookingVerificationError('An unresolved mutation blocks consolidation staging')
        receipt = engine.record_pending_consolidation(room=change.replacement.room, booking_date=str(change.replacement.day),
            start=change.replacement.as_booking()['startTime'], end=change.replacement.as_booking()['endTime'],
            event_url=change.original.event_url, original=record(change.original),
            originals=[record(r) for r in change.originals],
            bridges=[dict(original=record(b.original), replacement=record(b.replacement)) for b in change.bridges])
    applied_bridges = 0
    for step in (*change.bridges, change.prepared):
        changed = engine.edit_reservation_room_time(page, step, transaction_receipt=receipt,
            revalidate=lambda: revalidate_staging_step(engine, page, receipt, step), freeze_minutes=freeze_minutes)
        if not changed:
            restore_staged_consolidation(engine, page, receipt)
            return RestoredStaging(2 * applied_bridges, step)
        if isinstance(step, RoomUpgrade):
            applied_bridges += 1
    return True
