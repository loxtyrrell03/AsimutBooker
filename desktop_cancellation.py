"""Durable desktop cancellation using the shared exact-reservation engine.

The OS lock covers reservation, execution and review across desktop instances.
A non-daemon worker finishes verification if the UI closes. Crashes leave an
uncertain record which can only be reviewed after a fresh agenda read.
"""
from pathlib import Path
import json
import threading
from uuid import uuid4

from app_settings import atomic_write_json
from assistant_tools import BookerToolSurface
from mutation_receipts import list_pending
from phone_cancellation import CancellationNotStarted, cancel_phone_reservation, validate_target
from runtime_guard import SingleInstanceLock

ROOT = Path(__file__).resolve().parent


class DesktopCancellation:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.path = self.root / 'data' / 'desktop_cancellation.json'
        self.lock = SingleInstanceLock(self.root / 'data' / 'desktop-cancellation.lock')
        self.thread = None

    def snapshot(self):
        if not self.path.exists():
            return None
        value = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or value.get('state') not in {'running','uncertain','cancelled','rejected','reviewed'}:
            raise ValueError('The previous cancellation record needs review.')
        validate_target(value.get('target'))
        return value

    @property
    def active(self):
        if self.lock.acquired:
            return True
        probe=SingleInstanceLock(self.lock.path)
        if not probe.acquire():
            return True
        probe.release()
        return False

    def _write(self, job, **patch):
        job.update(patch)
        atomic_write_json(self.path, job)

    def start(self, target):
        target = validate_target(target)
        # SingleInstanceLock is re-entrant for the same object, so check its
        # local ownership too. Other windows/processes fail acquire below.
        if self.lock.acquired or not self.lock.acquire():
            raise ValueError('A cancellation is already running. Wait for its result.')
        try:
            previous = self.snapshot()
            if previous and previous['state'] in {'running','uncertain'}:
                raise ValueError('Review the previous cancellation before trying again.')
            job = dict(request_id=str(uuid4()),target=target,state='running',text='Checking your exact booking…')
            self._write(job)
            self.thread = threading.Thread(target=self._run, args=(job,), daemon=False)
            try:
                self.thread.start()
            except Exception:
                self._write(job,state='rejected',text='Cancellation could not start. No booking was changed.')
                raise
            return job
        except Exception:
            self.lock.release()
            raise

    def _run(self, job):
        try:
            result = cancel_phone_reservation(job['target'], progress=lambda text:self._write(job,text=text))
            state = 'uncertain' if result['reconciliation_required'] or not result['cancelled'] else 'cancelled'
            self._write(job,state=state,text=result['message'])
        except CancellationNotStarted as exc:
            self._write(job,state='rejected',text=str(exc))
        except Exception:
            self._write(job,state='uncertain',text='Cancellation was not confirmed. Review the latest booking before another action.')
        finally:
            self.lock.release()

    def review(self, *, surface=None):
        """Reconcile by reading; never replay cancellation during review."""
        if self.lock.acquired or not self.lock.acquire():
            raise ValueError('Cancellation is still running. Wait for verification to finish.')
        try:
            job = self.snapshot()
            if not job or job['state'] not in {'running','uncertain'}:
                return job
            surface = surface or BookerToolSurface()
            surface.dispatch('refresh_booker_data', {'scope':'agenda'})
            if list_pending(self.root / 'data' / 'mutation_receipts.json'):
                raise ValueError('The booking outcome still needs reconciliation. Check System details.')
            target = job['target']
            selection = surface.dispatch('find_reservations', {k:target[k] for k in ('date','room','start_time','end_time')})
            if not selection.get('fresh'):
                raise ValueError('A fresh booking check is required before review can finish.')
            remains = any(item.get('event_id') == target['event_id'] for item in selection.get('matches', []))
            self._write(job,state='reviewed',text='Review complete. The booking is still present.' if remains else 'Review complete. This booking is absent from the refreshed agenda.')
            return job
        finally:
            self.lock.release()
