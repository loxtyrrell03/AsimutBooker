"""Remember external cancellations and edits from complete authenticated scans."""

from datetime import datetime, timezone

from app_settings import SettingsError, update_settings
from booking_blackouts import load_rebooking_blackouts, make_rebooking_blackout, merge_rebooking_blackouts
from mutation_receipts import load_journal
from runtime_guard import parse_confirmed_event_id
from manual_booking_overrides import KEY, validate_records, pin_in_settings

OBSERVED_KEY = "observed_reservations"
TIMES_KEY = 'observed_reservation_times'


def released_windows(old, new):
    """Old occupied time minus new occupied time, independent of room."""
    original = make_rebooking_blackout(old['date'], old['startTime'], old['endTime'])
    if old['date'] != new['date']:
        return [original]
    current = make_rebooking_blackout(new['date'], new['startTime'], new['endTime'])
    pieces = [(original.start_minutes, min(original.end_minutes, current.start_minutes)),
              (max(original.start_minutes, current.end_minutes), original.end_minutes)]
    clock = lambda value: f'{value // 60:02d}:{value % 60:02d}'
    return [make_rebooking_blackout(old['date'], clock(a), clock(b)) for a, b in pieces if a < b]


def receipt_record(record):
    return dict(date=record['date'], room=record['room'], startTime=record['start'], endTime=record['end'])


def owned_transition(event_id, old, new, receipts, observed_at):
    """Only recent, exact verified outcomes explain a changed remote booking."""
    states = [old]
    for receipt in sorted(receipts, key=lambda r: r.get('verified_at', '')):
        if receipt['status'] != 'verified':
            continue
        if observed_at:
            verified = datetime.fromisoformat((receipt.get('verified_at') or receipt['updated_at']).replace('Z', '+00:00'))
            # Receipts use whole seconds, while scans retain microseconds.
            if verified < datetime.fromisoformat(observed_at).replace(microsecond=0):
                continue
        pairs = []
        original = receipt.get('original', {})
        if original.get('event_id') == event_id:
            pairs.append((receipt_record(original), receipt_record(receipt)))
        if receipt['kind'] == 'extension' and parse_confirmed_event_id(receipt.get('event_url', '')) == event_id:
            destination = receipt_record(receipt)
            for state in list(states):
                if all(state[k] == destination[k] for k in ('date', 'room', 'startTime')) and state['endTime'] <= destination['endTime']:
                    pairs.append((state, destination))
        if receipt['kind'] == 'transfer':
            transfer = receipt['transfer']
            sources = {r['event_id']:r for r in transfer['originals']}
            targets = {r['event_id']:r for r in transfer['remaining']}
            if transfer.get('seed_before'):
                sources[transfer['seed_before']['event_id']] = transfer['seed_before']
                targets[transfer['seed_before']['event_id']] = transfer['replacement']
            if event_id in sources and event_id in targets:
                pairs.append((receipt_record(sources[event_id]), receipt_record(targets[event_id])))
        for before, after in pairs:
            if before in states:
                states.append(after)
    return new in states


def remember_agenda(events, dates, *, settings_path, receipts_path, now=None):
    """Atomically consume disappearances and protect their room-independent time.

    Only call after complete agenda validation. Missing dates and pending internal
    mutations retain their baseline; expired sessions never create exclusions.
    Consuming each disappearance in the same write lets explicit reopen persist.
    """
    now = now or datetime.now()
    stamp = now.astimezone(timezone.utc).isoformat()
    covered = {day.isoformat() for day in dates}
    current, observed = {}, {}
    active_ids = {event.get("eventId") for event in events}
    for event in events:
        if event.get("isReservation") is not True:
            continue
        event_id = event.get("eventId")
        if type(event_id) is not int or event_id <= 0:
            raise SettingsError("Cancellation tracking requires exact reservation IDs")
        record = {key: event[key] for key in ("date", "startTime", "endTime", "room")}
        make_rebooking_blackout(record["date"], record["startTime"], record["endTime"])
        if str(event_id) in observed and observed[str(event_id)] != record:
            raise SettingsError("Ambiguous reservation in cancellation tracking")
        observed[str(event_id)] = record
        if datetime.fromisoformat(record["date"] + "T" + record["endTime"]) <= now:
            continue
        current[str(event_id)] = record

    receipts = list(load_journal(receipts_path)["receipts"].values())
    pending_ids, retired_ids = set(), set()
    for receipt in receipts:
        originals = [receipt.get("original", {}), *receipt.get("originals", [])]
        if receipt['kind'] == 'transfer':
            originals += receipt['transfer']['originals']
            originals += [receipt['transfer'].get('seed_before') or {}]
        if receipt["status"] == "pending":
            pending_ids.update(item.get("event_id") for item in originals)
            pending_ids.add(parse_confirmed_event_id(receipt.get('event_url', '')))
        if receipt["kind"] == "consolidation" and receipt["status"] == "verified":
            survivor = receipt["original"]["event_id"]
            retired_ids.update(item["event_id"] for item in originals if item.get("event_id") != survivor)
        if receipt['kind'] == 'transfer' and receipt['status'] == 'verified':
            remaining = {r['event_id'] for r in receipt['transfer']['remaining']}
            retired_ids.update(r['event_id'] for r in receipt['transfer']['originals'] if r['event_id'] not in remaining)
    # Compensation may recreate a cancelled donor under a different ID. Only
    # its verified child and resolved parent explain the original disappearance.
    parents = {r.get('id'): r for r in receipts if r['kind'] == 'transfer' and r['status'] == 'resolved'}
    for child in receipts:
        role = child.get('transfer_role', '')
        parent = parents.get(child.get('parent_id'))
        if child['kind'] != 'create' or child['status'] != 'verified' or not role.startswith('restore:') or not parent:
            continue
        for original in parent['transfer']['originals']:
            if role != f"restore:{original['event_id']}":
                continue
            new_id = parse_confirmed_event_id(child.get('event_url', ''))
            if observed.get(str(new_id)) == receipt_record(child) == receipt_record(original):
                retired_ids.add(original['event_id'])

    def mutate(settings):
        previous = validate_records(settings.get(OBSERVED_KEY, {}))
        pins = dict(validate_records(settings.get(KEY, {})))
        times = settings.get(TIMES_KEY, {})
        if not isinstance(times, dict):
            raise SettingsError('Invalid reservation observation times')
        try:
            for value in times.values():
                if datetime.fromisoformat(value).tzinfo is None:
                    raise ValueError()
        except (TypeError, ValueError) as exc:
            raise SettingsError('Invalid reservation observation timestamp') from exc
        retained = {}
        retained_times = {}
        added = []
        for key, record in previous.items():
            try:
                event_id = int(key)
                if str(event_id) != key or event_id <= 0 or set(record) != {"date", "startTime", "endTime", "room"}:
                    raise ValueError()
                window = make_rebooking_blackout(record["date"], record["startTime"], record["endTime"])
                end = datetime.fromisoformat(record["date"] + "T" + record["endTime"])
            except (ValueError, TypeError, KeyError) as exc:
                raise SettingsError("Invalid observed reservation") from exc
            if event_id in pending_ids:
                retained[key] = record
                if key in times:
                    retained_times[key] = times[key]
                continue
            if event_id in retired_ids and event_id not in active_ids:
                pins.pop(key, None)
                continue
            if event_id in active_ids:
                changed = observed.get(key)
                if changed is not None and changed != record:
                    if not owned_transition(event_id, record, changed, receipts, times.get(key)):
                        added.extend(released_windows(record, changed))
                        if key in current:
                            pins[key] = changed
                    elif key in pins:
                        pins[key] = changed
                if key not in current:
                    pins.pop(key, None)
                continue
            if end <= now:
                pins.pop(key, None)
                continue
            if record["date"] not in covered:
                retained[key] = record
                if key in times:
                    retained_times[key] = times[key]
            else:
                added.append(window)
                pins.pop(key, None)
        for key, record in current.items():
            if int(key) not in pending_ids or key not in previous:
                retained[key] = record
                retained_times[key] = stamp
        settings[OBSERVED_KEY] = retained
        settings[TIMES_KEY] = retained_times
        # Empty new metadata does not manufacture a preference change.
        if pins or KEY in settings:
            settings[KEY] = pins
            for key, record in pins.items():
                pin_in_settings(settings, int(key), record)
        blackouts = merge_rebooking_blackouts((*load_rebooking_blackouts(settings), *added))
        if added:
            settings["rebooking_blackouts"] = [window.to_dict() for window in blackouts]
        return blackouts

    return update_settings(mutate, settings_path)
