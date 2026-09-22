"""Owner-edited reservations are not automatic improvement candidates."""
from app_settings import SETTINGS_FILE, SettingsError, load_settings, update_settings
from booking_blackouts import make_rebooking_blackout

KEY = 'manual_booking_overrides'
FIELDS = {'date', 'startTime', 'endTime', 'room'}


def validate_records(raw):
    if not isinstance(raw, dict):
        raise SettingsError('Invalid manual reservation tracking state')
    for key, record in raw.items():
        try:
            if str(int(key)) != key or int(key) <= 0 or set(record) != FIELDS:
                raise ValueError()
            make_rebooking_blackout(record['date'], record['startTime'], record['endTime'])
            if not isinstance(record['room'], str) or not record['room'].strip():
                raise ValueError()
        except (ValueError, TypeError, KeyError) as exc:
            raise SettingsError('Invalid manual reservation identity') from exc
    return raw


def manual_booking_ids(settings):
    return {int(key) for key in validate_records(settings.get(KEY, {}))}


def pin_in_settings(settings, event_id, record):
    pins = dict(validate_records(settings.get(KEY, {})))
    pins[str(event_id)] = dict(record)
    validate_records(pins)
    settings[KEY] = pins
    # Retire obsolete goals too, rather than merely hiding their held minutes.
    if 'extendable_bookings' in settings:
        settings['extendable_bookings'] = [entry for entry in settings['extendable_bookings']
            if not extension_is_pinned(entry, settings)]


def extension_is_pinned(entry, settings):
    pins = validate_records(settings.get(KEY, {}))
    if entry.get('eventId') is not None:
        return str(entry['eventId']) in pins
    return any(entry.get('date') == record['date'] and entry.get('room') == record['room']
               for record in pins.values())


def pin_explicit_time_edit(receipt, *, path=SETTINGS_FILE):
    record = dict(date=receipt['date'], room=receipt['room'],
                  startTime=receipt['start'], endTime=receipt['end'])
    update_settings(lambda settings: pin_in_settings(settings, receipt['original']['event_id'], record), path)


def assert_automatic_change_allowed(event_ids, *, path=SETTINGS_FILE):
    """Call inside the normal settings/Save lock, including queued transactions."""
    if set(event_ids) & manual_booking_ids(load_settings(path)):
        from booking_preferences_guard import BookingPreferencesChanged
        raise BookingPreferencesChanged('Keeping a manually edited booking unchanged')
