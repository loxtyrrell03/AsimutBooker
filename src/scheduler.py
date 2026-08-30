"""Retired scheduler surface from the former alternate runtime.

Scheduling is owned by the verified ``AsimutBooker_Recurring`` Windows task.
Keeping the old in-process scheduler callable would create a second launch path
outside that task contract, so direct importers receive only this fail-closed
compatibility name.
"""


class BookingScheduler:
    """Fail closed for direct callers of the retired in-process scheduler."""

    _ERROR = (
        "src.scheduler is retired; install or repair AsimutBooker_Recurring "
        "through the GUI or setup_scheduled_tasks.ps1"
    )

    def __init__(self, *_args, **_kwargs):
        raise RuntimeError(self._ERROR)

    def start(self, *_args, **_kwargs):
        """Refuse execution even if construction was bypassed by old code."""

        raise RuntimeError(self._ERROR)

    def stop(self, *_args, **_kwargs):
        """Refuse use of the retired scheduler lifecycle surface."""

        raise RuntimeError(self._ERROR)
