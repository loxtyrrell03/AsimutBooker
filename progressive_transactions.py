"""Exact, durable scope for one bounded horizon transfer and its rollback.

An intent is separate from a pending transaction: only the latter permits edits.
All remote effects are verified by the engine; this module never calls Asimut.
"""
from dataclasses import dataclass
from datetime import date, datetime

from room_upgrades import Reservation, time_text


def record(r):
    return dict(event_id=r.event_id, date=r.day.isoformat(), room=r.room,
                start=time_text(r.start), end=time_text(r.end))


def transfer_summary(receipt):
    t = validate_transfer(receipt['transfer'])
    seed = reservation(t['replacement'])
    remaining = sum(reservation(r).duration for r in t['remaining'])
    return (f'UPGRADED PART: {seed.day} {seed.room} {time_text(seed.start)}-{time_text(seed.end)}; '
            f'{remaining} fallback minutes retained')


def reservation(r):
    from room_upgrades import clock_minutes
    if not isinstance(r, dict) or set(r) != {'event_id', 'date', 'room', 'start', 'end'}:
        raise ValueError('Expected an exact transfer reservation')
    result = Reservation(r['event_id'], date.fromisoformat(r['date']), r['room'],
                         clock_minutes(r['start']), clock_minutes(r['end']))
    if result.start % 15 or result.end % 15:
        raise ValueError('Transfer intervals must use quarter hours')
    return result


@dataclass(frozen=True)
class TransferEdit:
    original: Reservation
    replacement: Reservation

    def __post_init__(self):
        if (self.original.event_id != self.replacement.event_id
                or self.original.day != self.replacement.day
                or self.original.room != self.replacement.room
                or self.original == self.replacement):
            raise ValueError('Transfer edits retain exact ID, date and room')

    @property
    def originals(self):
        return (self.original,)


def validate_transfer(t):
    keys = {'plan_id', 'originals', 'seed_before', 'remaining', 'replacement',
            'target', 'opens_at', 'baseline_ids', 'minimum_minutes', 'adjustment_order', 'started_steps'}
    if not isinstance(t, dict) or set(t) != keys:
        raise ValueError('Invalid transfer transaction fields')
    from uuid import UUID
    UUID(t['plan_id'])
    originals = tuple(reservation(r) for r in t['originals'])
    remaining = tuple(reservation(r) for r in t['remaining'])
    seed = reservation(t['seed_before']) if t['seed_before'] is not None else None
    prefix, target = reservation(t['replacement']), reservation(t['target'])
    minimum = t['minimum_minutes']
    if type(minimum) is not int or minimum < 15 or minimum % 15:
        raise ValueError('Invalid transfer minimum')
    before = (*originals, *((seed,) if seed else ()))
    if not originals or len({r.event_id for r in before}) != len(before):
        raise ValueError('Transfer requires distinct original identities')
    if any(r.day != target.day or r.duration < minimum for r in (*before, *remaining, prefix)):
        raise ValueError('Transfer must retain date and useful durations')
    if (prefix.room != target.room or prefix.start != target.start or prefix.end > target.end
            or sum(r.duration for r in before) != target.duration
            or sum(r.duration for r in remaining) + prefix.duration != target.duration):
        raise ValueError('Transfer must preserve total minutes with a target prefix')
    ids = {r.event_id: r for r in originals}
    if len({r.event_id for r in remaining}) != len(remaining):
        raise ValueError('Duplicate retained identity')
    if any(r.event_id not in ids or r.room != ids[r.event_id].room
           or r.duration > ids[r.event_id].duration for r in remaining):
        raise ValueError('Fallbacks must retain their own room, identity and bounded duration')
    if seed is None and prefix.event_id != target.event_id:
        raise ValueError('A new seed must retain the target placeholder identity')
    if seed and (seed.room != prefix.room or seed.start != prefix.start
                 or seed.event_id != prefix.event_id or seed.end >= prefix.end):
        raise ValueError('Only the exact growing anchor may be extended')
    kept = {r.event_id: r for r in remaining}
    changed_ids = {r.event_id for r in originals if kept.get(r.event_id) != r}
    order = t['adjustment_order']
    if (not isinstance(order, list) or any(type(i) is not int or i <= 0 for i in order)
            or len(set(order)) != len(order) or set(order) != changed_ids):
        raise ValueError('Transfer adjustment order must identify every changed fallback exactly once')
    for group in (before, (*remaining, prefix)):
        ordered = sorted(group, key=lambda r: r.start)
        if any(a.end > b.start for a, b in zip(ordered, ordered[1:])):
            raise ValueError('Transfer cannot double-count overlapping practice')
    stamp = datetime.fromisoformat(t['opens_at'])
    if stamp.tzinfo is None:
        raise ValueError('Opening boundary must include timezone')
    if (not isinstance(t['baseline_ids'], list)
            or any(type(i) is not int or i <= 0 for i in t['baseline_ids'])
            or not {r.event_id for r in before}.issubset(t['baseline_ids'])):
        raise ValueError('Transfer requires its complete agenda identities')
    steps = t['started_steps']
    allowed = {'destination', *(f'source:{i}' for i in order), *(f'restore:{i}' for i in order)}
    if (not isinstance(steps, list) or any(not isinstance(s, str) or s not in allowed for s in steps)
            or len(set(steps)) != len(steps)):
        raise ValueError('Invalid attempted transfer operations')
    return t


def transfer_allows_step(receipt, step):
    if receipt.get('kind') != 'transfer' or not isinstance(step, TransferEdit):
        return False
    t = validate_transfer(receipt['transfer'])
    remaining = {r['event_id']: reservation(r) for r in t['remaining']}
    pairs = [(reservation(r), remaining[r['event_id']]) for r in t['originals']
             if r['event_id'] in remaining]
    if t['seed_before']:
        pairs.append((reservation(t['seed_before']), reservation(t['replacement'])))
    return any((step.original, step.replacement) in {(a, b), (b, a)} for a, b in pairs)


def transfer_allows_cancel(receipt, target):
    if receipt.get('kind') != 'transfer':
        return False
    t = validate_transfer(receipt['transfer'])
    return (target in tuple(reservation(r) for r in t['originals'])
            and target.event_id not in {r['event_id'] for r in t['remaining']})


def transaction_payload(plan, *, plan_id, baseline_ids, minimum_minutes, seed=None, adjustment_order=None):
    if adjustment_order is None:
        from progressive_planner import ordered_adjustments
        adjustments = ordered_adjustments(plan)
        if adjustments is None:
            raise ValueError('Transfer source adjustments have no executable order')
        adjustment_order = [before.event_id for before, _ in adjustments]
    return validate_transfer(dict(plan_id=plan_id, originals=[record(r) for r in plan.originals],
        remaining=[record(r) for r in plan.remaining], replacement=record(plan.replacement),
        target=record(plan.target), seed_before=record(seed) if seed else None,
        opens_at=plan.opens_at.isoformat(), baseline_ids=list(baseline_ids),
        minimum_minutes=minimum_minutes, adjustment_order=list(adjustment_order), started_steps=[]))
