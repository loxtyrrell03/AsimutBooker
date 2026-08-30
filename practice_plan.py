"""Pure validation and budgeting helpers for per-day practice targets."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping


MIN_TARGET_HOURS = 0.5
MAX_TARGET_HOURS = 12.0
TARGET_STEP_HOURS = 0.5
DEFAULT_TARGET_HOURS = 2.0


class PracticePlanError(ValueError):
    """Raised when a saved practice plan is unsafe or ambiguous."""


@dataclass(frozen=True)
class PracticePlan:
    enabled: bool = False
    default_hours: float = DEFAULT_TARGET_HOURS
    date_overrides: dict[str, float] | None = None

    def target_for(self, target_date: date | datetime) -> float | None:
        """Return this date's target, or None when legacy allocation is active."""

        if not self.enabled:
            return None
        key = as_date(target_date).isoformat()
        return (self.date_overrides or {}).get(key, self.default_hours)


def as_date(value: date | datetime) -> date:
    """Normalize date and datetime instances without calling date() on a date."""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise TypeError(f"Expected date or datetime, got {type(value).__name__}")


def _validate_hours(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PracticePlanError(f"{label} must be a number")
    hours = float(value)
    if not math.isfinite(hours):
        raise PracticePlanError(f"{label} must be finite")
    if not MIN_TARGET_HOURS <= hours <= MAX_TARGET_HOURS:
        raise PracticePlanError(
            f"{label} must be between {MIN_TARGET_HOURS:g} and {MAX_TARGET_HOURS:g} hours"
        )
    steps = hours / TARGET_STEP_HOURS
    if not math.isclose(steps, round(steps), abs_tol=1e-9):
        raise PracticePlanError(f"{label} must use {TARGET_STEP_HOURS:g}-hour steps")
    return hours


def load_practice_plan(settings: Mapping[str, Any]) -> PracticePlan:
    """Validate the optional practice_plan settings block."""

    raw = settings.get("practice_plan")
    if raw is None:
        return PracticePlan()
    if not isinstance(raw, Mapping):
        raise PracticePlanError("practice_plan must be a JSON object")

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise PracticePlanError("practice_plan.enabled must be true or false")

    default_hours = _validate_hours(
        raw.get("default_hours", DEFAULT_TARGET_HOURS),
        "practice_plan.default_hours",
    )

    raw_overrides = raw.get("date_overrides", {})
    if not isinstance(raw_overrides, Mapping):
        raise PracticePlanError("practice_plan.date_overrides must be a JSON object")

    overrides: dict[str, float] = {}
    for key, value in raw_overrides.items():
        if not isinstance(key, str):
            raise PracticePlanError("practice plan override dates must be strings")
        try:
            parsed = date.fromisoformat(key)
        except ValueError as exc:
            raise PracticePlanError(f"Invalid practice plan date: {key}") from exc
        if parsed.isoformat() != key:
            raise PracticePlanError(f"Practice plan date must use YYYY-MM-DD: {key}")
        overrides[key] = _validate_hours(value, f"practice_plan.date_overrides.{key}")

    return PracticePlan(enabled=enabled, default_hours=default_hours, date_overrides=overrides)


def remaining_target_hours(
    plan: PracticePlan,
    target_date: date | datetime,
    current_hours: float,
) -> float | None:
    """Return the unfilled daily target, rounded down to 15-minute precision."""

    target = plan.target_for(target_date)
    if target is None:
        return None
    remaining = max(0.0, target - max(0.0, float(current_hours)))
    return math.floor((remaining * 60 + 1e-9) / 15) * 15 / 60


def cap_duration_minutes(
    requested_minutes: float,
    remaining_daily_hours: float | None,
    remaining_weekly_hours: float,
    *,
    minimum_minutes: int = 30,
    maximum_minutes: int = 120,
) -> int:
    """Cap a booking to daily/weekly budgets and 15-minute Asimut increments."""

    caps = [float(requested_minutes), float(maximum_minutes), max(0.0, remaining_weekly_hours) * 60]
    if remaining_daily_hours is not None:
        caps.append(max(0.0, remaining_daily_hours) * 60)
    capped = math.floor((min(caps) + 1e-9) / 15) * 15
    return int(capped) if capped >= minimum_minutes else 0


def max_bookings_for_budget(
    hours: float | None,
    *,
    minimum_booking_hours: float = 0.5,
) -> int | None:
    """Return enough attempts to fill a budget using minimum-size bookings.

    Availability can be fragmented into 30-minute gaps.  Basing the ceiling on
    the two-hour maximum would stop a two-hour plan after its first 30-minute
    success, so the safe ceiling is the number of minimum-size bookings that
    could be required.
    """

    if hours is None:
        return None
    if hours < MIN_TARGET_HOURS:
        return 0
    if minimum_booking_hours <= 0:
        raise ValueError("minimum_booking_hours must be positive")
    return max(1, math.ceil(hours / minimum_booking_hours))
