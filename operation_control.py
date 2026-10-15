"""Cooperative cancellation/progress hooks scoped to one owned host operation."""
from contextlib import contextmanager
from contextvars import ContextVar
from app_settings import SettingsError

_CONTROL = ContextVar('owned_operation', default=None)
_GRID = ContextVar('room_grid_observer', default=None)


@contextmanager
def observe_room_grid(callback):
    token = _GRID.set(callback)
    try:
        yield
    finally:
        _GRID.reset(token)


def report_room_grid(target_date, snapshot, eligible, settings, page=None):
    check_operation_stop()
    callback = _GRID.get()
    if callback is not None:
        callback(target_date, snapshot, eligible, settings, page)


class OperationStopped(SettingsError):
    pass


@contextmanager
def owned_operation(stop_path, progress, availability):
    outcome = {}
    token = _CONTROL.set((stop_path, progress, availability, outcome))
    try:
        yield outcome
    finally:
        _CONTROL.reset(token)


def report_pause_stop():
    """Carry a proven Pause boundary to this operation's result classifier."""
    control = _CONTROL.get()
    if control is not None:
        control[3]['stop_reason'] = 'booking_pause'


def check_operation_stop():
    control = _CONTROL.get()
    if control is not None and control[0] is not None and control[0].exists():
        control[3]['stop_reason'] = 'operation_stop'
        raise OperationStopped('Stop requested; no further booking action will start')


def wait_for_operation(page, milliseconds):
    """Pump browser events in bounded steps while an owned read can be stopped."""
    check_operation_stop()
    control = _CONTROL.get()
    if control is None or control[0] is None:
        page.wait_for_timeout(milliseconds)
        return
    remaining = milliseconds
    while remaining > 0:
        step = min(250, remaining)
        page.wait_for_timeout(step)
        check_operation_stop()
        remaining -= step


@contextmanager
def operation_verification():
    """Finish read-only proof after Save even when Stop was requested."""
    control = _CONTROL.get()
    token = _CONTROL.set((None, control[1], control[2], control[3]) if control else None)
    try:
        yield
    finally:
        _CONTROL.reset(token)


def operation_stage(text):
    check_operation_stop()
    control = _CONTROL.get()
    if control is not None:
        control[1](text)


def report_available_gaps(target_date, rooms):
    check_operation_stop()
    control = _CONTROL.get()
    if control is not None:
        control[2](target_date, rooms)
