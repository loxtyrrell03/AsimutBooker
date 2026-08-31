"""Headless helper coverage for the standalone assistant chat surface."""

import threading
import unittest

from assistant_ui import (
    AssistantEvent,
    AssistantEventBuffer,
    AssistantPanel,
    composer_return_action,
    estimate_text_rows,
    normalize_assistant_event,
    responsive_content_width,
)


class AssistantEventNormalizationTests(unittest.TestCase):
    def test_codex_controller_event_aliases_are_normalized(self):
        cases = (
            ({"kind": "status", "message": "Inspecting agenda"}, "activity"),
            (
                {
                    "kind": "reasoning_summary_delta",
                    "delta": "Checking tomorrow",
                    "item_id": "reason-1",
                },
                "reasoning_delta",
            ),
            (
                {
                    "kind": "commentary_delta",
                    "delta": "Checking tomorrow",
                    "item_id": "commentary-1",
                },
                "commentary_delta",
            ),
            (
                {
                    "kind": "tool_call",
                    "tool_name": "get_agenda",
                    "call_id": "call-1",
                    "phase": "running",
                },
                "tool",
            ),
            ({"kind": "turn_completed", "turn_id": "turn-1"}, "done"),
            (
                {
                    "kind": "user_input_request",
                    "questions": [{"question": "Which 4pm booking do you mean?"}],
                },
                "activity",
            ),
            ({"kind": "history", "thread_id": "thread-1", "page": {}}, "activity"),
            (
                {
                    "kind": "activity",
                    "title": "Thinking",
                    "status": "started",
                    "item_id": "reason-2",
                    "summary_index": 0,
                },
                "reasoning_start",
            ),
        )
        for raw, expected_kind in cases:
            with self.subTest(raw=raw):
                event = normalize_assistant_event(raw)
                self.assertEqual(event.kind, expected_kind)

        reasoning = normalize_assistant_event(cases[1][0])
        self.assertEqual(reasoning.text, "Checking tomorrow")
        self.assertEqual(reasoning.event_id, "reason-1")

        tool = normalize_assistant_event(cases[3][0])
        self.assertEqual(tool.title, "get_agenda")
        self.assertEqual(tool.status, "running")
        self.assertEqual(tool.event_id, "call-1")

        clarification = normalize_assistant_event(cases[5][0])
        self.assertEqual(clarification.status, "attention")
        self.assertEqual(clarification.text, "Which 4pm booking do you mean?")

        history = normalize_assistant_event(cases[6][0])
        self.assertEqual(history.title, "Previous chat loaded")
        self.assertEqual(history.status, "completed")

    def test_neutral_type_and_common_payload_fields_are_supported(self):
        event = normalize_assistant_event(
            {
                "type": "assistant_delta",
                "delta": "Hello",
                "turn_id": "turn-7",
                "replace": False,
                "expanded": None,
            }
        )
        self.assertEqual(
            event,
            AssistantEvent("assistant_delta", "Hello", event_id="turn-7"),
        )

    def test_invalid_kinds_and_non_boolean_controls_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            normalize_assistant_event({"kind": "run_arbitrary_shell"})
        with self.assertRaisesRegex(ValueError, "replace"):
            normalize_assistant_event(
                {"kind": "assistant_message", "replace": "yes"}
            )
        with self.assertRaisesRegex(ValueError, "expanded"):
            normalize_assistant_event(
                {"kind": "reasoning_summary", "expanded": 1}
            )

    def test_boolean_and_object_text_fields_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "text"):
            normalize_assistant_event(AssistantEvent("assistant_message", text=True))
        with self.assertRaisesRegex(ValueError, "message"):
            normalize_assistant_event(
                {"kind": "assistant_message", "message": {"unsafe": "object"}}
            )


class AssistantEventBufferTests(unittest.TestCase):
    def test_adjacent_stream_deltas_for_the_same_item_are_coalesced(self):
        buffer = AssistantEventBuffer()
        self.assertTrue(buffer.put(AssistantEvent("assistant_delta", "Hel", event_id="a")))
        self.assertTrue(buffer.put(AssistantEvent("assistant_delta", "lo", event_id="a")))
        self.assertTrue(buffer.put(AssistantEvent("assistant_delta", "!", event_id="b")))

        self.assertEqual(
            buffer.drain(),
            [
                AssistantEvent("assistant_delta", "Hello", event_id="a"),
                AssistantEvent("assistant_delta", "!", event_id="b"),
            ],
        )

    def test_reasoning_and_answer_deltas_never_coalesce_together(self):
        buffer = AssistantEventBuffer()
        buffer.put(AssistantEvent("reasoning_delta", "Check", event_id="x"))
        buffer.put(AssistantEvent("assistant_delta", "Answer", event_id="x"))
        self.assertEqual(len(buffer), 2)

    def test_adjacent_commentary_deltas_are_coalesced(self):
        buffer = AssistantEventBuffer()
        buffer.put(AssistantEvent("commentary_delta", "Checking ", event_id="x"))
        buffer.put(AssistantEvent("commentary_delta", "agenda", event_id="x"))
        self.assertEqual(
            buffer.drain(),
            [AssistantEvent("commentary_delta", "Checking agenda", event_id="x")],
        )

    def test_terminal_update_survives_bounded_overflow(self):
        buffer = AssistantEventBuffer(capacity=8)
        for index in range(8):
            self.assertTrue(
                buffer.put(
                    AssistantEvent("activity", f"step {index}", event_id=str(index))
                )
            )
        self.assertFalse(
            buffer.put(AssistantEvent("activity", "overflow", event_id="overflow"))
        )
        self.assertTrue(buffer.put(AssistantEvent("done", "Ready")))
        drained = buffer.drain(20)
        self.assertEqual(len(drained), 8)
        self.assertEqual(drained[-1], AssistantEvent("done", "Ready"))
        self.assertNotIn("step 0", [event.text for event in drained])

    def test_buffer_accepts_concurrent_producers_without_losing_text(self):
        buffer = AssistantEventBuffer()
        producers = 6
        per_producer = 120

        def produce(index):
            for _ in range(per_producer):
                buffer.put(
                    AssistantEvent(
                        "assistant_delta",
                        "x",
                        event_id=f"stream-{index}",
                    )
                )

        threads = [threading.Thread(target=produce, args=(index,)) for index in range(producers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        streams = {}
        for event in buffer.drain(4096):
            streams[event.event_id] = streams.get(event.event_id, "") + event.text
        self.assertEqual(set(streams), {f"stream-{index}" for index in range(producers)})
        self.assertTrue(all(len(text) == per_producer for text in streams.values()))


class AssistantPanelDispatchTests(unittest.TestCase):
    def make_headless_panel(self):
        panel = object.__new__(AssistantPanel)
        panel._ui_thread_id = threading.get_ident()
        panel._closed = False
        panel._event_buffer = AssistantEventBuffer()
        panel._id_counter = 0
        panel._active_assistant_id = ""
        panel._active_reasoning_id = ""
        return panel

    def test_worker_thread_apply_only_queues_and_never_calls_ui_dispatch(self):
        panel = self.make_headless_panel()
        ui_calls = []
        panel._apply_event_on_ui = ui_calls.append
        results = []

        worker = threading.Thread(
            target=lambda: results.append(
                panel.apply_event(
                    {
                        "kind": "assistant_delta",
                        "delta": "streamed safely",
                        "turn_id": "turn-1",
                    }
                )
            )
        )
        worker.start()
        worker.join()

        self.assertEqual(results, [True])
        self.assertEqual(ui_calls, [])
        self.assertEqual(
            panel._event_buffer.drain(),
            [AssistantEvent("assistant_delta", "streamed safely", event_id="turn-1")],
        )

    def test_controller_ids_are_namespaced_to_prevent_card_collisions(self):
        panel = self.make_headless_panel()

        answer = panel._resolve_event_id(
            AssistantEvent("assistant_delta", "answer", event_id="turn-1")
        )
        activity = panel._resolve_event_id(
            AssistantEvent("activity", "working", event_id="turn-1")
        )

        self.assertEqual(answer.event_id, "assistant:turn-1")
        self.assertEqual(activity.event_id, "activity:turn-1")


class AssistantLayoutHelperTests(unittest.TestCase):
    def test_transcript_width_is_centered_and_capped_on_large_windows(self):
        self.assertEqual(responsive_content_width(1400), 900)
        self.assertEqual(responsive_content_width(1000), 900)

    def test_transcript_width_preserves_gutters_without_overflowing_small_windows(self):
        self.assertEqual(responsive_content_width(800), 748)
        self.assertEqual(responsive_content_width(500), 472)
        self.assertEqual(responsive_content_width(20), 1)
        self.assertLessEqual(responsive_content_width(320), 320)

    def test_invalid_width_arguments_are_rejected(self):
        with self.assertRaises(TypeError):
            responsive_content_width(True)
        with self.assertRaises(ValueError):
            responsive_content_width(800, maximum=200)

    def test_enter_sends_and_shift_enter_inserts_a_newline(self):
        self.assertEqual(composer_return_action(0), "send")
        self.assertEqual(composer_return_action(0x0001), "newline")
        self.assertEqual(composer_return_action(0x0005), "newline")

    def test_text_height_estimator_accounts_for_wraps_and_blank_lines(self):
        self.assertEqual(estimate_text_rows("", 10), 1)
        self.assertEqual(estimate_text_rows("1234567890", 10), 1)
        self.assertEqual(estimate_text_rows("12345678901", 10), 2)
        self.assertEqual(estimate_text_rows("one\n\nthree", 20), 3)
        self.assertEqual(estimate_text_rows("line\n", 20), 2)
        self.assertEqual(estimate_text_rows("x" * 1000, 10, maximum=8), 8)


if __name__ == "__main__":
    unittest.main()
