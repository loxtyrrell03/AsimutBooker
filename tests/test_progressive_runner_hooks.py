"""Isolated runner wiring for boundary transfers; no site or live-state writes."""
import contextlib
import io
import sys
import types
import unittest
from datetime import date
from types import SimpleNamespace
from unittest import mock

import book_week as b


class StopAfterPriority(Exception):
    pass


class ProgressiveRunnerHookTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.module = types.ModuleType("progressive_runtime")
        self.progressive = self.module.process_progressive_upgrades = mock.Mock()
        self.module.protected_event_ids = mock.Mock(return_value=set())
        self.stack.enter_context(mock.patch.dict(sys.modules, progressive_runtime=self.module))
        self.args = b.build_argument_parser().parse_args(["--headless"])
        self.tracker = mock.Mock(agenda_events=[], agenda_active_event_ids=[], bookings=[], peak_hours_by_day={})
        self.tracker.is_quota_full.return_value = False
        self.tracker.get_total_booking_hours.return_value = 28.0
        self.progressive.side_effect = lambda *args: (args[6], args[5])
        self.page = mock.MagicMock()
        self.context = mock.MagicMock()
        self.context.new_page.return_value = self.page
        browser = mock.MagicMock()
        browser.new_context.return_value = self.context
        playwright = mock.MagicMock()
        playwright.chromium.launch.return_value = browser
        manager = mock.MagicMock()
        manager.__enter__.return_value = playwright
        replacements = {
            "sync_playwright": mock.Mock(return_value=manager),
            "BookingTracker": mock.Mock(return_value=self.tracker),
            "authenticated_runtime_context_options": mock.Mock(return_value={}),
            "restore_page_authentication": mock.Mock(),
            "refresh_quota_balances": mock.Mock(),
            "refresh_live_room_policy": mock.Mock(),
            "validate_live_scope": mock.Mock(),
            "booking_window_dates": mock.Mock(return_value=(date.today(),)),
            "scan_agenda": mock.Mock(return_value=(0, [])),
            "reconcile_pending_mutation_receipts": mock.Mock(return_value=[]),
            "list_pending_mutation_receipts": mock.Mock(return_value=[]),
            "load_ignored_events": mock.Mock(return_value=set()),
            "load_rebooking_blackouts": mock.Mock(return_value=[]),
            "apply_rebooking_blackouts": mock.Mock(),
            "load_disabled_dates": mock.Mock(return_value=set()),
            "load_time_preferences": mock.Mock(return_value={"enabled": False}),
            "load_booking_strategy": mock.Mock(return_value={
                "reverse_date_order": False,
                "daily_planning": b.DailyPlanningPreferences(),
            }),
            "refresh_extension_capacity_holds": mock.Mock(),
            "process_pending_extensions": mock.Mock(side_effect=StopAfterPriority),
            "process_room_upgrades": mock.Mock(return_value=(0, self.tracker)),
            "persist_storage_state": mock.Mock(),
            "save_history": mock.Mock(),
            "send_notification": mock.Mock(),
            "generate_read_only_booking_plan": mock.Mock(),
        }
        self.mocks = replacements
        self.stack.enter_context(mock.patch.multiple(b, **replacements))

    def run_booker(self):
        return b.run_booking(self.args, {}, b.PracticePlan(), room_preferences=mock.Mock())

    def test_today_is_completed_before_future_upgrades_at_full_quota(self):
        self.tracker.is_quota_full.return_value = True
        self.mocks["process_pending_extensions"].side_effect = None
        self.mocks["process_pending_extensions"].return_value = (0, True)
        self.stack.enter_context(mock.patch('short_notice_bookings.run_short_notice_pass', return_value=(0, self.tracker)))
        order = []
        self.progressive.side_effect = lambda *args: (order.append("progressive") or 0, args[5])
        self.mocks["process_room_upgrades"].side_effect = lambda *args: (order.append("whole") or 0, args[5])
        self.assertEqual(self.run_booker(), 0)
        self.assertEqual(order, ["whole", "progressive", "whole"])
        self.mocks["process_pending_extensions"].assert_called_once()

    def test_progressive_runs_before_ordinary_extension_wait_and_updates_agenda(self):
        updated = mock.Mock(agenda_events=[
            {"eventId": 42, "isReservation": True, "endTime": "12:45"},
            {"eventId": 43, "isReservation": False},
        ])
        updated.is_quota_full.return_value = False
        self.progressive.return_value = (2, updated)
        self.progressive.side_effect = None
        with self.assertRaises(StopAfterPriority):
            self.run_booker()
        call = self.mocks["process_pending_extensions"].call_args.args
        self.assertIs(call[1], updated)
        self.assertEqual(call[2], [updated.agenda_events[0]])
        self.assertEqual(call[7], 2)
        self.assertEqual(self.mocks["refresh_extension_capacity_holds"].call_count, 2)

    def test_future_quota_refusal_cannot_skip_daily_priority(self):
        self.tracker.is_quota_full.return_value=True
        self.mocks['process_pending_extensions'].side_effect=None
        self.mocks['process_pending_extensions'].return_value=(0,True)
        short=self.stack.enter_context(mock.patch('short_notice_bookings.run_short_notice_pass',
            return_value=(0,self.tracker)))
        def refuse(*args):
            short.assert_called_once()
            self.mocks['process_pending_extensions'].assert_called_once()
            self.assertEqual(self.mocks['process_room_upgrades'].call_args.args[4].only_date,str(date.today()))
            raise b.QuotaWait('Requested booking exceeds your quota')
        self.progressive.side_effect=refuse
        with self.assertRaises(b.QuotaWait):
            self.run_booker()

    def test_between_edge_scheduled_pass_only_works_on_daily_practice(self):
        self.args.scheduled=True
        self.args.target_time=None
        self.tracker.is_quota_full.return_value=False
        self.mocks['process_pending_extensions'].side_effect=None
        self.mocks['process_pending_extensions'].return_value=(0,True)
        short=self.stack.enter_context(mock.patch('short_notice_bookings.run_short_notice_pass',
            return_value=(0,self.tracker)))
        self.assertEqual(self.run_booker(),0)
        short.assert_called_once()
        self.progressive.assert_not_called()
        self.assertEqual(self.mocks['process_room_upgrades'].call_args.args[4].only_date,str(date.today()))
        self.assertIsNone(self.args.only_date)

    def test_pending_transfer_blocks_fallthrough_to_unrelated_mutations(self):
        self.mocks["list_pending_mutation_receipts"].return_value = [{"kind": "transfer"}]
        with self.assertRaisesRegex(b.BookingVerificationError, "transfer remains pending"):
            self.run_booker()
        self.mocks["process_pending_extensions"].assert_not_called()
        self.mocks["process_room_upgrades"].assert_not_called()

    def test_read_only_plan_never_dispatches_progressive_mutations(self):
        self.args.plan_only = True
        self.assertEqual(self.run_booker(), 0)
        self.progressive.assert_not_called()
        self.mocks["process_pending_extensions"].assert_not_called()

    def new_rule_run(self):
        return b.run_booking(self.args, {'booking_rules': {'preset': 'new'}},
            b.PracticePlan(enabled=True, default_hours=4), room_preferences=mock.Mock())

    def test_new_weekly_allocation_retains_extensions_free_pass_and_upgrades(self):
        order = []
        self.mocks['process_pending_extensions'].side_effect = lambda *a: (order.append('extensions') or 0, True)
        self.mocks['process_room_upgrades'].side_effect = lambda *a: (order.append('upgrades') or a[6], a[5])
        with mock.patch('advance_runtime.run', side_effect=lambda *a, **kw: (
                order.append('weekly') or a[7], a[6], True)) as advance, \
             mock.patch('short_notice_bookings.run_short_notice_pass', side_effect=lambda *a, **kw: (
                order.append('free') or a[6], a[5])):
            self.assertEqual(self.new_rule_run(), 0)
        self.assertEqual(order, ['extensions', 'free', 'weekly', 'free', 'upgrades'])
        advance.assert_called_once()
        self.mocks['save_history'].assert_called_once()

    def test_new_read_only_plan_uses_weekly_allocator_without_mutations(self):
        self.args.plan_only = True
        with mock.patch('advance_runtime.run', return_value=(0, self.tracker, True)) as advance:
            self.assertEqual(self.new_rule_run(), 0)
        self.assertTrue(advance.call_args.kwargs['read_only'])
        self.progressive.assert_not_called()
        self.mocks['process_pending_extensions'].assert_not_called()
        self.mocks['process_room_upgrades'].assert_not_called()

    def test_recovered_quota_refusal_rebuilds_agenda_and_retains_action_budget(self):
        updated = mock.Mock(agenda_events=[{'eventId': 123, 'isReservation': True}])
        self.mocks['BookingTracker'].side_effect = [self.tracker, updated]
        refusal = b.QuotaWait('Requested booking exceeds your quota')
        refusal.completed_actions = 4
        self.progressive.side_effect = refusal
        with self.assertRaises(StopAfterPriority):
            self.new_rule_run()
        self.assertEqual(self.mocks['scan_agenda'].call_count, 2)
        self.assertIs(self.mocks['scan_agenda'].call_args.args[1], updated)
        call = self.mocks['process_pending_extensions'].call_args.args
        self.assertIs(call[1], updated)
        self.assertEqual(call[2], updated.agenda_events)
        self.assertEqual(call[7], 4)

    def test_unresolved_quota_refusal_cannot_continue_new_weekly_work(self):
        self.progressive.side_effect = b.QuotaWait('Requested booking exceeds your quota')
        self.mocks['list_pending_mutation_receipts'].return_value = [{'kind':'transfer'}]
        with self.assertRaises(b.QuotaWait):
            self.new_rule_run()
        self.mocks['process_pending_extensions'].assert_not_called()


class ProgressiveReconciliationHookTests(unittest.TestCase):
    def test_parent_is_retained_while_expired_child_is_reconciled(self):
        receipts = [
            {"id": "parent", "kind": "transfer"},
            {"id": "child", "kind": "create", "date": "2000-01-01", "end": "12:30"},
        ]
        with (
            mock.patch.object(b, "list_pending_mutation_receipts", return_value=receipts),
            mock.patch.object(b, "resolve_mutation_receipt") as resolve,
            mock.patch.object(b, "safe_goto") as navigate,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(b.reconcile_pending_mutation_receipts(mock.Mock(), []), ())
        self.assertEqual(resolve.call_count, 1)
        self.assertEqual(resolve.call_args.args[0], "child")
        navigate.assert_not_called()

    def test_ordinary_extension_skips_exact_transfer_owned_id(self):
        day = date.today().isoformat()
        owned = {"eventId": 42, "room": "Best", "date": day,
                 "startTime": "12:00", "endTime": "12:30", "target_end": "14:00"}
        ordinary = {**owned, "eventId": 43}
        module = types.ModuleType("progressive_runtime")
        module.protected_event_ids = lambda: {42}
        tracker = mock.Mock()
        tracker.get_hours_for_day.return_value = 0
        with (
            mock.patch.dict(sys.modules, progressive_runtime=module),
            mock.patch.object(b, "load_settings_document", return_value={}),
            mock.patch.object(b, "load_extendable_bookings", return_value=[owned, ordinary]),
            mock.patch.object(b, "booking_window_dates", return_value=(date.today(),)),
            mock.patch.object(b, "PRIORITY_ROOMS", ["Best"]),
            mock.patch.object(b, "extension_processing_sort_key", return_value=(0,)),
            mock.patch.object(b, "try_extend_booking", return_value=(False, None, "Unavailable")) as extend,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = b.process_pending_extensions(mock.Mock(), tracker, [], b.PracticePlan(),
                                                  set(), {}, SimpleNamespace(), 0, [])
        self.assertEqual(result, (0, True))
        self.assertEqual(extend.call_count, 1)
        self.assertEqual(extend.call_args.args[1]["eventId"], 43)
