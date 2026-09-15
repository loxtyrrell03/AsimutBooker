import contextlib
import copy
import io
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import book_week as b
import room_upgrade_runtime as runtime
from practice_plan import PracticePlan
from room_upgrades import Reservation, RoomUpgrade


class UpgradeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.now = datetime(2026, 9, 17, 16, tzinfo=timezone.utc)
        self.day = date(2026, 9, 19)
        self.original = Reservation(42, self.day, "Fallback", 720, 840)
        self.replacement = Reservation(42, self.day, "Best", 840, 960)
        self.upgrade = RoomUpgrade(self.original, self.replacement)
        self.events = [{**self.original.as_booking(), "isReservation": True, "title": "Reservation"}]
        self.gaps = [{"room": "Best", "slots": [{"startHour": 14, "endHour": 16}]}]
        self.settings = {"time_preferences": {"enabled": True, "preset": "afternoon", "strict_mode": False},
                         "booking_strategy": {"daily_planning": {"upgrade_freeze_hours": 24}}}
        self.plan = PracticePlan(enabled=True, default_hours=4)
        self.policy = SimpleNamespace(
            observed_at=self.now, booking_horizon=self.now + timedelta(days=7),
            room_order=("Best", "Spare", "Fallback"), room_horizon_minutes={"Best": 4320, "Spare": 10080, "Fallback": 10080},
            minimum_block_minutes=30, site_maximum_booking_minutes=120,
            allow_fragmented_sessions=True, site_clock_offset_bounds=(-1, 1),
            horizon_minutes_for=lambda room: 4320,
            booking_dates=lambda today: tuple(date(2026, 9, 17) + timedelta(days=i) for i in range(8)))
        self.stack.enter_context(mock.patch.multiple(b, ACTIVE_ROOM_POLICY=self.policy,
            PRIORITY_ROOMS=self.policy.room_order, ROOM_HORIZON_MINUTES=self.policy.room_horizon_minutes,
            MINIMUM_BLOCK_MINUTES=30, MIN_BOOKING_MINUTES=30, MAX_BOOKING_HOURS=2,
            ALLOW_FRAGMENTED_SESSIONS=True))
        self.stack.enter_context(mock.patch.object(b, "booking_window_dates", side_effect=self.policy.booking_dates))

    def coverage(self):
        return runtime.preserves_remaining_day_plan(b, self.upgrade, events=self.events, gaps=self.gaps,
            settings=self.settings, practice_plan=self.plan, policy=self.policy, now=self.now, extensions=[])

    def test_upgrade_cannot_consume_only_slot_needed_for_daily_target(self):
        self.assertFalse(self.coverage())

    def test_moving_keeps_target_possible_when_another_room_covers_freed_time(self):
        self.gaps.append({"room": "Spare", "slots": [{"startHour": 12, "endHour": 14}]})
        self.assertTrue(self.coverage())

    def test_existing_target_met_allows_same_duration_upgrade(self):
        self.plan = PracticePlan(enabled=True, default_hours=2)
        self.assertTrue(self.coverage())

    def test_single_session_preference_does_not_invent_an_extra_session(self):
        with mock.patch.object(b, "ALLOW_FRAGMENTED_SESSIONS", False):
            self.policy.allow_fragmented_sessions = False
            self.assertTrue(self.coverage())

    def test_tracker_rebuild_preserves_other_conflicts_and_full_quota(self):
        other = {**self.events[0], "eventId": 43, "startTime": "13:00", "endTime": "15:00"}
        tracker = runtime.tracker_for_events(b, [self.events[0], other], ())
        self.assertEqual(tracker.get_hours_for_day(self.day), 4)
        self.assertEqual(len(tracker.conflict_ranges[self.day.isoformat()]), 2)

    def prepare_runner(self):
        self.plan = PracticePlan(enabled=True, default_hours=2)
        clock = self.stack.enter_context(mock.patch.object(runtime, "datetime", wraps=datetime))
        clock.now.return_value = self.now
        self.args = SimpleNamespace(only_date=None, only_room=None, max_actions=1, max_action_minutes=None,
                                    upgrades_only=True, upgrade_dry_run=False, upgrade_event_id=None)
        self.page = mock.MagicMock()
        self.details = []
        self.stack.enter_context(mock.patch.object(b, "refresh_live_room_policy", return_value=self.policy))
        self.stack.enter_context(mock.patch.object(b, "load_extendable_bookings", return_value=[]))
        self.stack.enter_context(mock.patch.object(b, "open_practice_room_overview"))
        self.navigate = self.stack.enter_context(mock.patch.object(b, "navigate_to_day"))
        self.stack.enter_context(mock.patch.object(b, "wait_for_practice_room_grid"))
        self.stack.enter_context(mock.patch.object(b, "get_available_slots", side_effect=lambda page: copy.deepcopy(self.gaps)))
        def scan(page, tracker, *args, **kwargs):
            rebuilt = runtime.tracker_for_events(b, self.events, ())
            tracker.__dict__.update(rebuilt.__dict__)
            return len(self.events), self.events
        self.scan = self.stack.enter_context(mock.patch.object(b, "scan_agenda", side_effect=scan))
        self.edits = []
        def edit(page, choice, *, revalidate, dry_run, **kwargs):
            self.assertTrue(revalidate())
            self.edits.append(choice)
            if dry_run:
                return False
            self.events = [{**event, **choice.replacement.as_booking()} if event["eventId"] == choice.original.event_id else event
                           for event in self.events]
            return True
        self.edit = self.stack.enter_context(mock.patch.object(b, "edit_reservation_room_time", side_effect=edit))
        return runtime.tracker_for_events(b, self.events, ())

    def run_runner(self, tracker, total=0):
        return runtime.process_room_upgrades(b, self.page, self.settings, self.plan, self.args, tracker, total, self.details)

    def test_runner_refreshes_before_and_after_save_and_counts_only_verified_edits(self):
        tracker = self.prepare_runner()
        count, updated = self.run_runner(tracker)
        self.assertEqual(count, 1)
        self.assertEqual(updated.get_hours_for_day(self.day), 2)
        self.assertEqual(updated.agenda_events[0]["room"], "Best")
        self.assertEqual(len(self.edits), 1)
        self.assertEqual(self.scan.call_count, 3)  # initial, final revalidation, after Save
        self.navigate.assert_called()
        self.assertIn("UPGRADED:", self.details[0])

    def test_dry_run_never_counts_a_change_or_repeats_one_reservation(self):
        tracker = self.prepare_runner()
        self.args.upgrade_dry_run = True
        count, _ = self.run_runner(tracker)
        self.assertEqual(count, 0)
        self.assertEqual(len(self.edits), 1)
        self.assertFalse(self.details)

    def test_failed_scan_after_verified_edit_preserves_success_and_stops(self):
        tracker = self.prepare_runner()
        self.args.max_actions = 2
        normal_scan = self.scan.side_effect
        def scan(*args, **kwargs):
            if self.scan.call_count >= 3:
                raise RuntimeError('Connection lost after independent Save proof')
            return normal_scan(*args, **kwargs)
        self.scan.side_effect = scan
        count, updated = self.run_runner(tracker)
        self.assertEqual(count, 1)
        self.assertEqual(updated.agenda_events[0]['room'], 'Best')
        self.assertEqual(updated.get_hours_for_day(self.day), 2)
        self.assertEqual(len(self.edits), 1)
        self.assertEqual(len(self.details), 1)

    def test_new_conflict_at_final_revalidation_prevents_edit(self):
        tracker = self.prepare_runner()
        def edit(page, choice, *, revalidate, **kwargs):
            self.events.append(dict(eventId=99, date=self.day.isoformat(), room=None,
                                    startTime="14:00", endTime="16:00", isReservation=False, title="Class"))
            self.assertFalse(revalidate())
            return False
        self.edit.side_effect = edit
        count, _ = self.run_runner(tracker)
        self.assertEqual(count, 0)
        self.assertEqual(self.events[0]["room"], "Fallback")

    def test_full_weekly_quota_does_not_block_a_same_duration_replacement(self):
        tracker = self.prepare_runner()
        with mock.patch.object(b.BookingTracker, "get_remaining_quota_hours", return_value=0):
            count, _ = self.run_runner(tracker)
        self.assertEqual(count, 1)

    def test_controlled_scope_disabled_dates_and_zero_target_are_respected(self):
        tracker = self.prepare_runner()
        for patch in ({"only_room": "Spare"}, {"only_date": "2026-09-20"}, {"upgrade_event_id": 99}):
            original_args = copy.copy(self.args)
            self.args.__dict__.update(patch)
            self.assertEqual(self.run_runner(tracker)[0], 0)
            self.args = original_args
        self.settings["disabled_dates"] = [self.day.isoformat()]
        self.assertEqual(self.run_runner(tracker)[0], 0)
        self.settings.pop("disabled_dates")
        self.plan = PracticePlan(enabled=True, default_hours=0)
        self.assertEqual(self.run_runner(tracker)[0], 0)
        self.edit.assert_not_called()

    def test_other_modes_and_consumed_action_caps_never_run_upgrades(self):
        tracker = self.prepare_runner()
        for flag in ("check_only", "plan_only", "agenda_only", "horizon_only", "extensions_only"):
            setattr(self.args, flag, True)
            self.assertEqual(self.run_runner(tracker)[0], 0)
            setattr(self.args, flag, False)
        self.assertEqual(self.run_runner(tracker, total=1)[0], 1)
        self.settings["booking_strategy"]["daily_planning"]["upgrade_rooms"] = False
        self.assertEqual(self.run_runner(tracker)[0], 0)
        self.scan.assert_not_called()
