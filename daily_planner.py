"""Pure opportunity planning for intelligent Asimut booking decisions.

The functions in this module never navigate Asimut and never authorize a
mutation.  They rank opportunities derived from a fresh room grid so the
runtime can decide whether a current edge belongs to the best known day plan.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from math import floor, isfinite
from typing import Iterable, Sequence


QUARTER_MINUTES = 15
DEFAULT_PEAK_START_MINUTES = 9 * 60
DEFAULT_PEAK_END_MINUTES = 16 * 60


def _require_quarter_minutes(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < 0 or value > 24 * 60 or value % QUARTER_MINUTES:
        raise ValueError(f"{label} must be a quarter-hour minute value")
    return value


def _require_positive_quarter_duration(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value <= 0 or value % QUARTER_MINUTES:
        raise ValueError(f"{label} must be a positive 15-minute duration")
    return value


def hour_to_minutes(value: float, label: str = "hour") -> int:
    """Convert a finite quarter-hour decimal hour to minutes after midnight."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number")
    minutes = round(float(value) * 60)
    if abs(float(value) * 60 - minutes) > 1e-7:
        raise ValueError(f"{label} must use whole minutes")
    return _require_quarter_minutes(minutes, label)


def _gap_hour_to_whole_minutes(value: float, label: str) -> int:
    """Convert a finite decimal-hour site boundary without grid expansion."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number")
    numeric = float(value)
    if not isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    raw_minutes = numeric * 60
    minutes = round(raw_minutes)
    if abs(raw_minutes - minutes) > 1e-7:
        raise ValueError(f"{label} must use whole minutes")
    if minutes < 0 or minutes > 24 * 60:
        raise ValueError(f"{label} must be within one day")
    return minutes


def minutes_to_time_text(value: int) -> str:
    value = _require_quarter_minutes(value, "time")
    if value == 24 * 60:
        return "24:00"
    return f"{value // 60:02d}:{value % 60:02d}"


def interval_overlap_minutes(
    start: int,
    end: int,
    window_start: int,
    window_end: int,
) -> int:
    """Return the non-negative overlap between two minute intervals."""

    return max(0, min(end, window_end) - max(start, window_start))


def _planning_clock_minutes(planning, field: str) -> int:
    value = getattr(planning, field)
    if isinstance(value, int):
        return _require_quarter_minutes(value, field)
    if isinstance(value, str):
        pieces = value.split(":")
        if len(pieces) != 2 or not all(piece.isdigit() for piece in pieces):
            raise ValueError(f"{field} must be HH:MM")
        hour, minute = map(int, pieces)
        if hour > 23 or minute > 59:
            raise ValueError(f"{field} must be HH:MM")
        return _require_quarter_minutes(hour * 60 + minute, field)
    raise TypeError(f"{field} must be minutes or HH:MM")


@dataclass(frozen=True)
class BookingOpportunity:
    """One presently visible free interval and its exact initial unlock."""

    room: str
    target_date: date
    start_minutes: int
    end_minutes: int
    unlock_at: datetime
    room_priority: int
    initial_minutes: int
    potential_minutes: int
    preferred_minutes: int
    soft_preferred_minutes: int
    peak_minutes: int
    peak_window_start_minutes: int
    peak_window_end_minutes: int
    source_gap_start_minutes: int
    source_gap_end_minutes: int

    def __post_init__(self) -> None:
        if not isinstance(self.room, str) or not self.room.strip():
            raise ValueError("room must be a non-empty string")
        if not isinstance(self.target_date, date):
            raise TypeError("target_date must be a date")
        if not isinstance(self.unlock_at, datetime):
            raise TypeError("unlock_at must be a datetime")
        for label in (
            "start_minutes",
            "end_minutes",
            "initial_minutes",
            "potential_minutes",
            "peak_window_start_minutes",
            "peak_window_end_minutes",
            "source_gap_start_minutes",
            "source_gap_end_minutes",
        ):
            _require_quarter_minutes(getattr(self, label), label)
        if self.end_minutes <= self.start_minutes:
            raise ValueError("opportunity end must be later than start")
        if self.potential_minutes != self.end_minutes - self.start_minutes:
            raise ValueError("potential_minutes must equal the planned interval")
        if self.initial_minutes <= 0 or self.initial_minutes > self.potential_minutes:
            raise ValueError("initial_minutes must fit inside the potential interval")
        if isinstance(self.room_priority, bool) or self.room_priority < 0:
            raise ValueError("room_priority must be a non-negative integer")
        if (
            self.preferred_minutes < 0
            or self.soft_preferred_minutes < 0
            or self.peak_minutes < 0
        ):
            raise ValueError("overlap minutes cannot be negative")
        if self.peak_window_end_minutes <= self.peak_window_start_minutes:
            raise ValueError("peak window end must be after its start")

    @property
    def start_hour(self) -> float:
        return self.start_minutes / 60

    @property
    def end_hour(self) -> float:
        return self.end_minutes / 60

    @property
    def start_text(self) -> str:
        return minutes_to_time_text(self.start_minutes)

    @property
    def end_text(self) -> str:
        return minutes_to_time_text(self.end_minutes)


@dataclass(frozen=True)
class OpportunityDecision:
    """Explain whether the best current edge should be used or deferred."""

    action: str
    selected: BookingOpportunity | None
    alternatives: tuple[BookingOpportunity, ...]
    held_peak_minutes: int
    reason: str

    def __post_init__(self) -> None:
        if self.action not in {"book_now", "wait", "none"}:
            raise ValueError("unsupported opportunity decision")
        if self.action == "none" and self.selected is not None:
            raise ValueError("a none decision cannot select an opportunity")
        if self.action != "none" and self.selected is None:
            raise ValueError("a booking/wait decision requires an opportunity")


def _peak_capped_end(
    start: int,
    requested_end: int,
    remaining_peak_minutes: int,
    *,
    peak_start: int,
    peak_end: int,
) -> int:
    """Cap one contiguous interval without pretending it can skip peak time."""

    if requested_end <= peak_start or start >= peak_end:
        return requested_end
    remaining = max(0, int(floor(remaining_peak_minutes / 15)) * 15)
    if start < peak_start:
        peak_needed_to_exit = peak_end - peak_start
        if remaining >= peak_needed_to_exit:
            return requested_end
        return min(requested_end, peak_start + remaining)
    peak_needed_to_exit = peak_end - start
    if remaining >= peak_needed_to_exit:
        return requested_end
    return min(requested_end, start + remaining)


def enumerate_gap_opportunities(
    *,
    room: str,
    target_date: date,
    gap_start_hour: float,
    gap_end_hour: float,
    horizon_minutes: int,
    room_priority: int,
    planning,
    now: datetime,
    minimum_block_minutes: int,
    maximum_booking_minutes: int = 120,
    remaining_daily_minutes: int | None = None,
    remaining_weekly_minutes: int | None = None,
    remaining_peak_minutes: int = 120,
    strict_window: tuple[int, int] | None = None,
    soft_preferred_window: tuple[int, int] | None = None,
    peak_start_minutes: int = DEFAULT_PEAK_START_MINUTES,
    peak_end_minutes: int = DEFAULT_PEAK_END_MINUTES,
) -> list[BookingOpportunity]:
    """Enumerate every quarter-hour start worth considering in one free gap.

    ``unlock_at`` is when the selected minimum initial block can first be saved.
    ``end_minutes`` is the desired finished session, which may require later
    verified extensions.  The caller still has to re-read the live form before
    every mutation.
    """

    raw_gap_start = _gap_hour_to_whole_minutes(
        gap_start_hour,
        "gap_start_hour",
    )
    raw_gap_end = _gap_hour_to_whole_minutes(
        gap_end_hour,
        "gap_end_hour",
    )
    # Site events can end between quarter-hour boundaries (for example 10:20).
    # Candidate times still belong to Asimut's 15-minute grid, so round only
    # inward: never claim occupied minutes before the free start or after its
    # free end.
    gap_start = (
        (raw_gap_start + QUARTER_MINUTES - 1) // QUARTER_MINUTES
    ) * QUARTER_MINUTES
    gap_end = (raw_gap_end // QUARTER_MINUTES) * QUARTER_MINUTES
    if gap_end <= gap_start:
        return []
    _require_positive_quarter_duration(horizon_minutes, "horizon_minutes")
    minimum = _require_quarter_minutes(minimum_block_minutes, "minimum_block_minutes")
    maximum = _require_quarter_minutes(maximum_booking_minutes, "maximum_booking_minutes")
    if minimum <= 0 or maximum < minimum:
        raise ValueError("booking duration bounds are invalid")

    daily_limit = maximum if remaining_daily_minutes is None else max(0, remaining_daily_minutes)
    weekly_limit = maximum if remaining_weekly_minutes is None else max(0, remaining_weekly_minutes)
    desired_peak = int(getattr(planning, "desired_peak_block_minutes"))
    _require_quarter_minutes(desired_peak, "desired_peak_block_minutes")
    preferred_start = _planning_clock_minutes(planning, "preferred_peak_start")
    preferred_end = _planning_clock_minutes(planning, "preferred_peak_end")
    after_peak_mode = getattr(planning, "after_peak_mode")

    opportunities: list[BookingOpportunity] = []
    last_start = gap_end - minimum
    for start in range(gap_start, last_start + 1, QUARTER_MINUTES):
        desired = maximum if start >= peak_end_minutes and after_peak_mode == "longest_first" else desired_peak
        requested = min(desired, maximum, daily_limit, weekly_limit, gap_end - start)
        requested = int(floor(requested / QUARTER_MINUTES)) * QUARTER_MINUTES
        if requested < minimum:
            continue
        end = start + requested
        if strict_window is not None:
            strict_start, strict_end = strict_window
            if start < strict_start or start + minimum > strict_end:
                continue
            end = min(end, strict_end)
        if target_date.weekday() < 5:
            end = _peak_capped_end(
                start,
                end,
                remaining_peak_minutes,
                peak_start=peak_start_minutes,
                peak_end=peak_end_minutes,
            )
        end -= (end - start) % QUARTER_MINUTES
        if end - start < minimum:
            continue

        start_datetime = datetime.combine(
            target_date,
            time(start // 60, start % 60),
        )
        unlock_at = start_datetime - timedelta(minutes=horizon_minutes - minimum)
        preferred = (
            interval_overlap_minutes(start, end, preferred_start, preferred_end)
            if target_date.weekday() < 5
            else 0
        )
        peak = (
            interval_overlap_minutes(start, end, peak_start_minutes, peak_end_minutes)
            if target_date.weekday() < 5
            else 0
        )
        soft_preferred = (
            interval_overlap_minutes(start, end, *soft_preferred_window)
            if soft_preferred_window is not None
            else 0
        )
        opportunities.append(
            BookingOpportunity(
                room=room,
                target_date=target_date,
                start_minutes=start,
                end_minutes=end,
                unlock_at=unlock_at,
                room_priority=room_priority,
                initial_minutes=minimum,
                potential_minutes=end - start,
                preferred_minutes=preferred,
                soft_preferred_minutes=soft_preferred,
                peak_minutes=peak,
                peak_window_start_minutes=peak_start_minutes,
                peak_window_end_minutes=peak_end_minutes,
                source_gap_start_minutes=gap_start,
                source_gap_end_minutes=gap_end,
            )
        )
    return opportunities


def opportunity_rank(opportunity: BookingOpportunity, planning, *, now: datetime) -> tuple:
    """Return a transparent lexicographic rank; lower tuples are better."""

    preferred_start = _planning_clock_minutes(planning, "preferred_peak_start")
    preferred_end = _planning_clock_minutes(planning, "preferred_peak_end")
    desired = int(getattr(planning, "desired_peak_block_minutes"))
    reaches_desired = opportunity.potential_minutes >= desired
    fully_preferred = (
        opportunity.target_date.weekday() < 5
        and opportunity.start_minutes >= preferred_start
        and opportunity.end_minutes <= preferred_end
        and reaches_desired
    )
    wait_seconds = max(0, int((opportunity.unlock_at - now).total_seconds()))
    after_peak = opportunity.start_minutes >= opportunity.peak_window_end_minutes
    unrestricted = opportunity.peak_minutes == 0
    if fully_preferred:
        group = 0
    elif unrestricted and reaches_desired:
        group = 1
    elif reaches_desired:
        group = 2
    elif unrestricted:
        group = 3
    else:
        group = 4

    if after_peak:
        after_mode = getattr(planning, "after_peak_mode")
        if after_mode == "earliest_first":
            quality = (
                -opportunity.soft_preferred_minutes,
                opportunity.start_minutes,
                -opportunity.potential_minutes,
            )
        elif after_mode == "room_first":
            quality = (
                opportunity.room_priority,
                -opportunity.soft_preferred_minutes,
                -opportunity.potential_minutes,
                opportunity.start_minutes,
            )
        else:
            quality = (
                -opportunity.soft_preferred_minutes,
                -opportunity.potential_minutes,
                opportunity.start_minutes,
            )
    else:
        quality = (
            -opportunity.soft_preferred_minutes,
            -opportunity.preferred_minutes,
            -opportunity.potential_minutes,
            opportunity.start_minutes,
        )
    quality = (*quality, wait_seconds)
    if getattr(planning, "priority_mode") == "room_first":
        return (opportunity.room_priority, group, *quality)
    return (group, *quality, opportunity.room_priority)
def choose_horizon_opportunity(
    current: Sequence[BookingOpportunity],
    future: Sequence[BookingOpportunity],
    planning,
    *,
    now: datetime,
) -> OpportunityDecision:
    """Choose the best current edge or explain why a later edge is safer.

    A wait requires a configured number of distinct later room options that are
    all better than the best current edge.  This avoids deferring based on one
    duplicate time in the same room and makes the UI explanation auditable.
    """

    current_sorted = sorted(current, key=lambda item: opportunity_rank(item, planning, now=now))
    if not current_sorted:
        return OpportunityDecision("none", None, (), 0, "No exact edge is free now")
    best_current = current_sorted[0]
    if not getattr(planning, "enabled") or not getattr(planning, "hold_early_peak_edges"):
        return OpportunityDecision(
            "book_now",
            best_current,
            tuple(current_sorted[1:4]),
            0,
            "Daily foresight is disabled; using the best current edge",
        )
    if best_current.target_date.weekday() >= 5:
        return OpportunityDecision(
            "book_now",
            best_current,
            tuple(current_sorted[1:4]),
            0,
            "Weekend sessions do not consume weekday peak allowance",
        )
    fallback_start = best_current.peak_window_end_minutes - int(
        getattr(planning, "fallback_lead_minutes")
    )
    if best_current.start_minutes >= fallback_start:
        return OpportunityDecision(
            "book_now",
            best_current,
            tuple(current_sorted[1:4]),
            0,
            "The configured peak fallback point has been reached",
        )

    lookahead = timedelta(minutes=int(getattr(planning, "foresight_minutes")))
    current_rank = opportunity_rank(best_current, planning, now=now)
    later = [
        item
        for item in future
        if item.target_date == best_current.target_date
        and item.unlock_at > now
        and item.unlock_at <= now + lookahead
        and opportunity_rank(item, planning, now=now) < current_rank
    ]
    later.sort(key=lambda item: opportunity_rank(item, planning, now=now))
    distinct_rooms = []
    seen_rooms = set()
    for item in later:
        if item.room in seen_rooms:
            continue
        seen_rooms.add(item.room)
        distinct_rooms.append(item)
    minimum_options = int(getattr(planning, "minimum_later_options"))
    if len(distinct_rooms) < minimum_options:
        return OpportunityDecision(
            "book_now",
            best_current,
            tuple(distinct_rooms[:3]),
            0,
            f"Only {len(distinct_rooms)} better later room option(s) are visible; "
            f"{minimum_options} required",
        )

    selected = distinct_rooms[0]
    held_peak = selected.peak_minutes
    return OpportunityDecision(
        "wait",
        selected,
        tuple(distinct_rooms[1:4]),
        held_peak,
        f"Skipped {best_current.start_text} to preserve {held_peak} peak minutes "
        f"for {len(distinct_rooms)} better visible later room option(s); "
        f"best is {selected.start_text}-{selected.end_text} in {selected.room}",
    )


def select_day_plan(
    opportunities: Iterable[BookingOpportunity],
    planning,
    *,
    now: datetime,
    target_minutes: int,
    allow_fragmented_sessions: bool,
    remaining_peak_minutes: int | None = None,
    same_room_gap_minutes: int = 0,
    rank_key=None,
) -> tuple[BookingOpportunity, ...]:
    """Select a target-maximizing, desirable whole-day session portfolio.

    Opportunities are individually ranked, but a daily goal is not one
    opportunity.  A bounded seed search avoids the classic greedy failure where
    one attractive two-hour block prevents two compatible 90-minute blocks from
    fulfilling a three-hour target.  Every candidate portfolio still obeys the
    aggregate peak budget, schedule overlap, and same-room gap.
    """

    remaining = max(0, int(floor(target_minutes / QUARTER_MINUTES)) * QUARTER_MINUTES)
    peak_remaining = (
        24 * 60
        if remaining_peak_minutes is None
        else max(
            0,
            int(floor(remaining_peak_minutes / QUARTER_MINUTES))
            * QUARTER_MINUTES,
        )
    )
    same_room_gap_minutes = max(
        0,
        int(floor(same_room_gap_minutes / QUARTER_MINUTES)) * QUARTER_MINUTES,
    )
    if rank_key is None:
        rank_key = lambda value: opportunity_rank(value, planning, now=now)
    ranked = sorted(
        opportunities,
        key=lambda item: (
            rank_key(item),
            item.room,
            item.start_minutes,
            item.end_minutes,
        ),
    )
    if not ranked or remaining <= 0:
        return ()

    def fitted_candidate(
        item: BookingOpportunity,
        selected: Sequence[BookingOpportunity],
        minutes_left: int,
        peak_left: int,
        *,
        exact_minutes: int | None = None,
    ) -> BookingOpportunity | None:
        if minutes_left < item.initial_minutes:
            return None
        latest_end = item.end_minutes
        for existing in selected:
            if existing.start_minutes <= item.start_minutes < existing.end_minutes:
                return None
            if item.start_minutes < existing.start_minutes:
                latest_end = min(latest_end, existing.start_minutes)
            if item.room != existing.room:
                continue
            if item.start_minutes >= existing.end_minutes:
                if item.start_minutes < existing.end_minutes + same_room_gap_minutes:
                    return None
            else:
                latest_end = min(
                    latest_end,
                    existing.start_minutes - same_room_gap_minutes,
                )
        maximum = min(item.potential_minutes, minutes_left, latest_end - item.start_minutes)
        maximum -= maximum % QUARTER_MINUTES
        requested = maximum if exact_minutes is None else min(maximum, exact_minutes)
        requested -= requested % QUARTER_MINUTES
        if requested < item.initial_minutes:
            return None
        planned_end = _peak_capped_end(
            item.start_minutes,
            item.start_minutes + requested,
            peak_left,
            peak_start=item.peak_window_start_minutes,
            peak_end=item.peak_window_end_minutes,
        )
        planned_minutes = planned_end - item.start_minutes
        if planned_minutes < item.initial_minutes:
            return None
        planned_peak = interval_overlap_minutes(
            item.start_minutes,
            planned_end,
            item.peak_window_start_minutes,
            item.peak_window_end_minutes,
        )
        return replace(
            item,
            end_minutes=planned_end,
            potential_minutes=planned_minutes,
            preferred_minutes=min(item.preferred_minutes, planned_minutes),
            soft_preferred_minutes=min(item.soft_preferred_minutes, planned_minutes),
            peak_minutes=planned_peak,
        )

    def greedy_completion(
        seeded: Sequence[BookingOpportunity],
    ) -> tuple[BookingOpportunity, ...]:
        selected = list(seeded)
        used = sum(item.potential_minutes for item in selected)
        used_peak = sum(item.peak_minutes for item in selected)
        if not allow_fragmented_sessions and selected:
            return tuple(selected)
        for item in ranked:
            if used >= remaining:
                break
            chosen = fitted_candidate(
                item,
                selected,
                remaining - used,
                max(0, peak_remaining - used_peak),
            )
            if chosen is None:
                continue
            selected.append(chosen)
            used += chosen.potential_minutes
            used_peak += chosen.peak_minutes
            if not allow_fragmented_sessions:
                break
        return tuple(selected)

    def portfolio_key(plan: Sequence[BookingOpportunity]) -> tuple:
        total = sum(item.potential_minutes for item in plan)
        ranked_quality = tuple(
            sorted(
                (
                    rank_key(item),
                    -item.potential_minutes,
                    item.room,
                    item.start_minutes,
                    item.end_minutes,
                )
                for item in plan
            )
        )
        chronological = tuple(
            sorted((item.start_minutes, item.end_minutes, item.room) for item in plan)
        )
        return (-total, len(plan), ranked_quality, chronological)

    portfolios = [greedy_completion(())]
    if allow_fragmented_sessions:
        # Keep the search bounded on large fresh grids while retaining both the
        # strongest-ranked choices and temporal diversity across the day.
        seed_items = list(ranked[:96])
        seen_seed_ids = {
            (item.room, item.start_minutes, item.end_minutes, item.unlock_at)
            for item in seed_items
        }
        best_at_start: dict[int, BookingOpportunity] = {}
        for item in ranked:
            best_at_start.setdefault(item.start_minutes, item)
        for item in best_at_start.values():
            identity = (item.room, item.start_minutes, item.end_minutes, item.unlock_at)
            if identity not in seen_seed_ids:
                seed_items.append(item)
                seen_seed_ids.add(identity)

        for item in seed_items:
            maximum = min(item.potential_minutes, remaining)
            maximum -= maximum % QUARTER_MINUTES
            for duration in range(item.initial_minutes, maximum + 1, QUARTER_MINUTES):
                seed = fitted_candidate(
                    item,
                    (),
                    remaining,
                    peak_remaining,
                    exact_minutes=duration,
                )
                if seed is not None:
                    portfolios.append(greedy_completion((seed,)))

    best = min(portfolios, key=portfolio_key)
    return tuple(
        sorted(
            best,
            key=lambda item: (
                rank_key(item),
                item.room,
                item.start_minutes,
                item.end_minutes,
            ),
        )
    )
