"""Persistent time windows that autonomous booking must leave untouched.

Blackouts are created only after a cancellation has been proved by a complete
agenda scan.  They are intentionally room-independent: cancelling a practice
session means the user asked to keep that part of the day free, not merely to
avoid the room that happened to be booked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app_settings import SETTINGS_FILE, SettingsError, load_settings, update_settings


SETTINGS_KEY = "rebooking_blackouts"
_CLOCK_PATTERN = re.compile(r"(?:[01][0-9]|2[0-3]):(?:00|15|30|45)")
NATURAL_TIME_WINDOWS = {
    "morning": ("07:00", "12:00"),
    "afternoon": ("12:00", "18:00"),
    "evening": ("18:00", "22:00"),
}


@dataclass(frozen=True, order=True)
class RebookingBlackout:
    """One canonical half-open local wall-clock interval on an exact date."""

    date: date
    start_minutes: int
    end_minutes: int

    @property
    def start_time(self) -> str:
        return _format_minutes(self.start_minutes)

    @property
    def end_time(self) -> str:
        return _format_minutes(self.end_minutes)

    def to_dict(self) -> dict[str, str]:
        return {
            "date": self.date.isoformat(),
            "start_time": self.start_time,
            "end_time": self.end_time,
        }


def _format_minutes(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def _parse_date(value: Any, field: str = "date") -> date:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise SettingsError(f"Rebooking blackout {field} must use YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise SettingsError(
            f"Rebooking blackout {field} must be a valid YYYY-MM-DD date"
        ) from exc
    if parsed.isoformat() != value:
        raise SettingsError(f"Rebooking blackout {field} must use canonical YYYY-MM-DD")
    return parsed


def _parse_clock(value: Any, field: str) -> int:
    if not isinstance(value, str) or _CLOCK_PATTERN.fullmatch(value) is None:
        raise SettingsError(
            f"Rebooking blackout {field} must use a 15-minute HH:MM boundary"
        )
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def make_rebooking_blackout(
    blackout_date: date | str,
    start_time: str,
    end_time: str,
) -> RebookingBlackout:
    start = _parse_clock(start_time, "start_time")
    end = _parse_clock(end_time, "end_time")
    if end <= start:
        raise SettingsError("Rebooking blackout end_time must be after start_time")
    return RebookingBlackout(_parse_date(blackout_date), start, end)


def _blackout_from_mapping(raw: Any, index: int) -> RebookingBlackout:
    if not isinstance(raw, Mapping):
        raise SettingsError(f"{SETTINGS_KEY}[{index}] must be an object")
    expected = {"date", "start_time", "end_time"}
    if set(raw) != expected:
        raise SettingsError(
            f"{SETTINGS_KEY}[{index}] must contain exactly date, start_time, and end_time"
        )
    return make_rebooking_blackout(
        raw["date"],
        raw["start_time"],
        raw["end_time"],
    )


def merge_rebooking_blackouts(
    values: Iterable[RebookingBlackout],
) -> tuple[RebookingBlackout, ...]:
    """Return sorted windows, merging same-day overlap and adjacency."""

    supplied = tuple(values)
    if not all(isinstance(item, RebookingBlackout) for item in supplied):
        raise SettingsError("Rebooking blackouts must be validated blackout values")
    ordered = sorted(supplied)
    merged: list[RebookingBlackout] = []
    for item in ordered:
        if (
            merged
            and merged[-1].date == item.date
            and item.start_minutes <= merged[-1].end_minutes
        ):
            previous = merged[-1]
            merged[-1] = RebookingBlackout(
                previous.date,
                previous.start_minutes,
                max(previous.end_minutes, item.end_minutes),
            )
        else:
            merged.append(item)
    return tuple(merged)


def load_rebooking_blackouts(
    settings: Mapping[str, Any] | None = None,
    *,
    path: Path = SETTINGS_FILE,
) -> tuple[RebookingBlackout, ...]:
    """Strictly read the canonical blackout list; malformed state stops booking."""

    if settings is None:
        settings = load_settings(path)
    if not isinstance(settings, Mapping):
        raise SettingsError("Settings must be a JSON object")
    raw = settings.get(SETTINGS_KEY, [])
    if not isinstance(raw, list):
        raise SettingsError(f"{SETTINGS_KEY} must be a JSON list")
    parsed = tuple(_blackout_from_mapping(item, index) for index, item in enumerate(raw))
    canonical = merge_rebooking_blackouts(parsed)
    if [item.to_dict() for item in canonical] != raw:
        raise SettingsError(
            f"{SETTINGS_KEY} must be sorted, unique, and merge adjacent or overlapping windows"
        )
    return canonical


def _store(settings: dict[str, Any], values: Sequence[RebookingBlackout]) -> None:
    canonical = merge_rebooking_blackouts(values)
    settings[SETTINGS_KEY] = [item.to_dict() for item in canonical]


def add_rebooking_blackout(
    blackout_date: date | str,
    start_time: str,
    end_time: str,
    *,
    path: Path = SETTINGS_FILE,
) -> tuple[RebookingBlackout, ...]:
    """Atomically add one verified-cancellation window without losing settings."""

    return add_rebooking_blackouts(
        ((blackout_date, start_time, end_time),),
        path=path,
    )


def add_rebooking_blackouts(
    windows: Iterable[tuple[date | str, str, str]],
    *,
    path: Path = SETTINGS_FILE,
) -> tuple[RebookingBlackout, ...]:
    """Atomically add one or more exact or broader user-requested windows.

    Callers must establish their own mutation-success boundary first.  In
    particular, a broad natural-language scope should be passed here only when
    at least one reservation in that cancellation operation was verified
    absent.  Every input is validated before the settings lock is acquired.
    """

    try:
        supplied = tuple(windows)
    except TypeError as exc:
        raise SettingsError("Rebooking blackout windows must be an iterable") from exc
    added = []
    for index, window in enumerate(supplied):
        if not isinstance(window, (tuple, list)) or len(window) != 3:
            raise SettingsError(
                f"Rebooking blackout window {index} must be date, start_time, end_time"
            )
        added.append(make_rebooking_blackout(*window))
    added = tuple(added)
    if not added:
        raise SettingsError("At least one rebooking blackout window is required")

    def mutate(settings: dict[str, Any]) -> tuple[RebookingBlackout, ...]:
        result = merge_rebooking_blackouts(
            (*load_rebooking_blackouts(settings), *added)
        )
        _store(settings, result)
        return result

    return update_settings(mutate, path)


def subtract_rebooking_blackout(
    blackout_date: date | str,
    start_time: str,
    end_time: str,
    *,
    path: Path = SETTINGS_FILE,
) -> tuple[RebookingBlackout, ...]:
    """Atomically reopen an explicitly requested portion of existing windows."""

    removed = make_rebooking_blackout(blackout_date, start_time, end_time)

    def mutate(settings: dict[str, Any]) -> tuple[RebookingBlackout, ...]:
        remaining: list[RebookingBlackout] = []
        for item in load_rebooking_blackouts(settings):
            if (
                item.date != removed.date
                or removed.end_minutes <= item.start_minutes
                or removed.start_minutes >= item.end_minutes
            ):
                remaining.append(item)
                continue
            if item.start_minutes < removed.start_minutes:
                remaining.append(
                    RebookingBlackout(
                        item.date,
                        item.start_minutes,
                        removed.start_minutes,
                    )
                )
            if removed.end_minutes < item.end_minutes:
                remaining.append(
                    RebookingBlackout(
                        item.date,
                        removed.end_minutes,
                        item.end_minutes,
                    )
                )
        result = merge_rebooking_blackouts(remaining)
        _store(settings, result)
        return result

    return update_settings(mutate, path)


def blackouts_for_date(
    values: Iterable[RebookingBlackout],
    target_date: date | str,
) -> tuple[RebookingBlackout, ...]:
    selected_date = _parse_date(target_date)
    return tuple(item for item in values if item.date == selected_date)


def blackout_conflict_ranges(
    values: Iterable[RebookingBlackout],
) -> dict[str, tuple[tuple[float, float], ...]]:
    """Convert canonical windows to the Booker's existing conflict shape."""

    result: dict[str, list[tuple[float, float]]] = {}
    for item in merge_rebooking_blackouts(values):
        result.setdefault(item.date.isoformat(), []).append(
            (item.start_minutes / 60, item.end_minutes / 60)
        )
    return {key: tuple(ranges) for key, ranges in result.items()}


def interval_overlaps_blackout(
    values: Iterable[RebookingBlackout],
    target_date: date | str,
    start_minutes: int,
    end_minutes: int,
) -> bool:
    if (
        type(start_minutes) is not int
        or type(end_minutes) is not int
        or not 0 <= start_minutes < end_minutes <= 24 * 60
    ):
        raise ValueError("Interval must be ordered integer minutes within one day")
    return any(
        start_minutes < item.end_minutes and end_minutes > item.start_minutes
        for item in blackouts_for_date(values, target_date)
    )


__all__ = [
    "NATURAL_TIME_WINDOWS",
    "SETTINGS_KEY",
    "RebookingBlackout",
    "add_rebooking_blackout",
    "add_rebooking_blackouts",
    "blackout_conflict_ranges",
    "blackouts_for_date",
    "interval_overlaps_blackout",
    "load_rebooking_blackouts",
    "make_rebooking_blackout",
    "merge_rebooking_blackouts",
    "subtract_rebooking_blackout",
]
