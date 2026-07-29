"""Conversions between validated Asimut observations and the pure domain.

Keeping these translations in one place prevents browser-shaped data from
leaking into booking policy.  Missing quota data is considered a broken page
contract for write-capable runs; it is never interpreted as unlimited quota.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Iterable

from .agenda import AgendaEvent, AgendaObservation
from .config import AppConfig
from .errors import PageContractError
from .intervals import Interval
from .models import (
    CalendarEvent,
    OverviewSnapshot,
    RoomObservation,
    RoomPolicy,
)
from .overview import OverviewObservation
from .policies import (
    LONDON,
    BookingRules,
    PolicyContext,
    london_datetime,
    peak_minutes_used,
)


def domain_room_policies(config: AppConfig) -> tuple[RoomPolicy, ...]:
    """Return enabled, explicitly configured room policies."""

    return tuple(
        RoomPolicy(
            room_id=item.room_id,
            horizon_days=item.horizon_days,
            priority=item.priority,
            room_type=item.room_type,
            enabled=item.enabled,
        )
        for item in config.enabled_rooms
    )


def domain_rules(config: AppConfig) -> BookingRules:
    """Convert typed configuration to the policy engine's value object."""

    return config.to_domain_rules()


def domain_agenda_events(
    observation: AgendaObservation,
) -> tuple[CalendarEvent, ...]:
    """Translate exact agenda rows, retaining cancelled rows for diagnostics."""

    return tuple(_domain_agenda_event(item) for item in observation.events)


def _domain_agenda_event(item: AgendaEvent) -> CalendarEvent:
    return CalendarEvent(
        date=item.event_date,
        interval=Interval(item.start_minute, item.end_minute),
        room_id=item.room_name,
        title=item.title,
        event_id=item.event_id,
        is_reservation=item.is_reservation,
        cancelled=item.is_cancelled,
    )


def policy_context(
    observation: AgendaObservation,
    *,
    now: datetime,
    rules: BookingRules,
) -> PolicyContext:
    """Build context using both agenda events and Asimut's quota counter.

    The agenda may not render reservations beyond the scanned range.  The
    quota label is therefore used as a lower bound on consumed quota while the
    explicit events remain necessary for conflicts, peak time, and room gaps.
    """

    if observation.rolling_quota_remaining is None:
        raise PageContractError("agenda does not expose rolling quota; booking must fail closed")
    events = domain_agenda_events(observation)
    local_now = now.astimezone(LONDON)
    rolling_end = local_now + timedelta(days=7)
    visible_used = sum(
        item.interval.duration
        for item in events
        if item.is_reservation
        and not item.cancelled
        and london_datetime(item.date, item.interval.end) > local_now
        and london_datetime(item.date, item.interval.start) < rolling_end
    )
    counter_used = max(0, rules.future_quota_minutes - observation.rolling_quota_remaining)
    return PolicyContext(
        events=events,
        quota_used_minutes=max(visible_used, counter_used),
    )


def snapshot_from_overview(
    observation: OverviewObservation,
    *,
    observed_at: datetime,
) -> OverviewSnapshot:
    """Convert the validated SVG observation to pure free intervals."""

    return OverviewSnapshot(
        displayed_date=observation.observed_date,
        observed_at=observed_at,
        rooms=tuple(
            RoomObservation(
                room_id=room.name,
                date=observation.observed_date,
                free_intervals=tuple(Interval(item.start, item.end) for item in room.free),
                observed_at=observed_at,
            )
            for room in observation.rooms
        ),
        validated=True,
        contract_version=observation.contract_version,
    )


def rules_with_server_peak_limit(
    rules: BookingRules,
    context: PolicyContext,
    overview: OverviewObservation,
) -> BookingRules:
    """Constrain local peak policy by the target day's live quota counter."""

    if overview.observed_date.weekday() not in rules.peak_weekdays:
        return rules
    if overview.peak_quota_remaining is None:
        raise PageContractError("overview does not expose peak quota for a peak-hours day")
    used = peak_minutes_used(overview.observed_date, context, rules)
    entitlement_from_counter = used + overview.peak_quota_remaining
    return replace(
        rules,
        peak_limit_minutes=min(
            rules.peak_limit_minutes,
            entitlement_from_counter,
        ),
    )


def context_without_event(
    context: PolicyContext,
    *,
    event_id: str,
    current_duration: int,
) -> PolicyContext:
    """Remove an event when validating replacement/extension state."""

    events = tuple(item for item in context.events if item.event_id != event_id)
    return PolicyContext(
        events=events,
        quota_used_minutes=max(0, context.quota_used_minutes - current_duration),
    )


def context_with_event(
    context: PolicyContext,
    event: AgendaEvent,
) -> PolicyContext:
    """Return context updated after a remotely verified mutation."""

    converted = _domain_agenda_event(event)
    without_previous = tuple(item for item in context.events if item.event_id != converted.event_id)
    previous_duration = sum(
        item.interval.duration
        for item in context.events
        if item.event_id == converted.event_id and item.is_reservation and not item.cancelled
    )
    new_duration = (
        converted.interval.duration if converted.is_reservation and not converted.cancelled else 0
    )
    return PolicyContext(
        events=(*without_previous, converted),
        quota_used_minutes=max(
            0,
            context.quota_used_minutes - previous_duration + new_duration,
        ),
    )


def context_with_target_end(
    context: PolicyContext,
    event: AgendaEvent,
    target_end_minute: int,
) -> PolicyContext:
    """Reserve a verified booking's desired extension tail while planning.

    This does not claim that the tail is already booked remotely.  It prevents
    later local plans from consuming time and quota needed by a pending
    extension.
    """

    if target_end_minute < event.end_minute:
        raise ValueError("target end may not precede the verified event end")
    converted = _domain_agenda_event(event)
    target = replace(
        converted,
        interval=Interval(converted.interval.start, target_end_minute),
    )
    without_previous = tuple(item for item in context.events if item.event_id != target.event_id)
    previous_duration = sum(
        item.interval.duration
        for item in context.events
        if item.event_id == target.event_id and item.is_reservation and not item.cancelled
    )
    return PolicyContext(
        events=(*without_previous, target),
        quota_used_minutes=max(
            0,
            context.quota_used_minutes - previous_duration + target.interval.duration,
        ),
    )


def configured_room_names(config: AppConfig) -> frozenset[str]:
    return frozenset(item.room_id for item in config.enabled_rooms)


def missing_configured_rooms(
    overview: OverviewObservation,
    configured: Iterable[str],
) -> tuple[str, ...]:
    observed = frozenset(overview.rooms_by_name)
    return tuple(sorted(set(configured).difference(observed)))
