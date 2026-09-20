"""Admission budgets keep routine work away from competitive preparation edges."""
from datetime import datetime, timedelta

PREPARATION_SECONDS = 180


def seconds_before_next_preparation(now):
    boundary = now.replace(second=0, microsecond=0) + timedelta(minutes=15-now.minute%15)
    return max(0, (boundary-now).total_seconds()-PREPARATION_SECONDS)


def scheduled_work_fits(args, *, now=None, reserve_seconds=180, edge_work=False):
    """Never interrupt a started Save/recovery; admit only another whole action."""
    if not getattr(args, 'scheduled', False):
        return True
    now = now or datetime.now()
    target = getattr(args, 'target_time', None)
    if edge_work and target:
        hour, minute = map(int, target.split(':'))
        boundary = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if -30 <= (boundary-now).total_seconds() <= PREPARATION_SECONDS:
            return True
    return seconds_before_next_preparation(now) >= reserve_seconds
