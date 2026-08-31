"""Headless integration coverage for the Assistant tab and GUI lifecycle."""

import unittest
from unittest.mock import MagicMock, patch

import tkinter as tk

import gui
from assistant_ui import AssistantEvent


class _StopAfterAssistant(RuntimeError):
    pass


class _FakeWidget:
    def __init__(self, master=None, *args, **kwargs):
        self.master = master
        self.args = args
        self.kwargs = kwargs
        self.pack_calls = []

    def pack(self, *args, **kwargs):
        self.pack_calls.append((args, kwargs))


class _FakeNotebook(_FakeWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tabs = []
        self.selected = None
        self.explicit_select_calls = []

    def add(self, child, *, text):
        self.tabs.append((child, text))
        if self.selected is None:
            # ttk.Notebook displays the first added tab until explicitly changed.
            self.selected = child

    def select(self, tab=None):
        if tab is None:
            return self.selected
        self.explicit_select_calls.append(tab)
        self.selected = tab


class _AssistantPanelProbe(_FakeWidget):
    def pack(self, *args, **kwargs):
        super().pack(*args, **kwargs)
        # Stop before the unrelated Overview/Preferences widgets are created.
        raise _StopAfterAssistant


class GuiAssistantLayoutTests(unittest.TestCase):
    def test_assistant_is_first_default_tab_and_receives_all_callbacks(self):
        instance = object.__new__(gui.AsimutBookerGUI)
        instance.root = object()
        instance.ui_font_family = "Test UI"
        instance.ui_display_font_family = "Test Display"
        notebook = _FakeNotebook()
        captured = {}

        def notebook_factory(*args, **kwargs):
            notebook.master = args[0] if args else kwargs.get("master")
            notebook.kwargs = kwargs
            return notebook

        def assistant_factory(master, **kwargs):
            captured["master"] = master
            captured["kwargs"] = kwargs
            return _AssistantPanelProbe(master)

        with (
            patch("gui.ttk.Frame", _FakeWidget),
            patch("gui.ttk.Label", _FakeWidget),
            patch("gui.ttk.Notebook", side_effect=notebook_factory),
            patch("gui.AssistantPanel", side_effect=assistant_factory),
        ):
            with self.assertRaises(_StopAfterAssistant):
                instance.create_main_layout()

        self.assertEqual(
            [label for _tab, label in notebook.tabs],
            ["Assistant", "Overview", "Preferences", "Activity"],
        )
        self.assertIs(notebook.selected, notebook.tabs[0][0])
        self.assertEqual(notebook.explicit_select_calls, [])
        self.assertIs(captured["master"], notebook.tabs[0][0])
        self.assertEqual(captured["kwargs"]["on_send"], instance._send_assistant_message)
        self.assertEqual(captured["kwargs"]["on_stop"], instance._stop_assistant)
        self.assertEqual(captured["kwargs"]["on_new_chat"], instance._new_assistant_chat)
        self.assertEqual(captured["kwargs"]["font_family"], "Test UI")
        self.assertEqual(captured["kwargs"]["display_font_family"], "Test Display")
        self.assertEqual(
            instance.assistant_panel.pack_calls,
            [((), {"fill": tk.BOTH, "expand": True})],
        )


class GuiAssistantCallbackTests(unittest.TestCase):
    def test_send_stop_and_new_chat_forward_to_the_runtime(self):
        instance = object.__new__(gui.AsimutBookerGUI)
        runtime = MagicMock()
        runtime.send.return_value = "send-result"
        runtime.stop.return_value = "stop-result"
        runtime.new_chat.return_value = "new-result"
        instance.assistant_runtime = runtime

        self.assertEqual(instance._send_assistant_message("tomorrow at four"), "send-result")
        self.assertEqual(instance._stop_assistant(), "stop-result")
        self.assertEqual(instance._new_assistant_chat(), "new-result")

        runtime.send.assert_called_once_with("tomorrow at four")
        runtime.stop.assert_called_once_with()
        runtime.new_chat.assert_called_once_with()

    def test_callbacks_reject_cleanly_before_runtime_initialization(self):
        instance = object.__new__(gui.AsimutBookerGUI)
        instance.assistant_runtime = None

        self.assertFalse(instance._send_assistant_message("hello"))
        self.assertFalse(instance._stop_assistant())
        self.assertFalse(instance._new_assistant_chat())

    def test_runtime_initialization_restores_messages_before_starting(self):
        instance = object.__new__(gui.AsimutBookerGUI)
        panel = MagicMock()
        instance.assistant_panel = panel
        instance.assistant_runtime = None
        runtime = MagicMock()
        runtime.restored_messages.return_value = [
            {"role": "user", "text": "What is tomorrow's plan?"},
            {"role": "assistant", "text": "Tomorrow has one planned block."},
        ]

        with patch("gui.AssistantRuntime", return_value=runtime) as runtime_type:
            instance._initialize_assistant()

        runtime_type.assert_called_once_with(panel.post_event)
        self.assertIs(instance.assistant_runtime, runtime)
        self.assertEqual(panel.apply_event.call_count, 2)
        first, second = [call.args[0] for call in panel.apply_event.call_args_list]
        self.assertEqual(
            first,
            AssistantEvent(
                "user_message",
                text="What is tomorrow's plan?",
                event_id="restored-0",
                replace=True,
            ),
        )
        self.assertEqual(
            second,
            AssistantEvent(
                "assistant_message",
                text="Tomorrow has one planned block.",
                event_id="restored-1",
                replace=True,
            ),
        )
        runtime.start.assert_called_once_with()


class GuiAssistantCloseTests(unittest.TestCase):
    def make_instance(self):
        instance = object.__new__(gui.AsimutBookerGUI)
        instance._closing = False
        instance.assistant_panel = MagicMock()
        instance.assistant_runtime = MagicMock()
        instance.root = MagicMock()
        return instance

    def test_close_stops_panel_then_runtime_then_destroys_root_once(self):
        instance = self.make_instance()
        events = []
        instance.assistant_panel.close.side_effect = lambda: events.append("panel")
        instance.assistant_runtime.close.side_effect = lambda: events.append("runtime")
        instance.root.destroy.side_effect = lambda: events.append("root")

        instance._close_application()
        instance._close_application()

        self.assertTrue(instance._closing)
        self.assertEqual(events, ["panel", "runtime", "root"])

    def test_cleanup_failures_never_prevent_later_cleanup_or_root_destroy(self):
        instance = self.make_instance()
        instance.assistant_panel.close.side_effect = RuntimeError("panel already gone")
        instance.assistant_runtime.close.side_effect = RuntimeError("runtime already gone")

        instance._close_application()

        instance.assistant_panel.close.assert_called_once_with()
        instance.assistant_runtime.close.assert_called_once_with()
        instance.root.destroy.assert_called_once_with()

    def test_tcl_destroy_error_is_safe_during_shutdown(self):
        instance = self.make_instance()
        instance.root.destroy.side_effect = tk.TclError("application has been destroyed")

        instance._close_application()

        instance.assistant_panel.close.assert_called_once_with()
        instance.assistant_runtime.close.assert_called_once_with()
        instance.root.destroy.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
