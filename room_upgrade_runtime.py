"""Fresh scan, whole-day comparison and bounded execution of reservation upgrades.

The existing booking engine is passed explicitly because its CLI also runs as
``__main__``. Importing it here would create a second set of live-policy globals.
Normal creates/extensions finish first; this phase never reserves speculative
capacity or cancels any reservation.
"""

import copy
import time
from datetime import date, datetime

from booking_strategy import load_booking_strategy
from date_time_preferences import resolve_time_preferences
from event_identity import event_identity_v2, resolve_ignored_event_keys
from operation_control import operation_stage
from room_upgrades import Reservation, find_room_upgrades, local_instant, time_text, clock_minutes

UPGRADE_PHASE_SECONDS = 180
MAX_UPGRADE_ATTEMPTS = 6


def planning_events(engine, tracker, ignored_events):
    events = copy.deepcopy(tracker.agenda_events)
    ignored = resolve_ignored_event_keys(ignored_events, events).ignored_v2_keys
    ignored_ids = set()
    for event in events:
        if event_identity_v2(event) in ignored:
            event["blocksConflict"] = False
            ignored_ids.add(event["eventId"])
    return events, ignored_ids


def tracker_for_events(engine, events, blackouts):
    """Rebuild accounting by identity, avoiding subtraction of merged conflicts."""
    tracker = engine.BookingTracker()
    tracker.agenda_events = copy.deepcopy(events)
    tracker.agenda_active_event_ids = [e["eventId"] for e in events]
    for event in events:
        start = engine.normalize_time_string(event["startTime"])
        end = engine.normalize_time_string(event["endTime"])
        sh, sm = map(int, start.split(":"))
        eh, em = map(int, end.split(":"))
        tracker.add_existing_event(date.fromisoformat(event["date"]), sh + sm / 60,
                                   eh + em / 60, is_reservation=event["isReservation"],
                                   room=event["room"], blocks_conflict=event.get("blocksConflict", True))
    engine.apply_rebooking_blackouts(tracker, blackouts)
    return tracker


def preserves_remaining_day_plan(engine, upgrade, *, events, gaps, settings,
                                  practice_plan, policy, now, extensions):
    """An improved room may not consume the only useful remaining practice slot."""
    day = upgrade.original.day
    prefs = resolve_time_preferences(engine.load_time_preferences(settings), day)
    planning = load_booking_strategy(settings).daily_planning
    blackouts = engine.load_rebooking_blackouts(settings)
    before = tracker_for_events(engine, events, blackouts)
    disabled_dates = engine.load_disabled_dates(settings)
    enabled_dates = [d for d in policy.booking_dates(now.date()) if not engine.is_date_disabled(d, disabled_dates)]
    remaining_hours, _ = engine.calculate_target_hours_for_day(
        day, enabled_dates, before, practice_plan=practice_plan)
    if remaining_hours < policy.minimum_block_minutes / 60:
        return True
    after_events = [{**e, **upgrade.replacement.as_booking()} if e["eventId"] == upgrade.original.event_id else e
                    for e in events]
    after = tracker_for_events(engine, after_events, blackouts)

    def coverage(tracker):
        holds = engine.calculate_extension_capacity_holds(
            extensions, tracker, practice_plan, disabled_dates, time_prefs=prefs, now=now.replace(tzinfo=None))
        target_holds, peak_holds, _ = holds
        held = target_holds.get(day.isoformat(), 0)
        held_peak = peak_holds.get(day.isoformat(), 0)
        remaining = max(0, remaining_hours - held / 60)
        if remaining < policy.minimum_block_minutes / 60 or not engine.fragmentation_allows_new_booking(tracker, day)[0]:
            return (held, held)
        opportunities = engine.build_day_booking_opportunities(
            gaps, day, tracker, prefs, planning, now=now.replace(tzinfo=None),
            remaining_daily_hours=remaining,
            reserved_peak_minutes=held_peak, reserved_weekly_minutes=sum(target_holds.values()))
        chosen = engine.select_day_plan(
            opportunities, planning, now=now.replace(tzinfo=None),
            target_minutes=int(round(remaining * 60)),
            allow_fragmented_sessions=policy.allow_fragmented_sessions,
            remaining_peak_minutes=max(0, tracker.get_remaining_peak_minutes(day) - held_peak),
            same_room_gap_minutes=engine.SAME_ROOM_GAP_MINUTES)
        window = engine._soft_time_window(prefs)
        return (held + sum(item.potential_minutes for item in chosen),
                held + sum(engine.soft_time_value(item.start_minutes, item.potential_minutes, window) for item in chosen))

    old_coverage, old_quality = coverage(before)
    new_coverage, new_quality = coverage(after)
    return new_coverage >= old_coverage and new_quality + 1e-8 >= old_quality


def process_room_upgrades(engine, page, settings, practice_plan, args, tracker,
                          total_actions, booking_details):
    """Return updated action count/tracker after safe same-day improvements."""
    planning = load_booking_strategy(settings).daily_planning
    if not planning.upgrade_rooms:
        return total_actions, tracker
    if any(getattr(args, flag, False) for flag in
           ("check_only", "plan_only", "agenda_only", "horizon_only", "extensions_only")):
        return total_actions, tracker
    max_actions = getattr(args, "max_actions", None)
    if max_actions is not None and total_actions >= max_actions:
        return total_actions, tracker
    # Do not turn a no-op run into a costly grid scan when no reservation can move.
    now = datetime.now().astimezone()
    freeze_minutes = planning.upgrade_freeze_hours * 60
    if not any(e.get("isReservation") is True
               and (local_instant(date.fromisoformat(e["date"]), clock_minutes(e["startTime"])) - now).total_seconds() > freeze_minutes * 60
               for e in tracker.agenda_events):
        return total_actions, tracker
    operation_stage("Checking better rooms while retaining existing reservations")
    deadline = time.monotonic() + UPGRADE_PHASE_SECONDS
    policy = engine.refresh_live_room_policy(page, engine.load_room_preferences(settings), today=now.date())
    disabled_dates = engine.load_disabled_dates(settings)
    ignored_events = engine.load_ignored_events(settings)
    blackouts = engine.load_rebooking_blackouts(settings)
    attempts = set()
    upgraded_ids = set()
    dry_run = bool(getattr(args, "upgrade_dry_run", False))

    def fresh_agenda(scan_page):
        fresh = engine.BookingTracker()
        engine.apply_rebooking_blackouts(fresh, blackouts)
        engine.scan_agenda(scan_page, fresh, datetime.now().date(), ignored_events=ignored_events,
                           window_dates=policy.booking_dates(datetime.now().date()),
                           snapshot_path=engine.AGENDA_SNAPSHOT_FILE)
        return fresh

    def fresh_gaps(scan_page, day):
        today = datetime.now().date()
        engine.open_practice_room_overview(scan_page, today)
        if day != today:
            engine.navigate_to_day(scan_page, (day - today).days, 0, base_date=today)
        engine.wait_for_practice_room_grid(scan_page, day)
        return engine.get_available_slots(scan_page)

    def candidates_for(event, fresh, gaps, extensions, at):
        day = date.fromisoformat(event["date"])
        if engine.is_date_disabled(day, disabled_dates) or (practice_plan.enabled and practice_plan.target_for(day) <= 0):
            return ()
        if getattr(args, "only_date", None) and event["date"] != args.only_date:
            return ()
        if getattr(args, "upgrade_event_id", None) is not None and event["eventId"] != args.upgrade_event_id:
            return ()
        events, ignored_ids = planning_events(engine, fresh, ignored_events)
        prefs = resolve_time_preferences(engine.load_time_preferences(settings), day)
        blocked = [(a * 60, b * 60) for a, b in engine.blackout_conflict_ranges(blackouts).get(day.isoformat(), ())]
        found = find_room_upgrades(
            Reservation.from_event(event), events=events, available_data=gaps,
            policy=policy, now=at, time_preferences=prefs, planning=planning,
            peak_start=int(engine.PEAK_START * 60), peak_end=int(engine.PEAK_END * 60),
            peak_limit=int(engine.MAX_PEAK_HOURS * 60), same_room_gap=engine.SAME_ROOM_GAP_MINUTES,
            freeze_minutes=freeze_minutes, blocked_intervals=blocked,
            protected_extensions=extensions, ignored_event_ids=ignored_ids)
        return tuple(candidate for candidate in found
                     if (not getattr(args, "only_room", None) or candidate.replacement.room == args.only_room)
                     and (getattr(args, "max_action_minutes", None) is None or candidate.original.duration <= args.max_action_minutes))

    tracker = fresh_agenda(page)
    while len(attempts) < MAX_UPGRADE_ATTEMPTS and time.monotonic() < deadline:
        if max_actions is not None and total_actions >= max_actions:
            break
        now = datetime.now().astimezone()
        extensions = engine.load_extendable_bookings()
        events, _ = planning_events(engine, tracker, ignored_events)
        originals = [e for e in events if e.get("isReservation") is True and e["eventId"] not in upgraded_ids
                     and e["room"] in policy.room_order
                     and (getattr(args, "upgrade_event_id", None) is None or e["eventId"] == args.upgrade_event_id)
                     and (not getattr(args, "only_date", None) or e["date"] == args.only_date)
                     and (local_instant(date.fromisoformat(e["date"]), clock_minutes(e["startTime"])) - now).total_seconds() > freeze_minutes * 60]
        originals.sort(key=lambda e: (e["date"], e["startTime"], e["eventId"]),
                       reverse=load_booking_strategy(settings).reverse_date_order)
        candidate = None
        scanned = {}
        for original in originals:
            if time.monotonic() >= deadline:
                break
            day = date.fromisoformat(original["date"])
            if engine.is_date_disabled(day, disabled_dates):
                continue
            if day not in scanned:
                scanned[day] = fresh_gaps(page, day)
            gaps = scanned[day]
            for choice in candidates_for(original, tracker, gaps, extensions, now):
                key = (choice.original.event_id, choice.replacement.room, choice.replacement.start)
                if key in attempts:
                    continue
                if preserves_remaining_day_plan(engine, choice, events=events, gaps=gaps, settings=settings,
                                                 practice_plan=practice_plan, policy=policy, now=now, extensions=extensions):
                    candidate = choice
                    break
            if candidate:
                break
        if candidate is None:
            break
        attempts.add((candidate.original.event_id, candidate.replacement.room, candidate.replacement.start))
        operation_stage(f"Checking {candidate.replacement.room} {time_text(candidate.replacement.start)}–{time_text(candidate.replacement.end)}")

        def revalidate():
            check_page = page.context.new_page()
            try:
                fresh = fresh_agenda(check_page)
                current = [e for e in fresh.agenda_events if e.get("eventId") == candidate.original.event_id]
                if len(current) != 1 or Reservation.from_event(current[0]) != candidate.original:
                    return False
                gaps = fresh_gaps(check_page, candidate.original.day)
                at = datetime.now().astimezone()
                extensions = engine.load_extendable_bookings()
                if not any(c.replacement == candidate.replacement for c in candidates_for(current[0], fresh, gaps, extensions, at)):
                    return False
                fresh_events, _ = planning_events(engine, fresh, ignored_events)
                return preserves_remaining_day_plan(engine, candidate, events=fresh_events, gaps=gaps,
                    settings=settings, practice_plan=practice_plan, policy=policy, now=at, extensions=extensions)
            finally:
                check_page.close()

        changed = engine.edit_reservation_room_time(page, candidate, revalidate=revalidate,
                                                    dry_run=dry_run, freeze_minutes=freeze_minutes)
        if changed:
            total_actions += 1
            upgraded_ids.add(candidate.original.event_id)
            old, new = candidate.original, candidate.replacement
            booking_details.append(f"UPGRADED: {old.day} {old.room} {time_text(old.start)}-{time_text(old.end)} "
                                   f"-> {new.room} {time_text(new.start)}-{time_text(new.end)}")
            tracker = tracker_for_events(engine,
                [{**event, **new.as_booking()} if event['eventId'] == old.event_id else event
                 for event in events], blackouts)
        elif dry_run:
            upgraded_ids.add(candidate.original.event_id)  # One complete editor preview per reservation.
        try:
            tracker = fresh_agenda(page)
        except Exception:
            if not changed:
                raise
            # The exact edit already has an independently verified receipt.
            # Stop here: a failed later scan must not erase that success, and
            # the old tracker must never authorize another change.
            print("Room upgrade verified; further upgrades stopped because the agenda refresh failed")
            break
    return total_actions, tracker
