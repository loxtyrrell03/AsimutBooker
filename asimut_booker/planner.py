"""Pure candidate generation and ordering for ASIMUT room bookings.

The planner consumes a validated HTML-derived overview snapshot. It never
drives a browser or mutates state, keeping availability interpretation
deterministic and easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Mapping, Sequence

from .intervals import Interval, ceil_to_increment, floor_to_increment
from .models import (
    AvailabilityState,
    BookingCandidate,
    BookingIntent,
    OverviewSnapshot,
    PolicyDecision,
    RoomPolicy,
)
from .policies import (
    BookingRules,
    PolicyContext,
    evaluate_intent,
    minimum_booking_release_at,
    released_end_minute,
    require_aware,
)


class InvalidSnapshotError(ValueError):
    """Raised when candidate generation is attempted from untrusted page data."""


@dataclass(frozen=True, slots=True)
class PlannerOptions:
    """Controls desired duration and optional user-preferred time windows.

    In strict mode candidates must fit completely inside a preference window.
    In non-strict mode times outside the preferences remain fallback choices.
    A candidate beginning inside a preference is always capped at its end.
    """

    desired_duration: int = 120
    preference_windows: tuple[Interval, ...] = ()
    strict_preferences: bool = False
    include_blocked: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.desired_duration, int):
            raise TypeError("desired_duration must be an integer number of minutes")
        if self.desired_duration <= 0:
            raise ValueError("desired_duration must be positive")
        object.__setattr__(
            self,
            "preference_windows",
            tuple(sorted(set(self.preference_windows))),
        )


def candidate_start_minutes(
    free: Interval,
    *,
    increment: int = 15,
    minimum_duration: int = 30,
    preference_windows: Sequence[Interval] = (),
) -> tuple[int, ...]:
    """Return every viable increment plus relevant preference boundaries."""

    if increment <= 0:
        raise ValueError("increment must be positive")
    if minimum_duration <= 0:
        raise ValueError("minimum_duration must be positive")

    latest = free.end - minimum_duration
    if latest < free.start:
        return ()

    starts: set[int] = set()
    minute = ceil_to_increment(free.start, increment)
    while minute <= latest:
        starts.add(minute)
        minute += increment

    for window in preference_windows:
        for boundary in (window.start, window.end):
            if free.start <= boundary <= latest:
                starts.add(boundary)

    return tuple(sorted(starts))


def generate_candidates(
    snapshot: OverviewSnapshot,
    room_policies: Mapping[str, RoomPolicy] | Iterable[RoomPolicy],
    now: datetime,
    *,
    rules: BookingRules | None = None,
    context: PolicyContext | None = None,
    options: PlannerOptions | None = None,
) -> list[BookingCandidate]:
    """Generate rule-aware candidates from one validated daily snapshot.

    The final target is checked against quota, conflicts, peak rules, and room
    gaps. The submitted interval is shortened to the portion currently
    released by the exact room horizon, permitting safe later extension.
    """

    require_aware(now)
    if not snapshot.validated:
        raise InvalidSnapshotError(
            "overview snapshot is not validated; availability must fail closed"
        )

    active_rules = rules or BookingRules()
    active_context = context or PolicyContext()
    active_options = options or PlannerOptions()
    policies = _policy_map(room_policies)

    if active_options.desired_duration < active_rules.minimum_duration:
        raise ValueError("desired_duration is shorter than the booking minimum")

    candidates: list[BookingCandidate] = []
    seen: set[tuple[str, int, int]] = set()

    for observation in snapshot.rooms:
        policy = policies.get(observation.room_id)
        if policy is None:
            # Unknown rooms are never inferred to be safe to book.
            continue

        for free in observation.free_intervals:
            starts = candidate_start_minutes(
                free,
                increment=active_rules.booking_increment,
                minimum_duration=active_rules.minimum_duration,
                preference_windows=active_options.preference_windows,
            )
            for start in starts:
                preference = _containing_preference(
                    start,
                    active_rules.minimum_duration,
                    active_options.preference_windows,
                )
                preferred = preference is not None
                if active_options.strict_preferences and not preferred:
                    continue

                target_cap = min(
                    free.end,
                    start + active_options.desired_duration,
                    start + active_rules.maximum_duration,
                )
                if preference is not None:
                    target_cap = min(target_cap, preference.end)
                target_cap = floor_to_increment(target_cap, active_rules.booking_increment)

                target_intent, target_decision = _longest_policy_valid_target(
                    day=snapshot.displayed_date,
                    room_id=policy.room_id,
                    start=start,
                    target_cap=target_cap,
                    policy=policy,
                    now=now,
                    rules=active_rules,
                    context=active_context,
                )

                if target_intent is None:
                    if not active_options.include_blocked:
                        continue
                    minimum_end = start + active_rules.minimum_duration
                    if minimum_end > free.end:
                        continue
                    intent = BookingIntent(
                        date=snapshot.displayed_date,
                        room_id=policy.room_id,
                        interval=Interval(start, minimum_end),
                        target_end=minimum_end,
                    )
                    decision = target_decision
                    if decision is None:
                        decision = evaluate_intent(
                            intent,
                            policy,
                            now,
                            rules=active_rules,
                            context=active_context,
                            check_horizon=True,
                        )
                    release_at = minimum_booking_release_at(
                        snapshot.displayed_date,
                        start,
                        policy.horizon_days,
                        minimum_duration=active_rules.minimum_duration,
                    )
                    key = (policy.room_id, start, minimum_end)
                    if key not in seen:
                        seen.add(key)
                        candidates.append(
                            BookingCandidate(
                                intent=intent,
                                release_at=release_at,
                                state=decision.state,
                                reasons=decision.reasons,
                                room_priority=policy.priority,
                                preferred=preferred,
                                observed_at=observation.observed_at,
                            )
                        )
                    continue

                target_end = target_intent.interval.end
                release_at = minimum_booking_release_at(
                    snapshot.displayed_date,
                    start,
                    policy.horizon_days,
                    minimum_duration=active_rules.minimum_duration,
                )
                current_end = start + active_rules.minimum_duration
                if now >= release_at:
                    current_end = min(
                        target_end,
                        released_end_minute(
                            snapshot.displayed_date,
                            policy.horizon_days,
                            now,
                            increment=active_rules.booking_increment,
                        ),
                    )
                    current_end = floor_to_increment(current_end, active_rules.booking_increment)
                    current_end = max(current_end, start + active_rules.minimum_duration)

                intent = BookingIntent(
                    date=snapshot.displayed_date,
                    room_id=policy.room_id,
                    interval=Interval(start, current_end),
                    target_end=target_end,
                )
                decision = evaluate_intent(
                    intent,
                    policy,
                    now,
                    rules=active_rules,
                    context=active_context,
                    check_horizon=True,
                )
                key = (policy.room_id, start, target_end)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    BookingCandidate(
                        intent=intent,
                        release_at=release_at,
                        state=decision.state,
                        reasons=decision.reasons,
                        room_priority=policy.priority,
                        preferred=preferred,
                        observed_at=observation.observed_at,
                    )
                )

    return candidates


def order_snipe_candidates(
    candidates: Iterable[BookingCandidate],
) -> list[BookingCandidate]:
    """Order snipes by release instant, then preference and room priority."""

    return sorted(
        candidates,
        key=lambda candidate: (
            candidate.release_at,
            not candidate.preferred,
            candidate.room_priority,
            candidate.date,
            candidate.interval.start,
            candidate.room_id,
        ),
    )


def upcoming_snipe_candidates(
    candidates: Iterable[BookingCandidate],
    now: datetime,
    *,
    within: timedelta | None = None,
) -> list[BookingCandidate]:
    """Return unreleased candidates whose minimum booking is due soon."""

    require_aware(now)
    if within is not None and within < timedelta(0):
        raise ValueError("within must not be negative")
    upper = now + within if within is not None else None
    selected = [
        candidate
        for candidate in candidates
        if candidate.state is AvailabilityState.FREE_OUTSIDE_HORIZON
        and candidate.release_at >= now
        and (upper is None or candidate.release_at <= upper)
    ]
    return order_snipe_candidates(selected)


def bookable_candidates(
    candidates: Iterable[BookingCandidate],
) -> list[BookingCandidate]:
    """Return currently bookable candidates in preference order."""

    return sorted(
        (candidate for candidate in candidates if candidate.state is AvailabilityState.BOOKABLE),
        key=lambda candidate: (
            not candidate.preferred,
            candidate.room_priority,
            candidate.date,
            candidate.interval.start,
            -candidate.target_interval.duration,
            candidate.room_id,
        ),
    )


def _policy_map(
    room_policies: Mapping[str, RoomPolicy] | Iterable[RoomPolicy],
) -> dict[str, RoomPolicy]:
    if isinstance(room_policies, Mapping):
        policies = dict(room_policies)
        for room_id, policy in policies.items():
            if room_id != policy.room_id:
                raise ValueError(f"room policy key {room_id!r} does not match {policy.room_id!r}")
        return policies

    policies: dict[str, RoomPolicy] = {}
    for policy in room_policies:
        if policy.room_id in policies:
            raise ValueError(f"duplicate room policy for {policy.room_id!r}")
        policies[policy.room_id] = policy
    return policies


def _containing_preference(
    start: int,
    minimum_duration: int,
    preference_windows: Sequence[Interval],
) -> Interval | None:
    containing = [
        window
        for window in preference_windows
        if window.start <= start and start + minimum_duration <= window.end
    ]
    if not containing:
        return None
    return max(containing, key=lambda window: (window.end, -window.start))


def _longest_policy_valid_target(
    *,
    day: date,
    room_id: str,
    start: int,
    target_cap: int,
    policy: RoomPolicy,
    now: datetime,
    rules: BookingRules,
    context: PolicyContext,
) -> tuple[BookingIntent | None, PolicyDecision | None]:
    minimum_end = start + rules.minimum_duration
    last_decision: PolicyDecision | None = None
    end = target_cap
    while end >= minimum_end:
        intent = BookingIntent(
            date=day,
            room_id=room_id,
            interval=Interval(start, end),
            target_end=end,
        )
        decision = evaluate_intent(
            intent,
            policy,
            now,
            rules=rules,
            context=context,
            check_horizon=False,
        )
        last_decision = decision
        if decision.allowed:
            return intent, decision
        end -= rules.booking_increment
    return None, last_decision
