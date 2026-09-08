"""One owned phone operation. Private diagnostics never enter the HTTP response."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app_settings import atomic_write_json
from operation_control import owned_operation, check_operation_stop
from phone_system import ROOT, RUN_ACTIONS, SystemConflict, run_local_action, timestamp, validate_action
from runtime_guard import SingleInstanceAlreadyRunning


def execute(action, args, directory, *, root=ROOT, booker_main=None):
    validate_action(action, args)
    directory = Path(directory)
    stop = directory / 'stop'
    rows, scanned = [], []

    def progress(text):
        atomic_write_json(directory / 'progress.json', {'text': text, 'observed_at': timestamp()})

    def availability(target_date, rooms):
        key = target_date.isoformat()
        if key not in args.get('dates', []):
            return
        scanned.append(key)
        for room in rooms:
            for gap in room.get('slots', []):
                start, end = round(gap['startHour'] * 60), round(gap['endHour'] * 60)
                if 0 <= start < end <= 24 * 60:
                    rows.append({'date': key, 'room': str(room['room'])[:100],
                                 'start': f'{start // 60:02d}:{start % 60:02d}',
                                 'end': f'{end // 60:02d}:{end % 60:02d}', 'minutes': end - start})
        progress(f'Read room availability for {key}.')

    progress('Starting the PC operation…')
    try:
        with owned_operation(stop, progress, availability):
            check_operation_stop()
            if action in RUN_ACTIONS:
                if booker_main is None:
                    from book_week import main as booker_main
                flags = {'run': ['--headless'], 'run_visible': [],
                         'login': ['--headless', '--login-only'],
                         'scan': ['--headless', '--check-only'],
                         'agenda': ['--headless', '--agenda-only'],
                         'plan': ['--headless', '--plan-only']}[action]
                code = booker_main(flags)
                from mutation_receipts import list_pending
                if list_pending(root / 'data/mutation_receipts.json'):
                    return {'state': 'uncertain', 'message': 'A booking outcome needs reconciliation. Refresh the agenda and review System health.'}
                if code == 0:
                    result = {'state': 'completed', 'message': {
                        'run': 'Booker run completed. Refresh the agenda to see the result.',
                        'run_visible': 'Booker run completed. Refresh the agenda to see the result.',
                        'login': 'Login check completed.', 'scan': 'Availability scan completed.',
                        'agenda': 'Agenda refreshed.', 'plan': 'Practice plan refreshed.'}[action]}
                    if action == 'scan':
                        result['scan'] = {'observed_at': timestamp(), 'rows': rows[:12000],
                                          'scanned_dates': scanned,
                                          'unavailable_dates': sorted(set(args['dates']) - set(scanned))}
                    return result
                if code == 6:
                    return {'state': 'rejected', 'message': 'Another Booker process is running. No second run was started.'}
                if stop.exists():
                    return {'state': 'stopped', 'message': 'Stopped. Any bookings already verified remain in the agenda.'}
                return {'state': 'failed', 'message': 'The PC operation did not complete. Check System health and the latest agenda before starting another run.'}
            result = run_local_action(action, args, root)
            return {'state': 'completed', **result}
    except SingleInstanceAlreadyRunning:
        return {'state': 'rejected', 'message': 'Another Booker process is running. Wait for it to finish.'}
    except SystemConflict as exc:
        return {'state': 'rejected', 'message': str(exc)}
    except Exception:
        # A write may already have reached disk; never infer that an exception rolled it back.
        return {'state': 'uncertain', 'message': 'The operation could not be confirmed. Reload its page and review the result before trying again.'}


def main():
    from uuid import UUID
    payload = json.loads(sys.stdin.read(24000))
    request_id = str(UUID(payload['request_id']))
    # The parent chooses only a UUID below one fixed operations root.
    directory = ROOT / 'data/phone_operations' / request_id
    result = execute(payload['action'], payload['args'], directory)
    atomic_write_json(directory / 'result.json', result)


if __name__ == '__main__':
    main()
