import unittest
from datetime import date, timedelta

import mutation_receipts as receipts
from assistant_tools import AssistantToolError, dynamic_tool_specs, MUTATING_TOOLS
from tests import test_assistant_tools as fixtures


class AssistantTimeEditTests(unittest.TestCase):
    setUp = fixtures.AssistantToolSurfaceTests.setUp
    tearDown = fixtures.AssistantToolSurfaceTests.tearDown
    _publish = fixtures.AssistantToolSurfaceTests._publish
    _reservation = staticmethod(fixtures.AssistantToolSurfaceTests._reservation)
    _find_by_ids = fixtures.AssistantToolSurfaceTests._find_by_ids

    def prepare(self, *, extra=False):
        self.day = date(2099, 1, 1)
        self.event = self._reservation(42, self.day, "12:45", "14:45", "Weston Gallery")
        self.other = self._reservation(43, self.day, "17:00", "18:00", "B0.13")
        self._publish([self.event, self.other])
        selection = self._find_by_ids(42, 43) if extra else self._find_by_ids(42)
        self.args = dict(selection_id=selection["selection_id"], mode="trim_start", new_start_time="13:30",
                         request_quote="Trim my booking to start at 13:30 and keep its end.")

    def dispatch(self, **changes):
        args = {**self.args, **changes}
        return self.surface.dispatch("edit_reservation_time", args, user_request=args["request_quote"])

    def runner(self, *, persisted=True, refresh=True, exit_code=0):
        def run(flags, **kwargs):
            self.commands.append(list(flags))
            shift = "--shift-minutes" in flags
            new_end = "15:30" if shift else "14:45"
            receipt = receipts.record_pending_time_edit(room="Weston Gallery", booking_date=str(self.day),
                start="13:30", end=new_end, event_url="https://rwcmd.asimut.net/arrangement?eventId=42",
                original=dict(event_id=42, date=str(self.day), room="Weston Gallery", start="12:45", end="14:45"),
                path=self.paths.receipts)
            if persisted:
                receipts.mark_verified(receipt["id"], path=self.paths.receipts)
            if refresh:
                self._publish([{**self.event, "startTime": "13:30", "endTime": new_end}, self.other],
                              observed_at=self.last_observed + timedelta(seconds=1))
            return dict(completed=True, exit_code=exit_code, output=["TIME EDIT VERIFIED"])
        self.surface._command_runner = run

    def test_schema_and_mutation_authority_are_registered(self):
        spec = next(s for s in dynamic_tool_specs() if s["name"] == "edit_reservation_time")
        self.assertEqual(spec["type"], "function")
        self.assertIn("edit_reservation_time", MUTATING_TOOLS)
        self.assertNotIn("request_quote", spec["inputSchema"]["properties"])
        self.assertNotIn("oneOf", spec["inputSchema"])
        self.assertNotIn("allOf", spec["inputSchema"])
        self.assertEqual(set(spec["inputSchema"]["properties"]), {"selection_id", "mode", "new_start_time", "minutes"})

    def test_trim_uses_exact_worker_tuple_and_requires_receipt_plus_new_agenda(self):
        self.prepare()
        self.runner()
        result = self.dispatch()
        self.assertEqual(result["status"], "verified_changed")
        self.assertEqual(result["requested"]["duration_minutes"], 75)
        self.assertEqual(self.commands[0], ["--headless", "--edit-event-id", "42", "--edit-date", str(self.day),
            "--edit-room", "Weston Gallery", "--edit-start", "12:45", "--edit-end", "14:45", "--trim-start", "13:30"])
        self.assertEqual(result["released_window"]["end_time"], "13:30")

    def test_shift_uses_delta_and_preserves_duration(self):
        self.prepare()
        self.args.pop("new_start_time")
        self.args.update(mode="shift_later", minutes=45)
        self.runner()
        result = self.dispatch()
        self.assertTrue(result["verified_changed"])
        self.assertEqual(result["requested"]["end_time"], "15:30")
        self.assertEqual(result["requested"]["duration_minutes"], 120)
        self.assertEqual(self.commands[0][-2:], ["--shift-minutes", "45"])

    def test_exit_zero_and_printed_success_without_receipt_are_not_success(self):
        self.prepare()
        result = self.dispatch()
        self.assertEqual(result["status"], "unconfirmed")
        self.assertFalse(result["verified_changed"])

    def test_pending_receipt_and_matching_agenda_do_not_claim_success(self):
        self.prepare()
        self.runner(persisted=False, exit_code=5)
        result = self.dispatch()
        self.assertEqual(result["status"], "unconfirmed")
        self.assertTrue(result["reconciliation_required"])

    def test_verified_save_with_stale_agenda_explains_refresh_is_needed(self):
        self.prepare()
        self.runner(refresh=False, exit_code=5)
        self.assertEqual(self.dispatch()["status"], "verified_saved_refresh_required")

    def test_multiple_selection_never_chooses_a_booking_arbitrarily(self):
        self.prepare(extra=True)
        with self.assertRaisesRegex(AssistantToolError, "exactly one"):
            self.dispatch()
        self.assertFalse(self.commands)

    def test_stale_selection_and_current_tuple_drift_prevent_commands(self):
        self.prepare()
        self.surface.begin_turn()
        with self.assertRaises(AssistantToolError):
            self.dispatch()
        self.prepare()
        self._publish([{**self.event, "startTime": "13:00"}, self.other])
        with self.assertRaises(AssistantToolError):
            self.dispatch()
        self.assertFalse(self.commands)

    def test_second_edit_or_cancellation_cannot_retry_same_turn(self):
        self.prepare()
        self.dispatch()
        self.args["selection_id"] = self._find_by_ids(42)["selection_id"]
        with self.assertRaisesRegex(AssistantToolError, "already started"):
            self.dispatch()
        self.assertEqual(len(self.commands), 1)

    def test_bad_modes_and_time_precision_do_not_reach_worker(self):
        self.prepare()
        for changes in (dict(mode="earlier"), dict(minutes=45), dict(new_start_time="13:31"),
                        dict(new_start_time="14:45"), dict(new_start_time="12:30")):
            with self.subTest(changes=changes), self.assertRaises(AssistantToolError):
                self.dispatch(**changes)
        self.assertFalse(self.commands)

    def test_wrong_weekday_contradiction_prevents_edit(self):
        self.prepare()
        wrong = "Monday" if self.day.strftime("%A") != "Monday" else "Tuesday"
        with self.assertRaises(AssistantToolError):
            self.dispatch(request_quote=f"Trim my {wrong} booking to start at 13:30, same end.")
        self.assertFalse(self.commands)
