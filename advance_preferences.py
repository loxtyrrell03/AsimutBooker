"""User-controlled advance-credit policy; site and general hard limits still win."""
from dataclasses import asdict, dataclass
from collections.abc import Mapping, MutableMapping

from booking_strategy import BookingStrategyError, parse_clock_minutes
from daily_planner import interval_overlap_minutes, soft_time_value

KEY = 'advance_quota'


@dataclass(frozen=True)
class AdvancePeriod:
    days: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)
    start: str = '12:00'
    end: str = '16:00'


@dataclass(frozen=True)
class AdvanceQuotaPreferences:
    room_mode: str = 'top'
    top_room_count: int = 2
    room_order: tuple[str, ...] = ()
    room_fallback: bool = False
    periods: tuple[AdvancePeriod, ...] = ()
    period_mode: str = 'prefer'
    distribution: str = 'balanced'
    day_weights: tuple[int, ...] = (1, 1, 1, 1, 1, 1, 1)
    day_caps_minutes: tuple[int, ...] = (0, 0, 0, 0, 0, 0, 0)
    anchor_minutes: int = 60
    block_minutes: int = 0
    priority_mode: str = 'inherit'
    date_order: str = 'inherit'
    reserve_minutes: int = 0
    wait_for_opening: bool = True
    fallback_lead_minutes: int | None = None

    def to_dict(self):
        values = asdict(self)
        for key in ('room_order', 'day_weights', 'day_caps_minutes'):
            values[key] = list(values[key])
        values['periods'] = [dict(days=list(p.days), start=p.start, end=p.end) for p in self.periods]
        return values


def _integer(value, name, maximum, *, minimum=0, step=1):
    if type(value) is not int or not minimum <= value <= maximum or value % step:
        raise BookingStrategyError(f'{name} must be {minimum}-{maximum} in steps of {step}')
    return value


def load_advance_quota(settings):
    raw = settings.get(KEY, {})
    if not isinstance(raw, Mapping):
        raise BookingStrategyError('Advance quota settings must be an object')
    values = AdvanceQuotaPreferences().to_dict()
    if set(raw) - set(values):
        raise BookingStrategyError('Unsupported advance quota setting')
    values.update(raw)
    enums = dict(room_mode=('top', 'selected', 'all'), period_mode=('prefer', 'only'),
        distribution=('balanced', 'weighted', 'concentrated', 'quality'),
        priority_mode=('inherit', 'time_first', 'room_first'), date_order=('inherit', 'nearest', 'furthest'))
    for key, choices in enums.items():
        if not isinstance(values[key], str) or values[key] not in choices:
            raise BookingStrategyError(f'{key} must be one of: {", ".join(choices)}')
    for key in ('room_fallback', 'wait_for_opening'):
        if type(values[key]) is not bool:
            raise BookingStrategyError(f'{key} must be true or false')
    for key, maximum, minimum, step in (
        ('top_room_count', 100, 1, 1), ('anchor_minutes', 120, 30, 15),
        ('block_minutes', 120, 0, 30), ('reserve_minutes', 10080, 0, 15)):
        _integer(values[key], key, maximum, minimum=minimum, step=step)
    if values['fallback_lead_minutes'] is not None:
        _integer(values['fallback_lead_minutes'], 'fallback_lead_minutes', 1440, step=15)
    for key, maximum, step in (('day_weights', 10, 1), ('day_caps_minutes', 720, 15)):
        array = values[key]
        if not isinstance(array, list) or len(array) != 7:
            raise BookingStrategyError(f'{key} must contain seven values, Monday to Sunday')
        values[key] = tuple(_integer(v, key, maximum, step=step) for v in array)
    rooms = values['room_order']
    if (not isinstance(rooms, list) or len(rooms) > 100
            or any(not isinstance(r, str) or not r.strip() or r != r.strip() or len(r) > 160 for r in rooms)
            or len(set(rooms)) != len(rooms)):
        raise BookingStrategyError('Choose distinct room names in priority order')
    if values['room_mode'] == 'selected' and not rooms:
        raise BookingStrategyError('Select at least one room for advance bookings')
    values['room_order'] = tuple(rooms)
    periods = values['periods']
    if not isinstance(periods, list) or len(periods) > 14:
        raise BookingStrategyError('Use at most fourteen ranked time periods')
    parsed = []
    for period in periods:
        if not isinstance(period, dict) or set(period) != {'days', 'start', 'end'}:
            raise BookingStrategyError('Each period needs weekdays, start and end')
        days = period['days']
        if not isinstance(days, list) or not days or any(type(d) is not int or d not in range(7) for d in days) or len(set(days)) != len(days):
            raise BookingStrategyError('Choose distinct weekdays for each period')
        start, end = (parse_clock_minutes(period[k], k) for k in ('start', 'end'))
        if end-start < 30:
            raise BookingStrategyError('Each period must last at least 30 minutes within one day')
        if any(set(days) & set(old.days) and start < parse_clock_minutes(old.end) and end > parse_clock_minutes(old.start) for old in parsed):
            raise BookingStrategyError('Periods on the same weekday must not overlap')
        parsed.append(AdvancePeriod(tuple(sorted(days)), period['start'], period['end']))
    values['periods'] = tuple(parsed)
    return AdvanceQuotaPreferences(**values)


def apply_advance_quota(settings, patch):
    if not isinstance(settings, MutableMapping) or not isinstance(patch, Mapping):
        raise BookingStrategyError('Advance quota changes must be an object')
    merged = {**load_advance_quota(settings).to_dict(), **patch}
    result = load_advance_quota({KEY: merged})
    settings[KEY] = result.to_dict()
    return result


def preference_schema():
    """Assistant's scoped patch schema; runtime validation remains authoritative."""
    defaults = AdvanceQuotaPreferences().to_dict()
    properties = {}
    enums = dict(room_mode=['top', 'selected', 'all'], period_mode=['prefer', 'only'],
        distribution=['balanced', 'weighted', 'concentrated', 'quality'],
        priority_mode=['inherit', 'time_first', 'room_first'], date_order=['inherit', 'nearest', 'furthest'])
    for key, choices in enums.items(): properties[key] = {'type':'string', 'enum':choices}
    for key in ('room_fallback', 'wait_for_opening'): properties[key] = {'type':'boolean'}
    for key, maximum, minimum, step in (('top_room_count',100,1,1), ('anchor_minutes',120,30,15), ('block_minutes',120,0,30), ('reserve_minutes',10080,0,15)):
        properties[key] = dict(type='integer', minimum=minimum, maximum=maximum, multipleOf=step)
    properties['fallback_lead_minutes'] = dict(type=['integer','null'], minimum=0, maximum=1440, multipleOf=15)
    properties['room_order'] = dict(type='array', maxItems=100, uniqueItems=True, items=dict(type='string'))
    for key, maximum, step in (('day_weights',10,1), ('day_caps_minutes',720,15)):
        properties[key] = dict(type='array', minItems=7, maxItems=7, items=dict(type='integer', minimum=0, maximum=maximum, multipleOf=step))
    properties['periods'] = dict(type='array', maxItems=14, items=dict(type='object', additionalProperties=False,
        required=['days','start','end'], properties=dict(
            days=dict(type='array', minItems=1, maxItems=7, uniqueItems=True, items=dict(type='integer', minimum=0, maximum=6)),
            start=dict(type='string'), end=dict(type='string'))))
    assert set(properties) == set(defaults)
    return dict(type='object', additionalProperties=False, properties=properties,
        description='Advance-credit allocation only. Weekdays are Monday=0 to Sunday=6. Weight 0 disables advance on that weekday; other weights apply in weighted mode. Cap 0 uses the daily target. Ranked periods cannot overlap. Empty periods use Preferred times. Existing bookings and general hard limits are preserved.')


def rooms_for(policy, eligible, *, fallback=None):
    eligible = tuple(eligible)
    if policy.room_mode == 'all':
        return eligible
    chosen = eligible[:policy.top_room_count] if policy.room_mode == 'top' else tuple(r for r in policy.room_order if r in eligible)
    if (policy.room_fallback if fallback is None else fallback):
        return (*chosen, *(r for r in eligible if r not in chosen))
    return chosen


def windows_for(policy, day):
    return tuple((parse_clock_minutes(p.start), parse_clock_minutes(p.end))
                 for p in policy.periods if day.weekday() in p.days)


def interval_allowed(policy, day, start, end):
    windows = windows_for(policy, day)
    return not policy.periods or policy.period_mode != 'only' or any(a <= start < end <= b for a, b in windows)


def session_quality(item, policy, planning):
    """Additive room/period quality used after coverage in a day menu."""
    windows = windows_for(policy, item.target_date)
    if windows:
        times = tuple(interval_overlap_minutes(item.start_minutes, item.end_minutes, a, b) for a, b in windows)
    else:
        times = (round(soft_time_value(item.start_minutes, item.potential_minutes, item.soft_preferred_window), 8),)
    rooms = (-item.room_priority*item.potential_minutes,)
    mode = planning.priority_mode if policy.priority_mode == 'inherit' else policy.priority_mode
    return (*rooms, *times) if mode == 'room_first' else (*times, *rooms)


def week_quality(sessions, policy, planning):
    # Global period positions retain their meaning when weekdays have different
    # period counts. Days without custom periods use general time suitability.
    times = [0] * (len(policy.periods)+1)
    rooms = 0
    for item in sessions:
        rooms -= item.room_priority*item.potential_minutes
        if windows_for(policy, item.target_date):
            for index, period in enumerate(policy.periods):
                if item.target_date.weekday() in period.days:
                    times[index] += interval_overlap_minutes(item.start_minutes, item.end_minutes,
                        parse_clock_minutes(period.start), parse_clock_minutes(period.end))
        else:
            times[-1] += soft_time_value(item.start_minutes, item.potential_minutes, item.soft_preferred_window)
    mode = planning.priority_mode if policy.priority_mode == 'inherit' else policy.priority_mode
    return (rooms, *times) if mode == 'room_first' else (*times, rooms)
