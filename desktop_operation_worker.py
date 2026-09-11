"""Desktop subprocess entry point with the shared cooperative Stop contract."""
from pathlib import Path
import sys
from uuid import UUID

from operation_control import OperationStopped, check_operation_stop, owned_operation

ROOT = Path(__file__).resolve().parent


def execute(request_id, flags, *, root=ROOT, booker_main=None):
    directory = root / 'data' / 'desktop_operations' / str(UUID(request_id))
    directory.mkdir(parents=True, exist_ok=True)
    if booker_main is None:
        from book_week import main as booker_main
    def progress(text):
        print('ASIMUT_STAGE: ' + text, flush=True)
    try:
        with owned_operation(directory / 'stop', progress, lambda *_: None):
            check_operation_stop()
            return booker_main(flags)
    except OperationStopped:
        progress('Stopped. Any bookings already verified remain in the agenda.')
        return 130


if __name__ == '__main__':
    from runtime_guard import configure_utf8_stdio
    configure_utf8_stdio()
    sys.exit(execute(sys.argv[1], sys.argv[2:]))
