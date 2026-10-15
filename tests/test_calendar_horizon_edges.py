"""Observed calendar horizons must agree across every upgrade decision."""
import tempfile
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from booking_strategy import DailyPlanningPreferences
from booking_time_edits import requested_time_edit, validate_time_edit
from room_catalog import SITE_TIMEZONE
from room_upgrades import (Reservation, RoomUpgrade, UpgradeOpportunity,
                           find_room_upgrades, find_upgrade_opportunities, room_horizon_opening)
from progressive_discovery import find_partial_upgrade_opportunities
import progressive_state
from tests import test_progressive_planner as progressive_fixture
from tests import test_progressive_runtime as runtime_fixture


class CalendarHorizonTests(unittest.TestCase):
    days = (date(2026, 3, 29), date(2026, 10, 25))

    def opening(self, day, end_minutes, days=3):
        wall = datetime.combine(day - timedelta(days=days), time(), tzinfo=SITE_TIMEZONE)
        # The site's earliest clock may be one second behind the local clock.
        return (wall + timedelta(minutes=end_minutes)).astimezone(timezone.utc) + timedelta(seconds=1)

    def fixture(self, day):
        original = Reservation(42, day, 'Fallback', 720, 840)
        edge = self.opening(day, 840)
        policy = SimpleNamespace(room_order=('Best', 'Fallback'), minimum_block_minutes=30,
            site_minimum_booking_minutes=30, site_maximum_booking_minutes=120,
            site_minimum_booking_gap_minutes=60, all_room_location_ids={'Best':1, 'Fallback':2},
            booking_horizon=edge + timedelta(days=7), site_clock_offset_bounds=(-1, 1),
            horizon_minutes_for=lambda room: 3 * 1440, booking_dates=lambda today: (day,))
        args = dict(events=[{**original.as_booking(), 'isReservation': True}], policy=policy,
            now=edge, available_data=[{'room':'Best','slots':[{'startHour':12,'endHour':14}]}],
            planning=DailyPlanningPreferences(), time_preferences={'enabled':False})
        return original, args

    def test_whole_upgrade_waits_until_exact_local_policy_edge(self):
        for day in self.days:
            original, args = self.fixture(day)
            with self.subTest(day=day):
                self.assertEqual(find_room_upgrades(original, **{**args, 'now':args['now']-timedelta(seconds=1)}), ())
                self.assertEqual(len(find_room_upgrades(original, **args)), 1)

    def test_upgrade_forecast_reports_the_same_edge(self):
        for day in self.days:
            _, args = self.fixture(day)
            expected = args['now']
            args['now'] -= timedelta(hours=2)
            with self.subTest(day=day):
                candidates = find_upgrade_opportunities(**args)
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0].opens_at, expected)

    def test_requested_shift_uses_the_same_room_policy_edge(self):
        for day in self.days:
            _, args = self.fixture(day)
            original = Reservation(42, day, 'Fallback', 705, 825)
            edit = requested_time_edit(original, mode='shift_later', minutes=15)
            values = dict(events=[{**original.as_booking(), 'isReservation':True}],
                policy=args['policy'], gaps=[{'room':'Fallback','slots':[{'startHour':12,'endHour':14}]}])
            with self.subTest(day=day):
                with self.assertRaisesRegex(ValueError, 'horizon'):
                    validate_time_edit(edit, now=args['now']-timedelta(seconds=1), **values)
                validate_time_edit(edit, now=args['now'], **values)

    def test_progressive_seed_and_saved_next_edge_agree(self):
        for day in self.days:
            fixture = progressive_fixture.ProgressivePlannerTests()
            fixture.setUp()
            fixture.day = day
            fixture.originals = (Reservation(42, day, 'Fallback', 720, 840),)
            fixture.target = Reservation(42, day, 'Best', 720, 840)
            fixture.now = self.opening(day, 750, days=5)
            fixture.policy.booking_horizon = fixture.now + timedelta(days=7)
            with self.subTest(day=day):
                step = fixture.plan()
                self.assertIsNotNone(step)
                self.assertEqual(step.replacement.end, 750)
                self.assertEqual(step.opens_at, fixture.now)
                fixture.gaps[0]['slots'] = [{'startHour':12, 'endHour':12.5}]
                partial, = find_partial_upgrade_opportunities(**fixture.args())
                self.assertEqual(partial.first_step.opens_at, fixture.now)
                self.assertEqual(partial.first_step.replacement.end, 750)
                prospect = UpgradeOpportunity(RoomUpgrade(fixture.originals[0], fixture.target),
                    self.opening(day, 840, days=5))
                with tempfile.TemporaryDirectory() as directory, patch.object(progressive_state,
                        'STATE_FILE', Path(directory)/'state.json'):
                    progressive_state.remember_opportunities([prospect], settings={},
                        policy=fixture.policy, now=fixture.now-timedelta(minutes=5), checked_dates=[str(day)])
                    saved, = progressive_state.load_state()['plans']
                    self.assertEqual(datetime.fromisoformat(saved['next_at']), fixture.now)

    def test_runtime_dispatch_and_verified_continuation_keep_the_calendar_edge(self):
        for day in self.days:
            now = self.opening(day, 750, days=5) - timedelta(seconds=1)
            with self.subTest(day=day), patch.multiple(runtime_fixture, DAY=day, NOW=now):
                fixture = runtime_fixture.ProgressiveProcessPlannerTests()
                try:
                    fixture.setUp()
                    actions, _, details = fixture.run_process()
                    self.assertEqual(actions, 2)
                    self.assertEqual(fixture.total(), 120)
                    self.assertEqual(len(details), 1)
                    self.assertEqual(fixture.ctx.actual[1000].end, 750)
                    saved, = progressive_state.load_state()['plans']
                    self.assertEqual(datetime.fromisoformat(saved['next_at']), now+timedelta(minutes=15))
                finally:
                    fixture.doCleanups()

    def test_fractional_calendar_horizon_and_ambiguous_opening(self):
        _, args = self.fixture(date(2026, 10, 27))
        policy = args['policy']
        policy.horizon_minutes_for = lambda room: 4 * 1440 + 720
        expected = datetime(2026, 10, 22, 23, 30, 1, tzinfo=timezone.utc)
        self.assertEqual(room_horizon_opening(date(2026, 10, 27), 750,
                                             policy=policy, room='Best'), expected)
        # An opening in the repeated hour is ambiguous even if the booking
        # endpoint itself is unique. Do not invent an offset or a Save time.
        policy.horizon_minutes_for = lambda room: 4 * 1440 + 660
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            room_horizon_opening(date(2026, 10, 29), 750, policy=policy, room='Best')
