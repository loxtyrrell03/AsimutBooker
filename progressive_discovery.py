"""Discover useful partial upgrades even when no full-session room gap exists.

Only real minimum-length gaps seed this bounded search. The full-length target
is an aspiration; a planner-proved prefix and retained fallback are the actual
next step. Nothing here authorizes a mutation or claims the target tail is free.
"""
from dataclasses import dataclass
from datetime import timedelta, timezone
from heapq import nsmallest
from itertools import combinations
from math import ceil, floor

from daily_planner import interval_overlap_minutes, soft_time_value
from progressive_planner import TransferPlan, _room_intervals, plan_progressive_transfer
from room_upgrades import (Reservation, RoomUpgrade, RoomConsolidation,
                          clock_minutes, local_instant)


MAX_PARTIAL_SOURCE_GROUPS = 128
MAX_PARTIAL_TARGET_CHECKS = 128


@dataclass(frozen=True)
class PartialUpgradeOpportunity:
    change: RoomUpgrade | RoomConsolidation
    first_step: TransferPlan

    def __post_init__(self):
        if (self.first_step.seed is not None or self.first_step.originals != self.change.originals
                or self.first_step.target != self.change.replacement):
            raise ValueError('Partial discovery requires its exact planner-proved first step')

    @property
    def opens_at(self):
        return self.first_step.opens_at


def find_partial_upgrade_opportunities(*, events, available_data, policy, now,
                                       eligible_event_ids=None, **kwargs):
    """Inspect real sparse gaps with bounded source groups and target checks.

    The room grid must represent one date, identified by the eligible exact
    reservation IDs. Whole-gap opportunities retain their existing discovery
    path. Higher-rank rooms and earlier useful boundaries win the bounded queue.
    Final execution must still repeat all live checks and daily-capacity tests.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Partial discovery requires an aware current time')
    if policy.site_clock_offset_bounds is None:
        return ()
    ignored = set(kwargs.get('ignored_event_ids', ()))
    records = tuple(sorted((Reservation.from_event(event) for event in events
        if event.get('isReservation') is True and event.get('room') in policy.room_order
        and event.get('eventId') not in ignored
        and (eligible_event_ids is None or event.get('eventId') in eligible_event_ids)),
        key=lambda r: (r.start, r.event_id)))
    if not records:
        return ()
    if len({r.day for r in records}) != 1:
        raise ValueError('A partial discovery grid must describe one exact date')
    day = records[0].day
    minimum = int(ceil(policy.minimum_block_minutes / 15)) * 15
    # Round-robin group sizes so several short fragments can compete with
    # single sources even when a busy agenda supplies many possible pairs.
    groups = []
    sizes = range(1, min(len(records), policy.site_maximum_booking_minutes // minimum) + 1)
    families = [iter(combinations(records, size)) for size in sizes]
    while families and len(groups) < MAX_PARTIAL_SOURCE_GROUPS:
        remaining_families = []
        for family in families:
            for group in family:
                duration = sum(r.duration for r in group)
                if (minimum < duration <= policy.site_maximum_booking_minutes
                        and all(a.end <= b.start for a, b in zip(group, group[1:]))):
                    groups.append(group)
                    remaining_families.append(family)
                    break
            if len(groups) >= MAX_PARTIAL_SOURCE_GROUPS:
                break
        families = remaining_families
    if not groups:
        return ()
    intervals = _room_intervals(available_data)
    room_rank = {room: index for index, room in enumerate(policy.room_order)}
    planning = kwargs['planning']
    prefs = kwargs['time_preferences']
    window = ((round(prefs['start_hour'] * 60), round(prefs['end_hour'] * 60)) if prefs.get('enabled') else None)
    peak_window = (clock_minutes(planning.preferred_peak_start), clock_minutes(planning.preferred_peak_end))
    instants = {}
    def instant(minutes):
        if minutes not in instants:
            instants[minutes] = local_instant(day, minutes)
        return instants[minutes]

    def targets():
        for group in groups:
            anchor = group[0]
            duration = sum(r.duration for r in group)
            best_old_rank = min(room_rank[r.room] for r in group)
            old_value = sum(soft_time_value(r.start, r.duration, window) for r in group)
            old_preferred = (sum(interval_overlap_minutes(r.start, r.end, *peak_window) for r in group)
                             if planning.enabled and day.weekday() < 5 else 0)
            for room, gaps in intervals.items():
                if room not in room_rank or room_rank[room] > best_old_rank:
                    continue
                if len(group) == 1 and room == anchor.room:
                    continue
                starts = {start for a, b in gaps for start in range(int(ceil(a / 15)) * 15,
                          int(floor((b - minimum) / 15)) * 15 + 1, 15)}
                horizon = timedelta(minutes=policy.horizon_minutes_for(room),
                                    seconds=policy.site_clock_offset_bounds[0])
                for start in sorted(starts):
                    end = start + duration
                    if end >= 1440 or any(a <= start and b >= end for a, b in gaps):
                        continue
                    value = soft_time_value(start, duration, window)
                    preferred = (interval_overlap_minutes(start, end, *peak_window)
                                 if planning.enabled and day.weekday() < 5 else 0)
                    if value + 1e-8 < old_value or preferred < old_preferred:
                        continue
                    try:
                        if instant(end) > policy.booking_horizon:
                            continue
                        edge = instant(start + minimum) - horizon
                    except ValueError:
                        continue
                    target = Reservation(anchor.event_id, day, room, start, end)
                    rank = (-round(value, 8), -preferred, room_rank[room], abs(start - anchor.start), start, room)
                    change = (RoomUpgrade(anchor, target, rank) if len(group) == 1
                              else RoomConsolidation(group, target, rank))
                    priority = (max(now.astimezone(timezone.utc), edge), room_rank[room], rank,
                                len(group), tuple(r.event_id for r in group))
                    yield priority, change

    shortlisted = nsmallest(MAX_PARTIAL_TARGET_CHECKS, targets(), key=lambda item: item[0])
    result = []
    for _, change in shortlisted:
        step = plan_progressive_transfer(change, events=events, available_data=available_data,
                                          policy=policy, now=now, **kwargs)
        if step is not None:
            result.append(PartialUpgradeOpportunity(change, step))
    return tuple(sorted(result, key=lambda prospect: (prospect.opens_at, prospect.first_step.rank)))
