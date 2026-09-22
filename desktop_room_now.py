"""Durable PC requests through the same bounded worker as the phone."""
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from uuid import UUID, uuid4

from app_settings import atomic_write_json
from phone_system import timestamp
from room_now import validate_choices
from runtime_guard import SingleInstanceLock

ROOT = Path(__file__).resolve().parent


class DesktopRoomNow:
    def __init__(self, root=ROOT, runner=None):
        self.root = Path(root)
        self.path = self.root / 'data/desktop_room_now.json'
        self.lock = SingleInstanceLock(self.root / 'data/desktop-room-now.lock')
        self.thread = None
        self.runner = runner or self._subprocess

    @property
    def active(self):
        if self.lock.acquired:
            return True
        probe = SingleInstanceLock(self.lock.path)
        if not probe.acquire():
            return True
        probe.release()
        return False

    def snapshot(self):
        if not self.path.exists():
            return None
        job = json.loads(self.path.read_text(encoding='utf-8'))
        UUID(job['request_id'])
        if job.get('state') not in {'running', 'checking', 'completed', 'blocked', 'empty', 'stopped', 'failed', 'rejected', 'uncertain'}:
            raise ValueError('The previous room request needs review.')
        return job

    def start(self, choices=None, *, review=False):
        if not review:
            validate_choices(choices)
        if self.lock.acquired or not self.lock.acquire():
            raise ValueError('A room search is already running.')
        try:
            previous = self.snapshot()
            if review:
                if not previous:
                    raise ValueError('There is no room request to check.')
                job = {**previous, 'state': 'checking', 'text': 'Checking the latest agenda…'}
            else:
                if previous and previous['state'] in {'running', 'checking', 'uncertain'}:
                    raise ValueError('Check the previous booking status before trying again.')
                job = {'request_id': str(uuid4()), 'state': 'running', 'choices': choices,
                       'started_at': timestamp(), 'text': 'Finding a room for the earliest start…'}
            atomic_write_json(self.path, job)
            self.thread = threading.Thread(target=self._run, args=(job, review), daemon=False)
            try:
                self.thread.start()
            except Exception:
                atomic_write_json(self.path, {**job, 'state': 'uncertain', 'text': 'The request did not start normally. Check booking status.'})
                raise
            return job
        except Exception:
            self.lock.release()
            raise

    def stop(self):
        job = self.snapshot()
        if not job or not self.active:
            return
        folder = self.root / 'data/desktop_operations' / str(UUID(job['request_id']))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'stop').touch()

    def _run(self, job, review):
        try:
            result = self.runner(job, review)
            atomic_write_json(self.path, {**job, 'state': result['state'], 'text': result['message'], 'result': result})
        except Exception:
            atomic_write_json(self.path, {**job, 'state': 'uncertain', 'text': 'The booking result needs checking before another request.'})
        finally:
            self.lock.release()

    def _subprocess(self, job, review):
        directory = self.root / 'data/desktop_operations' / job['request_id']
        directory.mkdir(parents=True, exist_ok=True)
        if not review:
            atomic_write_json(directory / 'submitted.json', {'requested_at': job['started_at']})
        python = self.root / '.venv/Scripts/python.exe'
        process = subprocess.Popen([str(python if python.exists() else sys.executable), str(self.root / 'phone_operation_worker.py')],
            cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        process.stdin.write(json.dumps({'surface': 'desktop', 'request_id': job['request_id'],
            'action': 'room_now_review' if review else 'room_now', 'args': {} if review else job['choices']}))
        process.stdin.close()
        started = time.monotonic()
        while process.poll() is None:
            progress = directory / 'progress.json'
            if progress.exists():
                try:
                    value = json.loads(progress.read_text(encoding='utf-8'))
                    atomic_write_json(self.path, {**job, 'text': value['text']})
                except (OSError, ValueError, KeyError):
                    pass
            if time.monotonic() - started > 300 and not review:
                (directory / 'stop').touch()
            time.sleep(.5)
        if process.returncode:
            raise ValueError('The worker ended without a result.')
        return json.loads((directory / 'result.json').read_text(encoding='utf-8'))
