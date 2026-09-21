"""Pure, incremental transfers into rooms at their rolling booking boundary.

The result describes a completed step, not permission to shorten a reservation.
Every source change and destination Save still needs fresh runtime checks and a
durable recovery record. In particular, ``prepare_at`` never permits early trim.
"""

from booking_quotas import peak_quota_exempt
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from math import ceil, floor, isfinite

from daily_planner import interval_overlap_minutes, soft_time_value
from room_upgrades import (Reservation, RoomConsolidation, RoomUpgrade,
                           clock_minutes, find_room_upgrades, local_instant)


MAX_REMAINDER_SEARCH_NODES = 50000
MAX_LAYOUT_ACCEPTANCE_CHECKS = 64


@dataclass(frozen=True)
class TransferPlan:
    """One full-coverage step, with a placeholder ID only for a NEW seed.

    ``originals`` excludes an existing seed. Each ``remaining`` item retains a
    source identity and room, while an omitted source is to be retired. A new
    seed's ``replacement.event_id`` is only the target's placeholder: it must
    never be interpreted as changing or deleting the matching fallback ID.
    """

    originals: tuple[Reservation, ...]
    target: Reservation
    replacement: Reservation
    remaining: tuple[Reservation, ...]
    opens_at: datetime
    rank: tuple = ()
    seed_event_id: int | None = None
    preparation_seconds: int = 180
    seed: Reservation | None = None

    def __post_init__(self):
        if (not isinstance(self.originals, tuple) or not self.originals
                or not all(isinstance(r, Reservation) for r in self.originals)
                or len({r.event_id for r in self.originals}) != len(self.originals)):
            raise ValueError("Transfer requires exact distinct fallback identities")
        if self.opens_at.tzinfo is None or self.opens_at.utcoffset() is None:
            raise ValueError("Transfer opening must be timezone-aware")
        if type(self.preparation_seconds) is not int or self.preparation_seconds < 0:
            raise ValueError("Preparation lead must be non-negative whole seconds")
        old_by_id = {r.event_id: r for r in self.originals}
        if any(r.day != self.target.day for r in self.originals):
            raise ValueError("Transfer cannot change date")
        if (self.replacement.day != self.target.day or self.replacement.room != self.target.room
                or self.replacement.start != self.target.start or self.replacement.end > self.target.end):
            raise ValueError("Replacement must be a prefix of the final session")
        if self.seed is None:
            if self.seed_event_id is not None:
                raise ValueError("An existing seed ID requires its exact reservation")
            if self.replacement.event_id != self.target.event_id:
                raise ValueError("A new seed uses only the final target's placeholder ID")
            old_minutes = 0
        else:
            if (self.seed_event_id != self.seed.event_id or self.seed_event_id in old_by_id
                    or self.replacement.event_id != self.seed_event_id
                    or self.seed.day != self.target.day or self.seed.room != self.target.room
                    or self.seed.start != self.target.start or self.seed.end >= self.replacement.end):
                raise ValueError("Extension must grow the exact existing seed")
            old_minutes = self.seed.duration
        if not isinstance(self.remaining, tuple) or len({r.event_id for r in self.remaining}) != len(self.remaining):
            raise ValueError("Fallback identities may not be duplicated or split")
        for record in self.remaining:
            old = old_by_id.get(record.event_id)
            if (old is None or record.day != old.day or record.room != old.room
                    or record.duration > old.duration):
                raise ValueError("Retained fallbacks must preserve identity, room and bounded duration")
        total = old_minutes + sum(r.duration for r in self.originals)
        if total != self.target.duration or total != self.replacement.duration + sum(r.duration for r in self.remaining):
            raise ValueError("Each completed transfer must preserve every booked minute")
        before = (*self.originals, *((self.seed,) if self.seed else ()))
        if self.target.event_id not in {r.event_id for r in before}:
            raise ValueError("The final target placeholder must identify a known source or seed")
        ordered_before = sorted(before, key=lambda r: r.start)
        if any(a.end > b.start for a, b in zip(ordered_before, ordered_before[1:])):
            raise ValueError("Overlapping originals cannot establish distinct booked minutes")
        ordered = sorted((*self.remaining, self.replacement), key=lambda r: r.start)
        if any(a.end > b.start for a, b in zip(ordered, ordered[1:])):
            raise ValueError("Completed transfer sessions cannot overlap")

    @property
    def prepare_at(self):
        return self.opens_at - timedelta(seconds=self.preparation_seconds)

    @property
    def adjustments(self):
        kept = {r.event_id: r for r in self.remaining}
        return tuple((old, kept.get(old.event_id)) for old in self.originals
                     if kept.get(old.event_id) != old)

    @property
    def action_count(self):
        return len(self.adjustments) + 1

    @property
    def transferred_minutes(self):
        return self.replacement.duration - (self.seed.duration if self.seed else 0)


def _overlap(a, b, c, d):
    return a < d and b > c


def _merged(intervals):
    result = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def _room_intervals(available_data):
    rooms = {}
    for entry in available_data:
        room = entry.get("room")
        for slot in entry.get("slots", ()):
            start, end = slot["startHour"] * 60, slot["endHour"] * 60
            if (not all(isinstance(v, (int, float)) and isfinite(v) for v in (start, end))
                    or not 0 <= start < end <= 1440):
                raise ValueError("Invalid observed room gap")
            rooms.setdefault(room, []).append((start, end))
    return {room: _merged(intervals) for room, intervals in rooms.items()}


def _with_seed(available_data, seed):
    """Only an exact agenda-proved seed can supply its occupied destination gap."""
    rooms = _room_intervals(available_data)
    if seed is not None:
        rooms.setdefault(seed.room, []).append((seed.start, seed.end))
    return [{"room": room, "slots": [{"startHour": a / 60, "endHour": b / 60}
             for a, b in _merged(intervals)]} for room, intervals in rooms.items()]


def ordered_adjustments(plan, *, same_room_gap=60, peak_start=540, peak_end=960):
    """Order exact source edits without crossing another still-held booking.

    This does not authorize execution or establish live availability. Cyclic
    relocations return None. Temporary peak usage is bounded by the greater of
    the before/after totals, which the caller has independently checked against
    the user's other bookings. The existing seed remains held throughout.
    """
    return _ordered_adjustments(plan.originals, plan.remaining, plan.replacement, plan.seed,
                                same_room_gap, peak_start, peak_end)


def _ordered_adjustments(originals, remaining, replacement, seed, same_room_gap, peak_start, peak_end):
    current = {r.event_id: r for r in originals}
    old_seed = (seed,) if seed else ()
    def peak(records):
        return sum(interval_overlap_minutes(r.start, r.end, peak_start, peak_end) for r in records)
    peak_budget = max(peak((*originals, *old_seed)), peak((*remaining, replacement)))
    kept = {r.event_id: r for r in remaining}
    left = [(before, kept.get(before.event_id)) for before in originals if kept.get(before.event_id) != before]
    output = []
    while left:
        possible = []
        for before, after in left:
            others = tuple(r for event_id, r in current.items() if event_id != before.event_id) + old_seed
            if after is not None:
                if any(_overlap(after.start, after.end,
                    record.start - (same_room_gap if record.room == after.room else 0),
                    record.end + (same_room_gap if record.room == after.room else 0)) for record in others):
                    continue
                if peak((*others, after)) > peak_budget:
                    continue
            # An in-place reduction/cancellation cannot acquire a new conflict.
            simple = after is None or before.start <= after.start < after.end <= before.end
            possible.append((not simple, before.event_id, before, after))
        if not possible:
            return None
        _, _, before, after = min(possible, key=lambda item: item[:2])
        output.append((before, after))
        left.remove((before, after))
        if after is None:
            del current[before.event_id]
        else:
            current[before.event_id] = after
    return tuple(output)


def plan_progressive_transfer(opportunity, *, events, available_data, policy, now,
                              time_preferences, planning, seed=None, peak_start=540,
                              peak_end=960, peak_limit=120, same_room_gap=60,
                              freeze_minutes=0, blocked_intervals=(),
                              ignored_event_ids=(), protected_extensions=(),
                              preparation_seconds=180, acceptable_layout=None,
                              free_horizon_overrides_peak=False, free_horizon_minutes=300):
    """Return the largest feasible open prefix, or the earliest future step.

    ``opportunity`` may be an UpgradeOpportunity or its RoomUpgrade/
    RoomConsolidation. For a resumed transfer its originals contain the current
    seed and current fallbacks, never historical pre-trim records. Final room,
    time, date and whole-session constraints are reused from the existing
    planner. The aspirational remainder need not still be free: only the exact
    proposed prefix must be covered by fresh room availability.

    Remainders stay in their original rooms, with at most one record per ID.
    Their placements may use original occupied intervals plus freshly observed
    free gaps. The bounded search returns no plan if its budget is exhausted;
    it never presents an incomplete search as a safe completed arrangement.
    A pure ``acceptable_layout(plan)`` predicate may impose additional whole-day
    capacity requirements. Rejected arrangements yield other layouts and then
    other prefixes, within a shared bounded number of predicate evaluations.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Progressive planning requires an aware current time")
    if type(preparation_seconds) is not int or preparation_seconds < 0:
        raise ValueError("Preparation lead must be non-negative whole seconds")
    if acceptable_layout is not None and not callable(acceptable_layout):
        raise TypeError("Layout acceptance must be a callable pure predicate")
    # A progressive prefix plus fallback creates multiple reservations even
    # when their times touch. Respect an explicit single-booking preference;
    # the ordinary whole-session upgrade path remains available instead.
    if getattr(policy, 'allow_fragmented_sessions', True) is False:
        return None
    change = getattr(opportunity, "change", opportunity)
    if not isinstance(change, (RoomUpgrade, RoomConsolidation)):
        raise TypeError("Progressive transfer requires an exact upgrade opportunity")
    if isinstance(change, RoomConsolidation) and change.bridges:
        return None
    group = change.originals
    target = change.replacement
    if seed is not None and (not isinstance(seed, Reservation) or seed not in group
                             or seed.room != target.room or seed.start != target.start
                             or seed.day != target.day or seed.end >= target.end):
        return None
    originals = tuple(r for r in group if seed is None or r.event_id != seed.event_id)
    if not originals or sum(r.duration for r in group) != target.duration:
        return None
    # An extension owned by another process is never taken over implicitly.
    protected_extensions = tuple(protected_extensions)
    live_gaps = _with_seed(available_data, seed)
    live_intervals = _room_intervals(live_gaps)
    # A saved full target remains a preference, not a promise that its entire
    # tail is free. Reuse the full-session structural/rank/time checks with a
    # hypothetical gap, but never use that gap for prefix feasibility, source
    # placement, day-capacity accounting or an actual Save.
    structure_intervals = {room: list(values) for room, values in live_intervals.items()}
    structure_intervals.setdefault(target.room, []).append((target.start, target.end))
    structure_gaps = [{"room": room, "slots": [{"startHour": a / 60, "endHour": b / 60}
                       for a, b in _merged(values)]} for room, values in structure_intervals.items()]
    kwargs = dict(events=events, available_data=structure_gaps, policy=policy,
                  now=now, time_preferences=time_preferences, planning=planning,
                  peak_start=peak_start, peak_end=peak_end, peak_limit=peak_limit,
                  free_horizon_overrides_peak=free_horizon_overrides_peak, free_horizon_minutes=free_horizon_minutes,
                  same_room_gap=same_room_gap, freeze_minutes=freeze_minutes,
                  blocked_intervals=blocked_intervals, ignored_event_ids=ignored_event_ids,
                  protected_extensions=protected_extensions, _ignore_room_horizon=True)
    anchor = next((r for r in group if r.event_id == target.event_id), None)
    if anchor is None:
        return None
    options = find_room_upgrades(anchor, _originals=group, **kwargs)
    if not any(option.replacement == target and option.originals == group for option in options):
        return None
    minimum = int(ceil(policy.minimum_block_minutes / 15)) * 15
    first_end = seed.end + 15 if seed else target.start + minimum
    if first_end > target.end:
        return None
    now_utc = now.astimezone(timezone.utc)
    room_horizon = timedelta(minutes=policy.horizon_minutes_for(target.room),
                             seconds=policy.site_clock_offset_bounds[0])
    # Whole-session first moves retain the existing atomic room-upgrade path.
    # Only an already-started transfer may need a final all-remaining step here.
    if (seed is None and local_instant(target.day, target.end) - room_horizon <= now_utc
            and any(start <= target.start and finish >= target.end
                    for start, finish in live_intervals.get(target.room, ()))):
        return None
    cutoff = min(local_instant(r.day, r.start) for r in group)
    cutoff = min(cutoff, local_instant(target.day, target.start)) - timedelta(minutes=freeze_minutes)
    edges = []
    for end in range(first_end, target.end + 1, 15):
        if seed is None and end == target.end:
            continue
        opening = local_instant(target.day, end) - room_horizon
        if opening < cutoff:
            edges.append((end, opening))
    # Catch up immediately when a run starts late; otherwise prepare the first
    # legal boundary. Openings are genuine edges, not clamped to the scan time.
    edges.sort(key=lambda item: (0, -item[0]) if item[1] <= now_utc else (1, item[0]))
    acceptance_checks = 0
    for end, opening in edges:
        if not any(start <= target.start and finish >= end
                   for start, finish in live_intervals.get(target.room, ())):
            continue
        replacement = replace(target, event_id=seed.event_id if seed else target.event_id, end=end)
        def make_plan(remaining, remainder_rank):
            return TransferPlan(originals=originals, target=target, replacement=replacement,
                remaining=remaining, opens_at=opening, rank=(*change.rank, *remainder_rank),
                seed_event_id=seed.event_id if seed else None,
                preparation_seconds=preparation_seconds, seed=seed)

        def accepts(remaining, remainder_rank):
            nonlocal acceptance_checks
            if acceptable_layout is None:
                return True
            acceptance_checks += 1
            if acceptance_checks > MAX_LAYOUT_ACCEPTANCE_CHECKS:
                return _SEARCH_EXHAUSTED
            return acceptable_layout(make_plan(remaining, remainder_rank)) is True

        result = _find_remainders(originals, group, replacement, events=events,
                  available_data=available_data, policy=policy, now=max(now_utc, opening),
                  time_preferences=time_preferences, planning=planning, peak_start=peak_start,
                  peak_end=peak_end, peak_limit=peak_limit, same_room_gap=same_room_gap,
                  free_horizon_overrides_peak=free_horizon_overrides_peak, free_horizon_minutes=free_horizon_minutes,
                  freeze_minutes=freeze_minutes, blocked_intervals=blocked_intervals,
                  protected_extensions=protected_extensions, minimum=minimum,
                  accepts=accepts)
        if result is _SEARCH_EXHAUSTED:
            return None
        if result is not None:
            remaining, remainder_rank = result
            result = make_plan(remaining, remainder_rank)
            if ordered_adjustments(result, same_room_gap=same_room_gap,
                                   peak_start=peak_start, peak_end=peak_end) is not None:
                return result
    return None


_SEARCH_EXHAUSTED = object()


def _find_remainders(originals, group, replacement, *, events, available_data,
                     policy, now, time_preferences, planning, peak_start, peak_end,
                     peak_limit, same_room_gap, freeze_minutes, blocked_intervals,
                     protected_extensions, minimum, accepts, free_horizon_overrides_peak=False, free_horizon_minutes=300):
    needed = sum(r.duration for r in group) - replacement.duration
    day = replacement.day
    group_ids = {r.event_id for r in group}
    others = [e for e in events if e["date"] == str(day) and e.get("eventId") not in group_ids]
    pref = ((round(time_preferences["start_hour"] * 60), round(time_preferences["end_hour"] * 60))
            if time_preferences.get("enabled") else None)
    peak_pref = (clock_minutes(planning.preferred_peak_start), clock_minutes(planning.preferred_peak_end))
    old_value = sum(soft_time_value(r.start, r.duration, pref) for r in group)
    old_preferred = (sum(interval_overlap_minutes(r.start, r.end, *peak_pref) for r in group)
                     if planning.enabled and day.weekday() < 5 else 0)
    other_peak = sum(interval_overlap_minutes(clock_minutes(e["startTime"]), clock_minutes(e["endTime"]),
                                             peak_start, peak_end) for e in others if e.get("isReservation") is True)
    intervals = _room_intervals(available_data)
    for original in originals:
        intervals.setdefault(original.room, []).append((original.start, original.end))
    intervals = {room: _merged(values) for room, values in intervals.items()}
    options = {}
    for original in originals:
        possibilities = {None}
        for start, end in intervals.get(original.room, ()):
            for length in range(minimum, min(original.duration, needed) + 1, 15):
                for a in range(int(ceil(start / 15)) * 15, int(floor((end - length) / 15)) * 15 + 1, 15):
                    b = a + length
                    if not 0 <= a < b < 1440:
                        continue
                    record = replace(original, start=a, end=b)
                    gap = same_room_gap if record.room == replacement.room else 0
                    if _overlap(a, b, replacement.start - gap, replacement.end + gap):
                        continue
                    if any(_overlap(a, b, c, d) for c, d in blocked_intervals):
                        continue
                    if pref and time_preferences.get("strict_mode") and not pref[0] <= a < b <= pref[1]:
                        continue
                    try:
                        if local_instant(day, a) <= now + timedelta(minutes=freeze_minutes):
                            continue
                        # An unchanged reservation remains valid even if the
                        # site's current policy shortened since it was booked.
                        if record != original:
                            limit = now + timedelta(minutes=policy.horizon_minutes_for(record.room),
                                                    seconds=policy.site_clock_offset_bounds[0])
                            if local_instant(day, b) > min(limit, policy.booking_horizon.astimezone(timezone.utc)):
                                continue
                    except ValueError:
                        continue
                    invalid = False
                    for event in others:
                        c, d = clock_minutes(event["startTime"]), clock_minutes(event["endTime"])
                        if event.get("blocksConflict", True) and _overlap(a, b, c, d):
                            invalid = True
                        if (event.get("isReservation") is True and event.get("room") == record.room
                                and _overlap(a, b, c - same_room_gap, d + same_room_gap)):
                            invalid = True
                    for ext in protected_extensions:
                        if ext["date"] == str(day) and ext.get("eventId") not in group_ids:
                            gap = same_room_gap if ext["room"] == record.room else 0
                            if _overlap(a, b, clock_minutes(ext["startTime"]) - gap,
                                        clock_minutes(ext["target_end"]) + gap):
                                invalid = True
                    if not invalid:
                        possibilities.add(record)
        def local_rank(record):
            return (record != original, record is None,
                    abs(record.start - original.start) + abs(record.end - original.end) if record else original.duration,
                    record.start if record else 1440, -(record.duration if record else 0))
        options[original.event_id] = tuple(sorted(possibilities, key=local_rank))
    # Most constrained sources first, while preserving source order in output.
    order = sorted(originals, key=lambda r: (len(options[r.event_id]), r.event_id))
    best = None
    visited = 0
    exhausted = False

    def search(index, minutes_left, kept, changes, movement):
        nonlocal best, visited, exhausted
        visited += 1
        if visited > MAX_REMAINDER_SEARCH_NODES:
            exhausted = True
            return
        if minutes_left < 0 or minutes_left > sum(r.duration for r in order[index:]):
            return
        if best is not None and (changes, movement) > best[0][:2]:
            return
        if index == len(order):
            if minutes_left:
                return
            all_records = (*kept, replacement)
            value = sum(soft_time_value(r.start, r.duration, pref) for r in all_records)
            preferred = (sum(interval_overlap_minutes(r.start, r.end, *peak_pref) for r in all_records)
                         if planning.enabled and day.weekday() < 5 else 0)
            if value + 1e-8 < old_value or preferred < old_preferred:
                return
            if day.weekday() < 5 and other_peak + sum(interval_overlap_minutes(r.start, r.end,
                                                   peak_start, peak_end) for r in all_records) > peak_limit:
                old_by_id = {r.event_id: r for r in group}
                for record in all_records:
                    old = old_by_id.get(record.event_id)
                    # Unchanged/trimmed fallbacks acquire no time. Every moved
                    # peak interval must individually qualify, including its start.
                    retained = (old is not None and old.room == record.room
                                and old.start <= record.start < record.end <= old.end)
                    if (interval_overlap_minutes(record.start, record.end, peak_start, peak_end)
                            and not retained and not peak_quota_exempt(day, record.start / 60, record.end / 60,
                                now=now, free_horizon_overrides_peak=free_horizon_overrides_peak,
                                free_horizon_minutes=free_horizon_minutes)):
                        return
                if not free_horizon_overrides_peak:
                    return
            source_ids = {r.event_id for r in originals}
            seed = next((r for r in group if r.event_id not in source_ids), None)
            if _ordered_adjustments(originals, kept, replacement, seed,
                                    same_room_gap, peak_start, peak_end) is None:
                return
            ordered = sorted(all_records, key=lambda r: r.start)
            holes = sum(b.start - a.end for a, b in zip(ordered, ordered[1:]))
            rank = (changes, movement, holes, -round(value, 8), -preferred,
                    tuple((r.event_id, r.start, r.end) for r in sorted(kept, key=lambda r: r.event_id)))
            if best is None or rank < best[0]:
                mapping = {r.event_id: r for r in kept}
                remaining = tuple(mapping[r.event_id] for r in originals if r.event_id in mapping)
                accepted = accepts(remaining, rank)
                if accepted is _SEARCH_EXHAUSTED:
                    exhausted = True
                    return
                if accepted:
                    best = (rank, remaining)
            return
        original = order[index]
        for record in options[original.event_id]:
            if exhausted:
                return
            length = record.duration if record else 0
            if length > minutes_left:
                continue
            if record is not None and any(_overlap(record.start, record.end,
                r.start - (same_room_gap if r.room == record.room else 0),
                r.end + (same_room_gap if r.room == record.room else 0)) for r in kept):
                continue
            search(index + 1, minutes_left - length, (*kept, record) if record else kept,
                   changes + (record != original), movement + (abs(record.start - original.start)
                   + abs(record.end - original.end) if record else original.duration))

    search(0, needed, (), 0, 0)
    if exhausted:
        return _SEARCH_EXHAUSTED
    return (best[1], best[0]) if best is not None else None
