"""Cooperative cancellation/progress hooks scoped to one owned host operation."""
from contextlib import contextmanager
from contextvars import ContextVar
from app_settings import SettingsError

_CONTROL = ContextVar('owned_operation', default=None)


class OperationStopped(SettingsError):
    pass


@contextmanager
def owned_operation(stop_path, progress, availability):
    token = _CONTROL.set((stop_path, progress, availability))
    try:
        yield
    finally:
        _CONTROL.reset(token)


def check_operation_stop():
    control = _CONTROL.get()
    if control is not None and control[0].exists():
        raise OperationStopped('Stop requested; no further booking action will start')


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
