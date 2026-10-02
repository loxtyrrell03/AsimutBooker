"""Exact, revisioned controls for existing manual reservation protection.

Pins are deliberately binary. Removing one never resurrects an extension goal
or removes a released-time blackout. No operation in this module starts Booker.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from zoneinfo import ZoneInfo

from agenda_snapshot import DEFAULT_MAX_AGE, snapshot_from_dict
from app_settings import SettingsError
from booking_blackouts import make_rebooking_blackout
from manual_booking_overrides import KEY, extension_is_pinned, validate_records

TARGET_FIELDS = {'event_id', 'date', 'room', 'start_time', 'end_time'}
LONDON = ZoneInfo('Europe/London')


def _wall_time(day, clock):
    naive = datetime.fromisoformat(f'{day}T{clock}')
    possibilities = {candidate.astimezone(timezone.utc) for fold in (0, 1)
                     if (candidate := naive.replace(tzinfo=LONDON, fold=fold))
                     .astimezone(timezone.utc).astimezone(LONDON).replace(tzinfo=None) == naive}
    if len(possibilities) != 1:
        raise ValueError('The reservation time is ambiguous or does not exist in Europe/London.')
    return possibilities.pop()


def validate_target(target):
    if not isinstance(target, dict) or set(target) != TARGET_FIELDS:
        raise ValueError('Choose one exact reservation.')
    if type(target['event_id']) is not int or target['event_id'] <= 0:
        raise ValueError('The reservation must have a positive event ID.')
    make_rebooking_blackout(target['date'], target['start_time'], target['end_time'])
    room = target['room']
    if not isinstance(room, str) or not room.strip() or room != ' '.join(room.split()) or len(room) > 128:
        raise ValueError('Choose the exact reservation room.')
    _wall_time(target['date'], target['start_time'])
    _wall_time(target['date'], target['end_time'])
    return target


def record_for(target):
    return dict(date=target['date'], room=target['room'],
                startTime=target['start_time'], endTime=target['end_time'])


def _target(event):
    return dict(event_id=event['eventId'], date=event['date'], room=event['room'],
                start_time=event['startTime'], end_time=event['endTime'])


def _goals(settings):
    # Reuse the canonical strict validator, retaining even expired/pinned goals
    # in the revision so a scoped edit cannot silently discard unseen changes.
    from book_week import load_extendable_bookings
    load_extendable_bookings(settings)
    return deepcopy(settings.get('extendable_bookings', []))


def project_reservation_pins(events, settings, *, stale, observed_at,
                             covered_dates, pending_count, now=None):
    """Read-only projection of one agenda/settings snapshot supplied by a caller."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError('Current time must include a timezone.')
    if type(stale) is not bool or (pending_count is not None and
            (type(pending_count) is not int or pending_count < 0)):
        raise ValueError('Reservation evidence is invalid.')
    pins = deepcopy(validate_records(settings.get(KEY, {})))
    goals = _goals(settings)
    if not observed_at and not events and not covered_dates and stale:
        rows, dates = [], []
    else:
        snapshot = snapshot_from_dict(dict(version=1, observed_at=observed_at,
                                          dates=covered_dates, events=events))
        rows = snapshot.event_dicts()
        dates = [day.isoformat() for day in snapshot.dates]
        age = current.astimezone(timezone.utc) - snapshot.observed_at
        # The native reader already checks this; the pure bridge must not be
        # able to turn an old observation into current evidence accidentally.
        stale = stale or age > DEFAULT_MAX_AGE or age.total_seconds() < -300
    by_id = {}
    for event in rows:
        if event['eventId'] is not None:
            by_id.setdefault(event['eventId'], []).append(event)
    result = []
    present_ids = set()
    for event in rows:
        if not event['isReservation']:
            continue
        target = _target(event)
        event_id = target['event_id']
        present_ids.add(str(event_id))
        saved = pins.get(str(event_id))
        reason = None
        if stale:
            reason = 'Refresh the agenda before changing reservation protection.'
        elif pending_count is None:
            reason = 'Booking outcomes could not be checked. Refresh before changing protection.'
        elif pending_count:
            reason = 'Resolve pending booking outcomes before changing protection.'
        elif event_id is None:
            reason = 'This reservation has no verified event ID.'
        elif len(by_id[event_id]) != 1:
            reason = 'This event ID has conflicting agenda records. Refresh the agenda.'
        elif any(word in event['title'].casefold() for word in ('cancelled', 'canceled')):
            reason = 'This agenda entry is marked cancelled. Its protection cannot be changed here.'
        else:
            try:
                validate_target(target)
                if _wall_time(target['date'], target['start_time']) <= current:
                    reason = 'This reservation has already started. Only upcoming reservations can change protection here.'
            except (ValueError, SettingsError):
                reason = 'This reservation does not have a supported exact date, room and time.'
        try:
            validate_target(target)
            selected = {KEY: {str(event_id): record_for(target)}}
        except (ValueError, SettingsError):
            selected = {KEY: {}}
        result.append(dict(target=target, present=True, protected=saved is not None,
                           pin_matches=saved == record_for(target), saved_pin=deepcopy(saved),
                           extension_goals=[deepcopy(goal) for goal in goals
                                            if extension_is_pinned(goal, selected)],
                           eligible=reason is None, unsupported_reason=reason))
    for key, saved in pins.items():
        if key in present_ids:
            continue
        target = dict(event_id=int(key), date=saved['date'], room=saved['room'],
                      start_time=saved['startTime'], end_time=saved['endTime'])
        reason = ('The stored booking date was not checked by this agenda.' if saved['date'] not in dates else
                  'This stored booking is not present in the checked agenda.')
        if stale:
            reason = 'Refresh the agenda to check this stored protection.'
        result.append(dict(target=target, present=False, protected=True, pin_matches=False,
                           saved_pin=deepcopy(saved), extension_goals=[], eligible=False,
                           unsupported_reason=reason + ' Protection is retained.'))
    result.sort(key=lambda row: (row['target']['date'], row['target']['start_time'],
                                 row['target']['room'] or '', row['target']['event_id'] or 0))
    payload = dict(pins=pins, events=rows, dates=dates, extension_goals=goals)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    return dict(revision=digest, stale=stale, observed_at=observed_at, covered_dates=dates,
                pending_count=pending_count, protected_ids=sorted(int(key) for key in pins),
                reservations=result)


def apply_protection(settings, target, protected):
    """Caller must hold runtime/settings locks and validate its fresh document."""
    validate_target(target)
    if type(protected) is not bool:
        raise ValueError('Choose whether to protect this reservation.')
    pins = deepcopy(validate_records(settings.get(KEY, {})))
    key = str(target['event_id'])
    if protected:
        record = record_for(target)
        pins[key] = record
        if 'extendable_bookings' in settings:
            # Generic pin_in_settings also cleans goals for other existing pins.
            # An explicit single-row control must change only its reviewed target.
            selected = {KEY: {key: record}}
            settings['extendable_bookings'] = [goal for goal in _goals(settings)
                if not extension_is_pinned(goal, selected)]
    else:
        pins.pop(key, None)
    settings[KEY] = pins
