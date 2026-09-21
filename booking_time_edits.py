"""Exact user-requested start trims and later shifts of one reservation.

The type fixes identity, room and date. It is separate from automatic room
upgrades, whose equal-duration and better-room guarantees must stay intact.
"""

from booking_quotas import peak_quota_exempt
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from booking_quotas import PEAK_QUOTA_MINUTES as MAX_PEAK_MINUTES

from daily_planner import interval_overlap_minutes
from room_upgrades import Reservation, clock_minutes, local_instant, time_text, LONDON


@dataclass(frozen=True)
class BookingTimeEdit:
    original: Reservation
    replacement: Reservation

    def __post_init__(self):
        a, b = self.original, self.replacement
        if not isinstance(a, Reservation) or not isinstance(b, Reservation):
            raise ValueError("Time edits require exact reservations")
        if (a.event_id, a.day, a.room) != (b.event_id, b.day, b.room):
            raise ValueError("Time edits must keep the same booking, date and room")
        if any(v % 15 for r in (a, b) for v in (r.start, r.end)):
            raise ValueError("Booking times must use 15-minute boundaries")
        if b.start <= a.start or not (b.end == a.end or b.duration == a.duration):
            raise ValueError("Start later with the same end, or shift both times later equally")

    @property
    def originals(self):
        return (self.original,)

    @property
    def mode(self):
        return "trim_start" if self.replacement.end == self.original.end else "shift_later"

    @property
    def released_window(self):
        return (str(self.original.day), time_text(self.original.start),
                time_text(min(self.original.end, self.replacement.start)))


def requested_time_edit(original, *, mode, new_start_time=None, minutes=None):
    if mode == "trim_start" and minutes is None:
        new = replace(original, start=clock_minutes(new_start_time))
    elif mode == "shift_later" and new_start_time is None:
        if type(minutes) is not int or minutes <= 0 or minutes % 15:
            raise ValueError("Shift minutes must be a positive multiple of 15")
        new = replace(original, start=original.start + minutes, end=original.end + minutes)
    else:
        raise ValueError("Choose trim_start with new_start_time or shift_later with minutes")
    return BookingTimeEdit(original, new)


def time_edit_from_receipt(receipt):
    old = receipt["original"]
    original = Reservation(old["event_id"], date.fromisoformat(old["date"]), old["room"],
                           clock_minutes(old["start"]), clock_minutes(old["end"]))
    new = Reservation(original.event_id, date.fromisoformat(receipt["date"]), receipt["room"],
                      clock_minutes(receipt["start"]), clock_minutes(receipt["end"]))
    return BookingTimeEdit(original, new)


def validate_time_edit(edit, *, events, policy, now, gaps=(), blackouts=(),
                       peak_limit=None, peak_start=540, peak_end=960,
                       free_horizon_overrides_peak=False, free_horizon_minutes=300):
    """Validate current agenda and any newly occupied room time before editing.

    All other personal events block a move, including ignored planner conflicts.
    Saved target/time/room ranking preferences do not veto this explicit command;
    current site limits, existing bookings and protected free time still apply.
    """
    a, b = edit.original, edit.replacement
    if now.tzinfo is None:
        raise ValueError("Time-edit validation needs an aware current time")
    matches = [e for e in events if e.get("eventId") == a.event_id]
    if (len(matches) != 1 or matches[0].get("isReservation") is not True
            or Reservation.from_event(matches[0]) != a):
        raise ValueError("The selected booking changed; resolve it again")
    if b.day not in policy.booking_dates(now.astimezone(LONDON).date()):
        raise ValueError("The booking is outside the current live window")
    if b.room not in policy.all_room_location_ids:
        raise ValueError("The booking room has no verified live identity")
    if local_instant(b.day, b.start) <= now:
        raise ValueError("The new start time must still be in the future")
    if not policy.site_minimum_booking_minutes <= b.duration <= policy.site_maximum_booking_minutes:
        raise ValueError(f"The remaining booking must be {policy.site_minimum_booking_minutes}-"
                         f"{policy.site_maximum_booking_minutes} minutes under current site rules")
    other = [e for e in events if e["date"] == str(b.day) and e.get("eventId") != a.event_id]
    for e in other:
        start, end = clock_minutes(e["startTime"]), clock_minutes(e["endTime"])
        if interval_overlap_minutes(b.start, b.end, start, end):
            raise ValueError(f"The new time clashes with an existing event at {time_text(start)}-{time_text(end)}")
        gap = policy.site_minimum_booking_gap_minutes
        if (e.get("isReservation") is True and e.get("room") == b.room
                and b.start < end + gap and start - gap < b.end):
            raise ValueError("The new time is too close to another booking in the same room")
    for window in blackouts:
        if (str(window.date) == str(b.day)
                and interval_overlap_minutes(b.start, b.end, clock_minutes(window.start_time),
                                             clock_minutes(window.end_time))):
            raise ValueError("The new time overlaps protected free time; reopen that window first")
    # Shrinking an owned interval acquires no new room time or peak allowance.
    if edit.mode == "trim_start":
        return
    if b.day.weekday() < 5 and not peak_quota_exempt(b.day, b.start / 60, b.end / 60,
            now=now, free_horizon_overrides_peak=free_horizon_overrides_peak,
            free_horizon_minutes=free_horizon_minutes):
        peak = sum(interval_overlap_minutes(clock_minutes(e["startTime"]), clock_minutes(e["endTime"]),
                                             peak_start, peak_end) for e in other if e.get("isReservation") is True)
        limit = MAX_PEAK_MINUTES if peak_limit is None else peak_limit
        if peak + interval_overlap_minutes(b.start, b.end, peak_start, peak_end) > limit:
            raise ValueError(f"The shift would exceed the weekday peak allowance ({limit:g} minutes)")
    bounds = policy.site_clock_offset_bounds
    if bounds is None:
        raise ValueError("The site's current booking clock is unavailable")
    until = min(now.astimezone(timezone.utc) + timedelta(seconds=bounds[0],
                    minutes=policy.horizon_minutes_for(b.room)), policy.booking_horizon)
    if local_instant(b.day, b.end) > until:
        raise ValueError("The whole shifted booking is not yet inside this room's booking horizon")
    rows = [row for row in gaps if row.get("room") == b.room]
    if len(rows) != 1:
        raise ValueError("The room's current availability could not be verified")
    intervals = [(a.start, a.end)]
    for slot in rows[0].get("slots", ()):
        start, end = slot["startHour"] * 60, slot["endHour"] * 60
        if not all(isinstance(v, (int, float)) and isfinite(v) for v in (start, end)) or not 0 <= start < end <= 1440:
            raise ValueError("The room's observed availability is invalid")
        intervals.append((start, end))
    covered = b.start
    for start, end in sorted(intervals):
        if start <= covered:
            covered = max(covered, end)
    if covered < b.end:
        raise ValueError("The room is occupied during part of the shifted booking")


def time_edit_requested(args):
    return any(getattr(args, key, None) is not None for key in
               ("edit_event_id", "edit_date", "edit_room", "edit_start", "edit_end", "trim_start", "shift_minutes"))


def edit_from_args(args):
    original = Reservation(args.edit_event_id, date.fromisoformat(args.edit_date), args.edit_room,
                           clock_minutes(args.edit_start), clock_minutes(args.edit_end))
    return requested_time_edit(original, mode="trim_start" if args.trim_start is not None else "shift_later",
                               new_start_time=args.trim_start, minutes=args.shift_minutes)


def run_time_edit(engine, page, args, settings, policy):
    """One isolated edit, under the worker runtime lock and final Save guard."""
    edit = edit_from_args(args)
    if engine.list_pending_mutation_receipts():
        raise engine.BookingVerificationError("An unresolved mutation blocks the requested time edit")
    blackouts = engine.load_rebooking_blackouts(settings)

    def revalidate():
        check_page = page.context.new_page()
        try:
            now = datetime.now().astimezone()
            tracker = engine.BookingTracker()
            engine.scan_agenda(check_page, tracker, now.date(), ignored_events=(),
                window_dates=policy.booking_dates(now.date()), snapshot_path=engine.AGENDA_SNAPSHOT_FILE)
            gaps = ()
            if edit.mode == "shift_later":
                engine.open_practice_room_overview(check_page, now.date())
                if edit.original.day != now.date():
                    engine.navigate_to_day(check_page, (edit.original.day - now.date()).days, 0, base_date=now.date())
                engine.wait_for_practice_room_grid(check_page, edit.original.day)
                gaps = engine.get_available_slots(check_page)
            validate_time_edit(edit, events=tracker.agenda_events, policy=policy,
                               now=datetime.now().astimezone(), gaps=gaps, blackouts=blackouts,
                               peak_limit=engine.MAX_PEAK_HOURS * 60,
                               free_horizon_overrides_peak=getattr(engine, "FREE_HORIZON_OVERRIDES_PEAK", False) is True,
                               free_horizon_minutes=engine.FREE_HORIZON_MINUTES,
                               peak_start=engine.PEAK_START * 60, peak_end=engine.PEAK_END * 60)
            return True
        finally:
            check_page.close()

    changed = engine.edit_reservation_room_time(page, edit, revalidate=revalidate, freeze_minutes=0)
    if changed is not True:
        print("TIME EDIT NOT APPLIED: the requested edit was not saved")
        return 7
    tracker = engine.BookingTracker()
    engine.scan_agenda(page, tracker, datetime.now().date(), ignored_events=engine.load_ignored_events(settings),
        window_dates=policy.booking_dates(datetime.now().date()), snapshot_path=engine.AGENDA_SNAPSHOT_FILE)
    matches = [e for e in tracker.agenda_events if e.get("eventId") == edit.original.event_id]
    if (len(matches) != 1 or matches[0].get("isReservation") is not True
            or Reservation.from_event(matches[0]) != edit.replacement):
        raise engine.BookingVerificationError("Time edit was saved, but the refreshed agenda does not match")
    print("TIME EDIT VERIFIED: exact booking and refreshed agenda agree")
    return 0
