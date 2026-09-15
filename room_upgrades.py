"""Pure, coverage-preserving room/time upgrades from fresh site observations.

A candidate is one edit of one existing reservation, on the same date and for
the same duration. It never authorizes cancellation, shrinking or a new Save.
The runtime must refresh the agenda/grid and validate the exact edit with Asimut.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import ceil, floor, isfinite

from daily_planner import interval_overlap_minutes, soft_time_value
from room_catalog import SITE_TIMEZONE

LONDON = SITE_TIMEZONE
DEFAULT_FREEZE_MINUTES = 24 * 60


def clock_minutes(value):
    if not isinstance(value, str) or len(value) != 5:
        raise ValueError("Expected an exact HH:MM time")
    parsed = time.fromisoformat(value)
    if parsed.isoformat(timespec="minutes") != value:
        raise ValueError("Expected an exact HH:MM time")
    return parsed.hour * 60 + parsed.minute


def time_text(minutes):
    if type(minutes) is not int or not 0 <= minutes < 1440:
        raise ValueError("Time must be within one local day")
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def local_instant(day, minutes):
    """Reject skipped/repeated wall times instead of guessing a DST offset."""
    wall = datetime.combine(day, time(minutes // 60, minutes % 60))
    candidates = set()
    for fold in (0, 1):
        instant = wall.replace(tzinfo=LONDON, fold=fold).astimezone(timezone.utc)
        if instant.astimezone(LONDON).replace(tzinfo=None) == wall:
            candidates.add(instant)
    if len(candidates) != 1:
        raise ValueError("Ambiguous or nonexistent London booking time")
    return candidates.pop()


@dataclass(frozen=True)
class Reservation:
    event_id: int
    day: date
    room: str
    start: int
    end: int

    def __post_init__(self):
        if type(self.event_id) is not int or self.event_id <= 0:
            raise ValueError("A reservation requires an exact positive event ID")
        if type(self.day) is not date or not isinstance(self.room, str) or not self.room.strip():
            raise ValueError("A reservation requires an exact date and room")
        if any(type(value) is not int for value in (self.start, self.end)):
            raise ValueError("Reservation times must be whole minutes")
        if not 0 <= self.start < self.end < 1440:
            raise ValueError("Reservation must lie within one local day")

    @property
    def duration(self):
        return self.end - self.start

    @property
    def event_url(self):
        return f"https://rwcmd.asimut.net/arrangement?eventId={self.event_id}"

    def as_booking(self):
        return {"eventId": self.event_id, "event_url": self.event_url,
                "date": self.day.isoformat(), "room": self.room,
                "startTime": time_text(self.start), "endTime": time_text(self.end)}

    @classmethod
    def from_event(cls, event):
        return cls(event["eventId"], date.fromisoformat(event["date"]), event["room"],
                   clock_minutes(event["startTime"]), clock_minutes(event["endTime"]))


@dataclass(frozen=True)
class RoomUpgrade:
    original: Reservation
    replacement: Reservation
    rank: tuple = ()

    def __post_init__(self):
        a, b = self.original, self.replacement
        if a.event_id != b.event_id or a.day != b.day or a.duration != b.duration:
            raise ValueError("Upgrades must preserve event ID, date and full duration")
        if a.room == b.room:
            raise ValueError("A room upgrade must change the room")


def _overlaps(start, end, other_start, other_end):
    return start < other_end and end > other_start


def classify_upgrade_outcome(events, receipt):
    """A missing, changed or duplicate event is uncertain, never a safe retry."""
    original = receipt["original"]
    matches = [e for e in events if e.get("eventId") == original["event_id"]]
    if len(matches) != 1 or matches[0].get("isReservation") is not True:
        return "uncertain"
    event = matches[0]
    actual = (event.get("room"), event.get("date"), event.get("startTime"), event.get("endTime"))
    if actual == tuple(receipt[key] for key in ("room", "date", "start", "end")):
        return "applied"
    if actual == tuple(original[key] for key in ("room", "date", "start", "end")):
        return "not_applied"
    return "uncertain"


def find_room_upgrades(original, *, events, available_data, policy, now,
                       time_preferences, planning, peak_start=540, peak_end=960,
                       peak_limit=120, same_room_gap=60, freeze_minutes=DEFAULT_FREEZE_MINUTES,
                       blocked_intervals=(), protected_extensions=(), ignored_event_ids=()):
    """Rank superior rooms at all legal quarter-hour starts, keeping coverage.

    ``events`` is a complete fresh agenda, with optional ``blocksConflict=False``
    for an explicitly ignored event. Such reservations still consume peak quota
    and same-room spacing, and are never upgrade targets themselves. Extensions
    protect their complete intended intervals; an unfinished target is deferred.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Upgrade planning requires an aware current time")
    if type(freeze_minutes) is not int or freeze_minutes < 0:
        raise ValueError("Upgrade freeze must be non-negative whole minutes")
    if not isinstance(original, Reservation):
        raise TypeError("original must be a Reservation")
    matches = [e for e in events if e.get("eventId") == original.event_id]
    if len(matches) != 1 or matches[0].get("isReservation") is not True:
        return ()
    if Reservation.from_event(matches[0]) != original:
        return ()
    if original.event_id in ignored_event_ids or original.room not in policy.room_order:
        return ()
    if original.day not in policy.booking_dates(now.astimezone(LONDON).date()):
        return ()
    if any(v % 15 for v in (original.start, original.end)):
        return ()
    if not policy.minimum_block_minutes <= original.duration <= policy.site_maximum_booking_minutes:
        return ()
    now_utc = now.astimezone(timezone.utc)
    cutoff = now_utc + timedelta(minutes=freeze_minutes)
    try:
        if local_instant(original.day, original.start) <= cutoff:
            return ()
    except ValueError:
        return ()
    # Do not move a seed booking away from its still-active extension plan.
    for extension in protected_extensions:
        if (extension.get("eventId") == original.event_id
                and clock_minutes(extension["target_end"]) > original.end):
            return ()

    day_events = [e for e in events if e["date"] == original.day.isoformat()
                  and e.get("eventId") != original.event_id]
    other_peak = sum(interval_overlap_minutes(clock_minutes(e["startTime"]),
                    clock_minutes(e["endTime"]), peak_start, peak_end)
                    for e in day_events if e.get("isReservation") is True)
    pref_window = None
    if time_preferences.get("enabled"):
        pref_window = (int(round(time_preferences["start_hour"] * 60)),
                       int(round(time_preferences["end_hour"] * 60)))
    old_value = soft_time_value(original.start, original.duration, pref_window)
    peak_pref = (clock_minutes(planning.preferred_peak_start),
                 clock_minutes(planning.preferred_peak_end))
    old_peak_pref = (interval_overlap_minutes(original.start, original.end, *peak_pref)
                     if planning.enabled and original.day.weekday() < 5 else 0)
    old_rank = policy.room_order.index(original.room)
    results = {}
    for room_data in available_data:
        room = room_data.get("room")
        if room not in policy.room_order or policy.room_order.index(room) >= old_rank:
            continue
        room_rank = policy.room_order.index(room)
        horizon = timedelta(minutes=policy.horizon_minutes_for(room))
        # Earliest plausible site time prevents a fast local clock opening a room early.
        clock_bounds = policy.site_clock_offset_bounds
        if clock_bounds is None:
            continue
        bookable_until = min(now_utc + timedelta(seconds=clock_bounds[0]) + horizon,
                             policy.booking_horizon.astimezone(timezone.utc))
        for gap in room_data.get("slots", ()):
            a, b = gap["startHour"] * 60, gap["endHour"] * 60
            if not all(isinstance(v, (int, float)) and isfinite(v) for v in (a, b)):
                raise ValueError("Invalid observed room gap")
            if not 0 <= a < b <= 1440:
                raise ValueError("Observed room gap is outside the day")
            first = int(ceil(a / 15)) * 15
            last = min(1425 - original.duration, int(floor((b - original.duration) / 15)) * 15)
            for start in range(first, last + 1, 15):
                end = start + original.duration
                try:
                    if local_instant(original.day, start) <= cutoff:
                        continue
                    if local_instant(original.day, end) > bookable_until:
                        continue
                except ValueError:
                    continue
                value = soft_time_value(start, original.duration, pref_window)
                if value + 1e-8 < old_value:
                    continue
                if (pref_window and time_preferences.get("strict_mode")
                        and not pref_window[0] <= start < end <= pref_window[1]):
                    continue
                preferred = (interval_overlap_minutes(start, end, *peak_pref)
                             if planning.enabled and original.day.weekday() < 5 else 0)
                if preferred < old_peak_pref:
                    continue
                if (original.day.weekday() < 5
                        and other_peak + interval_overlap_minutes(start, end, peak_start, peak_end) > peak_limit):
                    continue
                if any(_overlaps(start, end, a, b) for a, b in blocked_intervals):
                    continue
                invalid = False
                for event in day_events:
                    a, b = clock_minutes(event["startTime"]), clock_minutes(event["endTime"])
                    if event.get("blocksConflict", True) and _overlaps(start, end, a, b):
                        invalid = True
                    if event.get("isReservation") is True and event.get("room") == room:
                        if _overlaps(start, end, a - same_room_gap, b + same_room_gap):
                            invalid = True
                for ext in protected_extensions:
                    if ext["date"] == original.day.isoformat() and ext.get("eventId") != original.event_id:
                        a, b = clock_minutes(ext["startTime"]), clock_minutes(ext["target_end"])
                        gap_minutes = same_room_gap if ext["room"] == room else 0
                        if _overlaps(start, end, a - gap_minutes, b + gap_minutes):
                            invalid = True
                if invalid:
                    continue
                replacement = Reservation(original.event_id, original.day, room, start, end)
                rank = (-round(value, 8), -preferred, room_rank,
                        abs(start - original.start), start, room)
                results[(room, start, end)] = RoomUpgrade(original, replacement, rank)
    return tuple(sorted(results.values(), key=lambda item: item.rank))
