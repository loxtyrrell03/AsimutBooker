"""One owned phone operation. Private diagnostics never enter the HTTP response."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app_settings import atomic_write_json, SettingsError
from operation_control import owned_operation, check_operation_stop, observe_room_grid
from phone_system import ROOT, RUN_ACTIONS, SystemConflict, run_local_action, timestamp, validate_action
from runtime_guard import SingleInstanceAlreadyRunning


def execute(action, args, directory, *, root=ROOT, booker_main=None):
    if action not in {'room_now_review', 'fill_range_review'}:
        validate_action(action, args)
    directory = Path(directory)
    stop = directory / 'stop'
    rows, scanned = [], []
    progress_warning = False

    def progress(text):
        nonlocal progress_warning
        try:
            atomic_write_json(directory / 'progress.json', {'text': text, 'observed_at': timestamp()})
        except (SettingsError, OSError) as exc:
            # Windows readers can briefly prevent replacement of this display
            # file. A missed progress update must never abort the booking run.
            # Keep one bounded private diagnostic; authoritative writes below
            # (results, settings and mutation receipts) remain strict.
            if not progress_warning:
                cause = exc.__cause__ or exc
                warning = dict(message='A live progress update could not be saved.',
                               observed_at=timestamp(), file='progress.json',
                               error_type=type(cause).__name__, errno=getattr(cause, 'errno', None),
                               winerror=getattr(cause, 'winerror', None))
                try:
                    atomic_write_json(directory / 'progress-warning.json', warning)
                except (SettingsError, OSError):
                    pass
            progress_warning = True

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

    def grid(target_date, snapshot, eligible, settings, page):
        from room_grid import publish_day, room_revision, enrich_grid
        if page is not None:
            snapshot = enrich_grid(page, target_date, snapshot, eligible)
        publish_day(target_date.isoformat(), snapshot, eligible, room_revision(settings), root=root)

    progress('Starting the PC operation…')
    try:
        if action in {'room_now_review', 'fill_range_review'}:
            from room_now import review_result
            if booker_main is None:
                from book_week import main as booker_main
            if action == 'fill_range_review':
                # Reconcile and pin verified results under one assistant/runtime owner.
                output = directory / 'fill-range.json'
                if booker_main(['--headless', '--fill-review-output', str(output)]) != 0:
                    return {'state': 'uncertain', 'message': 'The fill result could not be checked. Try Check booking status again.'}
                return json.loads(output.read_text(encoding='utf-8'))
            if booker_main(['--headless', '--agenda-only']) != 0:
                return {'state': 'uncertain', 'message': 'The booking could not be checked. Try Check booking status again.'}
            return review_result(directory, root)
        with owned_operation(stop, progress, availability), observe_room_grid(grid):
            check_operation_stop()
            if action in RUN_ACTIONS:
                if booker_main is None:
                    from book_week import main as booker_main
                flags = {'run': ['--headless'], 'run_visible': [],
                         'login': ['--headless', '--login-only'],
                         'scan': ['--headless', '--check-only', '--check-dates', *args.get('dates', [])],
                         'agenda': ['--headless', '--agenda-only'],
                         'plan': ['--headless', '--plan-only']}.get(action)
                if action == 'room_now':
                    from room_now import cli_flags, RoomNowRequest, local_now
                    from datetime import datetime
                    submitted = json.loads((directory / 'submitted.json').read_text(encoding='utf-8'))
                    try:
                        RoomNowRequest(args['mode'], args['minutes'], datetime.fromisoformat(submitted['requested_at'])).check_current(local_now())
                    except SettingsError as exc:
                        return {'state': 'blocked', 'message': str(exc)}
                    flags = cli_flags(args, submitted['requested_at'], directory / 'room-now.json')
                if action == 'fill_range':
                    from fill_range import cli_flags, FillRequest, local_now
                    submitted = json.loads((directory / 'submitted.json').read_text(encoding='utf-8'))
                    try:
                        FillRequest.create(args, submitted['requested_at']).check_current(local_now())
                    except SettingsError as exc:
                        return {'state': 'blocked', 'message': str(exc)}
                    flags = cli_flags(args, submitted['requested_at'], directory / 'fill-range.json')
                code = booker_main(flags)
                from mutation_receipts import list_pending
                if list_pending(root / 'data/mutation_receipts.json'):
                    if action == 'fill_range' and (directory / 'fill-range.json').exists():
                        result = json.loads((directory / 'fill-range.json').read_text(encoding='utf-8'))
                        return {**result, 'state': 'uncertain', 'message': 'The last booking needs checking before another fill.'}
                    return {'state': 'uncertain', 'message': 'A booking outcome needs reconciliation. Refresh the agenda and review System health.'}
                if action == 'fill_range' and (directory / 'fill-range.json').exists():
                    result = json.loads((directory / 'fill-range.json').read_text(encoding='utf-8'))
                    if result['state'] == 'running':
                        result.update(state='partial', message='The fill stopped. Review the remaining gaps before trying again.')
                    return result
                if action == 'fill_range':
                    return {'state': 'rejected' if code == 6 else 'blocked',
                            'message': 'The fill did not start. Wait for other work to finish and check System health.'}
                if action == 'room_now' and (directory / 'room-now.json').exists():
                    return json.loads((directory / 'room-now.json').read_text(encoding='utf-8'))
                if action == 'room_now' and code == 0:
                    return {'state': 'blocked', 'message': 'Asimut refused this booking. No room was confirmed; review the latest booking activity.'}
                if code == 0:
                    result = {'state': 'completed', 'message': {
                        'run': 'Booker run completed. Refresh the agenda to see the result.',
                        'run_visible': 'Booker run completed. Refresh the agenda to see the result.',
                        'login': 'Login check completed.', 'scan': 'Availability scan completed.',
                        'agenda': 'Agenda refreshed.', 'plan': 'Practice plan refreshed.'}[action]}
                    if progress_warning:
                        result['progress_warning'] = 'Some live progress updates were unavailable; the operation result was still checked.'
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
    surface = payload.get('surface', 'phone')
    if surface not in {'phone', 'desktop'}:
        raise ValueError('Unsupported operation surface')
    directory = ROOT / 'data' / ('desktop_operations' if surface == 'desktop' else 'phone_operations') / request_id
    result = execute(payload['action'], payload['args'], directory)
    atomic_write_json(directory / 'result.json', result)


if __name__ == '__main__':
    main()
