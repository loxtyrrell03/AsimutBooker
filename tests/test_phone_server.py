import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from phone_server import (
    EventHub,
    PhoneActiveTurnError,
    PhoneApplication,
    PhoneAssistantService,
    PhoneHTTPServer,
    PhoneServerError,
    PhoneServerConfig,
    RequestLedger,
    SessionStore,
    load_phone_server_config,
    public_assistant_event,
)


class MemoryLedger:
    def __init__(self):
        self.states = {}

    def lookup(self, request_id):
        return self.states.get(request_id)

    def reserve(self, request_id):
        return self.states.setdefault(request_id, "reserved")

    def mark(self, request_id, state):
        self.states[request_id] = state

    def unresolved_reserved_count(self):
        return sum(1 for state in self.states.values() if state == "reserved")

    def acknowledge_reserved(self):
        request_ids = [
            request_id for request_id, state in self.states.items() if state == "reserved"
        ]
        for request_id in request_ids:
            self.states[request_id] = "acknowledged"
        return len(request_ids)


class FakeRuntime:
    def __init__(self, event_handler, **_kwargs):
        self.event_handler = event_handler
        self.model_label = "GPT-5.6 Terra · medium"
        self.is_busy = False
        self.messages = []
        self.sent = []
        self.closed = False
        self.accept_send = True
        self.fail_terminally = False
        self.emit_turn_started = True
        self.refresh_calls = []
        self.refresh_error = None

    def start(self):
        self.event_handler({"kind": "connection", "status": "ready", "text": self.model_label})
        return True

    def restored_messages(self):
        return list(self.messages)

    def send(self, text):
        if self.is_busy:
            return False
        if not self.accept_send:
            return False
        self.is_busy = True
        self.sent.append(text)
        self.messages.append({"role": "user", "text": text, "created_at": "2026-08-31T10:00:00Z"})
        if self.fail_terminally:
            self.is_busy = False
            self.event_handler(
                {
                    "kind": "error",
                    "status": "send",
                    "title": "Asimut Assistant could not continue",
                    "text": "synthetic failure",
                }
            )
        elif self.emit_turn_started:
            self.event_handler(
                {"kind": "turn_started", "status": "in_progress", "turn_id": "turn-1"}
            )
        return True

    def complete(self, status="completed"):
        self.is_busy = False
        self.event_handler(
            {"kind": "turn_completed", "status": status, "turn_id": "turn-1"}
        )

    def stop(self):
        was_busy = self.is_busy
        self.is_busy = False
        return was_busy

    def refresh_booker_data(self, scope, *, progress):
        self.refresh_calls.append(scope)
        progress("Checking Asimut", "Fixture refresh")
        if self.refresh_error is not None:
            raise self.refresh_error
        return {"scope": scope, "fresh": True}

    def new_chat(self):
        if self.is_busy:
            return False
        self.messages = []
        return True

    def close(self, timeout=8):
        self.closed = True


class FakeAssistant:
    def __init__(self):
        self.runtime = type("Runtime", (), {"is_busy": False})()
        self.events = EventHub()
        self.stream_generation = "test-stream-generation"
        self.submissions = []
        self.closed = False
        self.submit_error = None
        self.submit_result = (True, "accepted")
        self.unresolved_reserved_count = 0
        self.live_refresh_calls = []
        self.live_refresh_error = None

    @property
    def is_busy(self):
        return self.runtime.is_busy

    def snapshot(self):
        return {
            "model": "GPT-5.6 Terra · medium",
            "busy": False,
            "messages": [],
            "event_cursor": self.events.cursor,
            "stream_generation": self.stream_generation,
            "active_client_message_id": None,
            "unresolved_reserved_count": self.unresolved_reserved_count,
            "booker": {"status": {"state": "ready"}},
        }

    def refresh_live(self, scope, *, force=False):
        self.live_refresh_calls.append((scope, force))
        if self.live_refresh_error is not None:
            raise self.live_refresh_error
        return self.snapshot()

    def submit(self, request_id, text):
        if self.submit_error is not None:
            raise self.submit_error
        self.submissions.append((request_id, text))
        return self.submit_result

    def stop(self):
        return False

    def new_chat(self):
        return True

    def acknowledge_uncertain(self):
        if self.is_busy:
            raise PhoneActiveTurnError("synthetic active turn")
        acknowledged = self.unresolved_reserved_count
        self.unresolved_reserved_count = 0
        return acknowledged

    def close(self):
        self.closed = True


class PublicEventTests(unittest.TestCase):
    def test_streamed_deltas_preserve_spaces_and_keep_commentary_out_of_chat(self):
        self.assertEqual(
            public_assistant_event(
                {"kind": "assistant_delta", "text": " tomorrow "}
            ),
            {"kind": "assistant.delta", "text": " tomorrow "},
        )
        self.assertEqual(
            public_assistant_event(
                {
                    "kind": "commentary_delta",
                    "text": "I’ll check ",
                    "first_delta": True,
                }
            ),
            {"kind": "progress.delta", "text": "I’ll check ", "replace": True},
        )

    def test_reasoning_parts_are_indexed_and_generic_thread_churn_is_hidden(self):
        self.assertEqual(
            public_assistant_event(
                {
                    "kind": "reasoning_summary_delta",
                    "text": "**Checking the plan** ",
                    "summary_index": 2,
                }
            ),
            {
                "kind": "reasoning.delta",
                "text": "**Checking the plan** ",
                "part": 2,
            },
        )
        self.assertIsNone(
            public_assistant_event(
                {
                    "kind": "status",
                    "title": "Chat status changed",
                    "status": "thread_status",
                }
            )
        )

    def test_tool_events_remove_arguments_tokens_and_internal_ids(self):
        mapped = public_assistant_event(
            {
                "kind": "tool_call",
                "title": "Finding reservations",
                "text": "Checking exact agenda identity",
                "status": "in_progress",
                "arguments": {"match_token": "sha256:secret"},
                "thread_id": "thread-secret",
                "request_quote": "cancel all",
            }
        )
        self.assertEqual(
            mapped,
            {
                "kind": "tool.status",
                "title": "Finding reservations",
                "text": "Checking exact agenda identity",
                "status": "in_progress",
            },
        )
        self.assertNotIn("secret", str(mapped))

    def test_raw_reasoning_and_unknown_events_are_not_exposed(self):
        self.assertIsNone(
            public_assistant_event({"kind": "reasoning_text_delta", "text": "private chain"})
        )
        self.assertIsNone(
            public_assistant_event({"kind": "reasoning_delta", "text": "private chain"})
        )
        self.assertIsNone(public_assistant_event({"kind": "history", "thread_id": "private"}))

    def test_sensitive_progress_is_redacted(self):
        mapped = public_assistant_event(
            {"kind": "activity", "title": "Checking", "text": "password: secret-value"}
        )
        self.assertEqual(
            mapped["text"], "Sensitive information was withheld from the phone display."
        )


class EventHubTests(unittest.TestCase):
    def test_replay_and_overflow_are_explicit(self):
        hub = EventHub(capacity=16)
        for index in range(20):
            hub.publish({"kind": "activity", "text": str(index)})
        events, overflow = hub.wait_after(0, timeout=0)
        self.assertTrue(overflow)
        self.assertEqual(events[0]["seq"], 5)
        latest, overflow = hub.wait_after(19, timeout=0)
        self.assertFalse(overflow)
        self.assertEqual([item["seq"] for item in latest], [20])


class PhoneAssistantServiceTests(unittest.TestCase):
    def test_live_plan_refresh_runs_real_runtime_work_and_returns_new_snapshot(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "phone_server.build_phone_snapshot", return_value={"status": {"state": "ready"}}
        ):
            service = PhoneAssistantService(
                runtime_factory=FakeRuntime,
                ledger=MemoryLedger(),
                state_path=Path(directory) / "state.json",
                workspace=Path(directory) / "workspace",
            )
            try:
                snapshot = service.refresh_live("plan", force=True)
                self.assertEqual(service.runtime.refresh_calls, ["plan"])
                self.assertEqual(snapshot["booker"]["status"]["state"], "ready")
                events, _ = service.events.wait_after(0, timeout=0)
                self.assertIn("Schedule refreshed", [item.get("title") for item in events])
            finally:
                service.close()

    def test_failed_live_refresh_keeps_last_snapshot_available(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "phone_server.build_phone_snapshot", return_value={"status": {"state": "stale"}}
        ):
            service = PhoneAssistantService(
                runtime_factory=FakeRuntime,
                ledger=MemoryLedger(),
                state_path=Path(directory) / "state.json",
                workspace=Path(directory) / "workspace",
            )
            try:
                service.runtime.refresh_error = RuntimeError("private fixture detail")
                with self.assertRaisesRegex(PhoneServerError, "did not complete"):
                    service.refresh_live("agenda", force=True)
                self.assertEqual(
                    service.snapshot()["booker"]["status"]["state"], "stale"
                )
            finally:
                service.close()

    def test_runtime_receives_the_configured_codex_executable(self):
        captured = {}

        class RecordingRuntime(FakeRuntime):
            def __init__(self, event_handler, **kwargs):
                captured.update(kwargs)
                super().__init__(event_handler, **kwargs)

        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "phone_server.build_phone_snapshot", return_value={"status": {"state": "ready"}}
        ):
            codex_executable = str(Path(directory) / "codex.exe")
            service = PhoneAssistantService(
                runtime_factory=RecordingRuntime,
                ledger=MemoryLedger(),
                state_path=Path(directory) / "state.json",
                workspace=Path(directory) / "workspace",
                codex_executable=codex_executable,
            )
            try:
                self.assertIsNotNone(service.runtime)
                self.assertEqual(
                    captured["controller_kwargs"]["codex_executable"],
                    codex_executable,
                )
            finally:
                service.close()

    def test_duplicate_message_id_executes_exactly_once(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "phone_server.build_phone_snapshot", return_value={"status": {"state": "ready"}}
        ):
            service = PhoneAssistantService(
                runtime_factory=FakeRuntime,
                ledger=MemoryLedger(),
                state_path=Path(directory) / "state.json",
                workspace=Path(directory) / "workspace",
            )
            try:
                request_id = "3d790c3f-d46d-4aad-a51f-3d31d8b2eec7"
                self.assertEqual(service.submit(request_id, "Explain tomorrow"), (True, "started"))
                self.assertEqual(service.ledger.states[request_id], "reserved")
                self.assertEqual(service.snapshot()["unresolved_reserved_count"], 0)
                service.runtime.complete()
                self.assertEqual(service.ledger.states[request_id], "accepted")
                self.assertEqual(service.submit(request_id, "Explain tomorrow"), (True, "duplicate"))
                self.assertEqual(service.runtime.sent, ["Explain tomorrow"])
                events, _ = service.events.wait_after(0, timeout=0)
                started = [item for item in events if item["kind"] == "turn.started"]
                completed = [item for item in events if item["kind"] == "turn.completed"]
                self.assertEqual(started[-1]["client_message_id"], request_id)
                self.assertEqual(completed[-1]["client_message_id"], request_id)
            finally:
                service.close()

    def test_concurrent_turn_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "phone_server.build_phone_snapshot", return_value={"status": {"state": "ready"}}
        ):
            service = PhoneAssistantService(
                runtime_factory=FakeRuntime,
                ledger=MemoryLedger(),
                state_path=Path(directory) / "state.json",
                workspace=Path(directory) / "workspace",
            )
            try:
                service.runtime.is_busy = True
                self.assertEqual(
                    service.submit("35c60f58-2321-4d10-aa86-7aca6fcf9fcb", "Second turn"),
                    (False, "turn_in_progress"),
                )
                self.assertEqual(service.runtime.sent, [])
            finally:
                service.close()

    def test_rejected_runtime_id_never_becomes_accepted_duplicate(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "phone_server.build_phone_snapshot", return_value={"status": {"state": "ready"}}
        ):
            service = PhoneAssistantService(
                runtime_factory=FakeRuntime,
                ledger=MemoryLedger(),
                state_path=Path(directory) / "state.json",
                workspace=Path(directory) / "workspace",
            )
            try:
                service.runtime.accept_send = False
                request_id = "55f2d82e-845e-47b2-a430-d381953cf4cd"
                self.assertEqual(service.submit(request_id, "Do not run"), (False, "not_accepted"))
                self.assertEqual(service.submit(request_id, "Do not run"), (False, "not_accepted"))
                self.assertEqual(service.ledger.states[request_id], "rejected")
            finally:
                service.close()

    def test_terminal_failure_before_start_remains_review_gated(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "phone_server.build_phone_snapshot", return_value={"status": {"state": "ready"}}
        ):
            service = PhoneAssistantService(
                runtime_factory=FakeRuntime,
                ledger=MemoryLedger(),
                state_path=Path(directory) / "state.json",
                workspace=Path(directory) / "workspace",
            )
            try:
                service.runtime.fail_terminally = True
                request_id = "2ad7f128-cb0b-4234-8894-3e89d92e010f"
                self.assertEqual(
                    service.submit(request_id, "Explain tomorrow"),
                    (True, "outcome_uncertain"),
                )
                events, _ = service.events.wait_after(0, timeout=0)
                kinds = [item["kind"] for item in events]
                self.assertIn("error", kinds)
                self.assertNotIn("turn.started", kinds)
                terminal = next(item for item in events if item["kind"] == "error")
                self.assertEqual(terminal["client_message_id"], request_id)
                self.assertTrue(terminal["terminal"])
                self.assertFalse(service.snapshot()["busy"])
                self.assertEqual(service.snapshot()["unresolved_reserved_count"], 1)
                self.assertEqual(service.ledger.states[request_id], "reserved")
            finally:
                service.close()

    def test_terminal_after_real_start_settles_immediately_before_event(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "phone_server.build_phone_snapshot", return_value={"status": {"state": "ready"}}
        ):
            service = PhoneAssistantService(
                runtime_factory=FakeRuntime,
                ledger=MemoryLedger(),
                state_path=Path(directory) / "state.json",
                workspace=Path(directory) / "workspace",
            )
            try:
                request_id = "74a9bb64-e28d-485a-a5e5-c0d7d9e3bd9d"
                self.assertEqual(service.submit(request_id, "Explain tomorrow"), (True, "started"))
                self.assertEqual(service.ledger.states[request_id], "reserved")
                service.runtime.complete(status="failed")
                self.assertEqual(service.ledger.states[request_id], "accepted")
                events, _ = service.events.wait_after(0, timeout=0)
                terminal = next(item for item in events if item["kind"] == "turn.completed")
                self.assertTrue(terminal["terminal"])
                self.assertEqual(terminal["client_message_id"], request_id)
                self.assertEqual(
                    service.submit(request_id, "Explain tomorrow"), (True, "duplicate")
                )
            finally:
                service.close()

    def test_reserved_retry_is_reported_uncertain_without_replay_or_deadlock(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "phone_server.build_phone_snapshot", return_value={"status": {"state": "ready"}}
        ):
            ledger = MemoryLedger()
            request_id = "c1299010-feb3-47b7-aeb2-262902c042cf"
            ledger.states[request_id] = "reserved"
            service = PhoneAssistantService(
                runtime_factory=FakeRuntime,
                ledger=ledger,
                state_path=Path(directory) / "state.json",
                workspace=Path(directory) / "workspace",
            )
            try:
                self.assertEqual(
                    service.submit(request_id, "Cancel the exact reservation"),
                    (True, "outcome_uncertain"),
                )
                self.assertEqual(service.runtime.sent, [])
                self.assertFalse(service.is_busy)
            finally:
                service.close()

    def test_restart_after_send_before_first_event_exposes_uncertain_request(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "phone_server.build_phone_snapshot", return_value={"status": {"state": "ready"}}
        ):
            base = Path(directory)
            ledger_path = base / "ledger.json"
            request_id = "5f95a5c0-63e1-4c4b-9fe9-65a56daab345"
            first = PhoneAssistantService(
                runtime_factory=FakeRuntime,
                ledger=RequestLedger(ledger_path),
                state_path=base / "state.json",
                workspace=base / "workspace-a",
            )
            try:
                first.runtime.emit_turn_started = False
                self.assertEqual(
                    first.submit(request_id, "Cancel the exact reservation"),
                    (True, "queued"),
                )
                self.assertTrue(first.snapshot()["busy"])
                self.assertEqual(first.snapshot()["unresolved_reserved_count"], 0)
                self.assertEqual(RequestLedger(ledger_path).lookup(request_id), "reserved")
            finally:
                first.close()

            restarted = PhoneAssistantService(
                runtime_factory=FakeRuntime,
                ledger=RequestLedger(ledger_path),
                state_path=base / "state.json",
                workspace=base / "workspace-b",
            )
            try:
                self.assertEqual(restarted.snapshot()["unresolved_reserved_count"], 1)
                self.assertEqual(
                    restarted.submit(request_id, "Cancel the exact reservation"),
                    (True, "outcome_uncertain"),
                )
                self.assertEqual(restarted.runtime.sent, [])
            finally:
                restarted.close()

    def test_restart_mid_turn_exposes_uncertain_request(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "phone_server.build_phone_snapshot", return_value={"status": {"state": "ready"}}
        ):
            base = Path(directory)
            ledger_path = base / "ledger.json"
            request_id = "87f27eaa-9712-4601-b891-4843e93247a9"
            first = PhoneAssistantService(
                runtime_factory=FakeRuntime,
                ledger=RequestLedger(ledger_path),
                state_path=base / "state.json",
                workspace=base / "workspace-a",
            )
            try:
                self.assertEqual(
                    first.submit(request_id, "Cancel the exact reservation"),
                    (True, "started"),
                )
                self.assertEqual(first.snapshot()["unresolved_reserved_count"], 0)
                self.assertEqual(RequestLedger(ledger_path).lookup(request_id), "reserved")
            finally:
                first.close()

            restarted = PhoneAssistantService(
                runtime_factory=FakeRuntime,
                ledger=RequestLedger(ledger_path),
                state_path=base / "state.json",
                workspace=base / "workspace-b",
            )
            try:
                snapshot = restarted.snapshot()
                self.assertFalse(snapshot["busy"])
                self.assertEqual(snapshot["unresolved_reserved_count"], 1)
            finally:
                restarted.close()

    def test_uncertain_review_is_rejected_while_turn_is_active(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "phone_server.build_phone_snapshot", return_value={"status": {"state": "ready"}}
        ):
            service = PhoneAssistantService(
                runtime_factory=FakeRuntime,
                ledger=MemoryLedger(),
                state_path=Path(directory) / "state.json",
                workspace=Path(directory) / "workspace",
            )
            try:
                self.assertEqual(
                    service.submit(
                        "beb23003-3858-44c3-8561-2ac20d5d5ca7",
                        "Explain tomorrow",
                    ),
                    (True, "started"),
                )
                with self.assertRaises(PhoneActiveTurnError):
                    service.acknowledge_uncertain()
                self.assertEqual(service.ledger.unresolved_reserved_count(), 1)
            finally:
                service.close()

    def test_nonbusy_runtime_without_terminal_clears_only_stale_active_metadata(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "phone_server.build_phone_snapshot", return_value={"status": {"state": "ready"}}
        ):
            service = PhoneAssistantService(
                runtime_factory=FakeRuntime,
                ledger=MemoryLedger(),
                state_path=Path(directory) / "state.json",
                workspace=Path(directory) / "workspace",
            )
            try:
                request_id = "3e5318c0-6c93-457d-86f2-416afc89af71"
                self.assertEqual(
                    service.submit(request_id, "Explain tomorrow"),
                    (True, "started"),
                )
                service.runtime.is_busy = False
                snapshot = service.snapshot()
                self.assertFalse(snapshot["busy"])
                self.assertIsNone(snapshot["active_client_message_id"])
                self.assertEqual(snapshot["unresolved_reserved_count"], 1)
                self.assertEqual(service.ledger.states[request_id], "reserved")
                self.assertEqual(service.acknowledge_uncertain(), 1)
                self.assertEqual(service.ledger.states[request_id], "acknowledged")
            finally:
                service.close()

    def test_snapshot_captures_cursor_before_a_terminal_state_transition(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "phone_server.build_phone_snapshot", return_value={"status": {"state": "ready"}}
        ):
            service = PhoneAssistantService(
                runtime_factory=FakeRuntime,
                ledger=MemoryLedger(),
                state_path=Path(directory) / "state.json",
                workspace=Path(directory) / "workspace",
                stream_generation="snapshot-race-generation",
            )
            try:
                runtime = service.runtime
                runtime.is_busy = True
                delegate = service.events

                class CursorRaceHub:
                    fired = False

                    @property
                    def cursor(self):
                        boundary = delegate.cursor
                        if not self.fired:
                            self.fired = True
                            runtime.is_busy = False
                            runtime.event_handler(
                                {"kind": "turn_completed", "status": "completed"}
                            )
                        return boundary

                    def publish(self, event):
                        return delegate.publish(event)

                    def wait_after(self, after, *, timeout=20.0):
                        return delegate.wait_after(after, timeout=timeout)

                service.events = CursorRaceHub()
                snapshot = service.snapshot()
                replay, overflow = service.events.wait_after(
                    snapshot["event_cursor"], timeout=0
                )
                self.assertFalse(snapshot["busy"])
                self.assertFalse(overflow)
                self.assertEqual([item["kind"] for item in replay], ["turn.completed"])
            finally:
                service.close()

    def test_restart_changes_generation_and_exposes_persisted_review_gate(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "phone_server.build_phone_snapshot", return_value={"status": {"state": "ready"}}
        ):
            path = Path(directory) / "ledger.json"
            request_id = "a40b0dd8-597b-45ff-9270-3691434dbaee"
            RequestLedger(path).reserve(request_id)
            service = PhoneAssistantService(
                runtime_factory=FakeRuntime,
                ledger=RequestLedger(path),
                state_path=Path(directory) / "state.json",
                workspace=Path(directory) / "workspace",
                stream_generation="new-process-generation",
            )
            try:
                snapshot = service.snapshot()
                self.assertEqual(snapshot["stream_generation"], "new-process-generation")
                self.assertEqual(snapshot["unresolved_reserved_count"], 1)
                self.assertEqual(
                    service.submit(
                        "2cffc66d-cd54-494e-9184-9cd85cb09fa8",
                        "A different command must also remain blocked",
                    ),
                    (False, "uncertain_review_required"),
                )
                self.assertEqual(service.acknowledge_uncertain(), 1)
                self.assertEqual(service.snapshot()["unresolved_reserved_count"], 0)
                self.assertEqual(
                    service.submit(request_id, "Never replay this command"),
                    (False, "uncertain_acknowledged"),
                )
                self.assertEqual(service.runtime.sent, [])
            finally:
                service.close()


class RequestLedgerTests(unittest.TestCase):
    def test_ledger_persists_only_ids_and_true_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            ledger = RequestLedger(path)
            request_id = "30e3cb70-7d8c-4222-898c-58150222f95c"
            self.assertEqual(ledger.reserve(request_id), "reserved")
            self.assertEqual(ledger.lookup(request_id), "reserved")
            ledger.mark(request_id, "accepted")
            self.assertEqual(ledger.lookup(request_id), "accepted")
            encoded = path.read_text(encoding="utf-8")
            self.assertIn(request_id, encoded)
            self.assertIn('"state": "accepted"', encoded)
            self.assertNotIn("prompt", encoded)

    def test_legacy_accepted_ids_migrate_without_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            request_id = "4975a0ed-1c33-4cad-9a14-24be6867f25c"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "requests": [{"id": request_id, "accepted_at": "2026-08-31T10:00:00Z"}],
                    }
                ),
                encoding="utf-8",
            )
            ledger = RequestLedger(path)
            self.assertEqual(ledger.lookup(request_id), "accepted")
            self.assertEqual(ledger.reserve(request_id), "accepted")

    def test_reserved_review_acknowledgment_survives_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            request_id = "b6d54c7d-67d7-45d5-b813-b570493a3829"
            RequestLedger(path).reserve(request_id)
            reloaded = RequestLedger(path)
            self.assertEqual(reloaded.unresolved_reserved_count(), 1)
            self.assertEqual(reloaded.acknowledge_reserved(), 1)
            final = RequestLedger(path)
            self.assertEqual(final.unresolved_reserved_count(), 0)
            self.assertEqual(final.lookup(request_id), "acknowledged")
            self.assertIn('"version": 3', path.read_text(encoding="utf-8"))


class SessionStoreTests(unittest.TestCase):
    def test_session_is_bound_to_exact_login(self):
        store = SessionStore()
        session = store.issue("owner@example.test")
        self.assertIsNotNone(store.get(session.token, "OWNER@example.test"))
        self.assertIsNone(store.get(session.token, "someone-else@example.test"))


class PhoneServerConfigTests(unittest.TestCase):
    def test_config_requires_one_existing_exact_codex_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "codex.exe"
            executable.write_bytes(b"test executable placeholder")
            path = root / "phone.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "allowed_login": "owner@example.test",
                        "public_origin": "https://lox-pc.tail.test:10443",
                        "host": "127.0.0.1",
                        "port": 8794,
                        "codex_executable": str(executable),
                    }
                ),
                encoding="utf-8",
            )
            config = load_phone_server_config(path)
            self.assertEqual(config.codex_executable, str(executable.resolve()))

            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["codex_executable"] = str(root / "missing" / "codex.exe")
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(PhoneServerError):
                load_phone_server_config(path)


class HTTPBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        static = Path(self.temp_dir.name)
        (static / "index.html").write_text("<!doctype html><title>Asimut</title>", encoding="utf-8")
        (static / "build-info.json").write_text('{"version":"test-sha"}\n', encoding="utf-8")
        self.assistant = FakeAssistant()
        config = PhoneServerConfig(
            allowed_login="owner@example.test",
            public_origin="https://lox-pc.tail.test:10443",
        )
        self.app = PhoneApplication(
            config,
            assistant=self.assistant,
            static_dir=static,
            secure_cookie=False,
        )
        self.server = PhoneHTTPServer(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def request(self, method, path, *, headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        base = {
            "Host": "lox-pc.tail.test:10443",
            "Origin": "https://lox-pc.tail.test:10443",
            "Tailscale-User-Login": "owner@example.test",
            "Sec-Fetch-Site": "same-origin",
        }
        base.update(headers or {})
        connection.request(method, path, body=body, headers=base)
        response = connection.getresponse()
        payload = response.read()
        result = response.status, dict(response.getheaders()), payload
        connection.close()
        return result

    def open_session(self):
        status, headers, body = self.request(
            "POST",
            "/api/v1/session",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"client": "asimut-phone-v1"}),
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        return cookie, payload["csrf_token"]

    def test_session_bootstrap_and_message_require_full_boundary(self):
        cookie, csrf = self.open_session()
        status, headers, body = self.request(
            "GET", "/api/v1/bootstrap", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 200)
        bootstrap = json.loads(body)
        self.assertEqual(bootstrap["model"], "GPT-5.6 Terra · medium")
        self.assertEqual(bootstrap["stream_generation"], "test-stream-generation")
        self.assertEqual(bootstrap["unresolved_reserved_count"], 0)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

        request_id = "85400f93-b2ac-4dde-8665-31c26a2c0151"
        status, _, _ = self.request(
            "POST",
            "/api/v1/assistant/messages",
            headers={
                "Cookie": cookie,
                "X-Asimut-CSRF": csrf,
                "Content-Type": "application/json",
            },
            body=json.dumps({"client_message_id": request_id, "text": "What is tomorrow?"}),
        )
        self.assertEqual(status, 202)
        self.assertEqual(self.assistant.submissions, [(request_id, "What is tomorrow?")])

    def test_live_refresh_endpoint_is_explicit_and_invokes_asimut_refresh(self):
        cookie, csrf = self.open_session()
        headers = {
            "Cookie": cookie,
            "X-Asimut-CSRF": csrf,
            "Content-Type": "application/json",
        }
        status, _, body = self.request(
            "POST",
            "/api/v1/live-refresh",
            headers=headers,
            body=json.dumps({"scope": "plan", "force": True}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.assistant.live_refresh_calls, [("plan", True)])
        self.assertEqual(json.loads(body)["booker"]["status"]["state"], "ready")

        status, _, body = self.request(
            "POST",
            "/api/v1/live-refresh",
            headers=headers,
            body=json.dumps({"scope": "all", "force": True}),
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "invalid_live_refresh")

        self.assistant.live_refresh_error = PhoneServerError("private fixture detail")
        status, _, body = self.request(
            "POST",
            "/api/v1/live-refresh",
            headers=headers,
            body=json.dumps({"scope": "agenda", "force": False}),
        )
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["error"], "live_refresh_failed")
        self.assertNotIn("private", body.decode("utf-8"))

    def test_wrong_identity_origin_and_csrf_fail_closed(self):
        status, _, _ = self.request(
            "POST",
            "/api/v1/session",
            headers={
                "Origin": "https://evil.example",
                "Content-Type": "application/json",
            },
            body='{"client":"asimut-phone-v1"}',
        )
        self.assertEqual(status, 403)

        cookie, _csrf = self.open_session()
        status, _, _ = self.request(
            "POST",
            "/api/v1/assistant/stop",
            headers={
                "Cookie": cookie,
                "X-Asimut-CSRF": "wrong",
                "Content-Type": "application/json",
            },
            body="{}",
        )
        self.assertEqual(status, 403)

        status, _, _ = self.request(
            "GET",
            "/api/v1/bootstrap",
            headers={"Cookie": cookie, "Tailscale-User-Login": "other@example.test"},
        )
        self.assertEqual(status, 403)

    def test_sensitive_files_and_traversal_are_not_served(self):
        for path in (
            "/data/settings.json",
            "/.git/config",
            "/%2e%2e/data/settings.json",
            "/assets/app.js.map",
        ):
            status, _, _ = self.request("GET", path)
            self.assertEqual(status, 404, path)

    def test_health_is_minimal_and_static_shell_has_security_headers(self):
        status, headers, body = self.request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "ok", "assistant": "ready", "version": "test-sha"})
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Asimut", body)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_ledger_failure_is_service_unavailable_not_rate_limit(self):
        cookie, csrf = self.open_session()
        self.assistant.submit_error = PhoneServerError("C:\\private\\ledger failure")
        status, _, body = self.request(
            "POST",
            "/api/v1/assistant/messages",
            headers={
                "Cookie": cookie,
                "X-Asimut-CSRF": csrf,
                "Content-Type": "application/json",
            },
            body=json.dumps(
                {
                    "client_message_id": "6e1de85f-ed7e-4e62-a55e-4ccb2defa3cf",
                    "text": "What is tomorrow?",
                }
            ),
        )
        self.assertEqual(status, 503)
        payload = json.loads(body)
        self.assertEqual(payload["error"], "assistant_unavailable")
        self.assertNotIn("private", str(payload))

    def test_reserved_request_is_terminal_uncertain_and_never_replayed(self):
        cookie, csrf = self.open_session()
        self.assistant.submit_result = (True, "outcome_uncertain")
        status, _, body = self.request(
            "POST",
            "/api/v1/assistant/messages",
            headers={
                "Cookie": cookie,
                "X-Asimut-CSRF": csrf,
                "Content-Type": "application/json",
            },
            body=json.dumps(
                {
                    "client_message_id": "d2821451-10b5-46b8-8824-4877b7a2d326",
                    "text": "Cancel my exact reservation",
                }
            ),
        )
        self.assertEqual(status, 202)
        payload = json.loads(body)
        self.assertFalse(payload["accepted"])
        self.assertTrue(payload["duplicate"])
        self.assertTrue(payload["outcome_uncertain"])

    def test_persisted_uncertain_gate_requires_csrf_and_explicit_review(self):
        self.assistant.unresolved_reserved_count = 2
        cookie, csrf = self.open_session()
        status, _, body = self.request(
            "GET", "/api/v1/bootstrap", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["unresolved_reserved_count"], 2)

        status, _, _ = self.request(
            "POST",
            "/api/v1/assistant/uncertain/acknowledge",
            headers={
                "Cookie": cookie,
                "X-Asimut-CSRF": "wrong",
                "Content-Type": "application/json",
            },
            body=json.dumps({"reviewed": True}),
        )
        self.assertEqual(status, 403)
        self.assertEqual(self.assistant.unresolved_reserved_count, 2)

        status, _, _ = self.request(
            "POST",
            "/api/v1/assistant/uncertain/acknowledge",
            headers={
                "Cookie": cookie,
                "X-Asimut-CSRF": csrf,
                "Content-Type": "application/json",
            },
            body=json.dumps({"reviewed": False}),
        )
        self.assertEqual(status, 400)
        self.assertEqual(self.assistant.unresolved_reserved_count, 2)

        self.assistant.runtime.is_busy = True
        status, _, body = self.request(
            "POST",
            "/api/v1/assistant/uncertain/acknowledge",
            headers={
                "Cookie": cookie,
                "X-Asimut-CSRF": csrf,
                "Content-Type": "application/json",
            },
            body=json.dumps({"reviewed": True}),
        )
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["error"], "turn_in_progress")
        self.assertEqual(self.assistant.unresolved_reserved_count, 2)
        self.assistant.runtime.is_busy = False

        status, _, body = self.request(
            "POST",
            "/api/v1/assistant/uncertain/acknowledge",
            headers={
                "Cookie": cookie,
                "X-Asimut-CSRF": csrf,
                "Content-Type": "application/json",
            },
            body=json.dumps({"reviewed": True}),
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["acknowledged"], 2)
        self.assertEqual(payload["bootstrap"]["unresolved_reserved_count"], 0)

    def test_restart_generation_resets_stale_query_and_last_event_cursor(self):
        cookie, _csrf = self.open_session()
        self.assistant.events.publish({"kind": "activity", "text": "fresh process"})
        with mock.patch("phone_server.SSE_CONNECTION_SECONDS", 0.05):
            status, headers, body = self.request(
                "GET",
                "/api/v1/assistant/events?after=99&generation=old-process-generation",
                headers={"Cookie": cookie, "Last-Event-ID": "99"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/event-stream; charset=utf-8")
        frames = body.decode("utf-8")
        self.assertIn("id: 1", frames)
        self.assertIn('"stream_generation": "test-stream-generation"', frames)
        self.assertIn("fresh process", frames)


if __name__ == "__main__":
    unittest.main()
