"""Pure allocation of scarce advance credit across preferred daily sessions.

Future room openings may reserve planning credit, but never authorize a Save.
Every selected interval still needs the runtime's fresh room, quota and event
checks. Daily targets, preferred-time scoring and session rules are unchanged.
"""
from dataclasses import dataclass, replace
from datetime import date, datetime
from fractions import Fraction

from daily_planner import BookingOpportunity, select_day_plan, soft_time_value, opportunity_rank
from session_preferences import comfort_key, enabled as comfort_enabled
from advance_preferences import AdvanceQuotaPreferences, session_quality, week_quality


@dataclass(frozen=True)
class AdvanceDay:
    target_date: date
    target_minutes: int
    confirmed_minutes: int
    quality_minutes: int
    opportunities: tuple[BookingOpportunity, ...]
    remaining_peak_minutes: int
    held_target_minutes: int = 0
    held_quality_minutes: int = 0
    existing_sessions: tuple = ()
    advance_cap_minutes: int = 0


@dataclass(frozen=True)
class AdvanceAllocation:
    target_date: date
    sessions: tuple[BookingOpportunity, ...]

    @property
    def minutes(self):
        return sum(item.potential_minutes for item in self.sessions)


def allocate_advance_week(days, planning, *, now: datetime, budget_minutes: int,
                          minimum_block_minutes: int, allow_fragmented_sessions: bool,
                          same_room_gap_minutes: int, reverse_date_order: bool = False,
                          quota_preferences=None):
    """Choose whole-day portfolios under one shared advance-credit budget.

    First maximize the weakest daily anchor, then the next weakest, and so on.
    Once every attainable anchor is covered, apply the same fairness to larger
    daily targets. Time suitability, room ranking and fewer sessions break ties.
    A one-hour anchor is an aim, not a new minimum: seven dates can share six
    hours as 45/60-minute blocks. The saved minimum remains a hard constraint.
    """
    policy = quota_preferences or AdvanceQuotaPreferences()
    if policy.date_order != 'inherit':
        reverse_date_order = policy.date_order == 'furthest'
    if policy.block_minutes:
        planning = replace(planning, preferred_block_minutes=policy.block_minutes)
    custom_quality = bool(policy.periods or policy.priority_mode != 'inherit' or planning.priority_mode == 'room_first')
    ordered = tuple(sorted(days, key=lambda day: day.target_date,
                           reverse=reverse_date_order))
    if len({day.target_date for day in ordered}) != len(ordered):
        raise ValueError('Advance planning needs one entry per date')
    budget = max(0, int(budget_minutes) // 15 * 15)
    if minimum_block_minutes < 30 or minimum_block_minutes % 15:
        raise ValueError('The advance block minimum must be at least 30 minutes on the time grid')

    menus = []
    for day in ordered:
        if any(item.target_date != day.target_date for item in day.opportunities):
            raise ValueError('An advance day cannot contain opportunities for another date')
        available = min(budget, max(0, day.target_minutes - day.confirmed_minutes
                                    - day.held_target_minutes))
        cap = policy.day_caps_minutes[day.target_date.weekday()] or day.advance_cap_minutes
        if cap:
            available = min(available, max(0, cap-day.confirmed_minutes-day.held_target_minutes))
        if not policy.day_weights[day.target_date.weekday()]:
            available = 0
        # Retain only the strongest portfolio for each actual credit cost.
        options = {0: AdvanceAllocation(day.target_date, ())}
        for capacity in range(minimum_block_minutes, available + 1, 15):
            sessions = select_day_plan(day.opportunities, planning, now=now,
                target_minutes=capacity, allow_fragmented_sessions=allow_fragmented_sessions,
                remaining_peak_minutes=day.remaining_peak_minutes,
                same_room_gap_minutes=same_room_gap_minutes,
                existing_sessions=day.existing_sessions,
                quality_score=(lambda item: session_quality(item, policy, planning)) if custom_quality else None,
                # Equally suitable preferred-room time that is already open
                # can be secured now, then upgraded through the existing path.
                # Do not hold credit for an equivalent unopened alternative.
                rank_key=lambda item: (item.unlock_at > now,
                    *opportunity_rank(item, planning, now=now)))
            allocation = AdvanceAllocation(day.target_date, sessions)
            old = options.get(allocation.minutes)
            existing = {day.target_date: day.existing_sessions}
            if old is None or _quality(allocation.sessions, planning, existing, policy if custom_quality else None) > _quality(old.sessions, planning, existing, policy if custom_quality else None):
                options[allocation.minutes] = allocation
        menus.append(tuple(options.values()))

    def score(allocations):
        anchors, targets, coverage = [], [], []
        sessions = []
        for day, allocation in zip(ordered, allocations):
            weight = policy.day_weights[day.target_date.weekday()]
            if not weight:
                continue
            secured = day.quality_minutes + day.held_quality_minutes + allocation.minutes
            target = max(1, day.target_minutes)
            anchor = max(1, min(target, max(policy.anchor_minutes, minimum_block_minutes)))
            if policy.distribution == 'weighted':
                anchor = min(target, anchor*weight)
            anchors.append(Fraction(min(anchor, secured), anchor))
            targets.append(Fraction(min(target, secured), target*(weight if policy.distribution == 'weighted' else 1)))
            coverage.append(secured)
            sessions.extend(allocation.sessions)
        quality = _quality(sessions, planning, {d.target_date: d.existing_sessions for d in ordered},
                           policy if custom_quality else None)
        if policy.distribution == 'concentrated':
            return (sum(a.minutes for a in allocations),
                    -sum(bool(a.sessions) and d.confirmed_minutes == 0 for d,a in zip(ordered,allocations)),
                    tuple(sorted(coverage, reverse=True)), *quality, tuple(coverage))
        if policy.distribution == 'quality':
            return (sum(a.minutes for a in allocations), *quality, tuple(sorted(anchors)), tuple(coverage))
        return (tuple(sorted(anchors)), tuple(sorted(targets)), *quality, tuple(coverage))

    # Multiple-choice knapsack. Sorted max-min coverage remains ordered when
    # the same later day is appended, so one best prefix per cost is sufficient.
    states = {0: ()}
    for options in menus:
        updated = {}
        for used, prefix in states.items():
            for option in options:
                cost = used + option.minutes
                if cost > budget:
                    continue
                candidate = (*prefix, option)
                previous = updated.get(cost)
                if previous is None or score(candidate) > score(previous):
                    updated[cost] = candidate
        states = updated
    return max(states.values(), key=score) if states else ()


def _quality(sessions, planning, existing_by_day, policy=None):
    comfort = [0, 0, 0]
    for day in ({item.target_date for item in sessions} if comfort_enabled(planning) else ()):
        schedule = (*existing_by_day.get(day, ()), *[(item.start_minutes, item.end_minutes, item.room)
                    for item in sessions if item.target_date == day])
        comfort = [a+b for a,b in zip(comfort, comfort_key(schedule, planning))]
    primary = week_quality(sessions, policy, planning) if policy else (
        round(sum(soft_time_value(item.start_minutes, item.potential_minutes,
                                 item.soft_preferred_window) for item in sessions), 8),
        -sum(item.room_priority * item.potential_minutes for item in sessions),
    )
    return (*primary,
        *(-value for value in comfort),
        -len(sessions),
    )
