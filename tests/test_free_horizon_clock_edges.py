"""Free-window planning, preparation and extension use the same real edge."""
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

import book_week as b
from booking_strategy import DailyPlanningPreferences
from booking_timing import as_utc, scheduled_work_fits
from types import SimpleNamespace
from room_catalog import SITE_TIMEZONE
from tempo_coordination import Snapshot
from tests import test_free_horizon_preparation as preparation_fixture


class FreeHorizonClockEdgesTests(unittest.TestCase):
    cases = ((date(2026, 3, 29), 4), (date(2026, 10, 25), 3))

    def edge(self, day, end):
        endpoint = datetime.combine(day, datetime.min.time(), tzinfo=SITE_TIMEZONE) + timedelta(hours=end)
        return (endpoint.astimezone(timezone.utc)-timedelta(hours=5)).astimezone(SITE_TIMEZONE).replace(tzinfo=None)

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.multiple(b, PRIORITY_ROOMS=['A'],
            MINIMUM_BLOCK_MINUTES=30, ALLOW_FRAGMENTED_SESSIONS=True))
        self.room_horizon = self.stack.enter_context(patch.object(b, 'room_horizon_minutes', return_value=10080))
        self.stack.enter_context(patch.object(b, 'tempo_coordination_snapshot',
            return_value=Snapshot(None, Path('unused-synthetic-coordination.json'))))
        self.tracker = b.BookingTracker()
        self.tracker.existing_reservation_hours = 6

    def opportunities(self, day, start, now):
        return b.build_day_booking_opportunities(
            [{'room':'A', 'slots':[{'startHour':start,'endHour':start+.5}]}], day,
            self.tracker, {'enabled':False}, DailyPlanningPreferences(), now=now,
            remaining_daily_hours=.5, free_horizon_only=True, include_free_horizon_intent=True)

    def test_forecast_and_ready_selection_use_the_elapsed_seed_edge(self):
        for day, start in self.cases:
            edge = self.edge(day, start+.5)
            with self.subTest(day=day):
                candidate, = self.opportunities(day, start, edge-timedelta(minutes=2))
                self.assertEqual(candidate.unlock_at, edge)
                plan = b.build_display_day_plan(day, [candidate], self.tracker,
                    DailyPlanningPreferences(), now=edge, target_minutes=30, free_horizon_only=True)
                self.assertEqual(plan.primary.state, 'ready')
                self.assertEqual(plan.primary.unlock_at.astimezone(timezone.utc),
                    edge.replace(tzinfo=SITE_TIMEZONE).astimezone(timezone.utc))

    def test_free_window_does_not_bypass_a_shorter_calendar_room_horizon(self):
        self.room_horizon.return_value = 300
        day, start = self.cases[0]
        candidate, = self.opportunities(day, start, self.edge(day, start+.5))
        self.assertEqual(candidate.unlock_at, datetime(2026, 3, 28, 23, 30))

    def test_extension_uses_each_actual_endpoint_edge_and_catches_up(self):
        for day, start in self.cases:
            edge = self.edge(day, start+.75)
            with self.subTest(day=day):
                waiting = b.plan_horizon_extension('A', str(day), start, start+.5, start+2,
                    now=edge-timedelta(seconds=1), horizon_limit_minutes=300)
                self.assertFalse(waiting.can_extend)
                self.assertEqual(waiting.next_unlock_at, edge)
                self.assertEqual(waiting.wait_seconds, 1)
                ready = b.plan_horizon_extension('A', str(day), start, start+.5, start+2,
                    now=edge, horizon_limit_minutes=300)
                self.assertTrue(ready.can_extend)
                self.assertEqual(ready.max_end_hour, start+.75)
                late = b.plan_horizon_extension('A', str(day), start, start+.5, start+2,
                    now=edge+timedelta(minutes=30), horizon_limit_minutes=300)
                self.assertEqual(late.max_end_hour, start+1.25)

    def test_prepared_create_reaches_exact_check_and_one_receipt_at_real_edge(self):
        for day, start in self.cases:
            fixture = preparation_fixture.FreeHorizonPreparationTests()
            try:
                fixture.setUp()
                edge = self.edge(day, start+.5)
                fixture.fixture.NOW = edge-timedelta(minutes=2)
                fixture.fixture.TARGET_DATE = day
                with self.subTest(day=day):
                    result, sequence, receipts, _, checks, waits = fixture.execute(boundary=edge, start=start)
                    self.assertTrue(result)
                    self.assertEqual(sequence, ['form_ready_before_boundary','exact_boundary_check'])
                    self.assertEqual((receipts, checks, waits), (1, 1, 1))
            finally:
                fixture.doCleanups()

    def test_extension_dispatch_prepares_the_correct_endpoint_and_deadline(self):
        for day, start in self.cases:
            edge = self.edge(day, start+.75)
            booking = dict(room='A', date=str(day), startTime=b.time_text(round(start*60)),
                endTime=b.time_text(round((start+.5)*60)), target_end=b.time_text(round((start+2)*60)),
                eventId=42, event_url='https://rwcmd.asimut.net/arrangement?eventId=42',
                created_at=self.edge(day,start+.5).isoformat())
            tracker = b.BookingTracker()
            tracker.existing_reservation_hours = 6
            with self.subTest(day=day), patch.object(b, 'edit_reservation_end_time', return_value=False) as edit:
                result = b.try_extend_booking(None, booking, tracker, now=edge-timedelta(seconds=1),
                    remaining_daily_hours=2, time_prefs={'enabled':False})
                self.assertTrue(edit.called, result)
                self.assertEqual(edit.call_args.args[2], b.time_text(round((start+.75)*60)))
                self.assertEqual(as_utc(edit.call_args.kwargs['save_not_before']), as_utc(edge))

    def test_fractional_maximum_duration_caps_extension_at_the_last_legal_quarter(self):
        day, start = self.cases[0]
        with patch.object(b, 'MAX_BOOKING_HOURS', 1.5):
            plan = b.plan_horizon_extension('A', str(day), start, start+.5, start+2,
                now=self.edge(day, start+2), horizon_limit_minutes=300)
        self.assertTrue(plan.can_extend)
        self.assertEqual(plan.max_end_hour, start+1.5)

    def test_target_clock_wait_crosses_skipped_and_repeated_hours(self):
        cases = (
            (datetime(2026,3,29,0,59,59), datetime(2026,3,29,2)),
            (datetime(2026,10,25,1,59,59), datetime(2026,10,25,1,fold=1)),
        )
        for now, edge in cases:
            with self.subTest(now=now), patch.object(b, 'datetime', wraps=datetime) as clock, \
                    patch.object(b.time, 'sleep') as sleep:
                clock.now.side_effect = [now, now, edge, edge]
                b.wait_until_target_time(edge.strftime('%H:%M'))
                sleep.assert_called_once_with(.5)

    def test_editor_wait_measures_real_seconds_across_both_clock_changes(self):
        cases = (
            (datetime(2026,3,29,0,59,59), datetime(2026,3,29,2)),
            (datetime(2026,10,25,1,59,59), datetime(2026,10,25,1,fold=1)),
        )
        for now, edge in cases:
            with self.subTest(now=now), patch.object(b, 'datetime', wraps=datetime) as clock, \
                    patch.object(b.time, 'sleep'), patch.object(b, 'check_prepared_booking'):
                clock.now.side_effect = [now, edge]
                self.assertEqual(b.wait_until_datetime(edge, settle_seconds=0), 1)

    def test_same_clock_in_the_second_fold_never_looks_already_open(self):
        now = datetime(2026,10,25,1,15)
        edge = now.replace(fold=1)
        with patch.object(b, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = now
            with self.assertRaisesRegex(b.BookingVerificationError, 'preparation limit'):
                b.wait_until_datetime(edge, settle_seconds=0)

    def test_repeated_hour_candidate_stays_waiting_until_its_actual_fold(self):
        day = date(2026,10,25)
        before = datetime(2026,10,25,1,31)
        candidate, = self.opportunities(day, 6, before)
        self.assertEqual(candidate.unlock_at.fold, 1)
        for fold, state in ((0, 'waiting'), (1, 'ready')):
            with self.subTest(fold=fold):
                plan = b.build_display_day_plan(day, [candidate], self.tracker,
                    DailyPlanningPreferences(), now=before.replace(fold=fold),
                    target_minutes=30, free_horizon_only=True)
                self.assertEqual(plan.primary.state, state)
                self.assertEqual(as_utc(plan.primary.unlock_at), as_utc(candidate.unlock_at))

    def test_scheduled_target_and_admission_follow_the_next_real_quarter(self):
        cases = (
            (datetime(2026,3,29,0,57), '02:00', datetime(2026,3,29,2)),
            (datetime(2026,10,25,1,57), '01:00', datetime(2026,10,25,1,fold=1)),
            (datetime(2026,10,25,1,27,fold=1), '01:30', datetime(2026,10,25,1,30,fold=1)),
        )
        for now, text, edge in cases:
            with self.subTest(now=now), patch.object(b, 'datetime', wraps=datetime) as clock:
                clock.now.return_value = now
                self.assertEqual(b._scheduled_target_time(now), text)
                self.assertEqual(as_utc(b._nearest_quarter_boundary(now)), as_utc(edge))
                self.assertEqual(as_utc(b.target_boundary_datetime(text)), as_utc(edge))
                self.assertTrue(scheduled_work_fits(SimpleNamespace(scheduled=True,target_time=text),
                    now=now, edge_work=True))

    def test_explicit_nonexistent_target_clock_is_rejected(self):
        with patch.object(b, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026,3,29,0,55)
            with self.assertRaisesRegex(ValueError, 'does not exist'):
                b.target_boundary_datetime('01:30')
