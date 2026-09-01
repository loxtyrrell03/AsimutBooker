import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from assistant_runtime import (
    ASSISTANT_DEVELOPER_INSTRUCTIONS,
    AssistantRuntime,
    AssistantRuntimeError,
    BookerCodexToolDispatcher,
    assistant_contract_fingerprint,
    load_assistant_state,
    save_assistant_state,
)
from assistant_tools import AssistantToolError
from codex_chat import CodexChatController, CodexToolFailure


class FakeSurface:
    def __init__(self):
        self.calls = []
        self.cancelled = False
        self.turns_started = 0
        self.cancellation_context_clears = 0
        self.refresh_calls = []
        self.refresh_error = None
        self.refresh_context = {
            "local_now": "2026-08-31T16:00:00+01:00",
            "sections": {
                "agenda": {
                    "available": True,
                    "stale": False,
                    "reservations": [],
                }
            },
            "errors": {},
        }
        self.mutation_context_calls = 0
        self.mutation_contexts = [
            {
                "local_now": "2026-08-31T16:00:00+01:00",
                "sections": {"mutations": {"pending": []}},
                "errors": {},
            }
        ]
        self.tool_specs = [
            {
                "type": "function",
                "name": "get_booker_context",
                "description": "fixture",
                "inputSchema": {"type": "object"},
            }
        ]

    def dispatch(self, tool, arguments, *, user_request, progress):
        self.calls.append((tool, dict(arguments), user_request))
        progress("Reading context", "Validated fixture state")
        return {"ok": True}

    def cancel_active(self):
        self.cancelled = True

    def begin_turn(self):
        self.cancelled = False
        self.turns_started += 1

    def clear_cancellation_context(self):
        self.cancellation_context_clears += 1

    def current_mutation_context(self):
        index = min(self.mutation_context_calls, len(self.mutation_contexts) - 1)
        self.mutation_context_calls += 1
        return json.loads(json.dumps(self.mutation_contexts[index]))

    def refresh_read_only(self, scope, *, progress):
        self.refresh_calls.append(scope)
        progress("Refreshing live agenda", "Fixture Asimut data refreshed")
        if self.refresh_error is not None:
            raise self.refresh_error
        return json.loads(json.dumps(self.refresh_context))


class FakeController:
    instances = []

    def __init__(self, dispatcher, event_handler, **kwargs):
        self.dispatcher = dispatcher
        self.event_handler = event_handler
        self.kwargs = kwargs
        self.thread_id = None
        self.active_turn_id = None
        self.started = 0
        self.shutdown_called = False
        self.force_shutdown_called = False
        self.block_new_chat = False
        self.new_chat_entered = threading.Event()
        self.new_chat_release = threading.Event()
        self.new_chat_release.set()
        self.new_chat_calls = 0
        self.block_send_message = False
        self.send_message_entered = threading.Event()
        self.send_message_release = threading.Event()
        self.send_message_release.set()
        self.block_shutdown = False
        self.shutdown_entered = threading.Event()
        self.sent_messages = []
        self.sent_message_kwargs = []
        self.resumed_thread_ids = []
        self.user_input_answers = []
        type(self).instances.append(self)

    async def start(self):
        self.started += 1
        self.event_handler({"kind": "connection", "status": "ready", "title": "ready"})

    async def new_chat(self):
        if self.block_new_chat:
            self.new_chat_entered.set()
            await asyncio.to_thread(self.new_chat_release.wait)
        self.new_chat_calls += 1
        self.thread_id = (
            f"thread-{len(type(self).instances)}-{self.new_chat_calls}"
        )
        return self.thread_id

    async def resume(self, thread_id):
        self.resumed_thread_ids.append(thread_id)
        self.thread_id = thread_id
        return thread_id

    async def send_message(self, text, **kwargs):
        if self.block_send_message:
            self.send_message_entered.set()
            await asyncio.to_thread(self.send_message_release.wait)
        self.sent_messages.append(text)
        self.sent_message_kwargs.append(dict(kwargs))
        self.active_turn_id = "turn-1"
        self.event_handler({"kind": "turn_started", "turn_id": "turn-1"})
        self.event_handler(
            {
                "kind": "reasoning_summary_delta",
                "item_id": "reason-1",
                "text": "Checking the fixture.",
            }
        )
        self.event_handler(
            {
                "kind": "assistant_delta",
                "item_id": "answer-1",
                "text": "Your fixture plan is ready.",
            }
        )
        self.active_turn_id = None
        self.event_handler(
            {"kind": "turn_completed", "turn_id": "turn-1", "status": "completed"}
        )
        return "turn-1"

    async def interrupt(self):
        self.active_turn_id = None
        return True

    async def respond_to_user_input(self, request_id, answers):
        self.user_input_answers.append((request_id, answers))

    async def shutdown(self):
        self.shutdown_called = True
        if self.block_shutdown:
            self.shutdown_entered.set()
            await asyncio.Future()

    async def force_shutdown(self):
        self.force_shutdown_called = True


class AssistantStateTests(unittest.TestCase):
    def test_state_round_trip_is_strict_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "assistant.json"
            state = {
                "version": 2,
                "contract_fingerprint": "a" * 64,
                "thread_id": "thread-1",
                "messages": [
                    {
                        "role": "user",
                        "text": "Plan next week",
                        "created_at": "2026-08-31T10:00:00Z",
                    }
                ],
            }
            save_assistant_state(state, path)
            self.assertEqual(load_assistant_state(path), state)

            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(AssistantRuntimeError, "unexpected"):
                load_assistant_state(path)

    def test_legacy_state_migrates_without_losing_visible_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "assistant.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "thread_id": "stale-thread",
                        "messages": [
                            {
                                "role": "user",
                                "text": "Cancel all my reservations",
                                "created_at": "2026-08-31T18:56:50Z",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            migrated = load_assistant_state(path)

            self.assertEqual(migrated["version"], 2)
            self.assertIsNone(migrated["contract_fingerprint"])
            self.assertEqual(migrated["thread_id"], "stale-thread")
            self.assertEqual(
                migrated["messages"][0]["text"], "Cancel all my reservations"
            )

    def test_contract_change_starts_fresh_thread_and_keeps_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "assistant.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "thread_id": "stale-thread",
                        "messages": [
                            {
                                "role": "user",
                                "text": "Cancel all my reservations",
                                "created_at": "2026-08-31T18:56:50Z",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            events = []
            surface = FakeSurface()
            FakeController.instances = []
            runtime = AssistantRuntime(
                events.append,
                tool_surface=surface,
                state_path=path,
                controller_factory=FakeController,
                assistant_workspace=Path(directory) / "workspace",
            )
            try:
                runtime.start()
                deadline = time.monotonic() + 3
                while (
                    not FakeController.instances
                    or FakeController.instances[0].thread_id is None
                ):
                    if time.monotonic() >= deadline:
                        self.fail("Timed out waiting for replacement assistant thread")
                    time.sleep(0.01)

                controller = FakeController.instances[0]
                self.assertNotEqual(controller.thread_id, "stale-thread")
                migrated = load_assistant_state(path)
                self.assertEqual(
                    migrated["contract_fingerprint"],
                    assistant_contract_fingerprint(surface.tool_specs),
                )
                self.assertEqual(
                    migrated["messages"][0]["text"],
                    "Cancel all my reservations",
                )
                self.assertIn(
                    "fresh reasoning context",
                    migrated["messages"][-1]["text"],
                )
                self.assertTrue(
                    any(
                        event.get("status") == "contract_refreshed"
                        for event in events
                    )
                )
            finally:
                runtime.close()

            replacement_thread = load_assistant_state(path)["thread_id"]
            FakeController.instances = []
            second_runtime = AssistantRuntime(
                lambda event: None,
                tool_surface=surface,
                state_path=path,
                controller_factory=FakeController,
                assistant_workspace=Path(directory) / "workspace-2",
            )
            try:
                second_runtime.start()
                deadline = time.monotonic() + 3
                while (
                    not FakeController.instances
                    or FakeController.instances[0].thread_id is None
                ):
                    if time.monotonic() >= deadline:
                        self.fail("Timed out waiting for migrated thread resume")
                    time.sleep(0.01)
                self.assertEqual(
                    FakeController.instances[0].thread_id,
                    replacement_thread,
                )
                self.assertEqual(
                    sum(
                        "fresh reasoning context" in item["text"]
                        for item in load_assistant_state(path)["messages"]
                    ),
                    1,
                )
            finally:
                second_runtime.close()

    def test_matching_contract_resumes_existing_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "assistant.json"
            surface = FakeSurface()
            fingerprint = assistant_contract_fingerprint(surface.tool_specs)
            save_assistant_state(
                {
                    "version": 2,
                    "contract_fingerprint": fingerprint,
                    "thread_id": "current-thread",
                    "messages": [],
                },
                path,
            )
            FakeController.instances = []
            runtime = AssistantRuntime(
                lambda event: None,
                tool_surface=surface,
                state_path=path,
                controller_factory=FakeController,
                assistant_workspace=Path(directory) / "workspace",
            )
            try:
                runtime.start()
                deadline = time.monotonic() + 3
                while (
                    not FakeController.instances
                    or FakeController.instances[0].thread_id is None
                ):
                    if time.monotonic() >= deadline:
                        self.fail("Timed out waiting for resumed assistant thread")
                    time.sleep(0.01)
                self.assertEqual(
                    FakeController.instances[0].thread_id, "current-thread"
                )
            finally:
                runtime.close()

    def test_contract_fingerprint_covers_core_prompt_app_prompt_and_tools(self):
        surface = FakeSurface()
        baseline = assistant_contract_fingerprint(surface.tool_specs)

        changed_tools = json.loads(json.dumps(surface.tool_specs))
        changed_tools[0]["description"] = "changed fixture"
        self.assertNotEqual(
            assistant_contract_fingerprint(changed_tools), baseline
        )
        with mock.patch(
            "assistant_runtime.ASSISTANT_DEVELOPER_INSTRUCTIONS",
            ASSISTANT_DEVELOPER_INSTRUCTIONS + "\nChanged app contract.",
        ):
            self.assertNotEqual(
                assistant_contract_fingerprint(surface.tool_specs), baseline
            )
        with mock.patch(
            "codex_chat._CORE_DEVELOPER_INSTRUCTIONS",
            "Changed core contract.",
        ):
            self.assertNotEqual(
                assistant_contract_fingerprint(surface.tool_specs), baseline
            )

        contract_variants = {
            "approval_policy": "on-request",
            "thread_sandbox": "workspace-write",
            "reasoning_summary": "detailed",
            "turn_sandbox_policy": {"type": "workspaceWrite"},
            "thread_config": {"features": {"shell_tool": True}},
        }
        for field, replacement in contract_variants.items():
            with self.subTest(field=field):
                changed_contract = CodexChatController.effective_thread_contract()
                changed_contract[field] = replacement
                with mock.patch.object(
                    CodexChatController,
                    "effective_thread_contract",
                    return_value=changed_contract,
                ):
                    self.assertNotEqual(
                        assistant_contract_fingerprint(surface.tool_specs), baseline
                    )

    def test_malformed_contract_fingerprint_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "assistant.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "contract_fingerprint": "not-a-fingerprint",
                        "thread_id": "stale-thread",
                        "messages": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                AssistantRuntimeError, "fingerprint is invalid"
            ):
                load_assistant_state(path)

    def test_failed_contract_migration_save_never_resumes_stale_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "assistant.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "thread_id": "stale-thread",
                        "messages": [],
                    }
                ),
                encoding="utf-8",
            )
            FakeController.instances = []
            with mock.patch(
                "assistant_runtime.save_assistant_state",
                side_effect=AssistantRuntimeError("fixture write failed"),
            ):
                runtime = AssistantRuntime(
                    lambda event: None,
                    tool_surface=FakeSurface(),
                    state_path=path,
                    controller_factory=FakeController,
                    assistant_workspace=Path(directory) / "workspace",
                )
            try:
                runtime.start()
                deadline = time.monotonic() + 3
                while (
                    not FakeController.instances
                    or FakeController.instances[0].thread_id is None
                ):
                    if time.monotonic() >= deadline:
                        self.fail("Timed out waiting for safe replacement thread")
                    time.sleep(0.01)
                self.assertNotEqual(
                    FakeController.instances[0].thread_id,
                    "stale-thread",
                )
            finally:
                runtime.close()


class BookerDispatcherTests(unittest.TestCase):
    def test_active_user_request_and_progress_are_forwarded(self):
        surface = FakeSurface()
        dispatcher = BookerCodexToolDispatcher(surface)
        dispatcher.set_active_user_request("Set Friday to two hours")
        updates = []

        result = dispatcher.dispatch(
            None,
            "get_booker_context",
            {"sections": ["agenda"]},
            {"emit_progress": updates.append},
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(surface.calls[0][2], "Set Friday to two hours")
        self.assertEqual(updates[0]["title"], "Reading context")
        self.assertEqual(updates[0]["text"], "Validated fixture state")

    def test_tool_failures_use_action_language_not_booking_confirmation(self):
        surface = FakeSurface()
        surface.dispatch = mock.Mock(side_effect=AssistantToolError("fixture rejected"))
        dispatcher = BookerCodexToolDispatcher(surface)

        with self.assertRaises(CodexToolFailure) as failure:
            dispatcher.dispatch(None, "fixture", {}, {})

        self.assertEqual(failure.exception.code, "booker_action_rejected")
        self.assertNotIn("confirm", failure.exception.code)


class AssistantRuntimeTests(unittest.TestCase):
    def setUp(self):
        FakeController.instances.clear()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.events = []
        self.events_lock = threading.Lock()

        def receive(event):
            with self.events_lock:
                self.events.append(dict(event))

        self.surface = FakeSurface()
        self.runtime = AssistantRuntime(
            receive,
            tool_surface=self.surface,
            state_path=Path(self.temp_dir.name) / "assistant.json",
            controller_factory=FakeController,
            assistant_workspace=Path(self.temp_dir.name) / "workspace",
        )

    def tearDown(self):
        self.runtime.close()
        self.temp_dir.cleanup()

    def wait_for(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out waiting for assistant runtime")

    def test_send_streams_summaries_and_persists_ordered_transcript(self):
        self.assertTrue(self.runtime.start())
        self.wait_for(lambda: FakeController.instances and FakeController.instances[0].thread_id)

        self.assertTrue(self.runtime.send("Explain my plan for tomorrow"))
        self.wait_for(lambda: not self.runtime.is_busy)

        state = load_assistant_state(Path(self.temp_dir.name) / "assistant.json")
        self.assertEqual([item["role"] for item in state["messages"]], ["user", "assistant"])
        self.assertEqual(state["messages"][1]["text"], "Your fixture plan is ready.")
        self.assertEqual(
            state["contract_fingerprint"],
            assistant_contract_fingerprint(self.surface.tool_specs),
        )
        kinds = [event["kind"] for event in self.events]
        self.assertIn("reasoning_summary_delta", kinds)
        self.assertIn("assistant_delta", kinds)
        self.assertIn("turn_completed", kinds)

        controller = FakeController.instances[0]
        self.assertEqual(controller.kwargs["dynamic_tools"], self.surface.tool_specs)
        self.assertEqual(controller.kwargs["tool_timeout"], 25 * 60)
        self.assertIn("only authorized data and action surface", controller.kwargs["developer_instructions"])
        self.assertEqual(self.runtime.model_label, "GPT-5.6 Terra · medium")
        self.assertEqual(self.surface.turns_started, 1)
        self.assertEqual(self.surface.refresh_calls, ["agenda"])
        live_preflight = json.loads(
            controller.sent_message_kwargs[0]["additional_context"]
            ["live_agenda_preflight"]["value"]
        )
        self.assertTrue(live_preflight["refreshed"])
        self.assertFalse(
            live_preflight["snapshot"]["sections"]["agenda"]["stale"]
        )

    def test_fresh_mutation_state_is_rebuilt_and_injected_on_every_turn(self):
        self.surface.mutation_contexts = [
            {
                "local_now": "2026-08-31T15:00:00+01:00",
                "sections": {
                    "mutations": {
                        "pending": [
                            {
                                "id": "58c047cf-eff8-44ab-9e4d-1b1d115cfba0",
                                "kind": "cancel",
                                "status": "pending",
                            }
                        ]
                    }
                },
                "errors": {},
            },
            {
                "local_now": "2026-08-31T16:07:00+01:00",
                "sections": {"mutations": {"pending": []}},
                "errors": {},
            },
        ]
        self.runtime.start()
        self.wait_for(lambda: FakeController.instances and FakeController.instances[0].thread_id)

        self.assertTrue(self.runtime.send("What is blocking changes?"))
        self.wait_for(lambda: not self.runtime.is_busy)
        self.assertTrue(self.runtime.send("Cancel my bookings tomorrow"))
        self.wait_for(lambda: not self.runtime.is_busy)

        controller = FakeController.instances[0]
        contracts = [
            json.loads(
                item["additional_context"]["current_mutation_state"]["value"]
            )
            for item in controller.sent_message_kwargs
        ]
        self.assertEqual(
            contracts[0]["snapshot"]["sections"]["mutations"]["pending"][0]["status"],
            "pending",
        )
        self.assertEqual(
            contracts[1]["snapshot"]["sections"]["mutations"]["pending"],
            [],
        )
        self.assertIn("supersedes", contracts[1]["authority"])
        self.assertTrue(contracts[1]["available"])
        self.assertEqual(self.surface.mutation_context_calls, 2)
        self.assertEqual(self.surface.refresh_calls, ["agenda", "agenda"])

    def test_failed_preflight_is_explicitly_non_authoritative(self):
        self.surface.refresh_error = AssistantToolError("fixture refresh failed")
        self.runtime.start()
        self.wait_for(lambda: FakeController.instances and FakeController.instances[0].thread_id)

        self.assertTrue(self.runtime.send("Cancel all my upcoming reservations"))
        self.wait_for(lambda: not self.runtime.is_busy)

        controller = FakeController.instances[0]
        preflight = json.loads(
            controller.sent_message_kwargs[0]["additional_context"]
            ["live_agenda_preflight"]["value"]
        )
        self.assertFalse(preflight["refreshed"])
        self.assertIn("cannot prove", preflight["authority"])
        self.assertEqual(preflight["snapshot"], {})
        self.assertTrue(
            any(
                event.get("title") == "Live agenda refresh unavailable"
                for event in self.events
            )
        )

    def test_unavailable_mutation_state_is_never_labelled_fresh(self):
        self.surface.mutation_contexts = [
            {
                "local_now": "2026-08-31T16:07:00+01:00",
                "sections": {},
                "errors": {"mutations": "fixture receipt read failed"},
            }
        ]
        self.runtime.start()
        self.wait_for(lambda: FakeController.instances and FakeController.instances[0].thread_id)

        self.assertTrue(self.runtime.send("Cancel my bookings tomorrow"))
        self.wait_for(lambda: not self.runtime.is_busy)

        controller = FakeController.instances[0]
        contract = json.loads(
            controller.sent_message_kwargs[0]["additional_context"][
                "current_mutation_state"
            ]["value"]
        )
        self.assertFalse(contract["available"])
        self.assertIn("unavailable", contract["authority"])
        self.assertNotIn("Fresh host-built state", contract["authority"])
        self.assertIn("mutations", contract["snapshot"]["errors"])

    def test_prompt_contract_separates_reconfirmation_from_every_supported_action(self):
        normalized_instructions = " ".join(ASSISTANT_DEVELOPER_INSTRUCTIONS.split())
        self.assertIn(
            "never a prerequisite for cancellation, editing, extension",
            normalized_instructions,
        )
        self.assertIn(
            "supersedes claims in prior chat turns",
            normalized_instructions,
        )
        self.assertIn(
            "Never say a mutation is pending based only on conversation history",
            normalized_instructions,
        )
        self.assertIn(
            "immediately preceding focused clarification",
            normalized_instructions,
        )
        self.assertIn(
            '"this week", "the next week", and "over the next week" mean the rolling next seven days',
            normalized_instructions,
        )
        self.assertIn("Use one inclusive range selection", normalized_instructions)
        self.assertIn(
            '"next week" means the next Monday-Sunday calendar week',
            normalized_instructions,
        )
        self.assertIn(
            "Saved preferences are autonomous defaults, not immutable site rules",
            normalized_instructions,
        )
        self.assertIn(
            "Do not ask whether to keep the previous preference",
            normalized_instructions,
        )

    def test_sensitive_prompt_is_not_duplicated_in_local_transcript(self):
        self.runtime.start()
        self.wait_for(lambda: FakeController.instances and FakeController.instances[0].thread_id)
        self.runtime.send("password: definitely-do-not-store")
        self.wait_for(lambda: not self.runtime.is_busy)

        encoded = json.dumps(
            load_assistant_state(Path(self.temp_dir.name) / "assistant.json")
        )
        self.assertNotIn("definitely-do-not-store", encoded)

    def test_obvious_secrets_are_rejected_before_controller_send(self):
        self.runtime.start()
        self.wait_for(lambda: FakeController.instances and FakeController.instances[0].thread_id)
        controller = FakeController.instances[0]

        for prompt in (
            "my password is definitely-do-not-send",
            "OTP 123456",
            "passcode: 998877",
        ):
            self.assertFalse(self.runtime.send(prompt))

        self.assertEqual(controller.sent_messages, [])
        self.assertFalse(self.runtime.is_busy)
        encoded = json.dumps(
            load_assistant_state(Path(self.temp_dir.name) / "assistant.json")
        )
        self.assertNotIn("definitely-do-not-send", encoded)
        self.assertNotIn("123456", encoded)
        self.assertNotIn("998877", encoded)
        self.assertTrue(
            any(
                event.get("title") == "Sensitive information was not sent"
                for event in self.events
            )
        )

    def test_new_chat_clears_local_history_and_stop_reaches_surface(self):
        self.runtime.start()
        self.wait_for(lambda: FakeController.instances and FakeController.instances[0].thread_id)
        self.runtime.send("Explain tomorrow")
        self.wait_for(lambda: not self.runtime.is_busy)

        self.runtime._busy = True
        self.runtime._active_turn_generation = self.runtime._thread_generation
        self.runtime._active_turn_token = "fixture-stop-turn"
        self.assertTrue(self.runtime.stop())
        self.wait_for(lambda: not self.runtime.is_busy)
        self.assertTrue(self.surface.cancelled)

        self.assertTrue(self.runtime.new_chat())
        self.wait_for(
            lambda: load_assistant_state(Path(self.temp_dir.name) / "assistant.json")["messages"] == []
        )
        self.assertEqual(self.surface.cancellation_context_clears, 1)

    def test_new_chat_stays_busy_and_rejects_send_until_thread_switch_finishes(self):
        self.runtime.start()
        self.wait_for(lambda: FakeController.instances and FakeController.instances[0].thread_id)
        controller = FakeController.instances[0]
        controller.block_new_chat = True
        controller.new_chat_release.clear()

        self.assertTrue(self.runtime.new_chat())
        self.assertTrue(controller.new_chat_entered.wait(timeout=2))
        self.assertTrue(self.runtime.is_busy)
        self.assertFalse(self.runtime.send("This must not target the old thread"))
        self.assertEqual(controller.sent_messages, [])

        controller.new_chat_release.set()
        self.wait_for(lambda: not self.runtime.is_busy)
        self.assertTrue(
            any(
                event.get("kind") == "busy" and event.get("status") == "ready"
                for event in self.events
            )
        )

    def test_new_chat_pointer_failure_cannot_resurrect_old_thread(self):
        self.runtime.start()
        self.wait_for(
            lambda: FakeController.instances
            and FakeController.instances[0].thread_id
        )
        controller = FakeController.instances[0]
        old_thread = controller.thread_id
        original_save = save_assistant_state
        writes = 0

        def fail_new_pointer(state, path):
            nonlocal writes
            writes += 1
            if writes == 1:
                return original_save(state, path)
            raise AssistantRuntimeError("fixture pointer write failed")

        with mock.patch(
            "assistant_runtime.save_assistant_state",
            side_effect=fail_new_pointer,
        ):
            self.assertTrue(self.runtime.new_chat())
            self.wait_for(lambda: not self.runtime.is_busy)

        self.assertNotEqual(controller.thread_id, old_thread)
        durable = load_assistant_state(
            Path(self.temp_dir.name) / "assistant.json"
        )
        self.assertIsNone(durable["thread_id"])
        self.assertEqual(durable["messages"], [])
        self.assertTrue(
            any(
                event.get("title")
                == "Asimut Assistant could not continue"
                for event in self.events
            )
        )

    def test_inflight_send_cannot_restore_old_pointer_during_new_chat_failure(self):
        state_path = Path(self.temp_dir.name) / "assistant.json"
        self.runtime.start()
        self.wait_for(
            lambda: FakeController.instances
            and FakeController.instances[0].thread_id
        )
        controller = FakeController.instances[0]
        old_thread = controller.thread_id
        controller.block_send_message = True
        controller.send_message_release.clear()
        controller.block_new_chat = True
        controller.new_chat_release.clear()

        self.assertTrue(self.runtime.send("Explain tomorrow"))
        self.assertTrue(controller.send_message_entered.wait(timeout=2))

        original_save = save_assistant_state

        def fail_replacement_pointer(state, path):
            thread_id = state.get("thread_id")
            if isinstance(thread_id, str) and thread_id != old_thread:
                raise AssistantRuntimeError("fixture replacement pointer failed")
            return original_save(state, path)

        with mock.patch(
            "assistant_runtime.save_assistant_state",
            side_effect=fail_replacement_pointer,
        ):
            self.assertTrue(self.runtime.new_chat())
            self.assertTrue(controller.new_chat_entered.wait(timeout=2))
            self.assertIsNone(load_assistant_state(state_path)["thread_id"])

            controller.send_message_release.set()
            self.wait_for(lambda: bool(controller.sent_messages))
            self.assertIsNone(load_assistant_state(state_path)["thread_id"])

            controller.new_chat_release.set()
            self.wait_for(lambda: not self.runtime.is_busy)

        durable = load_assistant_state(state_path)
        self.assertIsNone(durable["thread_id"])
        self.assertEqual(durable["messages"], [])

        replacement = AssistantRuntime(
            lambda event: None,
            tool_surface=self.surface,
            state_path=state_path,
            controller_factory=FakeController,
            assistant_workspace=Path(self.temp_dir.name) / "replacement-workspace",
        )
        try:
            replacement.start()
            self.wait_for(
                lambda: len(FakeController.instances) >= 2
                and FakeController.instances[-1].thread_id is not None
            )
            replacement_controller = FakeController.instances[-1]
            self.assertEqual(replacement_controller.resumed_thread_ids, [])
            self.assertNotEqual(replacement_controller.thread_id, old_thread)
        finally:
            replacement.close()

    def test_close_forces_controller_shutdown_after_graceful_timeout(self):
        self.runtime.start()
        self.wait_for(lambda: FakeController.instances and FakeController.instances[0].thread_id)
        controller = FakeController.instances[0]
        controller.block_shutdown = True

        self.runtime.close(timeout=0.05)

        self.assertTrue(controller.shutdown_entered.is_set())
        self.assertTrue(controller.force_shutdown_called)
        self.assertFalse(self.runtime._thread.is_alive())

    def test_form_style_clarification_is_redirected_into_normal_chat(self):
        self.runtime.start()
        self.wait_for(lambda: FakeController.instances and FakeController.instances[0].thread_id)
        controller = FakeController.instances[0]
        self.runtime._busy = True
        self.runtime._active_turn_generation = self.runtime._thread_generation
        self.runtime._active_turn_token = "fixture-clarification-turn"

        self.runtime._handle_controller_event(
            {
                "kind": "user_input_request",
                "request_id": "request-1",
                "questions": [{"id": "weekend_hours", "question": "How many hours?"}],
            }
        )

        self.wait_for(lambda: bool(controller.user_input_answers))
        request_id, answers = controller.user_input_answers[0]
        self.assertEqual(request_id, "request-1")
        self.assertIn("ordinary assistant message", answers["weekend_hours"])
        self.assertTrue(
            any(event.get("title") == "Preparing a clarification" for event in self.events)
        )

    def test_interrupted_turn_does_not_persist_a_partial_assistant_answer(self):
        self.runtime._busy = True
        self.runtime._active_turn_generation = self.runtime._thread_generation
        self.runtime._active_turn_token = "fixture-interrupted-turn"
        self.runtime._assistant_fragments = ["Partial answer that was stopped."]
        self.runtime._handle_controller_event(
            {"kind": "turn_completed", "turn_id": "turn-1", "status": "interrupted"}
        )

        self.assertFalse(self.runtime.is_busy)
        state = load_assistant_state(Path(self.temp_dir.name) / "assistant.json")
        self.assertEqual(state["messages"], [])

    def test_separate_assistant_updates_restore_as_separate_paragraphs(self):
        self.runtime._busy = True
        self.runtime._active_turn_generation = self.runtime._thread_generation
        self.runtime._active_turn_token = "fixture-separate-updates-turn"
        self.runtime._handle_controller_event(
            {"kind": "assistant_delta", "item_id": "progress-1", "text": "I’ll check."}
        )
        self.runtime._handle_controller_event(
            {"kind": "assistant_delta", "item_id": "answer-1", "text": "Two bookings found."}
        )
        self.runtime._handle_controller_event(
            {"kind": "turn_completed", "turn_id": "turn-1", "status": "completed"}
        )

        state = load_assistant_state(Path(self.temp_dir.name) / "assistant.json")
        self.assertEqual(state["messages"][0]["text"], "I’ll check.\n\nTwo bookings found.")

    def test_commentary_stays_in_progress_and_only_final_answer_is_persisted(self):
        self.runtime._busy = True
        self.runtime._active_turn_generation = self.runtime._thread_generation
        self.runtime._active_turn_token = "fixture-commentary-turn"
        self.runtime._handle_controller_event(
            {
                "kind": "assistant_delta",
                "item_id": "commentary-1",
                "phase": "commentary",
                "first_delta": True,
                "text": "I’ll check the agenda first.",
            }
        )
        self.runtime._handle_controller_event(
            {
                "kind": "assistant_delta",
                "item_id": "answer-1",
                "phase": "final_answer",
                "first_delta": True,
                "text": "Tomorrow is clear.",
            }
        )
        self.runtime._handle_controller_event(
            {"kind": "turn_completed", "turn_id": "turn-1", "status": "completed"}
        )

        state = load_assistant_state(Path(self.temp_dir.name) / "assistant.json")
        self.assertEqual(state["messages"][-1]["text"], "Tomorrow is clear.")
        commentary = [event for event in self.events if event["kind"] == "commentary_delta"]
        self.assertEqual(commentary[-1]["text"], "I’ll check the agenda first.")
        self.assertTrue(
            any(
                event["kind"] == "assistant_delta"
                and event.get("phase") == "final_answer"
                for event in self.events
            )
        )

    def test_old_completion_cannot_clear_a_new_runtime_turn(self):
        self.runtime._busy = True
        self.runtime._active_turn_generation = self.runtime._thread_generation
        self.runtime._active_turn_token = "fixture-new-runtime-turn"
        self.runtime._active_turn_id = "turn-2"
        self.runtime._active_prompt = "New request"
        self.runtime._assistant_fragments = ["New response in progress"]

        self.runtime._handle_controller_event(
            {"kind": "turn_completed", "turn_id": "turn-1", "status": "interrupted"}
        )

        self.assertTrue(self.runtime.is_busy)
        self.assertEqual(self.runtime._active_turn_id, "turn-2")
        self.assertEqual(self.runtime._active_prompt, "New request")
        self.assertEqual(self.runtime._assistant_fragments, ["New response in progress"])

    def test_stale_operation_failure_and_controller_event_cannot_clear_new_turn(self):
        old_generation = self.runtime._thread_generation
        old_token = "old-turn"
        self.runtime._thread_generation += 1
        self.runtime._busy = True
        self.runtime._active_turn_generation = self.runtime._thread_generation
        self.runtime._active_turn_token = "new-turn"
        self.runtime._active_prompt = "New request"
        self.runtime._assistant_fragments = ["New response in progress"]
        current_source = object()
        self.runtime._controller_event_source = current_source

        self.runtime._handle_operation_failure(
            "send",
            RuntimeError("late old failure"),
            expected_generation=old_generation,
            turn_token=old_token,
        )
        self.runtime._handle_controller_event(
            {"kind": "connection", "status": "failed"},
            object(),
        )

        self.assertTrue(self.runtime.is_busy)
        self.assertEqual(self.runtime._active_turn_token, "new-turn")
        self.assertEqual(self.runtime._active_prompt, "New request")
        self.assertEqual(self.runtime._assistant_fragments, ["New response in progress"])

    def test_transcript_write_failure_never_leaves_the_ui_busy(self):
        self.runtime._busy = True
        self.runtime._active_turn_generation = self.runtime._thread_generation
        self.runtime._active_turn_token = "fixture-write-failure-turn"
        self.runtime._assistant_fragments = ["A complete answer."]
        with mock.patch(
            "assistant_runtime.save_assistant_state",
            side_effect=AssistantRuntimeError("disk unavailable"),
        ):
            self.runtime._handle_controller_event(
                {"kind": "turn_completed", "turn_id": "turn-1", "status": "completed"}
            )

        self.assertFalse(self.runtime.is_busy)
        self.assertTrue(
            any(event.get("title") == "Chat history could not be saved" for event in self.events)
        )


if __name__ == "__main__":
    unittest.main()
