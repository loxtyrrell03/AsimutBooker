"""Shared exact-date preferred-time overrides for both calendars and booking."""
from datetime import date, datetime
import re

KEY = 'date_time_preferences'
FIELDS = {'enabled', 'start_time', 'end_time', 'strict_mode'}


def date_key(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('Choose a date in YYYY-MM-DD format')
    return value


def validate_window(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError('A date time preference needs enabled, start_time, end_time and strict_mode')
    for key in ('enabled', 'strict_mode'):
        if type(value[key]) is not bool:
            raise ValueError(f'{key} must be true or false')
    for key in ('start_time', 'end_time'):
        clock = value[key]
        if not isinstance(clock, str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', clock):
            raise ValueError('Use a time in HH:MM format')
        if int(clock[3:]) % 15:
            raise ValueError('Choose times in 15-minute steps')
    if value['end_time'] <= value['start_time']:
        raise ValueError('End time must be later than start time')
    return dict(value)


def load_date_time_preferences(settings):
    raw = settings.get(KEY, {})
    if not isinstance(raw, dict) or len(raw) > 3660:
        raise ValueError('Date time preferences must contain at most 3660 dated entries')
    return {date_key(key): validate_window(value) for key, value in raw.items()}


def apply_date_time_preferences(settings, patch):
    if not isinstance(patch, dict) or len(patch) > 3660:
        raise ValueError('Supply a map of dates and preferred times')
    values = load_date_time_preferences(settings)
    for key, value in patch.items():
        key = date_key(key)
        if value is None:
            values.pop(key, None)
        else:
            values[key] = validate_window(value)
    validated = load_date_time_preferences({KEY: values})
    settings[KEY] = validated
    return validated


def with_date_overrides(default, settings):
    overrides = load_date_time_preferences(settings)
    return {**default, 'date_overrides': overrides, '_default_preferences': dict(default)} if overrides else default


def resolve_time_preferences(preferences, target_date):
    """Re-resolving across dates always starts from the original global default."""
    if not preferences or not preferences.get('date_overrides'):
        return preferences
    base = preferences['_default_preferences']
    override = preferences['date_overrides'].get(date_key(target_date))
    effective = base
    if override is not None:
        def hour(clock):
            return int(clock[:2]) + int(clock[3:]) / 60
        effective = {'enabled': override['enabled'], 'strict_mode': override['strict_mode'],
                     'start_hour': hour(override['start_time']), 'end_hour': hour(override['end_time'])}
    return {**effective, 'date_overrides': preferences['date_overrides'], '_default_preferences': base}
