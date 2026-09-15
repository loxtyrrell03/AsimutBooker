"""Apply the ordinary whole-day capacity test to a completed partial transfer.

All arrays here are hypothetical. A new seed gets a unique synthetic identity
only for accounting; this identity is never suitable for a booking request.
"""
import copy
from dataclasses import replace
from math import isfinite

from room_catalog import SITE_TIMEZONE
from room_upgrades import Reservation


def _merge(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def availability_after_transfer(gaps, before, after):
    """Free exact source intervals, then occupy the prefix and every remainder."""
    rooms = {}
    for entry in gaps:
        intervals = rooms.setdefault(entry['room'], [])
        for slot in entry.get('slots', ()):
            start, end = slot['startHour'] * 60, slot['endHour'] * 60
            if (not all(isinstance(v, (int, float)) and isfinite(v) for v in (start, end))
                    or not 0 <= start < end <= 1440):
                raise ValueError('Invalid observed room interval')
            intervals.append((start, end))
    for original in before:
        rooms.setdefault(original.room, []).append((original.start, original.end))
    for room in rooms:
        free = _merge(rooms[room])
        for final in after:
            if final.room != room:
                continue
            next_free = []
            for start, end in free:
                if final.start >= end or final.end <= start:
                    next_free.append((start, end))
                else:
                    if start < final.start:
                        next_free.append((start, final.start))
                    if end > final.end:
                        next_free.append((final.end, end))
            free = next_free
        rooms[room] = free
    return [{'room': room, 'slots': [{'startHour': start / 60, 'endHour': end / 60}
             for start, end in intervals]} for room, intervals in rooms.items()]


def preserves_transfer_capacity(engine, plan, *, events, gaps, settings,
                                 practice_plan, policy, now, extensions):
    """Preserve attainable missing hours and their quality after this step.

    This is evaluated at the prospective opening when preparing early, and
    again from fresh observations immediately before any source is released.
    Complete booked time alone is insufficient: a shifted fallback can occupy
    the only remaining useful slot for the day's target.
    """
    from room_upgrade_runtime import preserves_day_transition

    before_records = (*plan.originals, *((plan.seed,) if plan.seed else ()))
    before_ids = {r.event_id for r in before_records}
    matched = {}
    for event in events:
        if event.get('eventId') not in before_ids:
            continue
        event_id = event['eventId']
        if event_id in matched or event.get('isReservation') is not True:
            return False
        try:
            matched[event_id] = (Reservation.from_event(event), event)
        except (KeyError, TypeError, ValueError):
            return False
    if any(r.event_id not in matched or matched[r.event_id][0] != r for r in before_records):
        return False

    prefix = plan.replacement
    if plan.seed is None:
        # Avoid every observed and protected-extension identity, including
        # unrelated events, so accounting never overwrites the real fallback.
        used = [e.get('eventId') for e in (*events, *extensions)]
        synthetic_id = max((i for i in used if type(i) is int and i > 0), default=0) + 1
        prefix = replace(prefix, event_id=synthetic_id)
    after_records = (*plan.remaining, prefix)
    after_events = [copy.deepcopy(event) for event in events if event.get('eventId') not in before_ids]
    for final in after_records:
        template_id = (plan.seed.event_id if plan.seed else plan.originals[0].event_id)
        template = matched.get(final.event_id, matched[template_id])[1]
        after_events.append({**copy.deepcopy(template), **final.as_booking(), 'isReservation': True})
    after_gaps = availability_after_transfer(gaps, before_records, after_records)
    at = max(now, plan.opens_at).astimezone(SITE_TIMEZONE)
    return preserves_day_transition(engine, day=plan.target.day, before_events=events,
        after_events=after_events, before_gaps=gaps, after_gaps=after_gaps,
        settings=settings, practice_plan=practice_plan, policy=policy, now=at,
        extensions=extensions)
