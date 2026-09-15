import contextlib
import copy
import io
import unittest
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

import book_week as engine
from practice_plan import PracticePlan
from progressive_capacity import availability_after_transfer, preserves_transfer_capacity
from progressive_planner import TransferPlan, plan_progressive_transfer
from booking_strategy import DailyPlanningPreferences
from room_catalog import SITE_TIMEZONE
from room_upgrade_runtime import preserves_day_transition
from room_upgrades import Reservation, RoomUpgrade, local_instant


class ProgressiveCapacityTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.day = date(2026, 9, 19)
        self.old = Reservation(42, self.day, 'Fallback', 720, 840)
        self.target = Reservation(42, self.day, 'Best', 750, 870)
        self.now = local_instant(self.day, 780) - timedelta(days=5) + timedelta(seconds=1)
        self.transfer = TransferPlan((self.old,), self.target, replace(self.target, end=780),
            (replace(self.old, start=780, end=870),), self.now)
        self.events = [{**self.old.as_booking(), 'isReservation': True, 'title': 'Reservation'}]
        self.gaps = [{'room': 'Best', 'slots': [{'startHour': 12.5, 'endHour': 14.5}]},
                     {'room': 'Fallback', 'slots': [{'startHour': 14, 'endHour': 14.5}]}]
        self.settings = {'time_preferences': {'enabled': True, 'preset': 'custom',
            'strict_mode': True, 'custom_start_hour': 12, 'custom_end_hour': 16}}
        self.practice = PracticePlan(enabled=True, default_hours=2.5)
        horizons = {'Best': 5 * 1440, 'Spare': 7 * 1440, 'Fallback': 7 * 1440}
        self.policy = SimpleNamespace(observed_at=self.now,
            booking_horizon=self.now + timedelta(days=7), room_order=tuple(horizons),
            room_horizon_minutes=horizons, minimum_block_minutes=30,
            site_maximum_booking_minutes=120, allow_fragmented_sessions=True,
            site_clock_offset_bounds=(-1, 1), horizon_minutes_for=horizons.__getitem__,
            booking_dates=lambda today: tuple(today + timedelta(days=i) for i in range(8)))
        self.stack.enter_context(mock.patch.multiple(engine, ACTIVE_ROOM_POLICY=self.policy,
            PRIORITY_ROOMS=self.policy.room_order, ROOM_HORIZON_MINUTES=horizons,
            MINIMUM_BLOCK_MINUTES=30, MIN_BOOKING_MINUTES=30, MAX_BOOKING_HOURS=2,
            ALLOW_FRAGMENTED_SESSIONS=True, SAME_ROOM_GAP_MINUTES=60,
            SITE_CLOCK_OFFSET_BOUNDS=(-1, 1)))
        self.stack.enter_context(mock.patch.object(engine, 'booking_window_dates', side_effect=self.policy.booking_dates))

    def capacity(self, **overrides):
        args = dict(events=self.events, gaps=self.gaps, settings=self.settings,
                    practice_plan=self.practice, policy=self.policy, now=self.now, extensions=[])
        args.update(overrides)
        return preserves_transfer_capacity(engine, self.transfer, **args)

    def test_mixed_state_cannot_consume_the_only_missing_target_slot(self):
        # Before: Best14:00-14:30 can supply the missing half-hour. After:
        # shifted fallback13:00-14:30 occupies that time. Released12:00-12:30
        # cannot be rebooked in Fallback because its remaining session starts13.
        self.assertFalse(self.capacity())

    def test_another_room_in_released_time_keeps_the_daily_target_possible(self):
        self.gaps.append({'room': 'Spare', 'slots': [{'startHour': 12, 'endHour': 12.5}]})
        self.assertTrue(self.capacity())

    def test_already_met_target_permits_the_same_partial_shift(self):
        self.practice = PracticePlan(enabled=True, default_hours=2)
        self.assertTrue(self.capacity())

    def test_transition_releases_sources_and_subtracts_every_partial_destination(self):
        seed = Reservation(99, self.day, 'Best', 720, 750)
        fallback = replace(self.old, start=750)
        after = (replace(seed, end=765), replace(fallback, start=765))
        gaps = [{'room': 'Best', 'slots': [{'startHour': 12.5, 'endHour': 14}]},
                {'room': 'Spare', 'slots': [{'startHour': 15, 'endHour': 16}]}]
        before = copy.deepcopy(gaps)
        result = availability_after_transfer(gaps, (seed, fallback), after)
        by_room = {r['room']: r['slots'] for r in result}
        self.assertEqual(by_room['Best'], [{'startHour': 12.75, 'endHour': 14}])
        self.assertEqual(by_room['Fallback'], [{'startHour': 12.5, 'endHour': 12.75}])
        self.assertEqual(by_room['Spare'], [{'startHour': 15, 'endHour': 16}])
        self.assertEqual(gaps, before)

    def test_new_prefix_identity_does_not_overwrite_retained_source_or_extension(self):
        extensions = [{'eventId': 1000}]
        with mock.patch('room_upgrade_runtime.preserves_day_transition', return_value=True) as compare:
            self.assertTrue(self.capacity(extensions=extensions))
        after = compare.call_args.kwargs['after_events']
        self.assertEqual({e['eventId'] for e in after}, {42, 1001})
        source = next(e for e in after if e['eventId'] == 42)
        self.assertEqual((source['room'], source['startTime'], source['endTime']), ('Fallback', '13:00', '14:30'))
        self.assertEqual(self.events[0]['startTime'], '12:00')

    def test_existing_seed_retains_its_actual_identity_in_accounting(self):
        seed = Reservation(99, self.day, 'Best', 720, 750)
        old = replace(self.old, start=750)
        target = replace(self.target, event_id=99, start=720, end=840)
        self.transfer = TransferPlan((old,), target, replace(seed, end=765),
            (replace(old, start=765),), self.now, seed_event_id=99, seed=seed)
        self.events = [{**r.as_booking(), 'isReservation': True} for r in (old, seed)]
        with mock.patch('room_upgrade_runtime.preserves_day_transition', return_value=True) as compare:
            self.assertTrue(self.capacity())
        after = compare.call_args.kwargs['after_events']
        self.assertEqual({e['eventId'] for e in after}, {42, 99})
        self.assertEqual(next(e for e in after if e['eventId'] == 99)['endTime'], '12:45')

    def test_preparation_checks_capacity_at_the_future_opening_in_london_time(self):
        with mock.patch('room_upgrade_runtime.preserves_day_transition', return_value=True) as compare:
            self.assertTrue(self.capacity(now=self.now - timedelta(seconds=120)))
        checked_at = compare.call_args.kwargs['now']
        self.assertEqual(checked_at, self.transfer.opens_at)
        self.assertEqual(checked_at.tzinfo, SITE_TIMEZONE)

    def test_fresh_new_conflict_cannot_be_hidden_by_a_cached_capacity_result(self):
        self.gaps.append({'room': 'Spare', 'slots': [{'startHour': 12, 'endHour': 12.5}]})
        self.assertTrue(self.capacity())
        self.events.append({'eventId': 200, 'date': str(self.day), 'room': 'Lesson',
            'startTime': '12:00', 'endTime': '12:30', 'isReservation': False})
        self.assertFalse(self.capacity())

    def test_stale_or_duplicate_exact_sources_fail_before_accounting(self):
        for events in ([], self.events * 2, [{**self.events[0], 'endTime': '13:00'}]):
            with self.subTest(events=events), mock.patch('room_upgrade_runtime.preserves_day_transition') as compare:
                self.assertFalse(self.capacity(events=events))
                compare.assert_not_called()

    def test_generic_transition_cannot_hide_lost_minutes_when_target_is_met(self):
        after = [{**self.events[0], 'endTime': '13:00'}]
        self.assertFalse(preserves_day_transition(engine, day=self.day,
            before_events=self.events, after_events=after, before_gaps=self.gaps, after_gaps=self.gaps,
            settings=self.settings, practice_plan=PracticePlan(enabled=True, default_hours=1),
            policy=self.policy, now=self.now, extensions=[]))

    def test_planner_tries_another_remainder_to_preserve_the_only_missing_target_slot(self):
        old = replace(self.old, start=750, end=870)
        target = replace(self.target, start=720, end=840)
        events = [{**old.as_booking(), 'isReservation': True}]
        gaps = [{'room': 'Best', 'slots': [{'startHour': 12, 'endHour': 14}]},
                {'room': 'Spare', 'slots': [{'startHour': 12.5, 'endHour': 13}]}]
        now = local_instant(self.day, 750) - timedelta(days=5) + timedelta(seconds=1)
        args = dict(events=events, available_data=gaps, policy=self.policy, now=now,
            time_preferences={'enabled': True, 'strict_mode': True, 'start_hour': 12, 'end_hour': 16},
            planning=DailyPlanningPreferences())
        opportunity = RoomUpgrade(old, target)
        ordinary = plan_progressive_transfer(opportunity, **args)
        self.assertEqual(ordinary.remaining, (replace(old, end=840),))
        def capacity(candidate):
            return preserves_transfer_capacity(engine, candidate, events=events, gaps=gaps,
                settings=self.settings, practice_plan=self.practice, policy=self.policy, now=now, extensions=[])
        self.assertFalse(capacity(ordinary))
        alternative = plan_progressive_transfer(opportunity, acceptable_layout=capacity, **args)
        self.assertIsNotNone(alternative)
        self.assertEqual(alternative.replacement.end, 750)
        self.assertEqual(alternative.remaining, (replace(old, start=780),))
        self.assertTrue(capacity(alternative))
        # Moving the fallback start to13:00 leaves12:30-13:00 available in
        # Spare. The earlier contiguous layout consumed that only extra slot.


if __name__ == '__main__':
    unittest.main()
