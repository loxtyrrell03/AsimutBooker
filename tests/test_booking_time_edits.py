import copy
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import book_week as engine
import mutation_receipts as receipts
from booking_blackouts import make_rebooking_blackout, load_rebooking_blackouts
from booking_time_edits import (BookingTimeEdit, requested_time_edit, validate_time_edit,
                                time_edit_from_receipt, run_time_edit)
from room_upgrades import Reservation, classify_upgrade_outcome, local_instant
from tests import test_upgrade_editor as editor_fixture


class TimeEditValidationTests(unittest.TestCase):
    def setUp(self):
        peak_patch = mock.patch("booking_time_edits.MAX_PEAK_MINUTES", 120)
        peak_patch.start()
        self.addCleanup(peak_patch.stop)
        self.day = date(2026, 9, 21)
        self.old = Reservation(42, self.day, "Weston Gallery", 765, 885)
        self.edit = requested_time_edit(self.old, mode="shift_later", minutes=45)
        self.events = [{**self.old.as_booking(), "isReservation": True}]
        self.now = datetime(2026, 9, 21, 9, tzinfo=timezone.utc)
        self.policy = SimpleNamespace(site_minimum_booking_minutes=30, site_maximum_booking_minutes=120,
            site_minimum_booking_gap_minutes=60, all_room_location_ids={self.old.room: 1},
            booking_dates=lambda today: (self.day,), site_clock_offset_bounds=(-1, 1),
            booking_horizon=self.now + timedelta(days=7), horizon_minutes_for=lambda room: 3 * 1440)
        self.gaps = [{"room": self.old.room, "slots": [{"startHour": 14.75, "endHour": 19}]}]

    def test_new_peak_limit_rejects_two_hour_shift_but_allows_offpeak_shift(self):
        with mock.patch('booking_time_edits.MAX_PEAK_MINUTES', 60):
            with self.assertRaisesRegex(ValueError, 'one-hour'):
                self.validate()
            self.edit = requested_time_edit(self.old, mode='shift_later', minutes=195)
            self.validate()

    def validate(self, **kwargs):
        values = dict(events=self.events, policy=self.policy, now=self.now, gaps=self.gaps)
        values.update(kwargs)
        return validate_time_edit(self.edit, **values)

    def test_requested_trim_keeps_exact_end_and_identity(self):
        edit = requested_time_edit(self.old, mode="trim_start", new_start_time="13:30")
        self.assertEqual(edit.replacement, replace(self.old, start=810))
        self.assertEqual(edit.replacement.duration, 75)
        self.assertEqual(edit.released_window, (str(self.day), "12:45", "13:30"))
        validate_time_edit(edit, events=self.events, policy=self.policy, now=self.now)

    def test_shift_keeps_duration_and_reuses_original_occupied_interval(self):
        self.assertEqual(self.edit.replacement, replace(self.old, start=810, end=930))
        self.validate()

    def test_nonoverlapping_shift_protects_only_original_interval(self):
        edit = requested_time_edit(self.old, mode="shift_later", minutes=180)
        self.assertEqual(edit.released_window, (str(self.day), "12:45", "14:45"))
        validate_time_edit(edit, events=self.events, policy=self.policy, now=self.now, gaps=self.gaps)

    def test_invalid_shapes_cannot_change_room_day_identity_or_duration_silently(self):
        for new in (replace(self.old, start=750), replace(self.old, start=780, end=900, room="Other"),
                    replace(self.old, start=780, end=900, event_id=99),
                    replace(self.old, start=780, end=900, day=self.day + timedelta(days=1)),
                    replace(self.old, start=780, end=870), replace(self.old, start=781)):
            with self.subTest(new=new), self.assertRaises(ValueError):
                BookingTimeEdit(self.old, new)
        for minutes in (0, -15, 20, True, "45", 1200):
            with self.subTest(minutes=minutes), self.assertRaises(ValueError):
                requested_time_edit(self.old, mode="shift_later", minutes=minutes)

    def test_end_equal_start_and_both_modes_are_rejected(self):
        with self.assertRaises(ValueError):
            requested_time_edit(self.old, mode="trim_start", new_start_time="14:45")
        with self.assertRaises(ValueError):
            requested_time_edit(self.old, mode="trim_start", new_start_time="13:30", minutes=45)

    def test_subminimum_trim_is_not_saved_or_rounded(self):
        self.edit = requested_time_edit(self.old, mode="trim_start", new_start_time="14:30")
        with self.assertRaisesRegex(ValueError, "30-120"):
            self.validate()

    def test_clash_with_later_booking_class_or_ignored_event_blocks_the_whole_shift(self):
        for reservation, ignored in ((True, False), (False, False), (True, True), (False, True)):
            event = dict(eventId=43, date=str(self.day), room="Other", startTime="15:00", endTime="16:00",
                         isReservation=reservation, blocksConflict=not ignored)
            with self.subTest(event=event), self.assertRaisesRegex(ValueError, "clashes"):
                self.validate(events=self.events + [event])

    def test_adjacent_other_room_is_allowed_but_same_room_gap_is_required(self):
        other = dict(eventId=43, date=str(self.day), room="Other", startTime="15:30", endTime="16:30", isReservation=False)
        self.validate(events=self.events + [other])
        with self.assertRaisesRegex(ValueError, "too close"):
            self.validate(events=self.events + [{**other, "isReservation": True, "room": self.old.room}])

    def test_room_occupancy_and_disconnected_gaps_block_shift(self):
        for slots in ([], [{"startHour": 15, "endHour": 19}], [{"startHour": 14.75, "endHour": 15}],
                      [{"startHour": 14.75, "endHour": 15}, {"startHour": 15.25, "endHour": 16}]):
            with self.subTest(slots=slots), self.assertRaisesRegex(ValueError, "occupied"):
                self.validate(gaps=[{"room": self.old.room, "slots": slots}])

    def test_missing_or_duplicate_row_fails_closed(self):
        for gaps in ([], self.gaps * 2):
            with self.assertRaisesRegex(ValueError, "availability"):
                self.validate(gaps=gaps)

    def test_whole_end_horizon_and_site_clock_required(self):
        self.policy.horizon_minutes_for = lambda room: 300
        with self.assertRaisesRegex(ValueError, "horizon"):
            self.validate()
        self.policy.site_clock_offset_bounds = None
        with self.assertRaisesRegex(ValueError, "clock"):
            self.validate()

    def test_shifting_into_peak_counts_other_reservations(self):
        other = dict(eventId=43, date=str(self.day), room="Other", startTime="09:00", endTime="10:00", isReservation=True)
        with self.assertRaisesRegex(ValueError, "peak allowance"):
            self.validate(events=self.events + [other])

    def test_protected_window_rejects_destination(self):
        with self.assertRaisesRegex(ValueError, "protected free time"):
            self.validate(blackouts=[make_rebooking_blackout(self.day, "15:00", "16:00")])

    def test_old_started_booking_can_trim_to_future_but_never_start_in_past(self):
        self.edit = requested_time_edit(self.old, mode="trim_start", new_start_time="13:30")
        self.validate(now=local_instant(self.day, 780))
        with self.assertRaisesRegex(ValueError, "future"):
            self.validate(now=local_instant(self.day, 810))

    def test_original_must_be_unique_and_unchanged(self):
        for events in ([], self.events * 2, [{**self.events[0], "startTime": "13:00"}],
                       [{**self.events[0], "isReservation": False}]):
            with self.assertRaisesRegex(ValueError, "changed"):
                self.validate(events=events)

    def test_cli_isolation_and_full_original_required(self):
        base = ["--edit-event-id", "42", "--edit-date", str(self.day), "--edit-room", self.old.room,
                "--edit-start", "12:45", "--edit-end", "14:45", "--trim-start", "13:30"]
        parser = engine.build_argument_parser()
        engine._validate_cli_args(parser, parser.parse_args(base))
        for extra in (["--agenda-only"], ["--shift-minutes", "45"], ["--scheduled"], ["--check-only"],
                      ["--upgrades-only"], ["--max-actions", "1"]):
            with self.subTest(extra=extra), self.assertRaises(SystemExit), mock.patch("sys.stderr"):
                engine._validate_cli_args(parser, parser.parse_args(base + extra))


class TimeEditReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "receipts.json"
        self.kw = dict(room="Weston Gallery", booking_date="2099-01-01", start="13:30", end="14:45",
            event_url="https://rwcmd.asimut.net/arrangement?eventId=42",
            original=dict(event_id=42, room="Weston Gallery", date="2099-01-01", start="12:45", end="14:45"), path=self.path)

    def test_journal_roundtrip_and_upgrade_invariants_stay_distinct(self):
        receipt = receipts.record_pending_time_edit(**self.kw)
        self.assertEqual(time_edit_from_receipt(receipt).replacement.duration, 75)
        self.assertEqual(receipts.list_pending(self.path), [receipt])
        with self.assertRaises(receipts.MutationReceiptError):
            receipts.record_pending_upgrade(**self.kw)
        for patch in (dict(room="Other"), dict(start="12:30"), dict(end="14:30")):
            with self.assertRaises(receipts.MutationReceiptError):
                receipts.record_pending_time_edit(**{**self.kw, **patch})

    def test_recovery_protects_released_time_only_after_independent_persisted_proof(self):
        receipt = receipts.record_pending_time_edit(**self.kw)
        new = {**time_edit_from_receipt(receipt).replacement.as_booking(), "isReservation": True}
        settings = Path(self.tmp.name) / "settings.json"
        settings.write_text('{"keep": true}')
        with mock.patch.object(engine, "list_pending_mutation_receipts", return_value=[receipt]), \
             mock.patch.object(engine, "safe_goto"), mock.patch.object(engine, "verify_persisted_booking_page"), \
             mock.patch.object(engine, "remove_extendable_booking_by_event_id"), mock.patch.object(engine, "clear_booking_plan"), \
             mock.patch.object(engine, "settings_file", settings), \
             mock.patch.object(engine, "verify_mutation_receipt", side_effect=lambda rid, **kw: receipts.mark_verified(rid, path=self.path, **kw)):
            engine.reconcile_pending_mutation_receipts(mock.Mock(), [new])
        self.assertEqual(load_rebooking_blackouts(__import__('json').loads(settings.read_text()))[0].end_time, "13:30")
        self.assertFalse(receipts.list_pending(self.path))

    def test_missing_changed_or_duplicate_outcomes_remain_pending(self):
        receipt = receipts.record_pending_time_edit(**self.kw)
        new = {**time_edit_from_receipt(receipt).replacement.as_booking(), "isReservation": True}
        with mock.patch.object(engine, "list_pending_mutation_receipts", return_value=[receipt]), \
             mock.patch.object(engine, "protect_time_edit_release") as protect:
            for events in ([], [new, new], [{**new, "endTime": "15:30"}]):
                with self.assertRaises(engine.BookingVerificationError):
                    engine.reconcile_pending_mutation_receipts(mock.Mock(), events)
            protect.assert_not_called()


class TimeEditRuntimeTests(unittest.TestCase):
    def setUp(self):
        peak_patch = mock.patch("booking_time_edits.MAX_PEAK_MINUTES", 120)
        peak_patch.start()
        self.addCleanup(peak_patch.stop)
        TimeEditValidationTests.setUp(self)
        self.engine = mock.Mock()
        self.engine.BookingVerificationError = engine.BookingVerificationError
        self.engine.list_pending_mutation_receipts.return_value = []
        self.engine.load_rebooking_blackouts.return_value = ()
        self.engine.load_ignored_events.return_value = ()
        self.engine.BookingTracker.side_effect = lambda: SimpleNamespace(agenda_events=[])
        self.engine.get_available_slots.return_value = self.gaps
        self.page = mock.Mock()
        self.args = SimpleNamespace(edit_event_id=42, edit_date=str(self.day), edit_room=self.old.room,
                                    edit_start="12:45", edit_end="14:45", trim_start=None, shift_minutes=45)
        self.saved = False
        def scan(page, tracker, *args, **kwargs):
            record = self.edit.replacement if self.saved else self.old
            tracker.agenda_events = [{**record.as_booking(), "isReservation": True}]
        self.engine.scan_agenda.side_effect = scan
        def edit(page, target, *, revalidate, **kwargs):
            self.assertEqual(target, self.edit)
            revalidate()
            self.saved = True
            return True
        self.engine.edit_reservation_room_time.side_effect = edit
        patch = mock.patch("booking_time_edits.datetime", wraps=datetime)
        clock = patch.start()
        clock.now.return_value = self.now
        self.addCleanup(patch.stop)

    def test_shift_refreshes_own_page_then_verifies_complete_agenda(self):
        self.assertEqual(run_time_edit(self.engine, self.page, self.args, {}, self.policy), 0)
        self.assertEqual(self.engine.scan_agenda.call_count, 2)
        self.engine.get_available_slots.assert_called_once()
        self.page.context.new_page.return_value.close.assert_called_once()
        self.engine.edit_reservation_room_time.assert_called_once()

    def test_rejected_edit_returns_without_postscan_or_second_action(self):
        self.engine.edit_reservation_room_time.side_effect = None
        self.engine.edit_reservation_room_time.return_value = False
        self.assertEqual(run_time_edit(self.engine, self.page, self.args, {}, self.policy), 7)
        self.engine.scan_agenda.assert_not_called()

    def test_pending_transaction_prevents_editor_and_recovery_writes(self):
        self.engine.list_pending_mutation_receipts.return_value = [{"kind": "transfer"}]
        with self.assertRaises(engine.BookingVerificationError):
            run_time_edit(self.engine, self.page, self.args, {}, self.policy)
        self.engine.edit_reservation_room_time.assert_not_called()

    def test_postsave_agenda_drift_is_not_success(self):
        def scan(page, tracker, *args, **kwargs):
            tracker.agenda_events = self.events
        self.engine.scan_agenda.side_effect = scan
        with self.assertRaises(engine.BookingVerificationError):
            run_time_edit(self.engine, self.page, self.args, {}, self.policy)
        self.engine.edit_reservation_room_time.assert_called_once()


class AssistantTimeEditBrowserTests(unittest.TestCase):
    setUpClass = classmethod(editor_fixture.UpgradeEditorTests.setUpClass.__func__)
    tearDownClass = classmethod(editor_fixture.UpgradeEditorTests.tearDownClass.__func__)
    site = editor_fixture.SameRoomAutofollowEditorTests.site
    run_edit = editor_fixture.UpgradeEditorTests.run_edit

    def setUp(self):
        editor_fixture.SameRoomAutofollowEditorTests.setUp(self)
        self.upgrade = requested_time_edit(self.original, mode="trim_start", new_start_time="13:00")
        self.new = self.upgrade.replacement
        for patch in (mock.patch.object(engine, "record_pending_time_edit",
                                       side_effect=lambda **kw: receipts.record_pending_time_edit(path=self.path, **kw)),
                      mock.patch.object(engine, "protect_time_edit_release")):
            value = patch.start()
            self.addCleanup(patch.stop)
            self.protect = value

    def test_trim_corrects_asimut_autofollow_and_saves_exactly_once(self):
        self.assertTrue(self.run_edit())
        self.assertEqual(self.persisted, self.new)
        self.assertEqual([(p['event']['st'][11:16], p['event']['en'][11:16]) for p in self.check_calls],
                         [('13:00', '15:00'), ('13:00', '14:00')])
        self.assertEqual(len(self.save_calls), 1)
        self.assertFalse(self.other_mutations)
        self.protect.assert_called_once()
        self.assertFalse(receipts.list_pending(self.path))

    def test_shift_autofollow_keeps_full_duration(self):
        self.upgrade = requested_time_edit(self.original, mode="shift_later", minutes=45)
        self.new = self.upgrade.replacement
        self.assertTrue(self.run_edit())
        self.assertEqual(self.persisted, self.new)
        self.assertEqual(len(self.check_calls), 1)
        self.assertEqual(len(self.save_calls), 1)

    def test_rejected_check_retains_original_and_never_saves(self):
        self.mode = "check_rejected"
        self.assertFalse(self.run_edit())
        self.assertEqual(self.persisted, self.original)
        self.assertFalse(self.save_calls)
        self.protect.assert_not_called()

    def test_rejected_save_verifies_original_without_protecting_time(self):
        self.mode = "save_rejected"
        self.assertFalse(self.run_edit())
        self.assertEqual(self.persisted, self.original)
        self.assertFalse(receipts.list_pending(self.path))
        self.protect.assert_not_called()

    def test_lost_response_remains_pending_without_retry(self):
        self.mode = "lost_response"
        with self.assertRaises(engine.BookingVerificationError):
            self.run_edit()
        self.assertEqual(len(self.save_calls), 1)
        self.assertEqual(len(receipts.list_pending(self.path)), 1)
        self.assertFalse(self.other_mutations)
        self.protect.assert_not_called()

    def test_failed_protection_after_save_stays_pending_for_recovery(self):
        self.protect.side_effect = OSError("disk unavailable")
        with self.assertRaises(engine.BookingVerificationError):
            self.run_edit()
        self.assertEqual(self.persisted, self.new)
        self.assertEqual(len(receipts.list_pending(self.path)), 1)
        self.assertEqual(len(self.save_calls), 1)
