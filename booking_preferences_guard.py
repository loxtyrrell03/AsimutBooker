"""Serialize the final Save decision with user preference edits."""

from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path

from app_settings import InterProcessFileLock, SettingsError, load_settings


class BookingPreferencesChanged(SettingsError):
    """The run must stop before Save and replan using the latest controls."""


_CONTROL_KEYS = (
    "time_preferences", "practice_plan", "disabled_dates", "room_preferences",
    "booking_strategy", "ignored_events", "rebooking_blackouts",
)
_ACTIVE_RUN = ContextVar("booking_preference_snapshot", default=None)


def _controls(settings):
    # Runtime-owned extension progress and display caches can change after each
    # verified action without invalidating the user's original instructions.
    return json.dumps({key: settings[key] for key in _CONTROL_KEYS if key in settings},
                      sort_keys=True, separators=(",", ":"), allow_nan=False)


@contextmanager
def booking_preference_run(path, settings):
    path = Path(path)
    token = _ACTIVE_RUN.set((path, _controls(settings), path.exists()))
    try:
        yield
    finally:
        _ACTIVE_RUN.reset(token)


@contextmanager
def booking_save_boundary():
    """Hold the shared settings lock only across the receipt and Save click.

    Main installs the snapshot for all production booking/extension runs. Direct
    low-level fixture calls without a run context retain their isolated behavior.
    Remote verification and local post-Save updates happen after releasing it.
    """
    active = _ACTIVE_RUN.get()
    if active is None:
        yield
        return
    path, expected, existed = active
    lock = InterProcessFileLock(path.with_suffix(path.suffix + ".lock"), timeout=1)
    try:
        lock.acquire()
    except SettingsError as exc:
        raise BookingPreferencesChanged("Preferences are being edited; stopping before Save") from exc
    try:
        try:
            current = load_settings(path, missing_ok=not existed)
            changed = _controls(current) != expected
        except (SettingsError, ValueError, TypeError) as exc:
            raise BookingPreferencesChanged("Preferences could not be rechecked; stopping before Save") from exc
        if changed:
            raise BookingPreferencesChanged("Preferences changed during this run; stopping before Save to replan")
        yield
    finally:
        lock.release()
