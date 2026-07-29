"""Pure booking-window and RWCMD policy evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .intervals import Interval, floor_to_increment
from .models import (
    AvailabilityState,
    BookingIntent,
    CalendarEvent,
    PolicyDecision,
    PolicyReason,
    RoomPolicy,
)

LONDON = ZoneInfo("Europe/London")


def require_aware(value: datetime, field_name: str = "datetime") -> None:
    """Raise if ``value`` lacks an explicit timezone."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def london_datetime(day: date, minute: int) -> datetime:
    """Construct an aware Europe/London datetime from a local date and minute."""

    if not 0 <= minute <= 24 * 60:
        raise ValueError("minute must be between 0 and 1440")
    if minute == 24 * 60:
        return datetime.combine(day + timedelta(days=1), time.min, tzinfo=LONDON)
    hour, minute_of_hour = divmod(minute, 60)
    return datetime.combine(day, time(hour=hour, minute=minute_of_hour), tzinfo=LONDON)


def horizon_release_at(target_date: date, booking_end_minute: int, horizon_days: int) -> datetime:
    """Return when an interval ending at ``booking_end_minute`` is released.

    Asimut's rolling horizon applies to the end of a booking.  Therefore the
    first legal 30-minute interval starting at 10:00 in a seven-day room is
    released at 10:30 exactly seven local calendar days earlier.
    """

    if horizon_days not in {3, 5, 7}:
        raise ValueError("horizon_days must be one of 3, 5, or 7")
    return london_datetime(target_date, booking_end_minute) - timedelta(days=horizon_days)


def minimum_booking_release_at(
    target_date: date,
    start_minute: int,
    horizon_days: int,
    minimum_duration: int = 30,
) -> datetime:
    """Return the first release instant for a booking beginning at ``start``."""

    return horizon_release_at(target_date, start_minute + minimum_duration, horizon_days)


def released_end_minute(
    target_date: date,
    horizon_days: int,
    now: datetime,
    *,
    increment: int = 15,
) -> int:
    """Return the latest released end minute on ``target_date``.

    The result is rounded down to the site's booking increment.  ``0`` means no
    end time on the target date has released; ``1440`` means the whole date is
    inside the rolling horizon.
    """

    require_aware(now, "now")
    local_now = now.astimezone(LONDON)
    frontier = local_now + timedelta(days=horizon_days)
    if frontier.date() < target_date:
        return 0
    if frontier.date() > target_date:
        return 24 * 60
    minute = frontier.hour * 60 + frontier.minute
    return floor_to_increment(minute, increment)


@dataclass(frozen=True, slots=True)
class BookingRules:
    """Institutional rules expressed entirely in integer minutes."""

    minimum_duration: int = 30
    maximum_duration: int = 120
    booking_increment: int = 15
    future_quota_minutes: int = 28 * 60
    peak_window: Interval = Interval(9 * 60, 16 * 60)
    peak_limit_minutes: int = 2 * 60
    peak_weekdays: frozenset[int] = frozenset({0, 1, 2, 3, 4})
    same_room_gap_minutes: int = 60

    def __post_init__(self) -> None:
        if self.minimum_duration <= 0:
            raise ValueError("minimum_duration must be positive")
        if self.maximum_duration < self.minimum_duration:
            raise ValueError("maximum_duration must be >= minimum_duration")
        if self.booking_increment <= 0:
            raise ValueError("booking_increment must be positive")
        if self.minimum_duration % self.booking_increment:
            raise ValueError("minimum_duration must align to booking_increment")
        if self.maximum_duration % self.booking_increment:
            raise ValueError("maximum_duration must align to booking_increment")
        if self.future_quota_minutes < 0 or self.peak_limit_minutes < 0:
            raise ValueError("quota limits must be non-negative")
        if self.same_room_gap_minutes < 0:
            raise ValueError("same_room_gap_minutes must be non-negative")
        if any(day < 0 or day > 6 for day in self.peak_weekdays):
            raise ValueError("peak weekdays must be in the range 0..6")


@dataclass(frozen=True, slots=True)
class PolicyContext:
    """Authoritative state used when evaluating a potential booking."""

    events: tuple[CalendarEvent, ...] = ()
    quota_used_minutes: int = 0

    def __post_init__(self) -> None:
        if self.quota_used_minutes < 0:
            raise ValueError("quota_used_minutes must be non-negative")
        object.__setattr__(self, "events", tuple(self.events))


def peak_overlap_minutes(day: date, interval: Interval, rules: BookingRules) -> int:
    """Return how many minutes of ``interval`` count as peak time."""

    if day.weekday() not in rules.peak_weekdays:
        return 0
    overlap = interval.intersection(rules.peak_window)
    return overlap.duration if overlap else 0


def peak_minutes_used(day: date, context: PolicyContext, rules: BookingRules) -> int:
    """Calculate existing reservation peak usage for one local date."""

    return sum(
        peak_overlap_minutes(day, event.interval, rules)
        for event in context.events
        if event.date == day and event.is_reservation and not event.cancelled
    )


def interval_gap(left: Interval, right: Interval) -> int:
    """Return separation in minutes, or ``-1`` when intervals overlap.

    Touching intervals have a gap of zero, which is intentionally rejected by
    a positive same-room gap requirement.
    """

    if left.overlaps(right):
        return -1
    if left.end <= right.start:
        return right.start - left.end
    return left.start - right.end


def _append_once(reasons: list[PolicyReason], reason: PolicyReason) -> None:
    if reason not in reasons:
        reasons.append(reason)


def evaluate_intent(
    intent: BookingIntent,
    room: RoomPolicy,
    now: datetime,
    *,
    rules: BookingRules | None = None,
    context: PolicyContext | None = None,
    check_horizon: bool = True,
) -> PolicyDecision:
    """Evaluate an intent against horizon, quota, peak, conflicts, and room gap.

    The function is deterministic and side-effect free.  A naïve ``now`` is
    rejected rather than silently interpreted in the machine's local timezone.
    """

    rules = rules or BookingRules()
    context = context or PolicyContext()
    require_aware(now, "now")
    if intent.room_id != room.room_id:
        raise ValueError("intent room does not match RoomPolicy")

    interval = intent.interval
    release_at = horizon_release_at(intent.date, interval.end, room.horizon_days)
    reasons: list[PolicyReason] = []

    if not room.enabled:
        _append_once(reasons, PolicyReason.ROOM_DISABLED)
    if interval.duration < rules.minimum_duration:
        _append_once(reasons, PolicyReason.DURATION_TOO_SHORT)
    if interval.duration > rules.maximum_duration:
        _append_once(reasons, PolicyReason.DURATION_TOO_LONG)
    if interval.start % rules.booking_increment or interval.end % rules.booking_increment:
        _append_once(reasons, PolicyReason.INVALID_INCREMENT)

    local_now = now.astimezone(LONDON)
    if london_datetime(intent.date, interval.start) <= local_now:
        _append_once(reasons, PolicyReason.ALREADY_STARTED)
    if check_horizon and local_now < release_at:
        _append_once(reasons, PolicyReason.OUTSIDE_HORIZON)

    if context.quota_used_minutes + interval.duration > rules.future_quota_minutes:
        _append_once(reasons, PolicyReason.QUOTA_EXCEEDED)

    active_events = [
        event for event in context.events if event.date == intent.date and not event.cancelled
    ]
    if any(interval.overlaps(event.interval) for event in active_events):
        _append_once(reasons, PolicyReason.PERSONAL_CONFLICT)

    new_peak = peak_overlap_minutes(intent.date, interval, rules)
    existing_peak = peak_minutes_used(intent.date, context, rules)
    if existing_peak + new_peak > rules.peak_limit_minutes:
        _append_once(reasons, PolicyReason.PEAK_QUOTA_EXCEEDED)

    for event in active_events:
        if not event.is_reservation or event.room_id != intent.room_id:
            continue
        if interval_gap(interval, event.interval) < rules.same_room_gap_minutes:
            _append_once(reasons, PolicyReason.SAME_ROOM_GAP)
            break

    if not reasons:
        return PolicyDecision(
            allowed=True,
            state=AvailabilityState.BOOKABLE,
            release_at=release_at,
        )

    if reasons == [PolicyReason.OUTSIDE_HORIZON]:
        state = AvailabilityState.FREE_OUTSIDE_HORIZON
    else:
        state = AvailabilityState.FREE_RULE_BLOCKED
    return PolicyDecision(
        allowed=False,
        state=state,
        reasons=tuple(reasons),
        release_at=release_at,
    )
