"""Optional session comfort: refine good bookings, never veto scarce hours.

The ordinary planner supplies a feasible baseline. Refinement keeps its exact
coverage and required source, and cannot worsen its primary time/room quality
or replace immediately bookable time with a future promise. Search limits keep
the baseline available even on highly contested, dense calendars.
"""
from bisect import bisect_left
from functools import lru_cache


def enabled(planning):
    return bool(getattr(planning, 'enabled', True) and (getattr(planning, 'preferred_block_minutes', 0)
                or getattr(planning, 'preferred_rest_minutes', 0)
                or getattr(planning, 'prefer_fewer_room_changes', False)))


def comfort_key(sessions, planning):
    """Lower is better; sessions are (start minute, end minute, room) tuples."""
    ordered = sorted(sessions)
    block = getattr(planning, 'preferred_block_minutes', 0)
    rest = getattr(planning, 'preferred_rest_minutes', 0)
    length_cost = sum(abs(end-start-block) for start, end, _ in ordered) if block else 0
    rest_cost = sum(max(0, rest-(right[0]-left[1]))
                    for left, right in zip(ordered, ordered[1:])) if rest else 0
    changes = sum(left[2] != right[2] for left, right in zip(ordered, ordered[1:])) \
        if getattr(planning, 'prefer_fewer_room_changes', False) else 0
    return length_cost, rest_cost, changes


def tracker_sessions(tracker, day, planning=None):
    """Confirmed practice only; lectures do not become practice sessions."""
    if planning is not None and not enabled(planning):
        return ()
    ranges = set(tracker.reservation_ranges.get(day.isoformat(), ()))
    ranges.update((start, end, room) for room, booked_day, start, end in tracker.bookings
                  if str(booked_day)[:10] == day.isoformat())
    return tuple(sorted((round(start*60), round(end*60), room) for start, end, room in ranges))


def refine_plan(baseline, opportunities, planning, *, now, remaining_peak_minutes,
                same_room_gap_minutes, allow_fragmented_sessions, rank_key=None,
                required_opportunity=None, existing_sessions=(), state_limit=15000,
                transition_limit=75000):
    from daily_planner import opportunity_rank, resize_opportunity, soft_time_value, soft_time_is_worth_booking
    if not baseline or not enabled(planning):
        return baseline
    target = sum(item.potential_minutes for item in baseline)
    peak_limit = 1440 if remaining_peak_minutes is None else remaining_peak_minutes
    rank = rank_key or (lambda item: opportunity_rank(item, planning, now=now))
    existing = tuple(existing_sessions)

    def source(item):
        return (item.room, item.target_date, item.start_minutes, item.unlock_at,
                item.initial_minutes, item.source_gap_start_minutes, item.source_gap_end_minutes)
    required = source(required_opportunity) if required_opportunity else None
    records = {}
    for item in opportunities:
        for duration in range(item.initial_minutes, min(target, item.potential_minutes)+1, 15):
            if not soft_time_is_worth_booking(item.start_minutes, duration, item.soft_preferred_window):
                continue
            variant = resize_opportunity(item, item.start_minutes+duration, planning)
            if variant.peak_minutes > peak_limit:
                continue
            if any(variant.start_minutes < end and variant.end_minutes > start
                   for start, end, _room in existing):
                continue
            key = (variant.start_minutes, variant.end_minutes, variant.room)
            record = (variant, source(item) == required)
            old = records.get(key)
            if old is None or (record[1] and not old[1]) or (record[1] == old[1] and rank(variant) < rank(old[0])):
                records[key] = record
    # Keep every baseline member even when an upstream source representation
    # was coalesced, so the bounded search can never lose its known solution.
    for item in baseline:
        key = (item.start_minutes, item.end_minutes, item.room)
        records.setdefault(key, (item, source(item) == required))
    rows = tuple(sorted(records.values(), key=lambda row: (
        row[0].start_minutes, rank(row[0]), row[0].end_minutes, row[0].room)))
    variants = tuple(row[0] for row in rows)
    minimum = min(item.potential_minutes for item in variants)
    starts = tuple(item.start_minutes for item in variants)
    room_ids = {room: n for n, room in enumerate(sorted({item.room for item in variants}))}

    def primary(plan):
        value = round(sum(soft_time_value(i.start_minutes, i.potential_minutes,
                                         i.soft_preferred_window) for i in plan), 7)
        preferred = sum(i.preferred_minutes for i in plan)
        rooms = sum(i.room_priority*i.potential_minutes for i in plan)
        return (-value, rooms, -preferred) if planning.priority_mode == 'room_first' else (-value, -preferred, rooms)

    def key(plan, previous=-1):
        selected = [variants[i] for i in plan]
        comfort = [(i.start_minutes, i.end_minutes, i.room) for i in selected]
        if previous >= 0:
            old = variants[previous]
            comfort.append((old.start_minutes, old.end_minutes, old.room))
        return (*primary(selected), *comfort_key((*existing, *comfort), planning), len(plan),
                tuple(sorted(rank(i) for i in selected)),
                tuple((i.start_minutes, i.end_minutes, i.room) for i in selected))

    states = transitions = 0
    class SearchLimit(Exception):
        pass

    @lru_cache(maxsize=None)
    def solve(position, minutes, peak, cooldowns, required_left, previous, slots_left):
        nonlocal states, transitions
        states += 1
        if states > state_limit:
            raise SearchLimit
        if minutes == 0:
            return None if required_left else ()
        if position >= len(rows) or minutes < minimum or slots_left <= 0:
            return None
        best = None
        # Group equal starts to avoid recursion through thousands of room rows.
        here = starts[position]
        end_position = bisect_left(starts, here+1)
        blocked = {room for room, until in cooldowns if until > here}
        for index in range(position, end_position):
            transitions += 1
            if transitions > transition_limit:
                raise SearchLimit
            item, is_required = rows[index]
            room = room_ids[item.room]
            if item.potential_minutes > minutes or item.peak_minutes > peak or room in blocked:
                continue
            next_position = bisect_left(starts, item.end_minutes)
            next_time = starts[next_position] if next_position < len(starts) else 1440
            updated = dict(cooldowns)
            updated[room] = item.end_minutes+same_room_gap_minutes
            updated = tuple(sorted((r, until) for r, until in updated.items() if until > next_time))
            tail = solve(next_position, minutes-item.potential_minutes, peak-item.peak_minutes,
                         updated, required_left and not is_required, index, slots_left-1)
            if tail is not None:
                candidate = (index, *tail)
                if best is None or key(candidate, previous) < key(best, previous):
                    best = candidate
        tail = solve(end_position, minutes, peak, cooldowns, required_left, previous, slots_left)
        if tail is not None and (best is None or key(tail, previous) < key(best, previous)):
            best = tail
        return best

    try:
        selected = solve(0, target, peak_limit, (), required is not None, -1,
                         target//minimum if allow_fragmented_sessions else 1)
    except SearchLimit:
        return baseline
    if selected is None:
        return baseline
    result = tuple(variants[index] for index in selected)
    # Comfort cannot replace secured time by a not-yet-bookable alternative.
    if sum(i.potential_minutes for i in result if i.unlock_at > now) > \
            sum(i.potential_minutes for i in baseline if i.unlock_at > now):
        return baseline
    baseline_key = (*primary(baseline), *comfort_key((*existing, *[
        (i.start_minutes, i.end_minutes, i.room) for i in baseline]), planning), len(baseline))
    if key(selected)[:len(baseline_key)] >= baseline_key:
        return baseline
    return tuple(sorted(result, key=rank))
