"""Open-ended preferred times, bounded by each room's actual available intervals.

Zero and 24 below mean no user-imposed cutoff on that side, not a claim that
rooms open at midnight. Fresh room grids and exact site validation still decide
when a room is open, including exceptional closures and different room hours.
"""
import re

TOKENS = {'start': 'rooms_open', 'end': 'rooms_closed'}
LABELS = {'rooms_open': 'Rooms open', 'rooms_closed': 'Rooms closed'}


def boundary_fields(preferences):
    result = {}
    for side, token in TOKENS.items():
        key = f'{side}_boundary'
        if key in preferences:
            if preferences[key] != token:
                raise ValueError(f'{key} must be {token}')
            result[key] = token
    return result


def endpoint_minutes(value, side, *, quarter=False):
    if value == TOKENS[side]:
        return 0 if side == 'start' else 1440
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', value):
        raise ValueError(f'Choose a time in HH:MM format or {LABELS[TOKENS[side]]}')
    minute = int(value[:2]) * 60 + int(value[3:])
    if quarter and minute % 15:
        raise ValueError('Choose times in 15-minute steps')
    return minute


def saved_endpoint(preferences, side):
    boundaries = boundary_fields(preferences)
    for part, maximum in (('hour', 23), ('min', 59)):
        field = f'custom_{side}_{part}'
        if type(preferences[field]) is not int or not 0 <= preferences[field] <= maximum:
            raise ValueError(f'{field} is invalid')
    return boundaries.get(f'{side}_boundary',
                          f"{preferences[f'custom_{side}_hour']:02d}:{preferences[f'custom_{side}_min']:02d}")


def custom_bounds(preferences):
    return tuple(endpoint_minutes(saved_endpoint(preferences, side), side) / 60
                 for side in ('start', 'end'))


def set_saved_endpoint(preferences, side, value, *, quarter=False):
    minute = endpoint_minutes(value, side, quarter=quarter)
    key = f'{side}_boundary'
    if value == TOKENS[side]:
        preferences[key] = value
    else:
        preferences.pop(key, None)
        preferences[f'custom_{side}_hour'] = minute // 60
        preferences[f'custom_{side}_min'] = minute % 60


def endpoint_label(value):
    return LABELS.get(value, value)


def runtime_window_label(preferences):
    def label(side):
        if f'{side}_boundary' in preferences:
            return endpoint_label(preferences[f'{side}_boundary'])
        minute = round(preferences[f'{side}_hour'] * 60)
        return f'{minute // 60:02d}:{minute % 60:02d}'
    return f"{label('start')} – {label('end')}"
