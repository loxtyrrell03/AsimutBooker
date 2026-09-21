"""Verified free-window peak policy, independent of the exhausted advance quota."""
import contextlib
import io
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import book_week as b
from app_settings import SettingsError, load_settings, save_settings
from booking_rules import apply_booking_rules, load_booking_rules, describe_booking_rules
from booking_quotas import peak_quota_exempt
from booking_plan import booking_plan_fingerprint
from booking_preferences_guard import booking_preference_run, booking_save_boundary, BookingPreferencesChanged
from booking_strategy import DailyPlanningPreferences
from phone_preferences import read_phone_preferences, save_phone_preferences
from room_catalog import SITE_TIMEZONE
from room_upgrades import select_upgrade_portfolio
from tests import test_new_booking_quotas as extension_fixture
from tests import test_room_upgrades as upgrade_fixture
from tests import test_booking_time_edits as time_fixture
from tests import test_progressive_planner as progressive_fixture
from tests import test_progressive_capacity as capacity_fixture
from tests import test_upgrade_portfolios as portfolio_fixture
from consolidation_staging import prepare_consolidation
import booking_run_report as report


class FreePeakTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch.multiple(b, FREE_HORIZON_OVERRIDES_PEAK=True,
            FREE_HORIZON_MINUTES=300, MAX_PEAK_HOURS=1, MAX_ROLLING_QUOTA_HOURS=6,
            PEAK_START=9, PEAK_END=16, MINIMUM_BLOCK_MINUTES=30,
            MIN_BOOKING_MINUTES=30, MAX_BOOKING_HOURS=2, PRIORITY_ROOMS=['Best', 'Fallback']))
        self.now = datetime(2026, 9, 21, 9, tzinfo=SITE_TIMEZONE)
        self.day = self.now.date()
        self.tracker = b.BookingTracker()
        self.tracker.existing_reservation_hours = 6
        self.tracker.peak_hours_by_day[str(self.day)] = 90
        self.tracker.live_quota_minutes = 0
        self.tracker.live_peak_minutes = {str(self.day): 0}

    def test_complete_window_boundary_and_opt_in(self):
        for start, end, valid in ((12, 14, True), (12, 14.25, False), (9, 10, False), (8, 10, False)):
            with self.subTest(start=start, end=end):
                self.assertEqual(peak_quota_exempt(self.day, start, end, now=self.now,
                    free_horizon_overrides_peak=True), valid)
                self.assertFalse(peak_quota_exempt(self.day, start, end, now=self.now))
        self.assertFalse(peak_quota_exempt(self.day + timedelta(days=1), 12, 13,
            now=self.now, free_horizon_overrides_peak=True))

    def test_exhausted_rolling_and_peak_can_book_two_peak_hours(self):
        self.assertTrue(self.tracker.can_book('Best', self.day, 12, 120, now=self.now)[0])
        self.assertEqual(self.tracker.get_remaining_peak_minutes(self.day), 0)
        self.assertEqual(self.tracker.get_remaining_quota_hours(), 0)
        with patch.object(b, 'FREE_HORIZON_OVERRIDES_PEAK', False):
            self.assertFalse(self.tracker.can_book('Best', self.day, 12, 120, now=self.now)[0])

    def test_advance_credit_cannot_make_outside_window_peak_legal(self):
        self.tracker.live_quota_minutes = 360
        self.tracker.existing_reservation_hours = 0
        self.assertFalse(self.tracker.can_book('Best', self.day, 13, 120, now=self.now)[0])
        self.assertFalse(self.tracker.can_book('Best', self.day + timedelta(days=1), 12, 120, now=self.now)[0])

    def test_classes_same_room_gap_and_max_duration_still_apply(self):
        self.tracker.add_existing_event(self.day, 13, 14, is_reservation=False)
        self.assertFalse(self.tracker.can_book('Best', self.day, 12, 120, now=self.now)[0])
        self.tracker.conflict_ranges.clear()
        self.tracker.reservation_ranges[str(self.day)] = [(11, 11.5, 'Best')]
        self.assertFalse(self.tracker.can_book('Best', self.day, 12, 120, now=self.now)[0])
        self.assertFalse(self.tracker.can_book('Fallback', self.day, 10, 150, now=self.now)[0])

    def test_free_planner_prefers_best_room_and_retains_full_intent(self):
        with patch.object(b, 'room_horizon_minutes', return_value=5 * 1440), patch.object(b, 'ALLOW_FRAGMENTED_SESSIONS', True):
            now = self.now.replace(hour=7, minute=30, tzinfo=None)
            opportunities = b.build_day_booking_opportunities([
                {'room': room, 'slots': [{'startHour': 12, 'endHour': 14}]}
                for room in ('Fallback', 'Best')], self.day, self.tracker,
                {'enabled': True, 'strict_mode': True, 'start_hour': 12, 'end_hour': 22},
                DailyPlanningPreferences(), now=now, remaining_daily_hours=6,
                free_horizon_only=True, include_free_horizon_intent=True)
            ready = [o for o in opportunities if o.unlock_at <= now]
            self.assertTrue(ready)
            self.assertTrue(any(o.potential_minutes == 120 for o in ready))
            for legacy in (False, True):
                args = [self.day, opportunities, self.tracker, DailyPlanningPreferences()]
                if legacy:
                    args.append({'enabled': False})
                builder = b.build_legacy_display_day_plan if legacy else b.build_display_day_plan
                plan = builder(*args, now=now, target_minutes=360, free_horizon_only=True)
                self.assertIsNotNone(plan.primary)
                self.assertEqual(plan.primary.room, 'Best')
            # Even with a future intent, a premature full booking cannot pass.
            self.assertFalse(self.tracker.can_book('Best', self.day, 12, 120, now=now)[0])
            self.assertTrue(self.tracker.can_book('Best', self.day, 12, 30, now=now)[0])

    def test_peak_extension_uses_complete_interval_and_can_exceed_one_hour(self):
        fixture = extension_fixture.PeakExtensionTests()
        result, edit, _, _ = fixture.run_extension(12, 12.5, 14, self.now, used_peak=90, full_quota=True)
        self.assertTrue(result[0], result)
        self.assertEqual(edit.call_args.args[2], '14:00')

    def test_peak_extension_uses_legal_prefix_even_with_unused_advance_credit(self):
        fixture = extension_fixture.PeakExtensionTests()
        result, edit, _, _ = fixture.run_extension(13, 13.5, 15, self.now, used_peak=90)
        self.assertTrue(result[0], result)
        self.assertEqual(edit.call_args.args[2], '14:00')
        result, edit, _, _ = fixture.run_extension(8, 9, 10, self.now, used_peak=90, full_quota=True)
        self.assertFalse(result[0])
        edit.assert_not_called()

    def test_peak_seed_keeps_future_extension_capacity_and_tracking(self):
        now = self.now.replace(hour=7, minute=30, tzinfo=None)
        self.tracker.add_existing_event(self.day, 12, 12.5, is_reservation=True, room='Best')
        booking = dict(room='Best', date=str(self.day), startTime='12:00', endTime='12:30',
                       target_end='14:00', created_at=now.isoformat(), eventId=42,
                       event_url='https://rwcmd.asimut.net/arrangement?eventId=42')
        with patch.object(b, 'booking_window_dates', return_value=(self.day,)), patch.object(b, 'room_horizon_minutes', return_value=10080):
            targets, _, held = b.calculate_extension_capacity_holds([booking], self.tracker,
                b.PracticePlan(enabled=True, default_hours=6), set(), time_prefs={'enabled':False}, now=now)
        self.assertEqual(targets[str(self.day)], 90)
        self.assertEqual(held[0]['target_end'], '14:00')
        with patch.object(b, 'edit_reservation_end_time', return_value=True), \
             patch.object(b, 'refresh_quota_balances'), \
             patch.object(b, 'update_extendable_booking_end_time') as update, \
             patch.object(b, 'remove_extendable_booking') as remove, \
             patch.object(b, 'room_horizon_minutes', return_value=10080):
            result = b.try_extend_booking(None, booking, self.tracker, now=now.replace(hour=8, minute=0),
                remaining_daily_hours=4, time_prefs={'enabled':False})
        self.assertTrue(result[0], result)
        self.assertEqual(result[1], '13:00')
        update.assert_called_once()
        remove.assert_not_called()

    def test_upgrade_and_portfolio_can_move_over_limit_booking_only_inside_window(self):
        fixture = upgrade_fixture.RoomUpgradePlannerTests()
        fixture.setUp()
        fixture.now = self.now.replace(hour=11)
        fixture.add_event('09:00', '10:00', reservation=True)
        candidates = fixture.plan(peak_limit=60, free_horizon_overrides_peak=True)
        self.assertTrue(candidates)
        selected = select_upgrade_portfolio(candidates, events=fixture.events, policy=fixture.policy,
            planning=fixture.planning, time_preferences=fixture.prefs, peak_limit=60,
            now=fixture.now, free_horizon_overrides_peak=True)
        self.assertTrue(selected)
        self.assertFalse(fixture.plan(peak_limit=60, free_horizon_overrides_peak=False))
        self.assertFalse(fixture.plan(peak_limit=60, free_horizon_overrides_peak=True, now=self.now))

    def test_shift_inside_free_window_preserves_conflict_and_horizon_checks(self):
        fixture = time_fixture.TimeEditValidationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.now = self.now.replace(hour=11)
        fixture.validate(peak_limit=60, free_horizon_overrides_peak=True)
        with self.assertRaisesRegex(ValueError, 'peak allowance'):
            fixture.validate(peak_limit=60, free_horizon_overrides_peak=True, now=self.now)
        fixture.events.append(dict(eventId=99, date=str(self.day), startTime='14:00',
                                   endTime='15:00', isReservation=False))
        with self.assertRaisesRegex(ValueError, 'clashes'):
            fixture.validate(peak_limit=60, free_horizon_overrides_peak=True)

    def test_progressive_upgrade_uses_exception_without_losing_fallback_minutes(self):
        fixture = progressive_fixture.ProgressivePlannerTests()
        fixture.setUp()
        fixture.now = self.now.replace(hour=10, second=1)
        fixture.policy.horizon_minutes_for = lambda room: 180 if room == 'Best' else 10080
        fixture.others.append(dict(eventId=99, date=str(self.day), room='Other fallback',
                                   startTime='09:00', endTime='10:00', isReservation=True))
        plan = fixture.plan(peak_limit=60, free_horizon_overrides_peak=True)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.replacement.duration + sum(r.duration for r in plan.remaining), 120)
        self.assertLess(plan.replacement.end, fixture.target.end)
        self.assertIsNone(fixture.plan(peak_limit=60, free_horizon_overrides_peak=False))

    def test_quarter_hour_matrix_never_spends_peak_credit_outside_free_window(self):
        # Independent expected rule over 432 requests with exhausted balances.
        for hour in (7, 9, 11, 13):
            now = self.now.replace(hour=hour)
            for minute in range(9*60, 18*60, 15):
                for length in (30, 60, 120):
                    expected = minute > hour*60 and minute + length <= (hour+5)*60
                    allowed, _ = self.tracker.can_book('Best', self.day, minute/60, length, now=now)
                    self.assertEqual(allowed, expected, (hour, minute, length))

    def test_free_capacity_is_preserved_when_an_upgrade_shifts_existing_time(self):
        fixture = capacity_fixture.ProgressiveCapacityTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.now = datetime(2026, 9, 19, 10, tzinfo=SITE_TIMEZONE)
        with patch.object(b.BookingTracker, 'get_remaining_quota_hours', return_value=0):
            self.assertFalse(fixture.capacity())
            fixture.gaps.append({'room':'Spare','slots':[{'startHour':12,'endHour':12.5}]})
            self.assertTrue(fixture.capacity())

    def test_free_consolidation_still_requires_personal_conflicts_to_be_staged(self):
        fixture = portfolio_fixture.UpgradePortfolioTests()
        fixture.setUp()
        fixture.now = self.now
        candidates = fixture.consolidate(peak_limit=60, free_horizon_overrides_peak=True)
        whole = next(c for c in candidates if len(c.originals) == 3)
        args = dict(events=fixture.events, available_data=fixture.gaps, policy=fixture.policy,
            now=fixture.now, time_preferences=fixture.prefs, planning=fixture.planning,
            peak_limit=60, free_horizon_overrides_peak=True)
        # The overlapping donors cannot simply be skipped because peak is waived.
        self.assertIsNone(prepare_consolidation(whole, **args))

    def test_report_explains_normal_peak_exhaustion_without_false_block(self):
        token = report.start(b, {}, b.PracticePlan(enabled=True, default_hours=6))
        self.addCleanup(report.finish, token)
        report.observe(self.tracker)
        text = '\n'.join(report.lines(now=self.now))
        self.assertIn('Extra peak time is allowed', text)
        self.assertIn('Remaining peak allowance: 0m', text)
        self.assertNotIn('No further peak time', text)

    def test_phone_save_preserves_preferences_and_preset_switch_keeps_exception(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'settings.json'
            save_settings({'keep':'untouched','practice_plan':{'enabled':True,'default_hours':6}}, path)
            before = read_phone_preferences(path)
            after = save_phone_preferences({'revision':before['revision'], 'changes':{
                'booking_rules':{'free_horizon_overrides_peak':True}}}, path)
            self.assertTrue(after['booking_rules']['free_horizon_overrides_peak'])
            settings = load_settings(path)
            self.assertEqual(settings['keep'], 'untouched')
            self.assertEqual(settings['practice_plan']['default_hours'], 6)
            for preset in ('legacy', 'new'):
                apply_booking_rules(settings, {'preset':preset})
                self.assertTrue(load_booking_rules(settings).free_horizon_overrides_peak)
            self.assertIn('waived only', describe_booking_rules(settings)['weekday_peak_quota'])
            for invalid in (1, 'true', None):
                with self.assertRaises(SettingsError):
                    apply_booking_rules(settings, {'free_horizon_overrides_peak':invalid})

    def test_exception_change_invalidates_prepared_save_and_display_plan(self):
        before = {'booking_rules':{'preset':'new'}}
        after = {'booking_rules':{'preset':'new','free_horizon_overrides_peak':True}}
        self.assertNotEqual(booking_plan_fingerprint(before), booking_plan_fingerprint(after))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'settings.json'
            save_settings(before, path)
            with booking_preference_run(path, before):
                save_settings(after, path)
                with self.assertRaises(BookingPreferencesChanged), booking_save_boundary():
                    self.fail('Stale Save must not run')


if __name__ == '__main__':
    unittest.main()
