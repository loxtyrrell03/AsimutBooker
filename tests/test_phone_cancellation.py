import unittest
from datetime import date, timedelta
from unittest.mock import patch
from uuid import uuid4

from assistant_tools import AssistantToolError
from phone_cancellation import cancel_phone_reservation, validate_target
from phone_server import PhoneAssistantService, PhoneActiveTurnError
from tests import test_assistant_tools, test_phone_server
from tests.test_phone_server import MemoryLedger


class DirectCancellationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_assistant_tools.AssistantToolSurfaceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        day = date.today() + timedelta(days=1)
        self.fixture._publish([self.fixture._reservation(101, day, "16:00", "17:00", "B0.29")])
        self.target = dict(event_id=101, date=day.isoformat(), room="B0.29",
                           start_time="16:00", end_time="17:00")

    def test_click_uses_exact_verified_engine_and_keeps_time_free(self):
        result = cancel_phone_reservation(self.target, surface=self.fixture.surface)
        self.assertTrue(result["cancelled"])
        self.assertIn("stay free", result["message"])
        self.assertEqual(len(self.fixture.commands), 2)
        flags = self.fixture.commands[1]
        self.assertEqual(flags[flags.index("--cancel-event-id") + 1], "101")

    def test_replacement_id_or_changed_time_never_cancels(self):
        for change in ({"event_id": 999}, {"end_time": "17:30"}):
            with self.subTest(change=change):
                self.fixture.commands.clear()
                with self.assertRaises(AssistantToolError):
                    cancel_phone_reservation({**self.target, **change}, surface=self.fixture.surface)
                self.assertEqual(len(self.fixture.commands), 1)

    def test_verified_persisted_blackout_overrides_redundant_host_save_warning(self):
        dispatch = self.fixture.surface.dispatch
        def wrapped(tool, *args, **kwargs):
            result = dispatch(tool, *args, **kwargs)
            if tool == "cancel_reservations":
                result["protection_persisted"] = False
            return result
        with patch.object(self.fixture.surface, "dispatch", side_effect=wrapped):
            result = cancel_phone_reservation(self.target, surface=self.fixture.surface)
        self.assertEqual(result["message"], "Booking cancelled. This time will stay free.")

    def test_invalid_identity_rejected(self):
        for value in (None, True, 0, -1, "101"):
            with self.assertRaises(ValueError):
                validate_target({**self.target, "event_id": value})

    def test_duplicate_request_never_replays_and_no_model_starts(self):
        service = PhoneAssistantService(ledger=MemoryLedger(), runtime_factory=lambda *a, **k: self.fail("Model runtime started"))
        payload = {"request_id": str(uuid4()), "reservation": self.target}
        with patch("phone_server.cancel_phone_reservation", return_value={"cancelled": True, "reconciliation_required": False, "message": "Cancelled"}) as cancel:
            service.cancel_reservation(payload)
            service._cancellation_thread.join(3)
            with self.assertRaises(PhoneActiveTurnError):
                service.cancel_reservation(payload)
            cancel.assert_called_once()

    def test_uncertain_failure_blocks_new_request_and_review_during_work(self):
        service = PhoneAssistantService(ledger=MemoryLedger())
        def fail(_target, **_kwargs):
            with self.assertRaises(PhoneActiveTurnError):
                service.acknowledge_uncertain()
            raise RuntimeError("Disconnected")
        with patch("phone_server.cancel_phone_reservation", side_effect=fail):
            service.cancel_reservation({"request_id": str(uuid4()), "reservation": self.target})
            service._cancellation_thread.join(3)
        with self.assertRaises(PhoneActiveTurnError):
            service.cancel_reservation({"request_id": str(uuid4()), "reservation": self.target})
        self.assertIsNone(service._runtime)

    def test_http_requires_session_origin_and_csrf(self):
        fixture = test_phone_server.HTTPBoundaryTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.assistant.cancel_reservation = unittest.mock.Mock(return_value={"cancelled": True})
        body = '{"request_id":"unused","reservation":{}}'
        endpoint = "/api/v1/reservations/cancel"
        self.assertEqual(fixture.request("POST", endpoint, body=body)[0], 401)
        cookie, csrf = fixture.open_session()
        headers = {"Cookie": cookie, "Content-Type": "application/json"}
        self.assertEqual(fixture.request("POST", endpoint, headers=headers, body=body)[0], 403)
        headers["X-Asimut-CSRF"] = csrf
        self.assertEqual(fixture.request("POST", endpoint, headers={**headers, "Origin": "https://evil.test"}, body=body)[0], 403)
        fixture.assistant.cancel_reservation.assert_not_called()
        self.assertEqual(fixture.request("POST", endpoint, headers=headers, body=body)[0], 202)
        fixture.assistant.cancel_reservation.assert_called_once()

    def test_busy_refresh_queues_original_click_and_reconnect_restores_progress(self):
        service = PhoneAssistantService(ledger=MemoryLedger())
        service._live_refresh_lock.acquire()
        payload = {"request_id": str(uuid4()), "reservation": self.target}
        def cancel(_target, *, progress):
            progress("Opening your booking…")
            return {"cancelled": True, "reconciliation_required": False, "message": "Cancelled"}
        with patch("phone_server.cancel_phone_reservation", side_effect=cancel) as operation:
            self.assertTrue(service.cancel_reservation(payload)["accepted"])
            operation.assert_not_called()
            snapshot = service.snapshot()
            self.assertTrue(snapshot["cancellation"]["active"])
            self.assertEqual(snapshot["unresolved_reserved_count"], 0)
            self.assertEqual(service.refresh_live("plan")["cancellation"], snapshot["cancellation"])
            with self.assertRaises(PhoneActiveTurnError):
                service.acknowledge_uncertain()
            service._live_refresh_lock.release()
            service._cancellation_thread.join(3)
            operation.assert_called_once()
            self.assertFalse(service.snapshot()["cancellation"]["active"])
            self.assertTrue(service.snapshot()["cancellation"]["cancelled"])


if __name__ == "__main__":
    unittest.main()
