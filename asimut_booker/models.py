"""Typed domain models shared by discovery, policy, planning, and execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from .intervals import Interval, merge_intervals


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class AvailabilityState(str, Enum):
    """A fail-closed classification of a room/time observation or decision."""

    UNKNOWN = "unknown"
    CLOSED = "closed"
    OCCUPIED = "occupied"
    FREE_OUTSIDE_HORIZON = "free_outside_horizon"
    FREE_RULE_BLOCKED = "free_rule_blocked"
    BOOKABLE = "bookable"
    FORM_REJECTED = "form_rejected"
    VERIFIED_BOOKED = "verified_booked"


class PolicyReason(str, Enum):
    """Stable reason codes for policy decisions and user-facing diagnostics."""

    ROOM_DISABLED = "room_disabled"
    DURATION_TOO_SHORT = "duration_too_short"
    DURATION_TOO_LONG = "duration_too_long"
    INVALID_INCREMENT = "invalid_increment"
    ALREADY_STARTED = "already_started"
    OUTSIDE_HORIZON = "outside_horizon"
    QUOTA_EXCEEDED = "quota_exceeded"
    PERSONAL_CONFLICT = "personal_conflict"
    PEAK_QUOTA_EXCEEDED = "peak_quota_exceeded"
    SAME_ROOM_GAP = "same_room_gap"


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    """A calendar event observed from Asimut."""

    date: date
    interval: Interval
    room_id: str | None = None
    title: str = ""
    event_id: str | None = None
    is_reservation: bool = False
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class RoomPolicy:
    """Configuration and priority for a single room."""

    room_id: str
    horizon_days: int
    priority: int = 0
    room_type: str = "practice"
    enabled: bool = True

    def __post_init__(self) -> None:
        room_id = self.room_id.strip()
        if not room_id:
            raise ValueError("room_id must not be empty")
        room_type = self.room_type.strip()
        if not room_type:
            raise ValueError("room_type must not be empty")
        if self.horizon_days not in {3, 5, 7}:
            raise ValueError("room horizon must be one of 3, 5, or 7 days")
        if self.priority < 0:
            raise ValueError("room priority must be non-negative")
        object.__setattr__(self, "room_id", room_id)
        object.__setattr__(self, "room_type", room_type)


@dataclass(frozen=True, slots=True)
class RoomObservation:
    """Validated visually-free intervals for one room on one local date."""

    room_id: str
    date: date
    free_intervals: tuple[Interval, ...]
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.observed_at, "observed_at")
        room_id = self.room_id.strip()
        if not room_id:
            raise ValueError("room_id must not be empty")
        object.__setattr__(self, "room_id", room_id)
        object.__setattr__(self, "free_intervals", tuple(merge_intervals(self.free_intervals)))


@dataclass(frozen=True, slots=True)
class OverviewSnapshot:
    """A page-contract-validated overview snapshot for exactly one date."""

    displayed_date: date
    observed_at: datetime
    rooms: tuple[RoomObservation, ...]
    validated: bool = True
    contract_version: str = ""

    def __post_init__(self) -> None:
        _require_aware(self.observed_at, "observed_at")
        object.__setattr__(self, "rooms", tuple(self.rooms))
        room_ids: set[str] = set()
        for room in self.rooms:
            if room.date != self.displayed_date:
                raise ValueError("room observation date does not match snapshot date")
            if room.room_id in room_ids:
                raise ValueError(f"duplicate room observation: {room.room_id}")
            room_ids.add(room.room_id)
        if self.validated and not self.rooms:
            raise ValueError("a validated overview snapshot must contain rooms")


@dataclass(frozen=True, slots=True)
class BookingIntent:
    """A concrete interval to submit, plus the desired eventual end time."""

    date: date
    room_id: str
    interval: Interval
    target_end: int | None = None

    def __post_init__(self) -> None:
        room_id = self.room_id.strip()
        if not room_id:
            raise ValueError("room_id must not be empty")
        object.__setattr__(self, "room_id", room_id)
        target_end = self.interval.end if self.target_end is None else self.target_end
        if not self.interval.end <= target_end <= 24 * 60:
            raise ValueError("target_end must be at or after the submitted end")
        object.__setattr__(self, "target_end", target_end)

    @property
    def key(self) -> tuple[str, str, int]:
        """Stable idempotency key shared by initial booking and extensions."""

        return (self.date.isoformat(), self.room_id, self.interval.start)

    @property
    def target_interval(self) -> Interval:
        """Return the intended final interval."""

        assert self.target_end is not None
        return Interval(self.interval.start, self.target_end)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """The result of evaluating a booking intent against all local rules."""

    allowed: bool
    state: AvailabilityState
    reasons: tuple[PolicyReason, ...] = ()
    release_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(self.reasons))
        if self.release_at is not None:
            _require_aware(self.release_at, "release_at")


@dataclass(frozen=True, slots=True)
class BookingCandidate:
    """A ranked potential booking produced from a validated room snapshot."""

    intent: BookingIntent
    release_at: datetime
    state: AvailabilityState
    reasons: tuple[PolicyReason, ...] = ()
    room_priority: int = 0
    preferred: bool = False
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_aware(self.release_at, "release_at")
        if self.observed_at is not None:
            _require_aware(self.observed_at, "observed_at")
        object.__setattr__(self, "reasons", tuple(self.reasons))

    @property
    def room_id(self) -> str:
        return self.intent.room_id

    @property
    def date(self) -> date:
        return self.intent.date

    @property
    def interval(self) -> Interval:
        return self.intent.interval

    @property
    def target_interval(self) -> Interval:
        return self.intent.target_interval
