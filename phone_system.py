"""Typed desktop tools for the private phone host; no arbitrary paths or commands."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

from app_settings import (InterProcessFileLock, SettingsError, atomic_write_json,
                          load_settings, settings_transaction)
from agenda_snapshot import read_agenda_snapshot
from event_identity import (EVENT_RESPECT_PREFIX, EventIdentityError, deduplicate_events, event_choice_eligibility, event_identity,
                            event_identity_payload, event_identity_v2,
                            legacy_event_identity, remote_event_id, resolve_ignored_event_keys)
from runtime_guard import SingleInstanceLock
from reservation_pins import project_reservation_pins

ROOT = Path(__file__).resolve().parent
RUN_ACTIONS = {'run', 'run_visible', 'login', 'scan', 'agenda', 'plan', 'room_now', 'fill_range'}
WRITE_ACTIONS = {'schedule_install', 'schedule_remove', 'history_clear', 'events_save',
                 'config_save', 'cleanup', 'reopen', 'pins_save'}
VIEWS = {'scan', 'schedule', 'history', 'events', 'config', 'logs', 'cleanup', 'protected', 'pins'}


class SystemConflict(ValueError):
    """The displayed source changed, or the host is occupied."""


def revision(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def validate_action(action, args):
    if action not in RUN_ACTIONS | WRITE_ACTIONS or not isinstance(args, dict):
        raise ValueError('Choose a supported action.')
    fields = {'scan': {'dates'}, 'history_clear': {'revision'},
              'events_save': {'revision', 'changes'}, 'config_save': {'revision', 'rules'},
              'pins_save': {'revision', 'target', 'protected'},
              'cleanup': {'revision'}, 'reopen': {'revision', 'window'},
              'room_now': {'mode', 'minutes'},
              'fill_range': {'date', 'start_time', 'end_time'}}.get(action, set())
    if set(args) != fields:
        raise ValueError('The action has unexpected fields.')
    if action == 'room_now':
        from room_now import validate_choices
        validate_choices(args)
    if action == 'fill_range':
        from fill_range import validate_choices
        validate_choices(args)
    if 'revision' in fields and (not isinstance(args['revision'], str) or
                                re.fullmatch('[a-f0-9]{64}', args['revision']) is None):
        raise ValueError('Reload this page before saving.')
    if action == 'scan':
        values = args['dates']
        if not isinstance(values, list) or not 1 <= len(values) <= 31:
            raise ValueError('Choose between one and 31 scan dates.')
        for value in values:
            if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                raise ValueError('Choose valid scan dates.')
        if len(set(values)) != len(values):
            raise ValueError('Scan dates must be unique.')
    if action == 'events_save':
        changes = args['changes']
        if not isinstance(changes, dict) or not 1 <= len(changes) <= 200:
            raise ValueError('Choose events to change.')
        if any(not isinstance(k, str) or re.fullmatch('[a-f0-9]{64}', k) is None
               or type(v) is not bool for k, v in changes.items()):
            raise ValueError('Event choices are invalid.')
    if action == 'config_save':
        from book_week import validate_config_document
        validate_config_document({'rules': args['rules']})
    if action == 'pins_save':
        from reservation_pins import validate_target
        validate_target(args['target'])
        if type(args['protected']) is not bool:
            raise ValueError('Choose whether to protect this reservation.')
    if action == 'reopen':
        from booking_blackouts import make_rebooking_blackout
        window = args['window']
        if not isinstance(window, dict) or set(window) != {'date', 'start_time', 'end_time'}:
            raise ValueError('Choose one protected time window.')
        make_rebooking_blackout(window['date'], window['start_time'], window['end_time'])
    return args


def _history(root):
    from gui import load_history_document
    return load_history_document(root / 'data/booking_history.json')


def project_event_choices(events, ignored_keys, *, stale, observed_at, covered_dates):
    """Project one already validated agenda/settings snapshot without re-reading.

    The revision covers the exact dated identities, scope and ALL saved choices.
    An observation-only refresh does not change permission; stale evidence still
    cannot be saved. This helper lets Tempo use its existing coherent snapshot.
    """
    events = deduplicate_events(events)
    if isinstance(ignored_keys, (str, bytes)):
        raise EventIdentityError('Ignored event keys must be a collection')
    ignored_keys = list(ignored_keys)
    resolution = resolve_ignored_event_keys(ignored_keys, events)
    ambiguous = resolution.ambiguous_legacy_keys | resolution.ambiguous_v2_keys
    rows = []
    for event in events:
        identity = event_identity(event)
        payload = event_identity_payload(event)
        eligible, unsupported = event_choice_eligibility(event, events)
        old, v2 = legacy_event_identity(event), event_identity_v2(event)
        ignored = identity in resolution.ignored_event_keys
        basis = None
        if identity in resolution.respected_event_keys or (ignored and identity.startswith('v3:') and identity in ignored_keys):
            basis = 'exact'
        elif ignored:
            basis = 'v2' if v2 in ignored_keys else 'legacy'
        rows.append(dict(key=revision(identity), identity=identity, event_id=remote_event_id(event),
                         date=payload['date'], start=payload['start'], end=payload['end'],
                         title=payload['title'], room=payload['room'], is_reservation=payload['isReservation'],
                         eligible=eligible, unsupported_reason=unsupported, ignored=ignored,
                         choice_basis=basis, unresolved_choice=bool({old, v2} & ambiguous)
                         or identity in resolution.ambiguous_event_keys))
    rows.sort(key=lambda row: (row['date'], row['start'], row['end'], row['identity']))
    dates = sorted(set(covered_dates))
    return dict(revision=revision([[row['identity'] for row in rows], sorted(set(ignored_keys)), dates]),
                stale=stale, observed_at=observed_at, covered_dates=dates, events=rows,
                unresolved_choice_count=len(ambiguous | (set(ignored_keys) & resolution.ambiguous_event_keys)))


def _event_document(root, settings=None):
    settings = load_settings(root / 'data/settings.json') if settings is None else settings
    result = read_agenda_snapshot(root / 'data/agenda_snapshot.json')
    if result.snapshot is None:
        raise SystemConflict('Refresh the agenda before managing events.')
    events = result.snapshot.event_dicts()
    document = project_event_choices(events, settings.get('ignored_events', []), stale=result.stale,
                                     observed_at=result.snapshot.observed_at.isoformat(),
                                     covered_dates=[day.isoformat() for day in result.snapshot.dates])
    identities = {row['key']: row['identity'] for row in document['events'] if row['eligible']}
    return document, identities, events


def _pins_document(root, settings=None):
    from mutation_receipts import list_pending
    settings = load_settings(root / 'data/settings.json') if settings is None else settings
    result = read_agenda_snapshot(root / 'data/agenda_snapshot.json')
    snapshot = result.snapshot
    return project_reservation_pins(snapshot.event_dicts() if snapshot else [], settings,
        stale=result.stale, observed_at=snapshot.observed_at.isoformat() if snapshot else None,
        covered_dates=[day.isoformat() for day in snapshot.dates] if snapshot else [],
        pending_count=len(list_pending(root / 'data/mutation_receipts.json')))


def _changed_event_choices(ignored_keys, events, changes, identities):
    """Only explicitly reviewed eligible rows may retire uniquely bound aliases."""
    ignored = set(ignored_keys)
    matches = {}
    for event in events:
        current = event_identity(event)
        for alias in (legacy_event_identity(event), event_identity_v2(event)):
            matches.setdefault(alias, set()).add(current)
    aliases_by_identity = {}
    for alias, targets in matches.items():
        if len(targets) == 1:
            aliases_by_identity.setdefault(next(iter(targets)), set()).add(alias)
    for key, allow in changes.items():
        identity = identities[key]
        # Out-of-view and ambiguous historical permissions stay untouched. An
        # explicit change can retire only aliases uniquely bound to this row.
        ignored.difference_update(aliases_by_identity.get(identity, set()))
        if allow:
            ignored.discard(EVENT_RESPECT_PREFIX + identity)
            ignored.add(identity)
        else:
            ignored.discard(identity)
            ignored.add(EVENT_RESPECT_PREFIX + identity)
    return sorted(ignored)


def _config(root):
    from book_week import load_config
    path = root / 'config/config.yaml'
    config = load_config(path)
    raw = path.read_bytes().hex() if path.exists() else None
    return {'revision': revision(raw), 'rules': {
        'rolling_quota': config['rolling_quota'],
        'same_room_gap_minutes': config['same_room_gap_minutes'],
        'peak_hours': {'start': f"{config['peak_start']:02d}:00",
                       'end': f"{config['peak_end']:02d}:00",
                       'max_hours': config['max_peak_hours']}}}


def _dated_logs(root):
    directory = (root / 'logs').resolve()
    rows = []
    if directory.is_dir():
        for path in directory.iterdir():
            # Only dated, inactive Booker logs. Never server/scheduler logs or auth files.
            if not re.fullmatch(r'booker_\d{4}-?\d{2}-?\d{2}(?:_\d{6})?\.log', path.name):
                continue
            if path.is_symlink() or not path.is_file() or path.resolve().parent != directory:
                continue
            stat = path.stat()
            rows.append({'name': path.name, 'bytes': stat.st_size, 'modified_ns': stat.st_mtime_ns})
    return sorted(rows, key=lambda row: row['name'])


def _old_logs(root):
    cutoff = (datetime.now(timezone.utc).timestamp() - 48 * 3600) * 1_000_000_000
    return [row for row in _dated_logs(root) if row['modified_ns'] < cutoff]


def _safe_log_rows(path):
    """Extract known status categories; never pass through arbitrary log text."""
    if path.is_symlink() or not path.is_file():
        return []
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size - 256_000))
        raw = stream.read(256_000).decode('utf-8', errors='replace')
    patterns = [(r'CHECK PASSED', 'Availability check completed'),
                (r'LOGIN SUCCESS', 'Login check completed'),
                (r'PLAN (?:SAVED|COMPLETE)', 'Practice plan updated'),
                (r'(?i)reconciliation.required', 'A booking outcome needs review'),
                (r'(?i)another.*(?:running|lock)|already running', 'Booker already running'),
                (r'(?i)\b(?:ERROR|Traceback)\b', 'Run reported an error'),
                (r'(?i)\b(?:starting|started)\b.*book', 'Booker started'),
                (r'(?i)\b(?:finished|completed)\b.*book', 'Booker finished')]
    rows = []
    for line in raw.splitlines():
        for pattern, label in patterns:
            if re.search(pattern, line):
                match = re.search(r'\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\b', line)
                rows.append({'time': match.group(0) if match else '', 'message': label})
                break
    return rows[-200:]


def read_view(view, root=ROOT):
    root = Path(root)
    if view == 'scan':
        path = root / 'data/phone_operations/latest-scan.json'
        return {'scan': json.loads(path.read_text(encoding='utf-8')) if path.exists() else None}
    if view == 'schedule':
        from gui import query_recurring_task_status
        return {'task': query_recurring_task_status(timeout=8)}
    if view == 'history':
        source = _history(root)
        runs = []
        for item in reversed(source['runs'][-100:]):
            if not isinstance(item, dict):
                continue
            # History details can include browser output: expose only typed facts.
            time = str(item.get('timestamp', ''))
            try:
                time = datetime.fromisoformat(time.replace('Z', '+00:00')).isoformat()
            except ValueError:
                time = ''
            count = item.get('bookings_made')
            runs.append({'time': time, 'bookings': count if type(count) is int and count >= 0 else None,
                         'outcome': item.get('outcome') if item.get('outcome') in
                         {'completed', 'reconciliation_required', 'failed'} else 'unknown'})
        return {'revision': revision(source), 'runs': runs}
    if view == 'events':
        return _event_document(root)[0]
    if view == 'pins':
        return _pins_document(root)
    if view == 'config':
        return _config(root)
    if view == 'cleanup':
        rows = _old_logs(root)
        return {'revision': revision(rows), 'files': rows}
    if view == 'logs':
        files = []
        for name in ['scheduler.log', *[r['name'] for r in _dated_logs(root)[-20:]]]:
            path = root / 'logs' / name
            if path.is_file() and not path.is_symlink():
                files.append({'name': name, 'entries': _safe_log_rows(path)})
        return {'files': files}
    if view == 'protected':
        from booking_blackouts import load_rebooking_blackouts
        rows = [w.to_dict() for w in load_rebooking_blackouts(load_settings(root / 'data/settings.json'))]
        return {'revision': revision(rows), 'windows': rows}
    raise ValueError('Unknown system page.')


def _require_revision(actual, expected):
    if actual != expected:
        raise SystemConflict('This page changed. Reload and review your choices before trying again.')


def run_local_action(action, args, root=ROOT):
    """Each write uses the runtime lock plus its own document's atomic revision check."""
    validate_action(action, args)
    root = Path(root)
    with SingleInstanceLock(root / 'data/booker-runtime.lock'):
        if action in {'schedule_install', 'schedule_remove'}:
            from gui import build_scheduler_setup_command, build_scheduler_remove_command, query_recurring_task_status
            command = (build_scheduler_setup_command(root / 'setup_scheduled_tasks.ps1')
                       if action == 'schedule_install' else build_scheduler_remove_command())
            reply = subprocess.run(command, capture_output=True, timeout=120,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            task = query_recurring_task_status(timeout=8)
            if reply.returncode or (action == 'schedule_remove' and task is not None) or (
                    action == 'schedule_install' and (not task or not task.get('healthy'))):
                raise SystemConflict('The schedule change could not be verified. Refresh its status.')
            return {'message': 'Automatic schedule repaired.' if action == 'schedule_install' else 'Automatic schedule removed.'}
        if action == 'history_clear':
            path = root / 'data/booking_history.json'
            with InterProcessFileLock(path.with_suffix('.json.lock')):
                _require_revision(revision(_history(root)), args['revision'])
                atomic_write_json(path, {'runs': []}, backup=True)
            return {'message': 'Booking history cleared. Reservations are unchanged.'}
        if action == 'config_save':
            path = root / 'config/config.yaml'
            with InterProcessFileLock(path.with_suffix('.yaml.lock')):
                _require_revision(_config(root)['revision'], args['revision'])
                atomic_write_json(path, {'rules': args['rules']}, backup=True)
            return {'message': 'Rules saved for the next Booker run.'}
        if action == 'events_save':
            from preference_runs import preferences_transaction
            with preferences_transaction(root / 'data/settings.json') as settings:
                doc, identities, events = _event_document(root, settings)
                _require_revision(doc['revision'], args['revision'])
                if doc['stale']:
                    raise SystemConflict('Refresh the agenda before changing events.')
                if set(args['changes']) - set(identities):
                    raise SystemConflict('An event changed. Refresh the agenda.')
                settings['ignored_events'] = _changed_event_choices(
                    settings.get('ignored_events', []), events, args['changes'], identities)
            from booking_plan import clear_booking_plan
            clear_booking_plan(root / 'data/booking_plan.json')
            return {'message': 'Event conflict choices saved.'}
        if action == 'pins_save':
            from reservation_pins import apply_protection
            with settings_transaction(root / 'data/settings.json') as settings:
                doc = _pins_document(root, settings)
                _require_revision(doc['revision'], args['revision'])
                matches = [row for row in doc['reservations']
                           if row['present'] and row['target'] == args['target']]
                if len(matches) != 1 or not matches[0]['eligible']:
                    raise SystemConflict('This reservation cannot be changed. Refresh and review its protection.')
                apply_protection(settings, args['target'], args['protected'])
            from booking_plan import clear_booking_plan
            clear_booking_plan(root / 'data/booking_plan.json')
            clear_booking_plan(root / 'data/upgrade_plan.json')
            return dict(ok=True, pins=_pins_document(root), target=args['target'], protected=args['protected'])
        if action == 'cleanup':
            rows = _old_logs(root)
            _require_revision(revision(rows), args['revision'])
            for row in rows:
                path = root / 'logs' / row['name']
                # Recheck each candidate immediately; a newly active log is retained.
                if row not in _old_logs(root):
                    raise SystemConflict('The log files changed. Reload the cleanup preview.')
                path.unlink()
            return {'message': f"Removed {len(rows)} old log files."}
        if action == 'reopen':
            from booking_blackouts import make_rebooking_blackout, load_rebooking_blackouts, merge_rebooking_blackouts
            window = args['window']
            with settings_transaction(root / 'data/settings.json') as settings:
                values = load_rebooking_blackouts(settings)
                rows = [w.to_dict() for w in values]
                _require_revision(revision(rows), args['revision'])
                if window not in rows:
                    raise SystemConflict('This protected time changed. Reload the list.')
                selected = make_rebooking_blackout(window['date'], window['start_time'], window['end_time'])
                # Exact whole-window reopening; no broad date or reservation mutation.
                from booking_blackouts import _store
                _store(settings, merge_rebooking_blackouts(w for w in values if w != selected))
            from booking_plan import clear_booking_plan
            clear_booking_plan(root / 'data/booking_plan.json')
            return {'message': 'This time is available to the automatic Booker again.'}
    raise ValueError('Unsupported local action.')
