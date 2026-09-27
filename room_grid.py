"""Display-only room timelines from complete, freshly verified ASIMUT grids.

Only the current run's eligible rooms enter this cache. It never authorizes a
Save, and a changed room preference document invalidates the display cache.
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import copy
from pathlib import Path

from app_settings import InterProcessFileLock, SettingsError, atomic_write_json, load_settings
from room_preferences import load_room_preferences, room_preferences_to_dict
from room_catalog import SITE_TIMEZONE

ROOT = Path(__file__).resolve().parent


def event_display(event, event_id, location_id, day, block):
    """Accept a name only with exact event, room, date and interval evidence."""
    if str(event.get('id')) != event_id or not any(str(r.get('id')) == location_id for r in event.get('rs', [])):
        return None
    try:
        start, end = (datetime.fromisoformat(event[k]).astimezone(SITE_TIMEZONE) for k in ('st', 'en'))
        left = start.hour * 60 + start.minute if start.date() == day else 0 if start.date() < day else 1440
        right = end.hour * 60 + end.minute if end.date() == day else 1440 if end.date() > day else 0
        if max(420, left) != max(420, round(block['startHour'] * 60)) or abs(min(1380, right) - min(1380, round(block['endHour'] * 60))) > 1:
            return None
    except (ValueError, KeyError, TypeError):
        return None
    people = []
    for role in event.get('ps', []):
        for person in role.get('bo', []):
            name = ' '.join(str(person.get(k) or '').strip() for k in ('fn', 'ln')).strip()
            if name and name not in people:
                people.append(name)
    title = ' · '.join(str(event.get(k) or '').strip() for k in ('ar', 'ev') if event.get(k))
    return {'label': ', '.join(people) if people else title or 'Booked · name not shown',
            'title': title, 'startHour': left / 60, 'endHour': right / 60}


def enrich_grid(page, day, snapshot, eligible):
    """Read the same event detail endpoint used by ASIMUT hover cards.

    Four bounded GETs at a time; no participant IDs, usernames or raw responses
    are retained. A failed detail lookup leaves the occupied interval intact.
    """
    from operation_control import check_operation_stop, operation_stage
    value = copy.deepcopy(snapshot)
    rows = [r for r in value['rooms'] if r['room'] in eligible]
    ids = [str(r.get('locationId') or '') for r in rows]
    if not all(i.isdecimal() for i in ids):
        return value  # Legacy grids still preserve visible text.
    midnight = datetime.combine(day, datetime.min.time(), tzinfo=SITE_TIMEZONE)
    start, end = midnight.replace(hour=7).isoformat(), midnight.replace(hour=22, minute=59).isoformat()
    # This is the exact existing overview metadata resource, scoped to eligible rooms.
    url = f'/services/v2/locations/location_ids={",".join(ids)};start_at={start};end_at={end};current_date={midnight.isoformat()}/meta'
    response = page.request.get('https://rwcmd.asimut.net' + url, timeout=15000)
    meta = response.json().get('response', {}) if response.ok else {}
    locations = {str(r['id']): r for r in meta.get('locations', [])}
    for row in rows:
        location = locations.get(str(row['locationId']))
        if location is None:
            raise ValueError('Room closing times could not be verified')
        closures = []
        for closed in location.get('closed_hours', []):
            left, right = (datetime.fromisoformat(closed[k]).astimezone(SITE_TIMEZONE) for k in ('st', 'en'))
            a, b = max(midnight, left), min(midnight + timedelta(days=1), right)
            if b > a:
                closures.append({'startHour': (a - midnight).total_seconds()/3600,
                                 'endHour': (b - midnight).total_seconds()/3600, 'closed': True})
        # The SVG also paints closure events as event overlays. Preserve closure
        # semantics once instead of drawing a fake unnamed booking over them.
        events = [b for b in row['blockedRanges'] if not b['closed'] and not any(
            c['startHour'] <= b['startHour'] + 1/60 and c['endHour'] >= b['endHour'] - 1/60 for c in closures)]
        row['blockedRanges'] = closures + events
    requests = [(row, block) for row in rows for block in row['blockedRanges']
                if not block['closed'] and str(block.get('eventId') or '').isdecimal()]
    for offset in range(0, len(requests), 4):
        check_operation_stop()
        operation_stage(f'Reading booking names for {day.isoformat()} ({min(offset + 4, len(requests))}/{len(requests)})…')
        batch = requests[offset:offset+4]
        details = page.evaluate('''async ids => Promise.all(ids.map(async id => {
            try { const r=await fetch('/services/v2/event/event_id='+id,{credentials:'same-origin',signal:AbortSignal.timeout(12000)});
                if(!r.ok)return null;const j=await r.json();const e=j.response?.event;
                return j.response?.event_exists && e ? {id:e.id,rs:e.rs,st:e.st,en:e.en,ar:e.ar,ev:e.ev,
                    ps:(e.ps||[]).map(role=>({bo:(role.bo||[]).map(p=>({fn:p.fn,ln:p.ln}))}))} : null;
            } catch {return null;}
        }))''', [str(block['eventId']) for _, block in batch])
        for (row, block), event in zip(batch, details):
            display = event_display(event, str(block['eventId']), str(row['locationId']), day, block) if isinstance(event, dict) else None
            if display:
                block.update(display)
    return value


def room_revision(settings):
    value = room_preferences_to_dict(load_room_preferences(settings))
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def clock(minutes):
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


def normalize_day(day, snapshot, eligible, *, observed_at=None):
    """Preserve every busy/closed interval, including fully occupied rooms."""
    date.fromisoformat(day)
    if snapshot.get('renderer') not in {'svg', 'legacy'}:
        raise ValueError('Unrecognized room grid')
    by_name = {row['room']: row for row in snapshot['rooms']}
    if len(by_name) != len(snapshot['rooms']) or any(name not in by_name for name in eligible):
        raise ValueError('Incomplete room grid')
    rooms = []
    for name in eligible:
        row = by_name[name]
        intervals = []
        for block in row['blockedRanges']:
            left, right = block['startHour'], block['endHour']
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                   for v in (left, right)) or type(block['closed']) is not bool or right <= left:
                raise ValueError('Invalid room interval')
            start, end = max(420, round(left * 60)), min(1380, round(right * 60))
            if start >= end:
                continue
            event_id = str(block.get('eventId') or '')
            intervals.append({'start': start, 'end': end,
                'kind': 'closed' if block['closed'] else 'booked',
                'label': ('Closed' if block['closed'] else ' '.join(str(block.get('label') or '').split())[:500] or 'Booked · name not shown'),
                'title': ' '.join(str(block.get('title') or '').split())[:500],
                'event_id': event_id if event_id.isdecimal() and len(event_id) <= 20 else None})
        intervals.sort(key=lambda item: (item['start'], item['end']))
        # Only an explicit terminal closure establishes the closing time.
        closed = sorted((b['start'], b['end']) for b in intervals if b['kind'] == 'closed')
        merged = []
        for start, end in closed:
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        terminal = next((start for start, end in merged if end >= 1380), None)
        rooms.append({'name': name, 'intervals': intervals,
                      'closed_all_day': terminal == 420, 'closes': clock(terminal) if terminal is not None and terminal > 420 else None})
    return {'date': day, 'observed_at': observed_at or datetime.now(timezone.utc).isoformat(), 'rooms': rooms}


def publish_day(day, snapshot, eligible, revision, *, root=ROOT):
    document = normalize_day(day, snapshot, eligible)
    path = Path(root) / 'data/room_grid.json'
    with InterProcessFileLock(path.with_suffix('.json.lock')):
        try:
            old = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            old = {}
        days = old.get('days', {}) if old.get('room_revision') == revision else {}
        days[day] = document
        today = date.today()
        days = {key: value for key, value in days.items()
                if today - timedelta(days=7) <= date.fromisoformat(key) <= today + timedelta(days=31)}
        atomic_write_json(path, {'version': 1, 'room_revision': revision, 'days': days})


def read_grid(*, root=ROOT, now=None):
    now = now or datetime.now(timezone.utc)
    revision = None
    try:
        revision = room_revision(load_settings(Path(root) / 'data/settings.json'))
        value = json.loads((Path(root) / 'data/room_grid.json').read_text(encoding='utf-8'))
        if value.get('version') != 1 or value.get('room_revision') != revision:
            return {'days': {}, 'revision': revision, 'message': 'Room filters changed. Availability needs a new check.'}
        for day in value['days'].values():
            age = (now - datetime.fromisoformat(day['observed_at'])).total_seconds()
            day['stale'] = not 0 <= age <= 300
        return {'days': value['days'], 'revision': revision, 'message': ''}
    except SettingsError:
        return {'days': {}, 'revision': None, 'message': 'Room settings could not be read. Check Settings before refreshing.'}
    except (OSError, ValueError, KeyError, TypeError):
        return {'days': {}, 'revision': revision, 'message': 'Room availability has not been loaded yet.'}
