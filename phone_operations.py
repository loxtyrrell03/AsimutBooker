"""Durable, non-replayed phone jobs coordinated with the host assistant."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import UUID

from app_settings import atomic_write_json
from phone_system import ROOT, SystemConflict, timestamp, validate_action


class PhoneOperations:
    def __init__(self, service, *, root=ROOT, state_dir=None, runner=None):
        self.service = service
        self.root = Path(root)
        self.directory = Path(state_dir) if state_dir else self.root / 'data/phone_operations'
        self.path = self.directory / 'latest.json'
        self.runner = runner or self._subprocess
        self.job = None
        self.thread = None
        if self.path.exists():
            try:
                self.job = json.loads(self.path.read_text(encoding='utf-8'))
                if self.job.get('active'):
                    self.job.update(active=False, state='uncertain',
                                    text='The host restarted during this operation. Review the outcome before continuing.')
            except (OSError, ValueError, AttributeError):
                self.job = {'active': False, 'state': 'uncertain', 'text': 'Operation history is unavailable. Review the host before continuing.'}

    @property
    def active(self):
        return bool(self.job and self.job.get('active'))

    def snapshot(self):
        with self.service._request_lock:
            return json.loads(json.dumps(self.job)) if self.job else None

    def _update(self, **fields):
        with self.service._request_lock:
            self.job = {**(self.job or {}), **fields, 'updated_at': timestamp()}
            atomic_write_json(self.path, self.job)

    def submit(self, payload):
        if set(payload) != {'request_id', 'action', 'args'}:
            raise ValueError('Choose one operation.')
        request_id = str(UUID(payload['request_id']))
        action, args = payload['action'], payload['args']
        validate_action(action, args)
        with self.service._request_lock:
            previous = self.service.ledger.lookup(request_id)
            if previous is not None:
                # Accepted or uncertain UUIDs never reach the runner twice.
                return {'accepted': False, 'duplicate': True, 'job': self.snapshot()}
            if self.active or self.service.is_busy or self.service.ledger.unresolved_reserved_count():
                raise SystemConflict('Wait for the current operation or review its outcome first.')
            self.service.ledger.reserve(request_id)
            try:
                self._update(request_id=request_id, action=action, active=True, state='queued',
                             text='Waiting for the current agenda check…', started_at=timestamp(), result=None)
            except Exception:
                self.job = {'request_id': request_id, 'active': False, 'state': 'uncertain',
                            'text': 'The operation could not be recorded. Review its status before continuing.'}
                raise
            self.thread = threading.Thread(target=self._run, args=(request_id, action, args), daemon=True)
            try:
                self.thread.start()
            except Exception:
                self.service.ledger.mark(request_id, 'rejected')
                self._update(active=False, state='rejected', text='The operation could not start.')
                raise
            return {'accepted': True, 'job': self.snapshot()}

    def stop(self, payload):
        if set(payload) != {'request_id'}:
            raise ValueError('Choose the current operation to stop.')
        request_id = str(UUID(payload['request_id']))
        with self.service._request_lock:
            if not self.active or self.job['request_id'] != request_id:
                raise SystemConflict('This operation is no longer running. Reload its status.')
            if self.job['action'] not in {'run', 'run_visible', 'scan', 'agenda', 'plan', 'login'}:
                raise SystemConflict('This short settings operation must finish before another action.')
            directory = self.directory / request_id
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'stop').touch()
            self._update(state='stopping', text='Stop requested. Waiting for the current step and any booking verification to finish…')
            return {'accepted': True, 'job': self.snapshot()}

    def _run(self, request_id, action, args):
        acquired = False
        try:
            acquired = self.service._live_refresh_lock.acquire(timeout=16 * 60)
            if not acquired:
                result = {'state': 'rejected', 'message': 'The previous check did not finish. This operation was not started.'}
            else:
                directory = self.directory / request_id
                directory.mkdir(parents=True, exist_ok=True)
                if (directory / 'stop').exists():
                    result = {'state': 'stopped', 'message': 'Stopped before starting.'}
                else:
                    self._update(state='running', text='Starting the PC operation…')
                    result = self.runner(request_id, action, args, directory)
            with self.service._request_lock:
                # Retain the last successful scan during later runs and failed refreshes.
                if result.get('scan') is not None and result['state'] == 'completed':
                    atomic_write_json(self.directory / 'latest-scan.json', result['scan'])
                # Persist terminal evidence before settling its durable reservation.
                self._update(active=False, state=result['state'], text=result['message'], result=result)
                if result['state'] != 'uncertain':
                    self.service.ledger.mark(request_id, 'accepted')
        except Exception:
            self._update(active=False, state='uncertain', text='The operation could not be confirmed. Review the outcome before trying again.')
        finally:
            if acquired:
                self.service._live_refresh_lock.release()

    def _subprocess(self, request_id, action, args, directory):
        python = self.root / '.venv/Scripts/python.exe'
        if not python.exists():
            python = Path(sys.executable)
        process = subprocess.Popen([str(python), str(self.root / 'phone_operation_worker.py')],
                                   cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        process.stdin.write(json.dumps({'request_id': request_id, 'action': action, 'args': args}))
        process.stdin.close()
        started, previous = time.monotonic(), ''
        while process.poll() is None:
            path = directory / 'progress.json'
            if path.exists() and not (directory / 'stop').exists():
                try:
                    progress = json.loads(path.read_text(encoding='utf-8'))
                    if progress['text'] != previous:
                        previous = progress['text']
                        self._update(text=previous)
                except (OSError, ValueError, KeyError):
                    pass
            if time.monotonic() - started > 20 * 60 and not (directory / 'stop').exists():
                (directory / 'stop').touch()
                self._update(state='stopping', text='This operation is taking too long. A stop was requested; waiting for the current step to settle.')
            # Never kill a process during Save or release ownership while it is alive.
            time.sleep(.5)
        path = directory / 'result.json'
        if process.returncode or not path.exists():
            return {'state': 'uncertain', 'message': 'The PC operation ended without a confirmed result. Review the agenda and System health.'}
        return json.loads(path.read_text(encoding='utf-8'))
