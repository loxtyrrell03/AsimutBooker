"""Fresh weekly discovery and guarded execution of preferred-room allocations."""
from datetime import datetime, timedelta
import time

from advance_planner import AdvanceDay, allocate_advance_week
from booking_rules import load_booking_rules
from booking_strategy import load_booking_strategy
from booking_quotas import refresh_quota_balances
from operation_control import operation_stage
from session_preferences import tracker_sessions


def _minutes(value):
    hour, minute = map(int, value.split(':'))
    return hour*60 + minute


def enabled(settings, practice_plan, args):
    # Explicit single-date/room operations retain their requested scope. Legacy
    # quota mode retains its established planner. No preference is rewritten.
    return bool(practice_plan.enabled and load_booking_rules(settings).preset != 'legacy'
        and load_booking_strategy(settings).daily_planning.enabled
        and not getattr(args, 'only_date', None) and not getattr(args, 'only_room', None)
        and not any(getattr(args, flag, False) for flag in (
            'horizon_only', 'extensions_only', 'upgrades_only', 'upgrade_dry_run',
            'check_only', 'agenda_only')))


def open_day(engine, page, day, today):
    engine.open_practice_room_overview(page, today)
    if day != today:
        engine.navigate_to_day(page, (day-today).days, 0, base_date=today)
    engine.wait_for_practice_room_grid(page, day)


def _quality_minutes(engine, tracker, day, rooms, preferences):
    ranges = set(tracker.reservation_ranges.get(day.isoformat(), ()))
    ranges.update((start, end, room) for room, booked_day, start, end in tracker.bookings
                  if engine.as_date(booked_day) == day)
    total = 0
    for start, end, room in ranges:
        if room not in rooms:
            continue
        total += _useful_minutes(engine, start*60, end*60, preferences)
    return min(int(round(tracker.get_hours_for_day(day)*60)), total)


def _useful_minutes(engine, start, end, preferences):
    window = ((int(preferences['start_hour']*60), int(preferences['end_hour']*60))
              if preferences.get('enabled') else None)
    if window and preferences.get('strict_mode'):
        start, end = max(start, window[0]), min(end, window[1])
        window = None
    duration = int(round(end-start))
    return duration if duration >= engine.MINIMUM_BLOCK_MINUTES and \
        engine.soft_time_is_worth_booking(start, duration, window) else 0


def plan_from_grids(engine, grids, settings, practice_plan, tracker, args, *, now):
    preferences = engine.load_time_preferences(settings)
    strategy = engine.load_booking_strategy_preferences(settings)
    planning = strategy.daily_planning
    disabled = engine.load_disabled_dates(settings)
    context = {}
    targets, peaks, held = engine.refresh_extension_capacity_holds(context, tracker,
        practice_plan, disabled, time_prefs=preferences, now=now)
    rooms = tuple(engine.PRIORITY_ROOMS[:2])
    days = []
    for day, grid in sorted(grids.items()):
        if engine.is_date_disabled(day, disabled):
            continue
        target = int(round((practice_plan.target_for(day) or 0)*60))
        if target <= 0:
            continue
        day_preferences = engine.resolve_time_preferences(preferences, day)
        confirmed = int(round(tracker.get_hours_for_day(day)*60))
        held_target = targets.get(day.isoformat(), 0)
        opportunities = ()
        if engine.fragmentation_allows_new_booking(tracker, day)[0]:
            premium_grid = []
            for row in grid:
                if row.get('room') not in rooms:
                    continue
                gaps = []
                for gap in row.get('slots', ()):
                    start, end = gap['startHour'], gap['endHour']
                    if day_preferences.get('enabled') and day_preferences.get('strict_mode'):
                        start = max(start, day_preferences['start_hour'])
                        end = min(end, day_preferences['end_hour'])
                    if end > start:
                        gaps.append({'startHour':start, 'endHour':end})
                premium_grid.append({'room':row['room'], 'slots':gaps})
            opportunities = engine.build_day_booking_opportunities(
                premium_grid, day, tracker,
                preferences, planning, now=now,
                remaining_daily_hours=max(0, target-confirmed-held_target)/60,
                reserved_peak_minutes=peaks.get(day.isoformat(), 0),
                reserved_weekly_minutes=sum(targets.values()))
            # The shared planner scores soft preferences and rejects terrible
            # fallbacks. Only an explicitly strict window is a hard cutoff.
        quality_hold = 0
        for booking in held:
            if booking['date'] != day.isoformat() or booking['room'] not in rooms:
                continue
            start = _minutes(booking['startTime'])
            quality_hold += max(0,
                _useful_minutes(engine, start, _minutes(booking['target_end']), day_preferences)
                - _useful_minutes(engine, start, _minutes(booking['endTime']), day_preferences))
        days.append(AdvanceDay(day, target, confirmed,
            _quality_minutes(engine, tracker, day, rooms, day_preferences),
            tuple(opportunities), max(0, int(tracker.get_remaining_peak_minutes(day)
                - peaks.get(day.isoformat(), 0))), held_target, int(quality_hold),
                tracker_sessions(tracker, day, planning)))
    budget = max(0, int(tracker.get_remaining_quota_hours()*60) - sum(targets.values()))
    allocations = allocate_advance_week(days, planning, now=now, budget_minutes=budget,
        minimum_block_minutes=engine.MINIMUM_BLOCK_MINUTES,
        allow_fragmented_sessions=engine.ALLOW_FRAGMENTED_SESSIONS,
        same_room_gap_minutes=engine.SAME_ROOM_GAP_MINUTES,
        reverse_date_order=strategy.reverse_date_order)
    return days, allocations, context


def publish(engine, days, allocations, context, policy, settings, tracker, *, now, grids=None):
    rows = []
    planning = engine.load_booking_strategy_preferences(settings).daily_planning
    for day, allocation in zip(sorted(days, key=lambda item: item.target_date),
                               sorted(allocations, key=lambda item: item.target_date)):
        peak_limit = int(engine.MAX_PEAK_HOURS*60) if day.target_date.weekday() < 5 else 0
        sessions = sorted(allocation.sessions, key=lambda item: (
            item.unlock_at > now, engine.opportunity_rank(item, planning, now=now)))
        candidates = tuple(engine._plan_candidate_from_opportunity(item,
            state='ready' if item.unlock_at <= now else 'waiting',
            reason=f'{allocation.minutes} advance minutes allocated to this date; '
                   'each booking still needs live approval') for item in sessions)
        if candidates:
            status = 'planned' if candidates[0].state == 'ready' else 'waiting'
            reason = f'{allocation.minutes} advance minutes allocated in preferred rooms and times'
        elif day.confirmed_minutes >= day.target_minutes:
            status, reason = 'complete', 'The daily practice target is already met'
        else:
            status = 'waiting'
            reason = ('Waiting for released advance quota or an eligible last-minute session'
                      if tracker.get_remaining_quota_hours() < engine.MINIMUM_BLOCK_MINUTES/60
                      else 'No additional preferred-room block fits the fair advance allocation')
        row = engine.DayPlan(date=day.target_date.isoformat(),
            target_minutes=day.target_minutes, existing_minutes=day.confirmed_minutes,
            peak_used_minutes=min(peak_limit, int(tracker.get_peak_used_for_day(day.target_date))),
            peak_limit_minutes=peak_limit, status=status,
            primary=candidates[0] if candidates else None, additional=candidates[1:], backups=(),
            held_peak_minutes=min(peak_limit, sum(item.peak_minutes for item in allocation.sessions)),
            reason=reason)
        if (not candidates and grids is not None
                and now.date() <= day.target_date <= (now + timedelta(minutes=engine.FREE_HORIZON_MINUTES)).date()
                and day.target_minutes > day.confirmed_minutes):
            from short_notice_bookings import reserve_advance_credit
            preferences = engine.load_time_preferences(settings)
            free_options = engine.build_day_booking_opportunities(
                grids.get(day.target_date, []), day.target_date, tracker, preferences, planning,
                now=now, remaining_daily_hours=max(0, day.target_minutes-day.confirmed_minutes
                    - day.held_target_minutes)/60,
                reserved_peak_minutes=context['extension_peak_by_date'].get(day.target_date.isoformat(), 0),
                free_horizon_only=True, include_free_horizon_intent=True)
            free_options = reserve_advance_credit(free_options, planning, active=not tracker.is_quota_full())
            if free_options and engine.fragmentation_allows_new_booking(tracker, day.target_date)[0]:
                row = engine.build_display_day_plan(day.target_date, free_options, tracker, planning,
                    now=now, target_minutes=day.target_minutes,
                    reserved_daily_minutes=day.held_target_minutes,
                    reserved_peak_minutes=context['extension_peak_by_date'].get(day.target_date.isoformat(), 0),
                    free_horizon_only=True)
        rows.append(engine.attach_extension_progress(row, context, now=now))
    minutes = sum(item.minutes for item in allocations)
    planned_days = sum(bool(item.sessions) for item in allocations)
    active = any(row.primary for row in rows)
    summary = (f'{minutes/60:g}h planned in preferred rooms across {planned_days} days' if minutes
               else 'Last-minute practice planned; advance bookings wait for quota' if active
               else 'Waiting for advance credit or preferred-room availability; daily checks continue')
    return engine.publish_booking_plan(rows, policy, settings, now=now,
        summary=summary, status='active' if active else 'idle')


def run(engine, page, policy, settings, practice_plan, args, tracker, total_actions,
        booking_details, *, read_only=False):
    """Own ordinary advance creates without bypassing existing mutation paths."""
    if not enabled(settings, practice_plan, args):
        return total_actions, tracker, False
    if engine.list_pending_mutation_receipts() and not read_only:
        raise engine.BookingVerificationError('Advance allocation waits for transaction recovery')
    if not read_only and args.max_actions is not None and total_actions >= args.max_actions:
        return total_actions, tracker, True
    now = datetime.now()
    # Leave enough time for one bounded fallback attempt and final verification.
    # Repeated slow, definitive failures must not consume the scheduled task's
    # entire 14-minute lifetime; the next run can continue the remaining dates.
    deadline = time.monotonic() + 480
    run_started = getattr(engine, '_booker_run_started_monotonic', None)
    if getattr(args, 'scheduled', False) and isinstance(run_started, (int, float)):
        deadline = min(deadline, run_started + 660)
    disabled = engine.load_disabled_dates(settings)
    grids = {}
    for day in engine.booking_window_dates(now.date()):
        if engine.is_date_disabled(day, disabled) or (practice_plan.target_for(day) or 0) <= 0:
            continue
        operation_stage(f'Planning preferred practice across the week: {day:%a %d %b}…')
        open_day(engine, page, day, now.date())
        grids[day] = engine.get_available_slots(page)
    attempts = set()
    declined_days = set()
    # The live quota is the hard aggregate boundary. This secondary action cap
    # keeps custom large quotas bounded; a later run continues remaining work.
    for _ in range(24):
        now = datetime.now()
        days, allocations, context = plan_from_grids(engine, grids, settings,
            practice_plan, tracker, args, now=now)
        publish(engine, days, allocations, context, policy, settings, tracker, now=now, grids=grids)
        if read_only or (args.max_actions is not None and total_actions >= args.max_actions):
            break
        if deadline - time.monotonic() < 180:
            operation_stage('Weekly booking checks will continue on the next run')
            break
        boundary = engine.target_boundary_datetime(args.target_time, base_date=now.date()) \
            if getattr(args, 'target_time', None) else now
        candidates = [(item, allocation) for allocation in allocations for item in allocation.sessions
            if (item.unlock_at <= now or (item.unlock_at <= boundary
                and 0 < (item.unlock_at-now).total_seconds() <= 180))
            and item.target_date not in declined_days
            and (item.target_date, item.room, item.start_minutes, item.end_minutes) not in attempts]
        if not candidates:
            break
        item, allocation = min(candidates, key=lambda pair: (
            0 if pair[0].unlock_at > now else 1, pair[0].unlock_at,
            engine.opportunity_rank(pair[0], engine.load_booking_strategy_preferences(settings).daily_planning,
                                    now=now)))
        key = (item.target_date, item.room, item.start_minutes, item.end_minutes)
        attempts.add(key)
        open_day(engine, page, item.target_date, now.date())
        grids[item.target_date] = engine.get_available_slots(page)
        # Replan globally after the last fresh grid. A stale allocation may not
        # spend credit reserved for another date or change its chosen interval.
        _, fresh_allocations, context = plan_from_grids(engine, grids, settings,
            practice_plan, tracker, args, now=datetime.now())
        if not any(key == (candidate.target_date, candidate.room, candidate.start_minutes, candidate.end_minutes)
                   for group in fresh_allocations for candidate in group.sessions):
            continue
        refresh_quota_balances(page, tracker, engine.booking_window_dates(now.date()))
        horizon = item.unlock_at > datetime.now()
        slot = (engine._opportunity_to_horizon_candidate(item, target_boundary=item.unlock_at)
                if horizon else engine._opportunity_to_normal_slot(item))
        result, _ = engine.attempt_booking_with_room_fallback(page, slot, item.target_date, tracker,
            (item.target_date-now.date()).days, remaining_daily_hours=item.potential_minutes/60,
            max_action_minutes=args.max_action_minutes,
            time_prefs=engine.load_time_preferences(settings),
            daily_planning=engine.load_booking_strategy_preferences(settings).daily_planning,
            planning_context=context, horizon=horizon, allowed_rooms=tuple(engine.PRIORITY_ROOMS[:2]))
        if not result:
            if engine.list_pending_mutation_receipts():
                raise engine.BookingVerificationError('Uncertain advance booking needs reconciliation')
            # Keep this date's allocation reserved, but do not let one proven
            # no-Save refusal starve the rest of the week. No more intervals on
            # this date are attempted in this pass. Quota/uncertainty exceptions
            # still stop the run through the existing guarded mutation path.
            declined_days.add(item.target_date)
        else:
            total_actions += 1
            booking_details.append(f'{result["date"]} {result["room"]} {result["start"]}-{result["end"]}')
        tracker = engine.BookingTracker()
        engine.scan_agenda(page, tracker, now.date(), ignored_events=engine.load_ignored_events(settings),
            window_dates=engine.booking_window_dates(now.date()), snapshot_path=engine.AGENDA_SNAPSHOT_FILE)
        engine.apply_rebooking_blackouts(tracker, engine.load_rebooking_blackouts(settings))
        refresh_quota_balances(page, tracker, engine.booking_window_dates(now.date()))
    return total_actions, tracker, True
