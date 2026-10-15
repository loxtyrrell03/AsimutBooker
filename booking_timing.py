"""Elapsed clock arithmetic and admission budgets for booking preparation."""
from datetime import datetime, timedelta, timezone
from room_catalog import SITE_TIMEZONE

PREPARATION_SECONDS = 180


def as_utc(value):
    """Planner clocks are London-local; retain fold when resolving an instant."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=SITE_TIMEZONE)
    return value.astimezone(timezone.utc)


def seconds_between(later, earlier):
    return (as_utc(later) - as_utc(earlier)).total_seconds()


def shift_clock(value, *, minutes=0, seconds=0):
    """Move by elapsed time, preserving the caller's aware/naive clock shape."""
    shifted = as_utc(value) + timedelta(minutes=minutes, seconds=seconds)
    local = shifted.astimezone(value.tzinfo or SITE_TIMEZONE)
    return local if value.tzinfo is not None else local.replace(tzinfo=None)


def local_boundary(day, hour, minute, *, now):
    """Resolve an HH:MM target to the occurrence nearest the current pass."""
    wall = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
    choices = []
    for fold in (0, 1):
        candidate = wall.replace(fold=fold)
        if as_utc(candidate).astimezone(SITE_TIMEZONE).replace(tzinfo=None) == wall:
            choices.append(candidate)
    if not choices:
        raise ValueError('The target clock does not exist in Europe/London')
    return min(choices, key=lambda value: (abs(seconds_between(value, now)),
                                          -seconds_between(value, now)))


def seconds_before_next_preparation(now):
    boundary = shift_clock(now.replace(second=0, microsecond=0), minutes=15-now.minute%15)
    return max(0, seconds_between(boundary, now)-PREPARATION_SECONDS)


def scheduled_work_fits(args, *, now=None, reserve_seconds=180, edge_work=False):
    """Never interrupt a started Save/recovery; admit only another whole action."""
    if not getattr(args, 'scheduled', False):
        return True
    now = now or datetime.now()
    target = getattr(args, 'target_time', None)
    if edge_work and target:
        hour, minute = map(int, target.split(':'))
        boundary = local_boundary(now.date(), hour, minute, now=now)
        if -30 <= seconds_between(boundary, now) <= PREPARATION_SECONDS:
            return True
    return seconds_before_next_preparation(now) >= reserve_seconds
