"""Desktop subprocess entry point with checked, request-bound terminal outcomes."""
import json
from pathlib import Path
import sys
from uuid import UUID

from app_settings import atomic_write_json
from mutation_receipts import list_pending
from operation_control import OperationStopped, check_operation_stop, owned_operation

ROOT = Path(__file__).resolve().parent


def _unsettled(root):
    try:
        return bool(list_pending(Path(root) / 'data/mutation_receipts.json'))
    except Exception:
        # Unreadable evidence is not an empty journal.
        return True


def _uncertain(message='A booking outcome needs checking. Review the agenda and System health before another run.'):
    return dict(state='uncertain', message=message)


def read_result(request_id, exit_code, *, root=ROOT):
    """Read only after process exit; a marker or exit zero cannot prove outcome."""
    request_id = str(UUID(request_id))
    path = Path(root) / 'data/desktop_operations' / request_id / 'result.json'
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(result, dict) or result.get('request_id') != request_id
                or type(result.get('protocol')) is not int or result['protocol'] != 1
                or type(result.get('exit_code')) is not int
                or result['exit_code'] != exit_code
                or result.get('state') not in {'completed', 'stopped', 'failed', 'uncertain'}
                or not isinstance(result.get('message'), str) or not result['message'].strip()):
            raise ValueError('Unmatched desktop outcome')
    except (OSError, ValueError, TypeError, UnicodeError):
        return _uncertain('The check outcome could not be confirmed. Review the agenda and System health before another run.')
    # A newer unresolved Save must not be hidden by an older terminal result.
    return _uncertain() if _unsettled(root) else result


def execute(request_id, flags, *, root=ROOT, booker_main=None):
    request_id = str(UUID(request_id))
    directory = Path(root) / 'data' / 'desktop_operations' / request_id
    directory.mkdir(parents=True, exist_ok=True)
    if booker_main is None:
        from book_week import main as booker_main
    def progress(text):
        print('ASIMUT_STAGE: ' + text, flush=True)
    with owned_operation(directory / 'stop', progress, lambda *_: None) as outcome:
        try:
            check_operation_stop()
            code = booker_main(flags)
        except OperationStopped:
            code = 130
            outcome['stop_reason'] = 'operation_stop'
    if _unsettled(root):
        result = _uncertain()
    elif code == 3 and outcome.get('stop_reason') == 'booking_pause':
        result = dict(state='stopped', reason='booking_pause',
                      message='Pause stopped this booking check. Any bookings already verified remain in the agenda.')
    elif code in {3, 130} and outcome.get('stop_reason') == 'operation_stop':
        result = dict(state='stopped', reason='operation_stop',
                      message='Stopped. Any bookings already verified remain in the agenda.')
    elif code == 0:
        result = dict(state='completed', message='Check finished. See the agenda for confirmed bookings.')
    else:
        result = dict(state='failed', message=f'The check did not complete (exit code {code}). Review the activity and System health.')
    atomic_write_json(directory / 'result.json', dict(result, protocol=1, request_id=request_id, exit_code=code))
    progress(result['message'])
    return code


if __name__ == '__main__':
    from runtime_guard import configure_utf8_stdio
    configure_utf8_stdio()
    sys.exit(execute(sys.argv[1], sys.argv[2:]))
