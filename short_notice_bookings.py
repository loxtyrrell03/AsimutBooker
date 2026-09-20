"""Bounded free-horizon pass using the normal planner and guarded Save path."""
from datetime import datetime, timedelta
from copy import copy

from booking_quotas import refresh_quota_balances
from operation_control import operation_stage


def is_fast_scheduled_pass(args):
    """Between quarter-hour preparations, focus the same worker on today's needs."""
    return bool(getattr(args, 'scheduled', False) and not getattr(args, 'target_time', None)
        and not any(getattr(args, flag, False) for flag in (
            'horizon_only','extensions_only','upgrades_only','upgrade_dry_run',
            'plan_only','check_only','agenda_only')))


def prioritise_daily_practice(engine, page, settings, practice_plan, args, tracker,
                             total_actions, booking_details):
    """Finish today's work before a future quota refusal can end the run.

    Unresolved transactions retain exclusive mutation ownership. Extension holds
    remain authoritative, and all actual writes use the existing guarded paths.
    """
    today = datetime.now().date()
    if (any(getattr(args, flag, False) for flag in (
            'horizon_only','extensions_only','upgrades_only','upgrade_dry_run',
            'plan_only','check_only','agenda_only'))
            or getattr(args,'only_date',None) not in (None,today.isoformat())
            or engine.list_pending_mutation_receipts()
            or not (tracker.is_quota_full() or is_fast_scheduled_pass(args))):
        return total_actions,tracker,False
    scoped=copy(args)
    scoped.only_date=today.isoformat()
    from booking_quotas import QuotaWait
    try:
        total_actions,_=engine.process_pending_extensions(page,tracker,
            [e for e in tracker.agenda_events if e.get('isReservation')],practice_plan,
            engine.load_disabled_dates(settings),engine.load_time_preferences(settings),
            scoped,total_actions,booking_details)
    except QuotaWait as exc:
        if engine.list_pending_mutation_receipts():
            raise
        print(f'  [Free horizon] Extension waits: {exc}')
    total_actions,tracker=run_short_notice_pass(engine,page,settings,practice_plan,
        args,tracker,total_actions,booking_details)
    # The earlier result is a whole-week tracker; only the upgrade scope changes.
    total_actions,tracker=engine.process_room_upgrades(engine,page,settings,practice_plan,
        scoped,tracker,total_actions,booking_details)
    return total_actions,tracker,True


def run_short_notice_pass(engine, page, settings, practice_plan, args, tracker,
                          total_actions, booking_details, *, after_horizon=False):
    # Scoped operations must never acquire unrelated last-minute reservations.
    if getattr(args, '_short_notice_declined', False) or any(getattr(args, flag, False) for flag in (
            'horizon_only', 'extensions_only', 'upgrades_only', 'upgrade_dry_run',
            'plan_only', 'check_only', 'agenda_only')):
        return total_actions, tracker
    if getattr(args, 'target_time', None) and not after_horizon and not tracker.is_quota_full():
        return total_actions, tracker
    disabled = engine.load_disabled_dates(settings)
    preferences = engine.load_time_preferences(settings)
    planning = engine.load_booking_strategy_preferences(settings).daily_planning
    now = datetime.now()
    days = tuple(day for day in engine.booking_window_dates(now.date())
                 if now.date() <= day <= (now + timedelta(minutes=engine.FREE_HORIZON_MINUTES)).date()
                 and not engine.is_date_disabled(day, disabled)
                 and (not args.only_date or day.isoformat() == args.only_date))
    def open_day(day, today):
        engine.open_practice_room_overview(page, today)
        if day != today:
            engine.navigate_to_day(page, (day - today).days, 0, base_date=today)
        engine.wait_for_practice_room_grid(page, day)
    # At most ten minimum-size sessions can fit in five hours. Each success
    # triggers fresh agenda, grid and quota proof; any rejection ends this pass.
    for _ in range(10):
        if args.max_actions is not None and total_actions >= args.max_actions:
            break
        operation_stage(f'Checking rooms in the next {engine.FREE_HORIZON_MINUTES / 60:g} hours…')
        now = datetime.now()
        planning_context = {}
        targets, peak_holds, _ = engine.refresh_extension_capacity_holds(
            planning_context, tracker, practice_plan, disabled,
            time_prefs=preferences, now=now)
        candidates = []
        future_candidates = []
        boundary = None
        if (getattr(args,'scheduled',False) and getattr(args,'target_time',None)
                and not getattr(args,'_free_boundary_waited',False)):
            value=engine.target_boundary_datetime(args.target_time,base_date=now.date())
            if 0 < (value-now).total_seconds() <= 180:
                boundary=value
        for day in days:
            if not engine.fragmentation_allows_new_booking(tracker, day)[0]:
                continue
            remaining = engine.remaining_target_hours(practice_plan, day, tracker.get_hours_for_day(day))
            if remaining is None:
                enabled = [d for d in engine.booking_window_dates(now.date())
                           if not engine.is_date_disabled(d, disabled)]
                remaining = max(0, engine.MAX_ROLLING_QUOTA_HOURS / max(1, len(enabled))
                                - tracker.get_hours_for_day(day))
            remaining = max(0, remaining - targets.get(day.isoformat(), 0) / 60)
            if remaining * 60 < engine.MINIMUM_BLOCK_MINUTES:
                continue
            open_day(day, now.date())
            gaps=engine.get_available_slots(page)
            opportunities = engine.build_day_booking_opportunities(
                gaps, day, tracker, preferences, planning,
                now=now, remaining_daily_hours=remaining,
                reserved_peak_minutes=peak_holds.get(day.isoformat(), 0),
                only_room=args.only_room, free_horizon_only=True)
            chosen = engine.select_day_plan(opportunities, planning, now=now,
                target_minutes=int(remaining * 60),
                allow_fragmented_sessions=engine.ALLOW_FRAGMENTED_SESSIONS,
                remaining_peak_minutes=max(0, tracker.get_remaining_peak_minutes(day)
                    - peak_holds.get(day.isoformat(), 0)),
                same_room_gap_minutes=engine.SAME_ROOM_GAP_MINUTES)
            candidates.extend((item, remaining) for item in chosen if item.unlock_at <= now)
            if boundary is not None:
                # Forecast only. After waiting, reread the grid and replan before
                # any Save; this cannot authorize early or stale-window bookings.
                later=engine.build_day_booking_opportunities(gaps,day,tracker,preferences,planning,
                    now=boundary,remaining_daily_hours=remaining,
                    reserved_peak_minutes=peak_holds.get(day.isoformat(),0),
                    only_room=args.only_room,free_horizon_only=True)
                future_candidates.extend(item for item in later if item.unlock_at <= boundary)
        if not candidates:
            if future_candidates and boundary is not None:
                args._free_boundary_waited=True
                operation_stage('Preparing for the next five-hour booking window…')
                engine.wait_until_datetime(boundary,page,label='FREE HORIZON',settle_seconds=0)
                continue
            print('  [Free horizon] No eligible short-notice booking is currently available for the remaining targets.')
            break
        item, remaining = min(candidates, key=lambda pair: engine.opportunity_rank(pair[0], planning, now=now))
        day = item.target_date
        # Refresh after grid traversal. The mutation path additionally requires
        # an exact, fresh ASIMUT check before Save; this balance is not permission.
        refresh_quota_balances(page, tracker, engine.booking_window_dates(now.date()))
        open_day(day, now.date())
        result, _ = engine.attempt_booking_with_room_fallback(
            page, engine._opportunity_to_normal_slot(item), day, tracker,
            (day - now.date()).days, remaining_daily_hours=remaining,
            max_action_minutes=args.max_action_minutes, time_prefs=preferences,
            daily_planning=planning, only_room=args.only_room,
            planning_context=planning_context, free_horizon_only=True)
        if not result:
            args._short_notice_declined = True
            print('  [Free horizon] Booking was not approved; ending this pass without retrying it.')
            break
        total_actions += 1
        booking_details.append(f'{result["date"]} {result["room"]} {result["start"]}-{result["end"]}')
        tracker = engine.BookingTracker()
        engine.scan_agenda(page, tracker, now.date(), ignored_events=engine.load_ignored_events(settings),
                           window_dates=engine.booking_window_dates(now.date()),
                           snapshot_path=engine.AGENDA_SNAPSHOT_FILE)
        engine.apply_rebooking_blackouts(tracker, engine.load_rebooking_blackouts(settings))
        refresh_quota_balances(page, tracker, engine.booking_window_dates(now.date()))
    return total_actions, tracker
