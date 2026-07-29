from __future__ import annotations

import queue

from gui import AsimutBookerGUI, _format_timestamp


class _FakeRoot:
    def __init__(self) -> None:
        self.after_calls: list[tuple[int, object]] = []

    def after(self, delay: int, callback: object) -> None:
        self.after_calls.append((delay, callback))


def test_format_timestamp_treats_task_scheduler_pre_epoch_sentinel_as_empty() -> None:
    assert _format_timestamp("1932-04-22T11:30:14.0000000+01:00") == ""


def test_format_timestamp_localises_normal_aware_timestamp() -> None:
    assert _format_timestamp("2026-07-30T07:28:00+01:00").startswith("2026-07-30 ")


def test_ui_queue_continues_after_callback_failure() -> None:
    app = object.__new__(AsimutBookerGUI)
    app._closing = False
    app._ui_queue = queue.Queue()
    app.root = _FakeRoot()
    errors: list[Exception] = []
    completed: list[str] = []
    app._report_ui_callback_error = errors.append

    def fail() -> None:
        raise ValueError("bad scheduler timestamp")

    app._ui_queue.put(fail)
    app._ui_queue.put(lambda: completed.append("health applied"))

    app._drain_ui_queue()

    assert len(errors) == 1
    assert completed == ["health applied"]
    assert len(app.root.after_calls) == 1
