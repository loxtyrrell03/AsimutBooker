import contextlib
import io
import unittest
from datetime import date, datetime
from unittest import mock

import book_week as b
from booking_plan import _day_from_dict, _day_to_dict
from booking_strategy import DailyPlanningPreferences


class ScheduledRuntimeQueueTests(unittest.TestCase):
    def run_scheduled(self, lock, clock, loader):
        with (
            mock.patch.object(b, "SingleInstanceLock", return_value=lock),
            mock.patch.object(b, "_scheduled_target_time", return_value=None),
            mock.patch.object(b, "_load_and_validate_runtime_settings", side_effect=loader),
            mock.patch.object(b.time, "monotonic", side_effect=clock),
            mock.patch.object(b.time, "sleep"),
            mock.patch.object(b, "booking_preference_run", return_value=contextlib.nullcontext()),
            mock.patch.object(b, "run_booking", return_value=0) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = b.main(["--scheduled"])
        lock.release.assert_called_once()
        return result, run

    def test_scheduled_run_queues_and_reloads_preferences_before_start(self):
        lock = mock.Mock()
        lock.acquire.side_effect = [False, False, True]
        before = ({"version": "old"}, b.PracticePlan(), mock.Mock())
        after = ({"version": "new"}, b.PracticePlan(), mock.Mock())
        result, run = self.run_scheduled(lock, [0, 0, 1], [before, after])
        self.assertEqual(result, 0)
        self.assertEqual(run.call_args.args[1:], after)

    def test_scheduled_queue_timeout_is_visible_failure_without_overlap(self):
        lock = mock.Mock()
        lock.acquire.return_value = False
        result, run = self.run_scheduled(
            lock, [0, 181], [({}, b.PracticePlan(), mock.Mock())]
        )
        self.assertEqual(result, 6)
        run.assert_not_called()

    def test_invalid_preferences_after_queue_stop_safely(self):
        lock = mock.Mock()
        lock.acquire.side_effect = [False, True]
        result, run = self.run_scheduled(
            lock, [0, 0], [({}, b.PracticePlan(), mock.Mock()), b.SettingsError("invalid")]
        )
        self.assertEqual(result, 3)
        run.assert_not_called()


class ExtensionCapacityPlanTests(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 9, 15)
        self.now = datetime(2026, 9, 8, 14, 30)
        self.tracker = mock.Mock()
        self.tracker.get_hours_for_day.return_value = 1
        self.tracker.get_peak_used_for_day.return_value = 60
        self.tracker.get_remaining_peak_minutes.return_value = 60
        self.tracker.get_remaining_quota_hours.return_value = 24
        self.preferences = DailyPlanningPreferences()
        self.context = {
            "extension_target_by_date": {self.day.isoformat(): 60},
            "extension_peak_by_date": {self.day.isoformat(): 60},
            "extension_bookings": [{"date": self.day.isoformat(), "room": "Gallery",
                "startTime": "12:45", "endTime": "13:45", "target_end": "14:45"}],
        }

    def opportunity(self, start, end):
        return b.BookingOpportunity(
            room="Other", target_date=self.day, start_minutes=start, end_minutes=end,
            unlock_at=self.now, room_priority=1, initial_minutes=30,
            potential_minutes=end-start, preferred_minutes=end-start,
            soft_preferred_minutes=end-start, peak_minutes=max(0, min(end, 960)-start),
            peak_window_start_minutes=540, peak_window_end_minutes=960,
            source_gap_start_minutes=start, source_gap_end_minutes=end,
        )

    def test_horizon_plan_reserves_extension_before_selecting_new_minutes(self):
        with mock.patch.multiple(b, PRIORITY_ROOMS=["Gallery", "Other"],
                                 ROOM_HORIZON_MINUTES={"Gallery": 10080},
                                 ALLOW_FRAGMENTED_SESSIONS=True):
            days = b.build_horizon_display_days(
                {self.day.isoformat(): [self.opportunity(960, 1080)]},
                self.context, self.tracker, b.PracticePlan(enabled=True, default_hours=3),
                self.preferences, now=self.now,
            )
        day = days[0]
        self.assertEqual(day.primary.confirmed_minutes, 60)
        self.assertEqual(day.primary.potential_minutes, 120)
        self.assertEqual(sum(x.potential_minutes for x in day.additional), 60)
        self.assertEqual(_day_from_dict(_day_to_dict(day, "day"), "day"), day)

    def test_modern_and_legacy_selection_cannot_spend_reserved_peak_capacity(self):
        for legacy in (False, True):
            args = [self.day, [self.opportunity(900, 960)], self.tracker, self.preferences]
            if legacy:
                args.append({"enabled": False, "strict_mode": False})
            builder = b.build_legacy_display_day_plan if legacy else b.build_display_day_plan
            day = builder(*args, now=self.now, target_minutes=180,
                          reserved_daily_minutes=60, reserved_peak_minutes=60)
            self.assertIsNone(day.primary)
