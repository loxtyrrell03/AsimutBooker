"""Strict, GUI-free settings for daily booking strategy.

The autonomous runtime and desktop UI use this module as the single schema for
booking-order and daily-planning preferences.  Validation is deliberately
strict: persisted values are never coerced and disabled planning settings are
still checked so re-enabling a saved strategy cannot activate stale data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping


BOOKING_STRATEGY_KEY = "booking_strategy"
DAILY_PLANNING_KEY = "daily_planning"

QUARTER_HOUR_MINUTES = 15

AFTER_PEAK_MODES = frozenset({"longest_first", "earliest_first", "room_first"})
PRIORITY_MODES = frozenset({"time_first", "room_first"})

_CLOCK_PATTERN = re.compile(r"^[0-2][0-9]:[0-5][0-9]$")
_STRATEGY_FIELDS = {"reverse_date_order", DAILY_PLANNING_KEY}
_DAILY_PLANNING_FIELDS = {
    "enabled",
    "preferred_peak_start",
    "preferred_peak_end",
    "desired_peak_block_minutes",
    "hold_early_peak_edges",
    "foresight_minutes",
    "minimum_later_options",
    "fallback_lead_minutes",
    "after_peak_mode",
    "priority_mode",
}


class BookingStrategyError(ValueError):
    """Raised when booking-strategy settings are malformed or unsupported."""


@dataclass(frozen=True)
class DailyPlanningPreferences:
    """Validated preferences used to plan a useful day of bookings."""

    enabled: bool = True
    preferred_peak_start: str = "12:00"
    preferred_peak_end: str = "16:00"
    desired_peak_block_minutes: int = 120
    hold_early_peak_edges: bool = True
    foresight_minutes: int = 420
    minimum_later_options: int = 2
    fallback_lead_minutes: int = 120
    after_peak_mode: str = "longest_first"
    priority_mode: str = "time_first"

    def to_dict(self) -> dict[str, Any]:
        return daily_planning_to_dict(self)


@dataclass(frozen=True)
class BookingStrategyPreferences:
    """Top-level strategy preferences, including legacy date ordering."""

    reverse_date_order: bool = False
    daily_planning: DailyPlanningPreferences = DailyPlanningPreferences()

    def to_dict(self) -> dict[str, Any]:
        return booking_strategy_to_dict(self)


def parse_clock_minutes(value: Any, label: str = "time") -> int:
    """Parse one canonical ``HH:MM`` value without accepting coercions."""

    if not isinstance(value, str) or _CLOCK_PATTERN.fullmatch(value) is None:
        raise BookingStrategyError(f"{label} must use canonical HH:MM format")
    hour = int(value[:2])
    minute = int(value[3:])
    if hour > 23:
        raise BookingStrategyError(f"{label} must use canonical HH:MM format")
    total = hour * 60 + minute
    if total % QUARTER_HOUR_MINUTES:
        raise BookingStrategyError(f"{label} must use 15-minute steps")
    return total


def format_clock_minutes(value: Any, label: str = "minutes") -> str:
    """Format a validated minute-of-day integer as canonical ``HH:MM``."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise BookingStrategyError(f"{label} must be an integer")
    if not 0 <= value < 24 * 60:
        raise BookingStrategyError(f"{label} must be within one day")
    if value % QUARTER_HOUR_MINUTES:
        raise BookingStrategyError(f"{label} must use 15-minute steps")
    return f"{value // 60:02d}:{value % 60:02d}"


def _require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise BookingStrategyError(f"{label} must be true or false")
    return value


def _require_step_int(
    value: Any,
    label: str,
    *,
    minimum: int,
    maximum: int,
    step: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BookingStrategyError(f"{label} must be an integer")
    if not minimum <= value <= maximum:
        raise BookingStrategyError(
            f"{label} must be between {minimum} and {maximum}"
        )
    if step is not None and value % step:
        raise BookingStrategyError(f"{label} must use {step}-minute steps")
    return value


def _require_enum(value: Any, label: str, choices: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise BookingStrategyError(
            f"{label} must be one of: {', '.join(sorted(choices))}"
        )
    return value


def _check_string_keys(raw: Mapping[Any, Any], label: str) -> None:
    if any(not isinstance(key, str) for key in raw):
        raise BookingStrategyError(f"{label} keys must be strings")


def _parse_daily_planning(raw: Mapping[str, Any]) -> DailyPlanningPreferences:
    _check_string_keys(raw, "booking_strategy.daily_planning")
    unknown = set(raw) - _DAILY_PLANNING_FIELDS
    if unknown:
        raise BookingStrategyError(
            "Unsupported daily planning setting(s): " + ", ".join(sorted(unknown))
        )

    start = raw.get("preferred_peak_start", "12:00")
    end = raw.get("preferred_peak_end", "16:00")
    start_minutes = parse_clock_minutes(
        start, "booking_strategy.daily_planning.preferred_peak_start"
    )
    end_minutes = parse_clock_minutes(
        end, "booking_strategy.daily_planning.preferred_peak_end"
    )
    if end_minutes <= start_minutes:
        raise BookingStrategyError(
            "booking_strategy.daily_planning.preferred_peak_end must be after "
            "preferred_peak_start"
        )

    return DailyPlanningPreferences(
        enabled=_require_bool(
            raw.get("enabled", True),
            "booking_strategy.daily_planning.enabled",
        ),
        preferred_peak_start=start,
        preferred_peak_end=end,
        desired_peak_block_minutes=_require_step_int(
            raw.get("desired_peak_block_minutes", 120),
            "booking_strategy.daily_planning.desired_peak_block_minutes",
            minimum=30,
            maximum=120,
            step=QUARTER_HOUR_MINUTES,
        ),
        hold_early_peak_edges=_require_bool(
            raw.get("hold_early_peak_edges", True),
            "booking_strategy.daily_planning.hold_early_peak_edges",
        ),
        foresight_minutes=_require_step_int(
            raw.get("foresight_minutes", 420),
            "booking_strategy.daily_planning.foresight_minutes",
            minimum=0,
            maximum=24 * 60,
            step=QUARTER_HOUR_MINUTES,
        ),
        minimum_later_options=_require_step_int(
            raw.get("minimum_later_options", 2),
            "booking_strategy.daily_planning.minimum_later_options",
            minimum=1,
            maximum=5,
        ),
        fallback_lead_minutes=_require_step_int(
            raw.get("fallback_lead_minutes", 120),
            "booking_strategy.daily_planning.fallback_lead_minutes",
            minimum=0,
            maximum=240,
            step=QUARTER_HOUR_MINUTES,
        ),
        after_peak_mode=_require_enum(
            raw.get("after_peak_mode", "longest_first"),
            "booking_strategy.daily_planning.after_peak_mode",
            AFTER_PEAK_MODES,
        ),
        priority_mode=_require_enum(
            raw.get("priority_mode", "time_first"),
            "booking_strategy.daily_planning.priority_mode",
            PRIORITY_MODES,
        ),
    )


def _parse_booking_strategy(raw: Mapping[str, Any]) -> BookingStrategyPreferences:
    _check_string_keys(raw, "booking_strategy")
    unknown = set(raw) - _STRATEGY_FIELDS
    if unknown:
        raise BookingStrategyError(
            "Unsupported booking strategy setting(s): " + ", ".join(sorted(unknown))
        )

    daily_raw = raw.get(DAILY_PLANNING_KEY, {})
    if not isinstance(daily_raw, Mapping):
        raise BookingStrategyError(
            "booking_strategy.daily_planning must be a JSON object"
        )
    return BookingStrategyPreferences(
        reverse_date_order=_require_bool(
            raw.get("reverse_date_order", False),
            "booking_strategy.reverse_date_order",
        ),
        daily_planning=_parse_daily_planning(daily_raw),
    )


def load_booking_strategy(settings: Mapping[str, Any]) -> BookingStrategyPreferences:
    """Load and strictly validate the optional booking-strategy block."""

    if not isinstance(settings, Mapping):
        raise BookingStrategyError("Settings must be a JSON object")
    raw = settings.get(BOOKING_STRATEGY_KEY, {})
    if not isinstance(raw, Mapping):
        raise BookingStrategyError("booking_strategy must be a JSON object")
    return _parse_booking_strategy(raw)


def daily_planning_to_dict(value: DailyPlanningPreferences) -> dict[str, Any]:
    """Serialize daily planning preferences to their stable JSON schema."""

    if not isinstance(value, DailyPlanningPreferences):
        raise TypeError("value must be a DailyPlanningPreferences instance")
    return {
        "enabled": value.enabled,
        "preferred_peak_start": value.preferred_peak_start,
        "preferred_peak_end": value.preferred_peak_end,
        "desired_peak_block_minutes": value.desired_peak_block_minutes,
        "hold_early_peak_edges": value.hold_early_peak_edges,
        "foresight_minutes": value.foresight_minutes,
        "minimum_later_options": value.minimum_later_options,
        "fallback_lead_minutes": value.fallback_lead_minutes,
        "after_peak_mode": value.after_peak_mode,
        "priority_mode": value.priority_mode,
    }


def booking_strategy_to_dict(value: BookingStrategyPreferences) -> dict[str, Any]:
    """Serialize validated strategy preferences to the persisted schema."""

    if not isinstance(value, BookingStrategyPreferences):
        raise TypeError("value must be a BookingStrategyPreferences instance")
    return {
        "reverse_date_order": value.reverse_date_order,
        DAILY_PLANNING_KEY: daily_planning_to_dict(value.daily_planning),
    }


def apply_booking_strategy_update(
    settings: MutableMapping[str, Any],
    patch: Mapping[str, Any],
) -> BookingStrategyPreferences:
    """Merge a scoped strategy patch while preserving every unrelated key.

    ``daily_planning`` itself is patchable: omitted nested fields retain their
    current validated values.  The supplied document is mutated only after the
    complete merged candidate passes the same strict loader used at runtime.
    """

    if not isinstance(settings, MutableMapping):
        raise BookingStrategyError("Settings must be a mutable JSON object")
    if not isinstance(patch, Mapping):
        raise BookingStrategyError("Booking strategy update must be a JSON object")
    _check_string_keys(patch, "Booking strategy update")
    unknown = set(patch) - _STRATEGY_FIELDS
    if unknown:
        raise BookingStrategyError(
            "Unsupported booking strategy update(s): "
            + ", ".join(sorted(unknown))
        )

    current = load_booking_strategy(settings)
    candidate = booking_strategy_to_dict(current)

    if "reverse_date_order" in patch:
        candidate["reverse_date_order"] = patch["reverse_date_order"]
    if DAILY_PLANNING_KEY in patch:
        daily_patch = patch[DAILY_PLANNING_KEY]
        if not isinstance(daily_patch, Mapping):
            raise BookingStrategyError(
                "booking_strategy.daily_planning update must be a JSON object"
            )
        _check_string_keys(daily_patch, "booking_strategy.daily_planning update")
        unknown_daily = set(daily_patch) - _DAILY_PLANNING_FIELDS
        if unknown_daily:
            raise BookingStrategyError(
                "Unsupported daily planning update(s): "
                + ", ".join(sorted(unknown_daily))
            )
        candidate_daily = dict(candidate[DAILY_PLANNING_KEY])
        candidate_daily.update(daily_patch)
        candidate[DAILY_PLANNING_KEY] = candidate_daily

    merged = _parse_booking_strategy(candidate)
    settings[BOOKING_STRATEGY_KEY] = booking_strategy_to_dict(merged)
    return merged
