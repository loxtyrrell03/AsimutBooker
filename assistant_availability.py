"""Bounded read-only availability adapter over the existing room-grid scan."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import re


def _local_boundary(day, clock, tz):
    value = datetime.fromisoformat(f'{day}T{clock}').replace(tzinfo=tz)
    # The room grid supplies wall times without a fold/offset. Do not invent
    # an instant for a skipped or repeated local time at a clock change.
    if value.utcoffset() != value.replace(fold=1).utcoffset():
        raise ValueError('Availability boundary is ambiguous at a clock change')
    return value


def resolve_query(arguments, now):
    allowed = {'date', 'start_time', 'end_time', 'next_minutes', 'minimum_block_minutes', 'room'}
    if not isinstance(arguments, dict) or set(arguments) - allowed:
        raise ValueError('Unknown availability fields')
    minimum = arguments.get('minimum_block_minutes', 1)
    if type(minimum) is not int or not 1 <= minimum <= 120:
        raise ValueError('Minimum duration must be between one and 120 minutes')
    room = arguments.get('room')
    if room is not None and (not isinstance(room, str) or not room.strip() or len(room) > 120):
        raise ValueError('Choose a room name')
    if 'next_minutes' in arguments:
        minutes = arguments['next_minutes']
        if type(minutes) is not int or not 1 <= minutes <= 1440:
            raise ValueError('Rolling window must be between one and 1440 minutes')
        if {'date', 'start_time', 'end_time'} & set(arguments):
            raise ValueError('Use either a rolling window or a dated window')
        start = now
        end = (now.astimezone(timezone.utc) + timedelta(minutes=minutes)).astimezone(now.tzinfo)
    else:
        if not {'date', 'start_time', 'end_time'} <= set(arguments):
            raise ValueError('Supply date, start_time and end_time')
        for field in ('start_time', 'end_time'):
            if not isinstance(arguments[field], str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):(?:00|15|30|45)', arguments[field]):
                raise ValueError('Use quarter-hour times')
        day = arguments['date']
        if not isinstance(day, str) or datetime.strptime(day, '%Y-%m-%d').date().isoformat() != day:
            raise ValueError('Use YYYY-MM-DD')
        start = _local_boundary(day, arguments['start_time'], now.tzinfo)
        end = _local_boundary(day, arguments['end_time'], now.tzinfo)
        if end <= start:
            raise ValueError('End time must follow start time')
        if end <= now:
            raise ValueError('Choose a future availability window')
        start = max(start, now)
    dates = []
    day = start.date()
    while day <= (end - timedelta(microseconds=1)).date():
        dates.append(day.isoformat())
        day += timedelta(days=1)
    return {'start': start.isoformat(), 'end': end.isoformat(), 'dates': dates,
            'minimum_block_minutes': minimum, 'room': room}


def filter_scan(scan, query, *, now):
    """Keep unknown coverage distinct from a successfully scanned empty grid."""
    if not isinstance(scan, dict) or not isinstance(scan.get('rows'), list):
        raise ValueError('Invalid scan')
    observed = datetime.fromisoformat(scan['observed_at'])
    if observed.tzinfo is None or not -60 <= (now - observed).total_seconds() <= 900:
        raise ValueError('Stale availability scan')
    scanned = scan['scanned_dates']
    if not isinstance(scanned, list) or any(not isinstance(day, str) for day in scanned):
        raise ValueError('Invalid scan coverage')
    start = max(datetime.fromisoformat(query['start']).astimezone(timezone.utc), now.astimezone(timezone.utc))
    end = datetime.fromisoformat(query['end']).astimezone(timezone.utc)
    rows = []
    for row in scan['rows']:
        if row['date'] not in query['dates'] or row['date'] not in scanned:
            continue
        if query['room'] and row['room'].casefold() != query['room'].casefold():
            continue
        left = _local_boundary(row['date'], row['start'], now.tzinfo).astimezone(timezone.utc)
        # The existing scan can represent midnight as 24:00.
        right = (datetime.fromisoformat(row['date']).replace(tzinfo=now.tzinfo) + timedelta(days=1)
                 if row['end'] == '24:00' else
                 _local_boundary(row['date'], row['end'], now.tzinfo)).astimezone(timezone.utc)
        left, right = max(left, start), min(right, end)
        # Do not offer elapsed time or a non-bookable partial minute after a slow scan.
        midnight = left.replace(hour=0, minute=0, second=0, microsecond=0)
        left = midnight + timedelta(minutes=15 * math.ceil((left - midnight).total_seconds() / 900))
        minutes = int((right - left).total_seconds() // 60)
        if minutes >= query['minimum_block_minutes']:
            left, right = left.astimezone(now.tzinfo), right.astimezone(now.tzinfo)
            rows.append({'date': row['date'], 'room': row['room'],
                         'start_time': left.strftime('%H:%M'), 'end_time': right.strftime('%H:%M'),
                         'minutes': minutes})
    rows.sort(key=lambda row: (row['date'], row['start_time'], row['room']))
    return {'read_only': True, 'observed_at': scan['observed_at'], 'window': query,
            'window_elapsed': end <= start,
            'rows': rows[:200], 'truncated': len(rows) > 200,
            'unavailable_dates': sorted(set(query['dates']) - set(scanned)),
            'scope': 'Visible free room intervals; personal conflicts, quotas and booking horizons still require checking.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--dates', nargs='+', required=True)
    args = parser.parse_args(argv)
    from phone_operation_worker import execute
    from app_settings import atomic_write_json
    # execute uses the shared runtime lock, check-only CLI and scoped scan hook.
    result = execute('scan', {'dates': args.dates}, args.output.parent)
    if result['state'] != 'completed' or not isinstance(result.get('scan'), dict):
        print(result['message'])
        return 1
    atomic_write_json(args.output, result['scan'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
