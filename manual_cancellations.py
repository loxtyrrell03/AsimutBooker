"""Remember externally removed reservations from complete authenticated scans."""

from datetime import datetime

from app_settings import SettingsError, update_settings
from booking_blackouts import load_rebooking_blackouts, make_rebooking_blackout, merge_rebooking_blackouts
from mutation_receipts import load_journal

OBSERVED_KEY = "observed_reservations"


def remember_agenda(events, dates, *, settings_path, receipts_path, now=None):
    """Atomically consume disappearances and protect their room-independent time.

    Only call after complete agenda validation. Missing dates and pending internal
    mutations retain their baseline; expired sessions never create exclusions.
    Consuming each disappearance in the same write lets explicit reopen persist.
    """
    now = now or datetime.now()
    covered = {day.isoformat() for day in dates}
    current = {}
    active_ids = {event.get("eventId") for event in events}
    for event in events:
        if event.get("isReservation") is not True:
            continue
        event_id = event.get("eventId")
        if type(event_id) is not int or event_id <= 0:
            raise SettingsError("Cancellation tracking requires exact reservation IDs")
        record = {key: event[key] for key in ("date", "startTime", "endTime", "room")}
        make_rebooking_blackout(record["date"], record["startTime"], record["endTime"])
        if datetime.fromisoformat(record["date"] + "T" + record["endTime"]) <= now:
            continue
        if str(event_id) in current and current[str(event_id)] != record:
            raise SettingsError("Ambiguous reservation in cancellation tracking")
        current[str(event_id)] = record

    pending_ids, retired_ids = set(), set()
    for receipt in load_journal(receipts_path)["receipts"].values():
        originals = [receipt.get("original", {}), *receipt.get("originals", [])]
        if receipt["status"] == "pending":
            pending_ids.update(item.get("event_id") for item in originals)
        if receipt["kind"] == "consolidation" and receipt["status"] == "verified":
            survivor = receipt["original"]["event_id"]
            retired_ids.update(item["event_id"] for item in originals if item.get("event_id") != survivor)

    def mutate(settings):
        previous = settings.get(OBSERVED_KEY, {})
        if not isinstance(previous, dict):
            raise SettingsError("Invalid cancellation tracking state")
        retained = {}
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
            if end <= now or event_id in retired_ids:
                continue
            if event_id in active_ids:
                continue
            if record["date"] not in covered or event_id in pending_ids:
                retained[key] = record
            else:
                added.append(window)
        retained.update(current)
        settings[OBSERVED_KEY] = retained
        blackouts = merge_rebooking_blackouts((*load_rebooking_blackouts(settings), *added))
        if added:
            settings["rebooking_blackouts"] = [window.to_dict() for window in blackouts]
        return blackouts

    return update_settings(mutate, settings_path)
