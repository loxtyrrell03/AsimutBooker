import asyncio
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from codex_chat import (
    CODEX_MODEL,
    CODEX_REASONING_EFFORT,
    CodexChatController,
    CodexConfigurationError,
    CodexProtocolError,
    CodexRequestTimeout,
)


_FAKE_SERVER = r'''
import json
import os
import sys
import time

MODE = os.environ.get("FAKE_CODEX_MODE", "normal")
thread_count = 0
turn_count = 0
waiting = None


def send(message, *, split=False):
    encoded = json.dumps(message, separators=(",", ":")) + "\n"
    if split:
        midpoint = max(1, len(encoded) // 2)
        sys.stdout.write(encoded[:midpoint])
        sys.stdout.flush()
        time.sleep(0.01)
        sys.stdout.write(encoded[midpoint:])
    else:
        sys.stdout.write(encoded)
    sys.stdout.flush()


def fail(request_id, message):
    send({"id": request_id, "error": {"code": -32602, "message": message}})


def thread_object(thread_id, *, turns=None):
    return {
        "id": thread_id,
        "extra": None,
        "sessionId": "session-1",
        "forkedFromId": None,
        "parentThreadId": None,
        "preview": "",
        "ephemeral": False,
        "section": None,
        "sectionEnteredAt": None,
        "projectId": None,
        "historyMode": "paginated",
        "modelProvider": "openai",
        "createdAt": 1,
        "updatedAt": 1,
        "recencyAt": 1,
        "status": {"type": "idle"},
        "path": "fake-rollout.jsonl",
        "cwd": os.getcwd(),
        "cliVersion": "fake",
        "source": "appServer",
        "canAcceptDirectInput": True,
        "threadSource": None,
        "agentNickname": None,
        "agentRole": None,
        "gitInfo": None,
        "name": None,
        "turns": turns or [],
    }


def turn_object(turn_id, status="inProgress", items=None):
    return {
        "id": turn_id,
        "items": items or [],
        "itemsView": "full",
        "status": status,
        "error": None,
        "startedAt": 1,
        "completedAt": 2 if status != "inProgress" else None,
        "durationMs": 1 if status != "inProgress" else None,
    }


def thread_result(thread_id, *, initial_page=None):
    result = {
        "thread": thread_object(thread_id),
        "model": "gpt-5.6-terra",
        "modelProvider": "openai",
        "serviceTier": None,
        "cwd": os.getcwd(),
        "runtimeWorkspaceRoots": [os.getcwd()],
        "instructionSources": [],
        "approvalPolicy": "never",
        "approvalsReviewer": "user",
        "sandbox": {"type": "readOnly", "networkAccess": False},
        "activePermissionProfile": None,
        "reasoningEffort": "medium",
        "multiAgentMode": "explicitRequestOnly",
    }
    if initial_page is not None:
        result.update({
            "initialTurnsPage": initial_page,
            "turnsBackwardsCursor": None,
            "itemsBackwardsCursor": None,
        })
    return result


for raw in sys.stdin:
    try:
        message = json.loads(raw)
    except Exception:
        sys.exit(30)

    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        capabilities = message.get("params", {}).get("capabilities", {})
        if capabilities.get("experimentalApi") is not True:
            fail(request_id, "experimental API not enabled")
            continue
        send({
            "id": request_id,
            "result": {
                "userAgent": "Codex Desktop/0.151.0-alpha.7.2 fake",
                "codexHome": os.getcwd(),
                "platformFamily": "windows",
                "platformOs": "windows",
            },
        })
        continue

    if method == "initialized":
        send({"method": "remoteControl/status/changed", "params": {"status": "off"}})
        continue

    if method == "model/list":
        if MODE == "timeout":
            continue
        if MODE == "malformed":
            sys.stdout.write("{not-json\n")
            sys.stdout.flush()
            continue
        if MODE == "eof":
            sys.exit(0)
        models = [] if MODE == "bad_model" else [{
            "id": "gpt-5.6-terra",
            "model": "gpt-5.6-terra",
            "displayName": "GPT-5.6-Terra",
            "description": "fake",
            "hidden": False,
            "supportedReasoningEfforts": [
                {"reasoningEffort": "low", "description": ""},
                {"reasoningEffort": "medium", "description": ""},
            ],
            "defaultReasoningEffort": "medium",
            "inputModalities": ["text"],
            "supportsPersonality": False,
            "additionalSpeedTiers": [],
            "serviceTiers": [],
            "defaultServiceTier": None,
            "isDefault": False,
        }]
        send({"id": request_id, "result": {"data": models, "nextCursor": None}})
        continue

    if method == "thread/start":
        params = message.get("params", {})
        config = params.get("config", {})
        exact = (
            params.get("model") == "gpt-5.6-terra"
            and params.get("allowProviderModelFallback") is False
            and params.get("approvalPolicy") == "never"
            and params.get("sandbox") == "read-only"
            and config.get("model_reasoning_effort") == "medium"
            and config.get("features", {}).get("shell_tool") is False
            and config.get("features", {}).get("multi_agent") is False
            and config.get("tools", {}).get("web_search") is False
            and config.get("agents", {}).get("enabled") is False
            and params.get("historyMode") == "paginated"
            and params.get("ephemeral") is False
            and params.get("environments") == []
            and isinstance(params.get("dynamicTools"), list)
        )
        if not exact:
            fail(request_id, "unsafe thread/start configuration")
            continue
        thread_count += 1
        thread_id = f"thread-{thread_count}"
        send({"method": "thread/started", "params": {"thread": thread_object(thread_id)}})
        send({"id": request_id, "result": thread_result(thread_id)})
        continue

    if method == "thread/resume":
        params = message.get("params", {})
        config = params.get("config", {})
        exact = (
            params.get("model") == "gpt-5.6-terra"
            and params.get("approvalPolicy") == "never"
            and params.get("sandbox") == "read-only"
            and config.get("model_reasoning_effort") == "medium"
            and config.get("features", {}).get("shell_tool") is False
            and config.get("features", {}).get("multi_agent") is False
            and config.get("tools", {}).get("web_search") is False
            and config.get("agents", {}).get("enabled") is False
            and params.get("excludeTurns") is True
            and params.get("initialTurnsPage", {}).get("itemsView") == "full"
        )
        if not exact:
            fail(request_id, "unsafe thread/resume configuration")
            continue
        if MODE == "resume_missing":
            send({"id": request_id, "error": {"code": -32600, "message": "no rollout found"}})
            continue
        thread_id = params["threadId"]
        page = {"data": [turn_object("old-turn", "completed")], "nextCursor": None, "backwardsCursor": None}
        send({"id": request_id, "result": thread_result(thread_id, initial_page=page)})
        continue

    if method == "thread/read":
        params = message.get("params", {})
        if params.get("includeTurns") is not False:
            fail(request_id, "deprecated history hydration requested")
            continue
        send({"id": request_id, "result": {"thread": thread_object(params["threadId"])}})
        continue

    if method == "thread/turns/list":
        params = message.get("params", {})
        if params.get("itemsView") != "full":
            fail(request_id, "full item history not requested")
            continue
        send({
            "id": request_id,
            "result": {"data": [turn_object("history-turn", "completed")], "nextCursor": None, "backwardsCursor": "back"},
        })
        continue

    if method == "turn/start":
        params = message.get("params", {})
        exact = (
            params.get("model") == "gpt-5.6-terra"
            and params.get("effort") == "medium"
            and params.get("summary") == "concise"
            and params.get("approvalPolicy") == "never"
            and params.get("sandboxPolicy") == {"type": "readOnly", "networkAccess": False}
            and params.get("environments") == []
            and params.get("input", [{}])[0].get("text_elements") == []
        )
        if not exact:
            fail(request_id, "unsafe turn/start configuration")
            continue
        turn_count += 1
        turn_id = f"turn-{turn_count}"
        if MODE == "stale_tool":
            send({"id": request_id, "result": {"turn": turn_object(turn_id)}})
            send({"method": "turn/started", "params": {"threadId": params["threadId"], "turn": turn_object(turn_id)}})
            if turn_count == 2:
                # Deliberately deliver an obsolete start notification after the
                # new turn is active, then an old-turn mutation request.
                send({"method": "turn/started", "params": {"threadId": params["threadId"], "turn": turn_object("turn-1")}})
                send({
                    "id": "server-stale-tool",
                    "method": "item/tool/call",
                    "params": {
                        "threadId": params["threadId"],
                        "turnId": "turn-1",
                        "callId": "stale-call",
                        "namespace": "booking",
                        "tool": "cancel",
                        "arguments": {"event_id": 41, "marker": "old-prompt"},
                    },
                })
                waiting = {
                    "kind": "stale_tool",
                    "thread_id": params["threadId"],
                    "turn_id": turn_id,
                }
            continue
        if turn_count == 1:
            send({"method": "turn/started", "params": {"threadId": params["threadId"], "turn": turn_object(turn_id)}}, split=True)
            send({"method": "item/started", "params": {"threadId": params["threadId"], "turnId": turn_id, "item": {"type": "reasoning", "id": "reason-1", "summary": [], "content": []}, "startedAtMs": 1}})
            send({"method": "item/reasoning/textDelta", "params": {"threadId": params["threadId"], "turnId": turn_id, "itemId": "reason-1", "contentIndex": 0, "delta": "PRIVATE RAW REASONING"}})
            send({"method": "item/reasoning/summaryPartAdded", "params": {"threadId": params["threadId"], "turnId": turn_id, "itemId": "reason-1", "summaryIndex": 0}})
            send({"method": "item/reasoning/summaryTextDelta", "params": {"threadId": params["threadId"], "turnId": turn_id, "itemId": "reason-1", "summaryIndex": 0, "delta": "Checking the exact booking."}})
            send({"method": "item/started", "params": {"threadId": params["threadId"], "turnId": turn_id, "item": {"type": "agentMessage", "id": "agent-1", "text": "", "phase": "commentary", "memoryCitation": None, "delivery": None}, "startedAtMs": 1}})
            send({"method": "item/agentMessage/delta", "params": {"threadId": params["threadId"], "turnId": turn_id, "itemId": "agent-1", "delta": "I’ll verify that booking first."}})
            send({
                "id": "server-tool-1",
                "method": "item/tool/call",
                "params": {
                    "threadId": params["threadId"],
                    "turnId": turn_id,
                    "callId": "call-1",
                    "namespace": "booking",
                    "tool": "cancel",
                    "arguments": {"event_id": 42, "date": "2026-09-01", "room": "B0.29", "start_time": "16:00", "end_time": "16:30"},
                },
            })
            waiting = {"kind": "tool", "turn_request_id": request_id, "thread_id": params["threadId"], "turn_id": turn_id}
            continue
        if turn_count == 2:
            send({"id": request_id, "result": {"turn": turn_object(turn_id)}})
            send({"method": "turn/started", "params": {"threadId": params["threadId"], "turn": turn_object(turn_id)}})
            continue
        send({"id": request_id, "result": {"turn": turn_object(turn_id)}})
        send({"method": "turn/started", "params": {"threadId": params["threadId"], "turn": turn_object(turn_id)}})
        send({
            "id": "server-input-1",
            "method": "item/tool/requestUserInput",
            "params": {
                "threadId": params["threadId"],
                "turnId": turn_id,
                "itemId": "input-1",
                "questions": [{"id": "when", "header": "Date", "question": "Which day?", "isOther": True, "isSecret": False, "options": None}],
                "isBlocking": True,
                "autoResolutionMs": None,
            },
        })
        waiting = {"kind": "input", "thread_id": params["threadId"], "turn_id": turn_id}
        continue

    if method == "turn/interrupt":
        params = message.get("params", {})
        send({"id": request_id, "result": {}})
        send({"method": "turn/completed", "params": {"threadId": params["threadId"], "turn": turn_object(params["turnId"], "interrupted")}})
        continue

    if method is None and request_id == "server-stale-tool" and waiting and waiting.get("kind") == "stale_tool":
        result = message.get("result", {})
        try:
            payload = json.loads(result["contentItems"][0]["text"])
        except Exception:
            sys.exit(51)
        if result.get("success") is not False or payload.get("error", {}).get("code") != "stale_turn":
            sys.exit(52)
        send({
            "id": "server-current-tool",
            "method": "item/tool/call",
            "params": {
                "threadId": waiting["thread_id"],
                "turnId": waiting["turn_id"],
                "callId": "current-call",
                "namespace": "booking",
                "tool": "cancel",
                "arguments": {"event_id": 99, "marker": "new-prompt"},
            },
        })
        waiting["kind"] = "current_tool"
        continue

    if method is None and request_id == "server-current-tool" and waiting and waiting.get("kind") == "current_tool":
        result = message.get("result", {})
        try:
            payload = json.loads(result["contentItems"][0]["text"])
        except Exception:
            sys.exit(53)
        if result.get("success") is not True or payload.get("ok") is not True:
            sys.exit(54)
        send({
            "method": "item/agentMessage/delta",
            "params": {
                "threadId": waiting["thread_id"],
                "turnId": waiting["turn_id"],
                "itemId": "agent-current",
                "delta": "Handled only the current request.",
            },
        })
        send({
            "method": "turn/completed",
            "params": {
                "threadId": waiting["thread_id"],
                "turn": turn_object(waiting["turn_id"], "completed"),
            },
        })
        waiting = None
        continue

    if method is None and request_id == "server-tool-1" and waiting and waiting.get("kind") == "tool":
        result = message.get("result", {})
        try:
            tool_text = result["contentItems"][0]["text"]
            tool_payload = json.loads(tool_text)
        except Exception:
            sys.exit(41)
        if result.get("success") is not True or tool_payload.get("ok") is not True:
            sys.exit(42)
        send({
            "id": "server-approval-1",
            "method": "item/commandExecution/requestApproval",
            "params": {
                "kind": "command",
                "threadId": waiting["thread_id"],
                "turnId": waiting["turn_id"],
                "itemId": "command-1",
                "startedAtMs": 1,
                "command": "unsafe-command",
                "cwd": os.getcwd(),
            },
        })
        waiting["kind"] = "approval"
        continue

    if method is None and request_id == "server-approval-1" and waiting and waiting.get("kind") == "approval":
        if message.get("result", {}).get("decision") != "decline":
            sys.exit(43)
        turn_request_id = waiting["turn_request_id"]
        thread_id = waiting["thread_id"]
        turn_id = waiting["turn_id"]
        send({"id": turn_request_id, "result": {"turn": turn_object(turn_id)}})
        send({"method": "item/completed", "params": {"threadId": thread_id, "turnId": turn_id, "item": {"type": "reasoning", "id": "reason-1", "summary": ["Checking the exact booking."], "content": ["PRIVATE RAW REASONING"]}, "completedAtMs": 2}})
        send({"method": "item/completed", "params": {"threadId": thread_id, "turnId": turn_id, "item": {"type": "agentMessage", "id": "agent-1", "text": "I’ll verify that booking first.", "phase": "commentary", "memoryCitation": None, "delivery": None}, "completedAtMs": 2}})
        send({"method": "turn/completed", "params": {"threadId": thread_id, "turn": turn_object(turn_id, "completed")}})
        waiting = None
        continue

    if method is None and request_id == "server-input-1" and waiting and waiting.get("kind") == "input":
        answers = message.get("result", {}).get("answers", {})
        if answers != {"when": {"answers": ["Tomorrow"]}}:
            sys.exit(44)
        send({"method": "turn/completed", "params": {"threadId": waiting["thread_id"], "turn": turn_object(waiting["turn_id"], "completed")}})
        waiting = None
        continue

    if request_id is not None:
        fail(request_id, "unexpected method: %r" % (method,))
'''


def _tool_specs():
    return [
        {
            "type": "namespace",
            "name": "booking",
            "description": "Verified booking operations",
            "tools": [
                {
                    "type": "function",
                    "name": "cancel",
                    "description": "Cancel one exact current reservation",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "event_id": {"type": "integer"},
                            "date": {"type": "string"},
                            "room": {"type": "string"},
                            "start_time": {"type": "string"},
                            "end_time": {"type": "string"},
                        },
                        "required": ["event_id", "date", "room", "start_time", "end_time"],
                        "additionalProperties": False,
                    },
                }
            ],
        }
    ]


class _FakeServerCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.fake_path = Path(self.tempdir.name) / "fake_codex_server.py"
        self.fake_path.write_text(textwrap.dedent(_FAKE_SERVER), encoding="utf-8")
        self.controllers = []

    async def asyncTearDown(self):
        for controller in reversed(self.controllers):
            await controller.shutdown()
        self.tempdir.cleanup()

    def make_controller(self, *, mode="normal", dispatcher=None, events=None, **kwargs):
        event_list = events if events is not None else []
        environment = {"FAKE_CODEX_MODE": mode}
        controller = CodexChatController(
            dispatcher,
            event_list.append,
            dynamic_tools=_tool_specs(),
            cwd=Path(__file__).resolve().parents[1],
            process_command=(sys.executable, "-u", str(self.fake_path)),
            process_environment=environment,
            request_timeout=kwargs.pop("request_timeout", 2.0),
            startup_timeout=kwargs.pop("startup_timeout", 2.0),
            tool_timeout=kwargs.pop("tool_timeout", 2.0),
            shutdown_timeout=kwargs.pop("shutdown_timeout", 1.0),
            **kwargs,
        )
        self.controllers.append(controller)
        return controller, event_list

    async def wait_for_event(self, events, kind, *, status=None, turn_id=None, timeout=2.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            for event in events:
                if (
                    event.get("kind") == kind
                    and (status is None or event.get("status") == status)
                    and (turn_id is None or event.get("turn_id") == turn_id)
                ):
                    return event
            await asyncio.sleep(0.01)
        self.fail(
            f"Timed out waiting for event kind={kind!r} status={status!r} "
            f"turn_id={turn_id!r}: {events!r}"
        )


class CodexChatProtocolTests(_FakeServerCase):
    async def test_force_shutdown_kills_and_releases_the_owned_process(self):
        class FakeStdin:
            def __init__(self):
                self.closed = False

            def is_closing(self):
                return self.closed

            def close(self):
                self.closed = True

        class StubbornProcess:
            def __init__(self):
                self.stdin = FakeStdin()
                self.returncode = None
                self.killed = False
                self._dead = asyncio.Event()

            def kill(self):
                self.killed = True
                self._dead.set()

            async def wait(self):
                await self._dead.wait()
                self.returncode = -9
                return self.returncode

        controller, _events = self.make_controller(dispatcher=lambda *_args: {"ok": True})
        process = StubbornProcess()
        controller._process = process
        controller._ready = True

        await controller.force_shutdown()

        self.assertTrue(process.killed)
        self.assertTrue(process.stdin.closed)
        self.assertIsNone(controller._process)
        self.assertFalse(controller.is_ready)

    async def test_new_chat_serializes_an_immediate_send_onto_the_new_thread(self):
        controller, _events = self.make_controller(dispatcher=lambda *_args: {"ok": True})
        controller._thread_id = "old-thread"
        entered = asyncio.Event()
        release = asyncio.Event()
        turn_targets = []

        async def already_started():
            return None

        async def request(method, params, **_kwargs):
            if method == "thread/start":
                entered.set()
                await release.wait()
                return {
                    "model": CODEX_MODEL,
                    "reasoningEffort": CODEX_REASONING_EFFORT,
                    "approvalPolicy": "never",
                    "sandbox": {"type": "readOnly", "networkAccess": False},
                    "thread": {"id": "new-thread"},
                }
            if method == "turn/start":
                turn_targets.append(params["threadId"])
                return {"turn": {"id": "new-turn"}}
            self.fail(f"Unexpected request: {method}")

        controller.start = already_started
        controller._request = request
        new_chat_task = asyncio.create_task(controller.new_chat())
        await entered.wait()
        send_task = asyncio.create_task(controller.send_message("Use the new thread"))
        await asyncio.sleep(0.02)
        self.assertEqual(turn_targets, [])

        release.set()
        self.assertEqual(await new_chat_task, "new-thread")
        self.assertEqual(await send_task, "new-turn")
        self.assertEqual(turn_targets, ["new-thread"])

    async def test_exact_configuration_streams_safe_updates_and_dispatches_tools(self):
        calls = []

        async def dispatch(namespace, tool, arguments, context):
            calls.append((namespace, tool, dict(arguments), context["call_id"]))
            context["emit_progress"]("Rechecking the current reservation")
            return {"ok": True, "cancelled_event_id": arguments["event_id"]}

        controller, events = self.make_controller(dispatcher=dispatch)
        await controller.start()
        thread_id = await controller.new_chat()
        turn_id = await controller.send_message("Remove my booking tomorrow at 4pm")
        completed = await self.wait_for_event(events, "turn_completed")

        self.assertEqual(CODEX_MODEL, "gpt-5.6-terra")
        self.assertEqual(thread_id, "thread-1")
        self.assertEqual(turn_id, "turn-1")
        self.assertEqual(completed["status"], "completed")
        self.assertIsNone(controller.active_turn_id)
        self.assertEqual(calls[0][0:2], ("booking", "cancel"))
        self.assertEqual(calls[0][2]["event_id"], 42)

        kinds = [event["kind"] for event in events]
        self.assertIn("connection", kinds)
        self.assertIn("turn_started", kinds)
        self.assertIn("assistant_delta", kinds)
        self.assertIn("reasoning_summary_delta", kinds)
        self.assertIn("activity", kinds)
        self.assertIn("tool_call", kinds)
        self.assertIn("error", kinds)  # unexpected command approval was declined
        rendered_text = " ".join(str(event.get("text", "")) for event in events)
        self.assertIn("Checking the exact booking.", rendered_text)
        self.assertIn("I’ll verify that booking first.", rendered_text)
        self.assertNotIn("PRIVATE RAW REASONING", rendered_text)
        self.assertTrue(
            any(event.get("title") == "Rechecking the current reservation" for event in events)
        )

    async def test_interrupt_and_user_input_response(self):
        async def dispatch(_namespace, _tool, _arguments, _context):
            return {"ok": True}

        controller, events = self.make_controller(dispatcher=dispatch)
        await controller.start()
        await controller.new_chat()

        slow_turn = await controller.send_message("Take a while")
        self.assertEqual(slow_turn, "turn-1")
        await self.wait_for_event(events, "turn_completed")

        active_turn = await controller.send_message("This turn should be stopped")
        self.assertEqual(active_turn, "turn-2")
        self.assertTrue(await controller.interrupt())
        interrupted = await self.wait_for_event(events, "turn_completed", status="interrupted")
        self.assertEqual(interrupted["turn_id"], "turn-2")

        clarification_turn = await controller.send_message("Do the ambiguous thing")
        self.assertEqual(clarification_turn, "turn-3")
        request = await self.wait_for_event(events, "user_input_request")
        await controller.respond_to_user_input(request["request_id"], {"when": "Tomorrow"})
        done = await self.wait_for_event(
            events,
            "turn_completed",
            status="completed",
            turn_id="turn-3",
        )
        self.assertEqual(done["turn_id"], "turn-3")

    async def test_stale_tool_request_after_interrupt_never_reaches_dispatcher(self):
        calls = []

        async def dispatch(_namespace, _tool, arguments, context):
            calls.append((dict(arguments), context["turn_id"]))
            return {"ok": True}

        controller, events = self.make_controller(mode="stale_tool", dispatcher=dispatch)
        await controller.start()
        await controller.new_chat()

        old_turn = await controller.send_message("Stop this old prompt")
        self.assertEqual(old_turn, "turn-1")
        self.assertTrue(await controller.interrupt())
        await self.wait_for_event(
            events,
            "turn_completed",
            status="interrupted",
            turn_id="turn-1",
        )

        current_turn = await controller.send_message("Use only this new prompt")
        self.assertEqual(current_turn, "turn-2")
        await self.wait_for_event(
            events,
            "turn_completed",
            status="completed",
            turn_id="turn-2",
        )

        self.assertEqual(calls, [({"event_id": 99, "marker": "new-prompt"}, "turn-2")])
        self.assertTrue(
            any(
                event.get("kind") == "status"
                and event.get("status") == "stale_request_rejected"
                and event.get("turn_id") == "turn-1"
                for event in events
            )
        )
        self.assertEqual(controller.active_turn_id, None)

    async def test_old_completion_cannot_retire_a_new_active_turn(self):
        controller, events = self.make_controller(dispatcher=lambda *_args: {"ok": True})
        controller._thread_id = "thread-1"
        controller._active_turn_id = "turn-2"
        controller._completed_turn_ids.add("turn-1")

        await controller._handle_notification(
            "turn/completed",
            {
                "threadId": "thread-1",
                "turn": {"id": "turn-1", "status": "interrupted", "items": []},
            },
        )

        self.assertEqual(controller.active_turn_id, "turn-2")
        self.assertFalse(
            any(
                event.get("kind") == "turn_completed"
                and event.get("turn_id") == "turn-1"
                for event in events
            )
        )

        await controller._handle_notification(
            "item/agentMessage/delta",
            {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "itemId": "late-old-message",
                "delta": "This must not leak into turn two.",
            },
        )
        self.assertFalse(
            any("must not leak" in str(event.get("text", "")) for event in events)
        )

    async def test_resume_read_history_and_new_chat(self):
        controller, events = self.make_controller(dispatcher=lambda *_args: {"ok": True})
        await controller.start()
        resumed = await controller.resume("durable-thread")
        self.assertEqual(resumed, "durable-thread")
        self.assertTrue(any(event["kind"] == "history" for event in events))

        read = await controller.read_thread()
        self.assertEqual(read["thread"]["id"], "durable-thread")
        page = await controller.read_history()
        self.assertEqual(page["data"][0]["id"], "history-turn")
        new_id = await controller.new_chat()
        self.assertEqual(new_id, "thread-1")


class CodexChatFailureTests(_FakeServerCase):
    async def test_missing_exact_model_fails_closed(self):
        controller, events = self.make_controller(mode="bad_model")
        with self.assertRaises(CodexConfigurationError):
            await controller.start()
        self.assertFalse(controller.is_ready)
        self.assertTrue(any(event.get("status") == "starting" for event in events))

    async def test_malformed_protocol_fails_pending_request(self):
        controller, _events = self.make_controller(mode="malformed")
        with self.assertRaises(CodexProtocolError):
            await controller.start()
        self.assertFalse(controller.is_ready)

    async def test_request_timeout_is_bounded(self):
        controller, _events = self.make_controller(
            mode="timeout",
            request_timeout=0.15,
            startup_timeout=0.15,
        )
        with self.assertRaises(CodexRequestTimeout):
            await controller.start()
        self.assertFalse(controller.is_ready)

    async def test_clean_eof_fails_as_connection_error(self):
        from codex_chat import CodexConnectionError

        controller, _events = self.make_controller(mode="eof")
        with self.assertRaises(CodexConnectionError):
            await controller.start()
        self.assertFalse(controller.is_ready)


if __name__ == "__main__":
    unittest.main()
