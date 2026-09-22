"""Durable, coalesced Booker checks requested by successful preference saves.

The request lives in the same atomic settings write as the preferences. Only
explicit preference editors use update_preferences; worker bookkeeping does not.
One hidden dispatcher waits for existing owners and uses the normal guarded
Booker entry point. A changed request during a run remains pending afterwards.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app_settings import InterProcessFileLock, SettingsError, load_settings, update_settings, settings_transaction
from runtime_guard import SingleInstanceLock

ROOT = Path(__file__).resolve().parent
SETTINGS = ROOT / 'data/settings.json'
KEY = 'preference_run'
CONTROL_KEYS = ('practice_plan', 'time_preferences', 'date_time_preferences',
                'disabled_dates', 'room_preferences', 'booking_strategy',
                'booking_rules', 'advance_quota', 'ignored_events')
DEBOUNCE_SECONDS = 2
_dispatching = False
LOGGER = logging.getLogger(__name__)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def controls(settings):
    return json.dumps({key: settings.get(key) for key in CONTROL_KEYS},
                      sort_keys=True, separators=(',', ':'), allow_nan=False)


def pending(settings):
    request = settings.get(KEY)
    return request if isinstance(request, dict) and request.get('state') in {'pending', 'running'} else None


def public_status(settings):
    request = settings.get(KEY)
    if not isinstance(request, dict) or request.get('state') not in {'pending', 'running', 'completed', 'failed', 'paused'}:
        return None
    return {'state': request['state'], 'message': request.get('message', '')}


def queue_if_changed(before, settings):
    changed = controls(settings) != before
    if changed:
        settings[KEY] = {'id': str(uuid4()), 'requested_at': timestamp(),
                         'state': 'pending', 'message': 'Settings saved; Booker check queued.'}
    return changed


def update_preferences(mutator, path=SETTINGS):
    """Persist a preference edit and its run request together, then wake a worker."""
    changed = False
    def mutate(settings):
        nonlocal changed
        before = controls(settings)
        result = mutator(settings)
        changed = queue_if_changed(before, settings)
        return result
    result = update_settings(mutate, path)
    if changed:
        kick(path)
    return result


@contextmanager
def preferences_transaction(path=SETTINGS):
    """The same save contract for editors that need a locked settings document."""
    with settings_transaction(path) as settings:
        before = controls(settings)
        yield settings
        changed = queue_if_changed(before, settings)
    if changed:
        kick(path)


def settle(path, request_id, state, message, *, code=None):
    """Never acknowledge or overwrite a newer save with an older run's result."""
    def mutate(settings):
        request = pending(settings)
        if request and request.get('id') == request_id:
            request.update(state=state, message=message, updated_at=timestamp())
            if code is not None:
                request['exit_code'] = code
    update_settings(mutate, path)


def complete_matching_run(path, settings, args):
    """An ordinary full run using this save can satisfy the queued request."""
    if any(getattr(args, name, False) for name in (
            'only_date', 'only_room', 'check_only', 'agenda_only', 'plan_only',
            'upgrade_dry_run', 'horizon_only', 'extensions_only', 'upgrades_only',
            'max_actions', 'max_action_minutes', 'room_now_mode')):
        return
    if getattr(args, 'scheduled', False) and not getattr(args, 'target_time', None):
        return  # Intervening scheduled passes may check only today's practice.
    request = pending(settings)
    if request:
        settle(path, request['id'], 'completed', 'Booker checked the saved preferences.', code=0)


def kick(path=SETTINGS):
    """Best-effort wake-up; durable requests survive a failed launch or exit."""
    path = Path(path)
    if _dispatching or path.resolve() != SETTINGS.resolve():
        return False
    try:
        if not pending(load_settings(path)):
            return False
        python = ROOT / '.venv/Scripts/python.exe'
        if not python.is_file():
            return False
        # Avoid spawning duplicates while another dispatcher owns the queue.
        probe = SingleInstanceLock(ROOT / 'data/preference-dispatch.lock')
        if not probe.acquire():
            return False
        probe.release()
        folder = ROOT / 'logs/preference-runs'
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / (datetime.now().strftime('%Y-%m-%d') + '.log')).open('a', encoding='utf-8') as log:
            subprocess.Popen([str(python), '-u', str(ROOT / 'preference_runs.py')],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return True
    except (OSError, SettingsError):
        LOGGER.exception('Preference save succeeded; its queued Booker check could not be launched yet')
        return False


def automatic_booking_enabled():
    """Respect the owner's existing automatic-booking Off choice."""
    from gui import query_recurring_task_status
    status = query_recurring_task_status(timeout=10)
    return bool(status and status.get('healthy'))


def run_pending(*, root=ROOT, booker_main=None, enabled=None, sleep=time.sleep,
                monotonic=time.monotonic, max_wait_seconds=20 * 60):
    """Drain the newest request, without replaying one failed run indefinitely."""
    global _dispatching
    root = Path(root)
    path = root / 'data/settings.json'
    lock = SingleInstanceLock(root / 'data/preference-dispatch.lock')
    if not lock.acquire():
        return
    _dispatching = True
    drained = False
    deadline = monotonic() + max_wait_seconds
    try:
        while monotonic() < deadline:
            request = pending(load_settings(path))
            if request is None:
                drained = True
                break
            request_id = request['id']
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(request['requested_at'])).total_seconds()
            if age < DEBOUNCE_SECONDS:
                sleep(min(DEBOUNCE_SECONDS - age, .5))
                continue
            # Probe runtime first; never hold the assistant lock while waiting
            # on a running or prepared booking. main() acquires it again and
            # safely returns busy if a scheduled worker wins the small race.
            runtime = SingleInstanceLock(root / 'data/booker-runtime.lock')
            if not runtime.acquire():
                sleep(1)
                continue
            runtime.release()
            assistant = InterProcessFileLock(root / 'data/assistant-mutation.lock', timeout=0)
            try:
                assistant.acquire()
            except SettingsError:
                sleep(1)
                continue
            try:
                latest = pending(load_settings(path))
                if not latest or latest['id'] != request_id:
                    continue
                try:
                    allowed = (enabled or automatic_booking_enabled)()
                except Exception:
                    settle(path, request_id, 'failed', 'Settings saved; automatic booking status could not be checked.')
                    continue
                if not allowed:
                    settle(path, request_id, 'paused', 'Settings saved; automatic booking is off or needs attention.')
                    continue
                latest = pending(load_settings(path))
                if not latest or latest['id'] != request_id:
                    continue
                settle(path, request_id, 'running', 'Booker is checking the saved preferences.')
                print(f'{timestamp()} Starting preference check {request_id}', flush=True)
                if booker_main is None:
                    from book_week import main as booker_main
                try:
                    code = booker_main(['--headless'])
                except Exception:
                    LOGGER.exception('Preference check ended without a normal result')
                    code = 1
                if code == 6:
                    # Contention is not an attempt and must not lose the save.
                    settle(path, request_id, 'pending', 'Waiting for the current Booker run to finish.')
                else:
                    settle(path, request_id, 'completed' if code == 0 else 'failed',
                        'Booker checked the saved preferences.' if code == 0 else
                        'Booker could not finish this check. Review its latest activity.', code=code)
                print(f'{timestamp()} Preference check {request_id}: exit {code}', flush=True)
            finally:
                assistant.release()
            # A later save gets one new pass. No-change saves, quota waits,
            # failed checks and worker bookkeeping cannot form a retry loop.
            if code == 6:
                sleep(1)
    finally:
        _dispatching = False
        lock.release()
        # Close the last-save/dispatcher-exit race after releasing ownership.
        # After this check a new save can acquire the dispatcher lock itself.
        if drained:
            kick(path)


if __name__ == '__main__':
    from runtime_guard import configure_utf8_stdio
    configure_utf8_stdio()
    run_pending()
