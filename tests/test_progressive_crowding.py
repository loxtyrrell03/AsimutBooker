"""Deterministic crowded calendars; no browser, network, live files or clocks."""
import copy
import random
import unittest
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

from booking_strategy import DailyPlanningPreferences
from daily_planner import interval_overlap_minutes, soft_time_value
from progressive_planner import ordered_adjustments, plan_progressive_transfer
from room_upgrades import (Reservation, RoomConsolidation, RoomUpgrade,
                          clock_minutes, local_instant)


def overlap(a, b, c, d):
    return a < d and b > c


def slots_from_quarters(free):
    intervals = []
    for start in sorted(free):
        if intervals and intervals[-1][1] == start:
            intervals[-1] = (intervals[-1][0], start + 15)
        else:
            intervals.append((start, start + 15))
    return [{'startHour': start / 60, 'endHour': end / 60} for start, end in intervals]


class ProgressiveCrowdingTests(unittest.TestCase):
    def policy(self, day, now, rooms):
        horizons = {room: (5 if room == rooms[0] else 7) * 1440 for room in rooms}
        return SimpleNamespace(room_order=rooms, minimum_block_minutes=30,
            site_maximum_booking_minutes=120, booking_horizon=now + timedelta(days=7),
            site_clock_offset_bounds=(-1, 1), horizon_minutes_for=horizons.__getitem__,
            booking_dates=lambda today: tuple(today + timedelta(days=i) for i in range(8)))

    def assert_valid(self, plan, *, group, events, gaps, policy, now, prefs, planning,
                     peak_limit, blocked, same_room_gap=60):
        sources = {r.event_id: r for r in group}
        finals = (*plan.remaining, plan.replacement)
        self.assertEqual(sum(r.duration for r in group), sum(r.duration for r in finals))
        self.assertEqual(sum(r.duration for r in finals), plan.target.duration)
        self.assertTrue(all(r.duration >= policy.minimum_block_minutes for r in finals))
        self.assertEqual(len({r.event_id for r in plan.remaining}), len(plan.remaining))
        self.assertEqual(plan.replacement.day, plan.target.day)
        self.assertEqual(plan.replacement.room, plan.target.room)
        self.assertEqual(plan.replacement.start, plan.target.start)
        self.assertLessEqual(plan.replacement.end, plan.target.end)
        self.assertEqual(plan.seed_event_id, plan.seed.event_id if plan.seed else None)
        for final in finals:
            self.assertEqual(final.day, plan.target.day)
            self.assertEqual(final.start % 15, 0)
            self.assertEqual(final.end % 15, 0)
            self.assertLess(final.start, final.end)
            self.assertLess(final.end, 1440)
            self.assertTrue(720 <= final.start < final.end <= 960)
            self.assertFalse(any(overlap(final.start, final.end, a, b) for a, b in blocked))
        for final in plan.remaining:
            original = sources[final.event_id]
            self.assertEqual(final.room, original.room)
            self.assertLessEqual(final.duration, original.duration)
        self.assertTrue(all(policy.room_order.index(plan.target.room) <= policy.room_order.index(r.room)
                            for r in group))
        self.assertGreaterEqual(sum(soft_time_value(r.start, r.duration, (720, 960)) for r in finals),
                                sum(soft_time_value(r.start, r.duration, (720, 960)) for r in group))
        if planning.enabled and plan.target.day.weekday() < 5:
            self.assertGreaterEqual(sum(interval_overlap_minutes(r.start, r.end, 720, 960) for r in finals),
                                    sum(interval_overlap_minutes(r.start, r.end, 720, 960) for r in group))
        # Independently expand actual grid evidence into quarter cells. Only
        # the exact existing seed may supply already occupied target cells.
        room_free = {}
        for entry in gaps:
            cells = room_free.setdefault(entry['room'], set())
            for slot in entry['slots']:
                cells.update(range(round(slot['startHour'] * 60), round(slot['endHour'] * 60), 15))
        target_free = set(room_free.get(plan.target.room, ()))
        if plan.seed:
            target_free.update(range(plan.seed.start, plan.seed.end, 15))
        self.assertTrue(set(range(plan.replacement.start, plan.replacement.end, 15)).issubset(target_free))
        for final in plan.remaining:
            allowed = set(room_free.get(final.room, ()))
            for original in plan.originals:
                if original.room == final.room:
                    allowed.update(range(original.start, original.end, 15))
            self.assertTrue(set(range(final.start, final.end, 15)).issubset(allowed))
        expected_open = local_instant(plan.target.day, plan.replacement.end) - timedelta(
            minutes=policy.horizon_minutes_for(plan.target.room), seconds=policy.site_clock_offset_bounds[0])
        self.assertEqual(plan.opens_at, expected_open)
        self.assertEqual(plan.prepare_at, expected_open - timedelta(seconds=180))
        at = max(now, plan.opens_at)
        for final in finals:
            self.assertGreater(local_instant(final.day, final.start), at)
            self.assertLessEqual(local_instant(final.day, final.end), policy.booking_horizon)
            if final == plan.replacement or sources.get(final.event_id) != final:
                self.assertLessEqual(local_instant(final.day, final.end), at + timedelta(
                    minutes=policy.horizon_minutes_for(final.room), seconds=policy.site_clock_offset_bounds[0]))
        for index, final in enumerate(finals):
            for other in finals[index + 1:]:
                gap = same_room_gap if final.room == other.room else 0
                self.assertFalse(overlap(final.start, final.end, other.start - gap, other.end + gap))
            for event in events:
                if event['eventId'] in sources:
                    continue
                start, end = clock_minutes(event['startTime']), clock_minutes(event['endTime'])
                if event.get('blocksConflict', True):
                    self.assertFalse(overlap(final.start, final.end, start, end))
                if event.get('isReservation') and event['room'] == final.room:
                    self.assertFalse(overlap(final.start, final.end, start - same_room_gap, end + same_room_gap))
        others_peak = sum(interval_overlap_minutes(clock_minutes(e['startTime']), clock_minutes(e['endTime']), 540, 960)
            for e in events if e['eventId'] not in sources and e.get('isReservation'))
        if plan.target.day.weekday() < 5:
            self.assertLessEqual(others_peak + sum(interval_overlap_minutes(r.start, r.end, 540, 960)
                                                   for r in finals), peak_limit)
        # Independently replay source ordering against the still-held originals.
        sequence = ordered_adjustments(plan)
        self.assertIsNotNone(sequence)
        actual = {r.event_id: r for r in group}
        for before, after in sequence:
            self.assertEqual(actual[before.event_id], before)
            del actual[before.event_id]
            if after:
                for other in actual.values():
                    gap = same_room_gap if other.room == after.room else 0
                    self.assertFalse(overlap(after.start, after.end, other.start - gap, other.end + gap))
                actual[after.event_id] = after
        if plan.seed:
            del actual[plan.seed.event_id]
        self.assertEqual(actual, {r.event_id: r for r in plan.remaining})

    def test_180_crowded_calendars_with_31_rooms(self):
        rng = random.Random(20260915)
        rooms = tuple(f'Room {i:02}' for i in range(31))
        accepted = 0
        rejected = 0
        kinds_seen = set()
        for case in range(180):
            day = date(2026, 9, 21 if case % 5 else 20)
            kind = case % 7
            kinds_seen.add(kind)
            seed = None
            target = Reservation(42, day, rooms[0], 720, 840)
            if kind == 0:
                group = (Reservation(42, day, rooms[20], 720, 840),)
            elif kind == 1:
                group = (Reservation(42, day, rooms[20], 750, 870),)
            elif kind == 2:
                group = (Reservation(42, day, rooms[20], 720, 840),)
                target = replace(target, start=750, end=870)
            elif kind == 3:
                group = tuple(Reservation(42 + i, day, rooms[20 + i], a, b)
                              for i, (a, b) in enumerate(((720, 750), (750, 780), (780, 840))))
            else:
                seed_end = 810 if kind == 6 else 750
                seed = Reservation(99, day, rooms[0], 720, seed_end)
                tails = ((750, 780), (780, 840)) if kind == 5 else ((seed_end, 840),)
                group = (seed, *(Reservation(42 + i, day, rooms[20 + i], a, b)
                                 for i, (a, b) in enumerate(tails)))
                target = replace(target, event_id=99)
            open_end = rng.choice(tuple(range(target.start + 30, target.end + 1, 15)))
            now = local_instant(day, open_end) - timedelta(days=5) + timedelta(seconds=rng.choice((-119, 0, 1, 37)))
            policy = self.policy(day, now, rooms)
            target_horizon = rng.choice((3 * 1440, 5 * 1440, 5 * 1440 + 15))
            policy.horizon_minutes_for = lambda room, minutes=target_horizon: minutes if room == rooms[0] else 7 * 1440
            policy.site_clock_offset_bounds = rng.choice(((-1, 1), (-5, 2), (0.5, 2)))
            force_clear = case % 4 == 0
            if force_clear:
                now = local_instant(day, target.start + 30) - timedelta(days=5) + timedelta(seconds=1)
            grid = {}
            for room in rooms:
                free = {minute for minute in range(600, 1200, 15) if rng.random() < 0.44}
                for original in group:
                    if original.room == room:
                        free.difference_update(range(original.start, original.end, 15))
                grid[room] = free
            # Most calendars offer a useful initial prefix but teachers take
            # differing parts of the aspirational tail.
            target_end = target.end if force_clear else rng.choice(tuple(range(target.start + 15, target.end + 1, 15)))
            grid[rooms[0]] = set(range(target.start, target_end, 15))
            if seed:
                grid[rooms[0]].difference_update(range(seed.start, seed.end, 15))
            events = [{**r.as_booking(), 'isReservation': True} for r in group]
            blocked = ()
            peak_limit = 240 if case % 3 else 120
            if not force_clear:
                if case % 9 == 0:
                    grid = {room: set() for room in rooms}  # genuinely fully occupied
                if case % 11 == 0:
                    blocked = ((target.start + 15, target.start + 30),)
                if case % 13 == 0:
                    policy.site_clock_offset_bounds = None
                if case % 17 == 0:
                    policy.booking_horizon = local_instant(day, target.end - 15)
                if case % 19 == 0:
                    policy.room_order = (*rooms[1:], rooms[0])  # a saved ranking change can invalidate the target
                for i in range(case % 3):
                    start = rng.choice(tuple(range(660, 1050, 15)))
                    event = Reservation(200 + i, day, rooms[rng.randrange(31)], start, start + 30)
                    events.append({**event.as_booking(), 'isReservation': bool((case + i) % 2),
                                   'blocksConflict': bool((case + i) % 4)})
                    grid[event.room].difference_update(range(event.start, event.end, 15))
            gaps = [{'room': room, 'slots': slots_from_quarters(grid[room])} for room in rooms]
            self.assertEqual(len(gaps), 31)
            prefs = {'enabled': True, 'strict_mode': True, 'start_hour': 12, 'end_hour': 16}
            planning = DailyPlanningPreferences()
            change = RoomUpgrade(group[0], target) if len(group) == 1 else RoomConsolidation(group, target)
            snapshots = copy.deepcopy((events, gaps))
            with self.subTest(case=case, kind=kind, open_end=open_end):
                plan = plan_progressive_transfer(change, events=events, available_data=gaps,
                    policy=policy, now=now, time_preferences=prefs, planning=planning, seed=seed,
                    peak_limit=peak_limit, blocked_intervals=blocked)
                self.assertEqual((events, gaps), snapshots)
                if plan is None:
                    rejected += 1
                    continue
                accepted += 1
                self.assert_valid(plan, group=group, events=events, gaps=gaps, policy=policy,
                    now=now, prefs=prefs, planning=planning, peak_limit=peak_limit, blocked=blocked)
        self.assertEqual(kinds_seen, set(range(7)))
        self.assertGreaterEqual(accepted, 30)
        self.assertGreaterEqual(rejected, 30)

    def test_98_small_cases_match_independent_prefix_enumeration(self):
        day = date(2026, 9, 21)
        rooms = ('Best', 'Fallback')
        for has_seed in (False, True):
            for free_end in range(750, 841, 15):
                for open_end in range(750, 841, 15):
                    seed = Reservation(99, day, 'Best', 720, 750) if has_seed else None
                    fallback = Reservation(42, day, 'Fallback', 750 if has_seed else 720, 840)
                    group = (seed, fallback) if has_seed else (fallback,)
                    target = Reservation(99 if has_seed else 42, day, 'Best', 720, 840)
                    now = local_instant(day, open_end) - timedelta(days=5) + timedelta(seconds=1)
                    policy = self.policy(day, now, rooms)
                    free_start = 750 if has_seed else 720
                    gaps = [{'room': 'Best', 'slots': ([{'startHour': free_start / 60, 'endHour': free_end / 60}]
                                                      if free_end > free_start else [])}]
                    events = [{**r.as_booking(), 'isReservation': True} for r in group]
                    change = RoomConsolidation(group, target) if has_seed else RoomUpgrade(fallback, target)
                    possible = [end for end in range(765 if has_seed else 750, 841, 15)
                                if end <= free_end and (840 - end == 0 or 840 - end >= 30)
                                and (has_seed or end < 840)]
                    ready = [end for end in possible if end <= open_end]
                    expected = max(ready) if ready else min(possible, default=None)
                    if not has_seed and free_end == 840 and open_end == 840:
                        expected = None  # ordinary atomic upgrade owns the full available session
                    with self.subTest(seed=has_seed, free_end=free_end, open_end=open_end):
                        plan = plan_progressive_transfer(change, events=events, available_data=gaps,
                            policy=policy, now=now, time_preferences={'enabled': True, 'strict_mode': True,
                                'start_hour': 12, 'end_hour': 16}, planning=DailyPlanningPreferences(), seed=seed)
                        self.assertEqual(plan.replacement.end if plan else None, expected)


if __name__ == '__main__':
    unittest.main()
