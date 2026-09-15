"""Pure, coverage-preserving room/time upgrades from fresh site observations.

A candidate is one edit of one existing reservation, on the same date and for
the same duration. It never authorizes cancellation, shrinking or a new Save.
The runtime must refresh the agenda/grid and validate the exact edit with Asimut.
"""

from dataclasses import dataclass
from itertools import combinations
from bisect import bisect_left
from functools import lru_cache
from datetime import date, datetime, time, timedelta, timezone
from math import ceil, floor, isfinite

from daily_planner import interval_overlap_minutes, soft_time_value
from room_catalog import SITE_TIMEZONE

LONDON = SITE_TIMEZONE
DEFAULT_FREEZE_MINUTES = 0


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

    @property
    def originals(self):
        return (self.original,)


@dataclass(frozen=True)
class RoomConsolidation:
    """One enlarged anchor secured before its redundant originals are retired."""
    originals: tuple[Reservation, ...]
    replacement: Reservation
    rank: tuple = ()

    def __post_init__(self):
        group = self.originals
        if not isinstance(group, tuple) or len(group) < 2 or not all(isinstance(r, Reservation) for r in group):
            raise ValueError("Consolidation requires at least two exact reservations")
        if len({r.event_id for r in group}) != len(group):
            raise ValueError("Consolidation originals must have distinct identities")
        if any(r.day != self.replacement.day for r in group):
            raise ValueError("Consolidation must retain the original date")
        if sum(r.duration for r in group) != self.replacement.duration:
            raise ValueError("Consolidation must retain every booked minute")
        if self.replacement.event_id not in {r.event_id for r in group}:
            raise ValueError("Consolidation must retain one original event as its anchor")
        ordered = sorted(group, key=lambda r: r.start)
        if any(a.end > b.start for a, b in zip(ordered, ordered[1:])):
            raise ValueError("Overlapping originals cannot establish distinct practice hours")

    @property
    def original(self):
        return next(r for r in self.originals if r.event_id == self.replacement.event_id)

    @property
    def retired(self):
        return tuple(r for r in self.originals if r.event_id != self.replacement.event_id)


def apply_upgrade_to_events(events, upgrade):
    """Final accounting, shared by single edits, consolidation and look-ahead."""
    removed = {r.event_id for r in upgrade.originals}
    return [{**event, **upgrade.replacement.as_booking()} if event["eventId"] == upgrade.original.event_id else event
            for event in events if event["eventId"] not in removed or event["eventId"] == upgrade.original.event_id]


def availability_after_upgrade(available_data, upgrade):
    """Model the old rooms being freed and the replacement being occupied."""
    rooms = {r["room"]: [(slot["startHour"] * 60, slot["endHour"] * 60) for slot in r.get("slots", ())]
             for r in available_data}
    for original in upgrade.originals:
        rooms.setdefault(original.room, []).append((original.start, original.end))
    output = []
    for room, intervals in rooms.items():
        merged = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        free = []
        for start, end in merged:
            new = upgrade.replacement
            if room != new.room or not _overlaps(start, end, new.start, new.end):
                free.append((start, end))
            else:
                if start < new.start:
                    free.append((start, new.start))
                if new.end < end:
                    free.append((new.end, end))
        output.append({"room": room, "slots": [{"startHour": a / 60, "endHour": b / 60} for a, b in free]})
    return output


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


def consolidation_from_receipt(receipt):
    def record(value):
        return Reservation(value["event_id"], date.fromisoformat(value["date"]), value["room"],
                           clock_minutes(value["start"]), clock_minutes(value["end"]))
    originals = tuple(record(r) for r in receipt["originals"])
    return RoomConsolidation(originals, record({**receipt, "event_id": receipt["original"]["event_id"]}))


def classify_consolidation_outcome(events, receipt):
    """Interpret a complete fresh agenda; no missing anchor is ever success."""
    group = consolidation_from_receipt(receipt)
    actual = {}
    ids = {r.event_id for r in group.originals}
    for event in events:
        if event.get("eventId") in ids:
            if event["eventId"] in actual or event.get("isReservation") is not True:
                return "uncertain"
            actual[event["eventId"]] = Reservation.from_event(event)
    anchor = actual.get(group.original.event_id)
    if anchor == group.original and all(actual.get(r.event_id) == r for r in group.retired):
        return "untouched"
    if anchor != group.replacement:
        return "uncertain"
    if any(r.event_id in actual and actual[r.event_id] != r for r in group.retired):
        return "uncertain"
    return "secured" if any(r.event_id in actual for r in group.retired) else "complete"


def find_room_upgrades(original, *, events, available_data, policy, now,
                       time_preferences, planning, peak_start=540, peak_end=960,
                       peak_limit=120, same_room_gap=60, freeze_minutes=DEFAULT_FREEZE_MINUTES,
                       blocked_intervals=(), protected_extensions=(), ignored_event_ids=(),
                       _originals=None, _ignore_room_horizon=False):
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
    originals = _originals or (original,)
    original_ids = {r.event_id for r in originals}
    duration = sum(r.duration for r in originals)
    for record in originals:
        matches = [e for e in events if e.get("eventId") == record.event_id]
        if (len(matches) != 1 or matches[0].get("isReservation") is not True
                or Reservation.from_event(matches[0]) != record
                or record.event_id in ignored_event_ids or record.room not in policy.room_order):
            return ()
    if original.day not in policy.booking_dates(now.astimezone(LONDON).date()):
        return ()
    if any(v % 15 for r in originals for v in (r.start, r.end)):
        return ()
    if not policy.minimum_block_minutes <= duration <= policy.site_maximum_booking_minutes:
        return ()
    now_utc = now.astimezone(timezone.utc)
    cutoff = now_utc + timedelta(minutes=freeze_minutes)
    try:
        if any(local_instant(r.day, r.start) <= cutoff for r in originals):
            return ()
    except ValueError:
        return ()
    # Do not move a seed booking away from its still-active extension plan.
    for extension in protected_extensions:
        if any(extension.get("eventId") == r.event_id
                and clock_minutes(extension["target_end"]) > r.end for r in originals):
            return ()

    day_events = [e for e in events if e["date"] == original.day.isoformat()
                  and e.get("eventId") not in original_ids]
    other_peak = sum(interval_overlap_minutes(clock_minutes(e["startTime"]),
                    clock_minutes(e["endTime"]), peak_start, peak_end)
                    for e in day_events if e.get("isReservation") is True)
    pref_window = None
    if time_preferences.get("enabled"):
        pref_window = (int(round(time_preferences["start_hour"] * 60)),
                       int(round(time_preferences["end_hour"] * 60)))
    old_value = sum(soft_time_value(r.start, r.duration, pref_window) for r in originals)
    peak_pref = (clock_minutes(planning.preferred_peak_start),
                 clock_minutes(planning.preferred_peak_end))
    old_peak_pref = (sum(interval_overlap_minutes(r.start, r.end, *peak_pref) for r in originals)
                     if planning.enabled and original.day.weekday() < 5 else 0)
    old_rank = min(policy.room_order.index(r.room) for r in originals)
    results = {}
    for room_data in available_data:
        room = room_data.get("room")
        if room not in policy.room_order:
            continue
        room_rank = policy.room_order.index(room)
        if room_rank > old_rank or (room_rank == old_rank and len(originals) == 1):
            continue
        horizon = timedelta(minutes=policy.horizon_minutes_for(room))
        # Earliest plausible site time prevents a fast local clock opening a room early.
        clock_bounds = policy.site_clock_offset_bounds
        if clock_bounds is None:
            continue
        bookable_until = min(now_utc + timedelta(seconds=clock_bounds[0]) + horizon,
                             policy.booking_horizon.astimezone(timezone.utc))
        room_gaps = list(room_data.get("slots", ()))
        # The anchor's own interval is released by the same atomic edit. Other
        # reservations remain occupied until the longer anchor is verified.
        if len(originals) > 1 and room == original.room:
            intervals = [(g["startHour"], g["endHour"]) for g in room_gaps]
            intervals.append((original.start / 60, original.end / 60))
            merged = []
            for a, b in sorted(intervals):
                if merged and a <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
                else:
                    merged.append((a, b))
            room_gaps = [{"startHour": a, "endHour": b} for a, b in merged]
        for gap in room_gaps:
            a, b = gap["startHour"] * 60, gap["endHour"] * 60
            if not all(isinstance(v, (int, float)) and isfinite(v) for v in (a, b)):
                raise ValueError("Invalid observed room gap")
            if not 0 <= a < b <= 1440:
                raise ValueError("Observed room gap is outside the day")
            first = int(ceil(a / 15)) * 15
            last = min(1425 - duration, int(floor((b - duration) / 15)) * 15)
            for start in range(first, last + 1, 15):
                end = start + duration
                try:
                    if local_instant(original.day, start) <= cutoff:
                        continue
                    end_at = local_instant(original.day, end)
                    if end_at > policy.booking_horizon.astimezone(timezone.utc):
                        continue
                    if not _ignore_room_horizon and end_at > bookable_until:
                        continue
                except ValueError:
                    continue
                value = soft_time_value(start, duration, pref_window)
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
                    if ext["date"] == original.day.isoformat() and ext.get("eventId") not in original_ids:
                        a, b = clock_minutes(ext["startTime"]), clock_minutes(ext["target_end"])
                        gap_minutes = same_room_gap if ext["room"] == room else 0
                        if _overlaps(start, end, a - gap_minutes, b + gap_minutes):
                            invalid = True
                if invalid:
                    continue
                replacement = Reservation(original.event_id, original.day, room, start, end)
                rank = (-round(value, 8), -preferred, room_rank,
                        abs(start - original.start), start, room)
                results[(room, start, end)] = (RoomUpgrade(original, replacement, rank) if len(originals) == 1
                                               else RoomConsolidation(originals, replacement, rank))
    return tuple(sorted(results.values(), key=lambda item: item.rank))


def find_room_consolidations(*, events, policy, eligible_event_ids=None, **kwargs):
    """Search every eligible same-day group up to the site's session ceiling.

    The runtime still needs server approval while all donor bookings exist.
    A feasible final arrangement is never permission to cancel first.
    """
    by_day = {}
    for event in events:
        if (event.get("isReservation") is True and event.get("room") in policy.room_order
                and (eligible_event_ids is None or event.get("eventId") in eligible_event_ids)):
            record = Reservation.from_event(event)
            by_day.setdefault(record.day, []).append(record)
    found = []
    for records in by_day.values():
        records.sort(key=lambda r: (r.start, r.event_id))
        # Every record is at least a quarter-hour; this is a capacity bound,
        # not an arbitrary candidate cap that hides larger combinations.
        for size in range(2, min(len(records), policy.site_maximum_booking_minutes // 15) + 1):
            for group in combinations(records, size):
                if sum(r.duration for r in group) > policy.site_maximum_booking_minutes:
                    continue
                if any(a.end > b.start for a, b in zip(group, group[1:])):
                    continue
                for anchor in group:
                    found.extend(find_room_upgrades(anchor, events=events, policy=policy, _originals=group, **kwargs))
    return tuple(sorted(found, key=lambda c: (-(len(c.originals) - 1), c.rank, c.original.event_id)))


@dataclass(frozen=True)
class UpgradeOpportunity:
    """Display-only prospect; the execution path must rediscover it live."""
    change: RoomUpgrade | RoomConsolidation
    opens_at: datetime


def find_upgrade_opportunities(*, events, policy, now, eligible_event_ids=None, **kwargs):
    """Inspect the full observed window, including shorter horizons yet to open.

    No probability is invented: a future prospect means the grid currently
    shows a gap. Teachers/other students can still occupy it before it opens.
    """
    changes = []
    for event in events:
        if event.get("isReservation") is True and (eligible_event_ids is None or event.get("eventId") in eligible_event_ids):
            changes.extend(find_room_upgrades(Reservation.from_event(event), events=events, policy=policy, now=now,
                                              _ignore_room_horizon=True, **kwargs))
    changes.extend(find_room_consolidations(events=events, policy=policy, now=now,
                   eligible_event_ids=eligible_event_ids, _ignore_room_horizon=True, **kwargs))
    output = []
    for change in changes:
        new = change.replacement
        opens_at = max(now.astimezone(timezone.utc), local_instant(new.day, new.end)
                       - timedelta(minutes=policy.horizon_minutes_for(new.room), seconds=policy.site_clock_offset_bounds[0]))
        deadline = min(*(local_instant(r.day, r.start) for r in change.originals), local_instant(new.day, new.start))
        deadline -= timedelta(minutes=kwargs.get("freeze_minutes", DEFAULT_FREEZE_MINUTES))
        if opens_at < deadline:
            output.append(UpgradeOpportunity(change, opens_at))
    return tuple(sorted(output, key=lambda item: (item.opens_at, item.change.rank)))


def select_upgrade_portfolio(candidates, *, policy, planning, time_preferences,
                             events, same_room_gap=60, peak_start=540, peak_end=960, peak_limit=120):
    """Choose compatible improvements together, preserving every original once.

    Exact memoized interval search includes original identity and room cooldowns.
    The runtime executes one selected change and replans from fresh evidence.
    """
    choices = tuple(sorted(candidates, key=lambda c: (c.replacement.start, c.replacement.end,
                    c.replacement.room, tuple(r.event_id for r in c.originals), c.original.event_id)))
    if not choices:
        return ()
    day = choices[0].replacement.day
    if any(c.replacement.day != day for c in choices):
        raise ValueError("An upgrade portfolio must describe one date")
    starts = tuple(sorted({c.replacement.start for c in choices}))
    by_start = {start: [] for start in starts}
    for index, c in enumerate(choices):
        by_start[c.replacement.start].append(index)
    ids = {r.event_id for c in choices for r in c.originals}
    bit = {event_id: 1 << index for index, event_id in enumerate(sorted(ids))}
    masks = tuple(sum(bit[r.event_id] for r in c.originals) for c in choices)
    rooms = {room: i for i, room in enumerate(policy.room_order)}
    pref = ((round(time_preferences["start_hour"] * 60), round(time_preferences["end_hour"] * 60))
            if time_preferences.get("enabled") else None)
    peak_pref = (clock_minutes(planning.preferred_peak_start), clock_minutes(planning.preferred_peak_end))
    weekday = day.weekday() < 5
    initial_peak = sum(interval_overlap_minutes(clock_minutes(e["startTime"]), clock_minutes(e["endTime"]),
                                               peak_start, peak_end) for e in events
                       if e.get("isReservation") is True and e["date"] == day.isoformat())
    limit = peak_limit if weekday else 1440
    deltas = []
    scores = []
    for c in choices:
        new = c.replacement
        time_gain = round(soft_time_value(new.start, new.duration, pref)
                         - sum(soft_time_value(r.start, r.duration, pref) for r in c.originals), 8)
        quality_gain = sum(rooms[r.room] * r.duration for r in c.originals) - rooms[new.room] * new.duration
        preferred_gain = (interval_overlap_minutes(new.start, new.end, *peak_pref)
                          - sum(interval_overlap_minutes(r.start, r.end, *peak_pref) for r in c.originals)
                          if planning.enabled and weekday else 0)
        primary = ((quality_gain, time_gain, preferred_gain) if planning.priority_mode == "room_first"
                   else (time_gain, preferred_gain, quality_gain))
        scores.append((*primary, len(c.originals) - 1, -abs(new.start - min(r.start for r in c.originals))))
        deltas.append(interval_overlap_minutes(new.start, new.end, peak_start, peak_end)
                      - sum(interval_overlap_minutes(r.start, r.end, peak_start, peak_end) for r in c.originals))
    zero = (0, 0, 0, 0, 0)

    @lru_cache(maxsize=None)
    def solve(position, used, cooldowns, peak):
        if position >= len(starts):
            return (zero, ()) if peak <= limit else None
        next_start = starts[position + 1] if position + 1 < len(starts) else 1440
        best = solve(position + 1, used, tuple((r, end) for r, end in cooldowns if end > next_start), peak)
        for index in by_start[starts[position]]:
            c = choices[index]
            new = c.replacement
            active = dict(cooldowns)
            room = rooms[new.room]
            if not (used & masks[index]) and active.get(room, 0) <= new.start:
                next_pos = bisect_left(starts, new.end, lo=position + 1)
                active[room] = new.end + same_room_gap
                next_time = starts[next_pos] if next_pos < len(starts) else 1440
                remaining = tuple(sorted((r, end) for r, end in active.items() if end > next_time))
                tail = solve(next_pos, used | masks[index], remaining, peak + deltas[index])
                if tail is not None:
                    score = tuple(a + b for a, b in zip(scores[index], tail[0]))
                    proposed = (score, (index, *tail[1]))
                    if best is None or proposed[0] > best[0] or (proposed[0] == best[0] and proposed[1] < best[1]):
                        best = proposed
        return best

    result = solve(0, 0, (), initial_peak)
    return tuple(choices[i] for i in result[1]) if result else ()
