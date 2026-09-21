"""Run-local explanations. Observations never authorize or change a booking."""
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime


_current = ContextVar('booking_run_report', default=None)


@dataclass
class RunReport:
    engine: object
    settings: dict
    practice: object
    tracker: object = None
    quota_tracker: object = None
    notes: dict = field(default_factory=dict)
    choices: dict = field(default_factory=dict)


def start(engine, settings, practice):
    return _current.set(RunReport(engine, settings, practice))


def finish(token):
    _current.reset(token)


def preferences(settings, practice):
    report = _current.get()
    if report is not None:
        report.settings, report.practice = settings, practice


def note(key, text):
    report = _current.get()
    if report is not None:
        # A new observation replaces its own old state, not other decisions.
        report.notes[key] = str(text)


def observe(tracker):
    report = _current.get()
    if report is not None:
        report.tracker = tracker
        if tracker.live_quota_minutes is not None:
            report.quota_tracker = tracker


def clock(minutes):
    return f'{int(minutes)//60:02d}:{int(minutes)%60:02d}'


def duration(minutes):
    hours, minutes = divmod(max(0, round(minutes)), 60)
    return (f'{hours}h {minutes}m' if hours and minutes else
            f'{hours}h' if hours else f'{minutes}m')


def priority_text(planning):
    return ('room order before preferred times' if planning.priority_mode == 'room_first'
            else 'preferred times before room order')


def choice(day, room, start_minutes, target_minutes, reason):
    report = _current.get()
    if report is not None:
        report.choices[(str(day), room, clock(start_minutes))] = (clock(target_minutes), reason)


def confirmation(receipt):
    """Only called after persisted receipt verification; match actual identity."""
    report = _current.get()
    selected = report.choices.get((receipt['date'], receipt['room'], receipt['start'])) if report else None
    if selected:
        target, reason = selected
        result = f'Why: {reason}'
        if target > receipt['end']:
            result += f'\nAim: extend to {target}; that extra time is not booked yet.'
        return result
    kind = receipt.get('kind')
    if kind == 'extension':
        return 'Why: lengthen an existing tracked session within the booking limits.'
    if kind in {'upgrade', 'consolidation', 'transfer'}:
        return 'Why: improve the room while retaining the verified practice time.'
    if kind == 'time_edit':
        return 'Why: apply your requested time change.'
    return 'The room and exact interval were confirmed in ASIMUT.'


def day_plan(day, plan, opportunities, planning, *, now):
    """Explain the actual selected portfolio, including unconfirmed future seeds."""
    if _current.get() is None:
        return
    primary = plan.primary
    if primary is None:
        note(f'free:{day}', f'{day}: {plan.reason}.')
        return
    opening = primary.unlock_at
    current = now.astimezone() if opening.tzinfo is not None and now.tzinfo is None else now
    text = (f'Watching {day}: {primary.room} {primary.start_time}-{primary.end_time} '
            f'(planned, not booked). {plan.reason}.')
    if opening > current:
        text += (f' First {primary.initial_minutes}m eligible {opening:%a %d %b %H:%M}; '
                 'the whole booking must fit the room/free window.')
    else:
        text += ' Eligible now, subject to a fresh ASIMUT check.'
    text += f' Selection: {priority_text(planning)}.'
    chosen = next((o for o in opportunities if o.room == primary.room and o.start_text == primary.start_time), None)
    if chosen is not None and chosen.room_priority > 0:
        higher = []
        seen = set()
        for other in sorted((o for o in opportunities if o.room_priority < chosen.room_priority),
                            key=lambda o: (o.room_priority, o.unlock_at, o.start_text)):
            if other.room not in seen:
                seen.add(other.room)
                higher.append(f'{other.room} {other.start_text}-{other.end_text} '
                              f'(first {other.initial_minutes}m from {other.unlock_at:%H:%M})')
        text += (' Higher-ranked alternatives in this scan: ' + '; '.join(higher[:2]) + '.'
                 if higher else ' No eligible higher-ranked option in this scan.')
    note(f'free:{day}', text)


def lines(*, now=None):
    report = _current.get()
    if report is None:
        return []
    result = []
    tracker, engine = report.tracker, report.engine
    now = now or datetime.now()
    if tracker is not None:
        day = now.date()
        booked = round(tracker.get_hours_for_day(day) * 60)
        target = report.practice.target_for(day) if report.practice is not None else None
        if target is not None:
            target = round(target * 60)
            result.append(f'Today: {duration(booked)} confirmed / {duration(target)} target; '
                          f'{duration(max(0, target-booked))} still needed.')
        quota_tracker = report.quota_tracker or tracker
        remaining = round(quota_tracker.get_remaining_quota_hours() * 60)
        if quota_tracker.live_quota_minutes is not None:
            result.append(f'Usable advance credit: {duration(remaining)} '
                          '(last live ASIMUT check with your rolling limit applied; no weekly reset).')
        else:
            result.append(f'Estimated advance credit: {duration(remaining)}; live balance not verified in this phase.')
        if day.weekday() < 5:
            used = round(tracker.get_peak_used_for_day(day))
            limit = round(engine.MAX_PEAK_HOURS * 60)
            result.append(f'Today\'s peak use: {duration(used)} / {duration(limit)} limit.' +
                          (' No further peak time, including last-minute bookings.' if used >= limit else ''))
    result.extend(report.notes.values())
    return result


def bounded(text, limit=3700):
    """Bound UTF-8 payloads; history retains the complete explanation."""
    raw = text.encode('utf-8')
    if len(raw) <= limit:
        return text
    suffix = '\nFull explanation saved in the local booking-history file.'
    return raw[:limit-len(suffix.encode('utf-8'))].decode('utf-8', errors='ignore') + suffix


def message(lead, details=()):
    return bounded('\n'.join([lead, *details, *lines()]))
