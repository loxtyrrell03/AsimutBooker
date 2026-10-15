"""One persistent booking hold shared by desktop, phone and Tempo.

Only the settings lock is needed: Pause must remain available during a run.
The lock also serializes the hold against receipt creation and the Save click.
"""
import hashlib
import json
import uuid
from pathlib import Path

from app_settings import SETTINGS_FILE, SettingsError, load_settings, InterProcessFileLock, atomic_write_json


class PauseConflict(ValueError):
    """The reviewed pause state changed before this choice could be saved."""


class BookingPaused(SettingsError):
    """An explicit owner hold prevents booking writes."""


def _state(settings):
    value = settings.get('booking_pause', {'paused': False, 'generation': 0})
    if (not isinstance(value, dict) or type(value.get('paused')) is not bool
            or type(value.get('generation')) is not int or value['generation'] < 0):
        raise SettingsError('Booking pause could not be checked. Booking is stopped until its status is available.')
    return value


def pause_document(settings):
    value = _state(settings)
    revision = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return dict(paused=value['paused'], revision=revision,
                last_request_id=value.get('request_id'),
                message=('Bookings are paused. Existing reservations are kept.' if value['paused'] else
                         'Bookings are unpaused. Your existing automatic booking setting applies.'))


def read_pause(path=SETTINGS_FILE):
    return pause_document(load_settings(path, missing_ok=False))


def assert_pause_unchanged(expected_revision, path=SETTINGS_FILE):
    """Retire waiting mutation work after Pause, even if already unpaused."""
    current = read_pause(path)
    if current['paused']:
        raise BookingPaused('Bookings are paused. Existing reservations and settings are kept.')
    if current['revision'] != expected_revision:
        raise BookingPaused('Booking Pause changed while waiting; this run is stopped. A new run will use the current choice.')


def validate_pause(args):
    if (not isinstance(args, dict) or set(args) != {'revision', 'paused', 'request_id'}
            or type(args['paused']) is not bool or not isinstance(args['revision'], str)
            or len(args['revision']) != 64 or any(c not in '0123456789abcdef' for c in args['revision'])):
        raise ValueError('Refresh booking status, then choose Pause or Unpause.')
    try:
        if str(uuid.UUID(args['request_id'])) != args['request_id']:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('The booking pause request is invalid.') from None


def save_pause(args, path=SETTINGS_FILE):
    validate_pause(args)
    path = Path(path)
    with InterProcessFileLock(path.with_suffix(path.suffix + '.lock')):
        settings = load_settings(path, missing_ok=False)
        value = _state(settings)
        fingerprint = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
        if value.get('request_id') == args['request_id']:
            if value.get('request_hash') != fingerprint:
                raise PauseConflict('This request already has a different choice. Refresh booking status.')
            return pause_document(settings)
        if pause_document(settings)['revision'] != args['revision']:
            raise PauseConflict('Booking pause changed elsewhere. Refresh its status before choosing again.')
        # Even a Pause -> Unpause cycle retires the old run's Save permission.
        settings['booking_pause'] = dict(paused=args['paused'], generation=value['generation'] + 1,
                                        request_id=args['request_id'], request_hash=fingerprint)
        atomic_write_json(path, settings, backup=True)
        return pause_document(settings)


def assert_booking_unpaused(settings):
    if _state(settings)['paused']:
        raise BookingPaused('Bookings are paused. Select Unpause bookings before making or changing a booking.')
