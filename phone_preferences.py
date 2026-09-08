"""Explicit phone preference edits, sharing the desktop/assistant validators."""
from copy import deepcopy
from hashlib import sha256
import json

from app_settings import SETTINGS_FILE, load_settings, update_settings
from assistant_tools import BookerToolSurface, AssistantToolError
from room_preferences import load_room_preferences, apply_room_preferences_update
from booking_strategy import load_booking_strategy, apply_booking_strategy_update
from date_time_preferences import load_date_time_preferences, apply_date_time_preferences


class PreferenceConflict(ValueError):
    pass


def preference_document(settings):
    # Validators normalize copies; reading must never modify saved settings.
    copy = deepcopy(settings)
    days = copy.get('disabled_dates', [])
    if not isinstance(days, list):
        raise ValueError('Saved practice dates are invalid')
    for day in days:
        BookerToolSurface._apply_booking_days(copy, [{'date': day, 'enabled': False}])
    values = {
        'practice_plan': BookerToolSurface._apply_practice_plan(copy, {}),
        'time_preferences': BookerToolSurface._apply_time_preferences(copy, {}),
        'disabled_dates': sorted(days),
        'room_preferences': load_room_preferences(copy).to_dict(),
        'booking_strategy': load_booking_strategy(copy).to_dict(),
        'date_time_preferences': load_date_time_preferences(copy),
    }
    revision = sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    return {'revision': revision, **values}


def read_phone_preferences(path=SETTINGS_FILE):
    return preference_document(load_settings(path))


def save_phone_preferences(payload, path=SETTINGS_FILE):
    if not isinstance(payload, dict) or set(payload) != {'revision', 'changes'}:
        raise ValueError('Supply the settings revision and changes')
    changes = payload['changes']
    if not isinstance(changes, dict) or not changes or set(changes) - {
        'practice_plan', 'time_preferences', 'booking_days', 'room_preferences',
        'booking_strategy', 'date_time_preferences'
    }:
        raise ValueError('Unsupported preference changes')

    def mutate(settings):
        if payload['revision'] != preference_document(settings)['revision']:
            raise PreferenceConflict('Settings changed elsewhere. Reload settings before saving.')
        for name, patch in changes.items():
            if name == 'room_preferences':
                apply_room_preferences_update(settings, patch)
            elif name == 'booking_strategy':
                apply_booking_strategy_update(settings, patch)
            elif name == 'date_time_preferences':
                apply_date_time_preferences(settings, patch)
            else:
                getattr(BookerToolSurface, '_apply_' + name)(settings, patch)
        return preference_document(settings)

    return update_settings(mutate, path)
