"""Validated run-scoped room policy derived from Asimut's live catalog.

The persistent catalog is display-only.  A booking run may construct this
policy only from a catalog that was freshly and completely observed in the
current authenticated browser session.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import Any, Mapping

from room_preferences import (
    RoomPreferences,
    RoomPreferencesError,
    filter_effective_rooms,
)


class LiveRoomPolicyError(ValueError):
    """Raised when live site policy and saved preferences cannot be combined."""


def _positive_quarter_minutes(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise LiveRoomPolicyError(f"{label} must be a positive integer")
    if value % 15:
        raise LiveRoomPolicyError(f"{label} must use 15-minute steps")
    return value


def _aware_datetime(value: Any, label: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise LiveRoomPolicyError(f"{label} must be a timezone-aware datetime")
    return value


@dataclass(frozen=True)
class LiveRoomPolicy:
    """One immutable room/window policy for a single booking pass."""

    observed_at: datetime
    booking_horizon: datetime
    global_horizon_minutes: int
    room_order: tuple[str, ...]
    room_horizon_minutes: Mapping[str, int]
    room_metadata: Mapping[str, Mapping[str, Any]]
    site_minimum_booking_minutes: int
    site_maximum_booking_minutes: int
    site_minimum_booking_gap_minutes: int
    minimum_block_minutes: int
    allow_fragmented_sessions: bool
    all_room_names: tuple[str, ...] = ()
    site_clock_offset_bounds: tuple[float, float] | None = None

    def horizon_minutes_for(self, room: str) -> int:
        """Return one proven room horizon; unknown/excluded rooms fail closed."""

        try:
            return self.room_horizon_minutes[room]
        except (KeyError, TypeError) as exc:
            raise LiveRoomPolicyError(
                f"Room {room!r} has no fresh eligible horizon in this run"
            ) from exc

    def booking_dates(self, today: date | None = None) -> tuple[date, ...]:
        """Return every local calendar date exposed by the live global cutoff."""

        if today is None:
            today = datetime.now().astimezone().date()
        if isinstance(today, datetime) or not isinstance(today, date):
            raise LiveRoomPolicyError("today must be a date")
        final_date = self.booking_horizon.astimezone().date()
        if final_date < today:
            raise LiveRoomPolicyError("The live booking horizon is already in the past")
        return tuple(
            today + timedelta(days=offset)
            for offset in range((final_date - today).days + 1)
        )

    def day_offsets(self, today: date | None = None) -> tuple[int, ...]:
        """Return live-window offsets for calendar navigation."""

        dates = self.booking_dates(today)
        first = dates[0]
        return tuple((value - first).days for value in dates)


def build_live_room_policy(catalog: Any, preferences: RoomPreferences) -> LiveRoomPolicy:
    """Combine one complete fresh site catalog with strict saved preferences."""

    if not isinstance(preferences, RoomPreferences):
        raise TypeError("preferences must be a RoomPreferences instance")
    require_fresh = getattr(catalog, "require_fresh", None)
    if not callable(require_fresh):
        raise LiveRoomPolicyError("Live room catalog does not expose freshness proof")
    try:
        require_fresh()
    except Exception as exc:
        raise LiveRoomPolicyError(
            "A complete room catalog freshly observed in this run is required"
        ) from exc
    if getattr(catalog, "complete", False) is not True:
        raise LiveRoomPolicyError("The live room catalog is incomplete")

    observed_at = _aware_datetime(getattr(catalog, "observed_at", None), "observed_at")
    booking_horizon = _aware_datetime(
        getattr(catalog, "booking_horizon", None), "booking_horizon"
    )
    global_horizon_minutes = _positive_quarter_minutes(
        getattr(catalog, "global_horizon_minutes", None),
        "global_horizon_minutes",
    )
    site_minimum = _positive_quarter_minutes(
        getattr(catalog, "minimum_booking_minutes", None),
        "minimum_booking_minutes",
    )
    site_maximum = _positive_quarter_minutes(
        getattr(catalog, "maximum_booking_minutes", None),
        "maximum_booking_minutes",
    )
    site_gap = _positive_quarter_minutes(
        getattr(catalog, "minimum_booking_gap_minutes", None),
        "minimum_booking_gap_minutes",
    )
    if site_minimum > site_maximum:
        raise LiveRoomPolicyError("The live minimum booking length exceeds the maximum")
    if not site_minimum <= preferences.minimum_block_minutes <= site_maximum:
        raise LiveRoomPolicyError(
            "The selected minimum block length is outside Asimut's current "
            f"{site_minimum}-{site_maximum} minute limits"
        )

    try:
        metadata = catalog.as_live_metadata()
        room_order = filter_effective_rooms(preferences, metadata)
    except (AttributeError, RoomPreferencesError, TypeError, ValueError) as exc:
        raise LiveRoomPolicyError(f"Live room metadata is invalid: {exc}") from exc
    if not room_order:
        raise LiveRoomPolicyError(
            "Room exclusions and requirements leave no eligible live rooms"
        )

    catalog_rooms = getattr(catalog, "rooms", None)
    if not isinstance(catalog_rooms, tuple):
        raise LiveRoomPolicyError("Live catalog rooms must be an immutable tuple")
    by_name: dict[str, Any] = {}
    for room in catalog_rooms:
        name = getattr(room, "name", None)
        if not isinstance(name, str) or not name or name in by_name:
            raise LiveRoomPolicyError("Live catalog contains an invalid or duplicate room")
        by_name[name] = room

    horizons: dict[str, int] = {}
    selected_metadata: dict[str, Mapping[str, Any]] = {}
    for room_name in room_order:
        room = by_name.get(room_name)
        if room is None:
            raise LiveRoomPolicyError(
                f"Eligible room {room_name} is missing from the live catalog"
            )
        horizon = _positive_quarter_minutes(
            getattr(room, "horizon_minutes", None),
            f"{room_name}.horizon_minutes",
        )
        if horizon > global_horizon_minutes:
            raise LiveRoomPolicyError(
                f"{room_name} horizon exceeds the live global booking horizon"
            )
        horizons[room_name] = horizon
        selected_metadata[room_name] = MappingProxyType(dict(metadata[room_name]))

    raw_clock_bounds = getattr(catalog, "site_clock_offset_bounds", None)
    clock_bounds: tuple[float, float] | None = None
    if raw_clock_bounds is not None:
        if (
            not isinstance(raw_clock_bounds, tuple)
            or len(raw_clock_bounds) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                for value in raw_clock_bounds
            )
        ):
            raise LiveRoomPolicyError("Live site clock bounds are invalid")
        lower, upper = (float(value) for value in raw_clock_bounds)
        if (
            not (math.isfinite(lower) and math.isfinite(upper))
            or lower > upper
            or upper - lower > 2
        ):
            raise LiveRoomPolicyError("Live site clock bounds are too uncertain")
        clock_bounds = (lower, upper)

    return LiveRoomPolicy(
        observed_at=observed_at,
        booking_horizon=booking_horizon,
        global_horizon_minutes=global_horizon_minutes,
        room_order=tuple(room_order),
        room_horizon_minutes=MappingProxyType(horizons),
        room_metadata=MappingProxyType(selected_metadata),
        site_minimum_booking_minutes=site_minimum,
        site_maximum_booking_minutes=site_maximum,
        site_minimum_booking_gap_minutes=site_gap,
        minimum_block_minutes=preferences.minimum_block_minutes,
        allow_fragmented_sessions=preferences.allow_fragmented_sessions,
        all_room_names=tuple(by_name),
        site_clock_offset_bounds=clock_bounds,
    )


def format_horizon_minutes(minutes: int) -> str:
    """Format an arbitrary 15-minute site horizon without assuming day buckets."""

    minutes = _positive_quarter_minutes(minutes, "horizon minutes")
    days, remainder = divmod(minutes, 24 * 60)
    hours, remainder = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if remainder:
        parts.append(f"{remainder}m")
    return " ".join(parts)
