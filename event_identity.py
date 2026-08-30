"""Shared, collision-safe identity for cached and scanned Asimut events."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date


EVENT_IDENTITY_V2_PREFIX = "v2:"
_TIME_RE = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
_LEGACY_KEY_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}_"
    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]_"
    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]"
)


class EventIdentityError(ValueError):
    """Raised when a scanned/cached event cannot be identified safely."""


@dataclass(frozen=True)
class IgnoredEventResolution:
    """Resolved v2 ignores plus legacy keys that were unsafe to apply."""

    ignored_v2_keys: frozenset[str]
    ambiguous_legacy_keys: frozenset[str]


def _canonical_text(value: object, field: str, *, optional: bool = False) -> str:
    if optional and value is None:
        return ""
    if not isinstance(value, str):
        raise EventIdentityError(f"Event {field} must be text")
    normalized = " ".join(value.split())
    if not optional and not normalized:
        raise EventIdentityError(f"Event {field} cannot be empty")
    return normalized


def _canonical_date(value: object) -> str:
    raw = _canonical_text(value, "date")
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise EventIdentityError("Event date must use YYYY-MM-DD") from exc
    if parsed.isoformat() != raw:
        raise EventIdentityError("Event date must be canonical YYYY-MM-DD")
    return raw


def _canonical_time(value: object, field: str) -> str:
    raw = _canonical_text(value, field)
    if _TIME_RE.fullmatch(raw) is None:
        raise EventIdentityError(f"Event {field} must use canonical HH:MM")
    return raw


def event_identity_payload(event: Mapping[str, object]) -> dict[str, object]:
    """Return the canonical fields covered by the v2 event identity."""

    if not isinstance(event, Mapping):
        raise EventIdentityError("Event must be an object")
    is_reservation = event.get("isReservation")
    if not isinstance(is_reservation, bool):
        raise EventIdentityError("Event isReservation must be true or false")

    title = _canonical_text(event.get("title"), "title")
    room = _canonical_text(event.get("room"), "room", optional=True).upper()
    return {
        "date": _canonical_date(event.get("date")),
        "start": _canonical_time(event.get("startTime"), "startTime"),
        "end": _canonical_time(event.get("endTime"), "endTime"),
        "title": title,
        "room": room,
        "isReservation": is_reservation,
    }


def event_identity_v2(event: Mapping[str, object]) -> str:
    """Build a deterministic, delimiter-safe identity covering every event field."""

    payload = event_identity_payload(event)
    return EVENT_IDENTITY_V2_PREFIX + json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def legacy_event_identity(event: Mapping[str, object]) -> str:
    """Build the historical time-only key used by older settings files."""

    payload = event_identity_payload(event)
    return f"{payload['date']}_{payload['start']}_{payload['end']}"


def is_v2_event_identity_key(value: object) -> bool:
    """Return whether a string is a canonical v2 event identity key."""

    if not isinstance(value, str) or not value.startswith(EVENT_IDENTITY_V2_PREFIX):
        return False
    try:
        payload = json.loads(value[len(EVENT_IDENTITY_V2_PREFIX) :])
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    canonical = EVENT_IDENTITY_V2_PREFIX + json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return canonical == value


def deduplicate_events(
    events: Iterable[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    """Remove repeated DOM copies while preserving distinct remote events.

    Current Asimut cards expose a positive eventId. Repeated lazy-rendered
    copies of one card share that id, while two genuinely separate events may
    otherwise have the same room/date/time/title identity. Legacy/cache records
    without an id continue to use the complete v2 logical identity.
    """

    unique_events: list[Mapping[str, object]] = []
    seen: set[str] = set()
    for event in events:
        event_id = event.get("eventId")
        if isinstance(event_id, bool):
            event_id = None
        if isinstance(event_id, int) and event_id > 0:
            key = f"event-id:{event_id}"
        elif (
            isinstance(event_id, str)
            and event_id
            and event_id.isascii()
            and event_id.isdecimal()
            and not event_id.startswith("0")
        ):
            key = f"event-id:{event_id}"
        else:
            key = event_identity_v2(event)
        if key in seen:
            continue
        seen.add(key)
        unique_events.append(event)
    return unique_events


def resolve_ignored_event_keys(
    ignored_keys: Iterable[str],
    events: Iterable[Mapping[str, object]],
) -> IgnoredEventResolution:
    """Resolve v2 keys and only unambiguous historical keys for one event set.

    A legacy ``date_start_end`` key is applied only if it maps to exactly one
    distinct v2 identity in the current scan/cache.  If a class and reservation
    share that time, neither is ignored.
    """

    if isinstance(ignored_keys, (str, bytes)):
        raise EventIdentityError("Ignored event keys must be a collection")

    v2_events: dict[str, Mapping[str, object]] = {}
    legacy_matches: dict[str, set[str]] = defaultdict(set)
    for event in events:
        v2_key = event_identity_v2(event)
        v2_events.setdefault(v2_key, event)
        legacy_matches[legacy_event_identity(event)].add(v2_key)

    resolved: set[str] = set()
    ambiguous: set[str] = set()
    for ignored_key in ignored_keys:
        if not isinstance(ignored_key, str):
            raise EventIdentityError("Ignored event keys must be text")
        if ignored_key.startswith(EVENT_IDENTITY_V2_PREFIX):
            if ignored_key in v2_events and is_v2_event_identity_key(ignored_key):
                resolved.add(ignored_key)
            continue
        if _LEGACY_KEY_RE.fullmatch(ignored_key) is None:
            continue
        matches = legacy_matches.get(ignored_key, set())
        if len(matches) == 1:
            resolved.update(matches)
        elif len(matches) > 1:
            ambiguous.add(ignored_key)

    return IgnoredEventResolution(
        ignored_v2_keys=frozenset(resolved),
        ambiguous_legacy_keys=frozenset(ambiguous),
    )


__all__ = [
    "EVENT_IDENTITY_V2_PREFIX",
    "EventIdentityError",
    "IgnoredEventResolution",
    "deduplicate_events",
    "event_identity_payload",
    "event_identity_v2",
    "is_v2_event_identity_key",
    "legacy_event_identity",
    "resolve_ignored_event_keys",
]
