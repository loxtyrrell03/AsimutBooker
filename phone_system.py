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
from event_identity import event_identity_v2, legacy_event_identity, resolve_ignored_event_keys
from runtime_guard import SingleInstanceLock

ROOT = Path(__file__).resolve().parent
RUN_ACTIONS = {'run', 'run_visible', 'login', 'scan', 'agenda', 'plan'}
WRITE_ACTIONS = {'schedule_install', 'schedule_remove', 'history_clear', 'events_save',
                 'config_save', 'cleanup', 'reopen'}
VIEWS = {'scan', 'schedule', 'history', 'events', 'config', 'logs', 'cleanup', 'protected'}


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
              'cleanup': {'revision'}, 'reopen': {'revision', 'window'}}.get(action, set())
    if set(args) != fields:
        raise ValueError('The action has unexpected fields.')
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


def _event_document(root, settings=None):
    settings = load_settings(root / 'data/settings.json') if settings is None else settings
    result = read_agenda_snapshot(root / 'data/agenda_snapshot.json')
    if result.snapshot is None:
        raise SystemConflict('Refresh the agenda before managing events.')
    events = result.snapshot.event_dicts()
    ignored = settings.get('ignored_events', [])
    resolution = resolve_ignored_event_keys(ignored, events)
    identities = {revision(event_identity_v2(e)): event_identity_v2(e) for e in events}
    return {'revision': revision([events, ignored]), 'stale': result.stale,
            'events': [{'key': revision(event_identity_v2(e)), 'date': e['date'],
                        'start': e['startTime'], 'end': e['endTime'],
                        'title': e['title'][:200], 'room': (e.get('room') or '')[:100],
                        'ignored': event_identity_v2(e) in resolution.ignored_v2_keys}
                       for e in events]}, identities, events


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
            with settings_transaction(root / 'data/settings.json') as settings:
                doc, identities, events = _event_document(root, settings)
                _require_revision(doc['revision'], args['revision'])
                if doc['stale']:
                    raise SystemConflict('Refresh the agenda before changing events.')
                if set(args['changes']) - set(identities):
                    raise SystemConflict('An event changed. Refresh the agenda.')
                ignored = set(settings.get('ignored_events', []))
                resolved = resolve_ignored_event_keys(ignored, events)
                # Migrate only unambiguous legacy keys, preserving unrelated choices.
                for event in events:
                    old = legacy_event_identity(event)
                    if old in ignored and old not in resolved.ambiguous_legacy_keys:
                        ignored.remove(old)
                        ignored.add(event_identity_v2(event))
                for key, ignore in args['changes'].items():
                    if ignore:
                        ignored.add(identities[key])
                    else:
                        ignored.discard(identities[key])
                settings['ignored_events'] = sorted(ignored)
            from booking_plan import clear_booking_plan
            clear_booking_plan(root / 'data/booking_plan.json')
            return {'message': 'Event conflict choices saved.'}
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
