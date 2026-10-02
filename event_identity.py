"""Shared, collision-safe identity for cached and scanned Asimut events."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date


EVENT_IDENTITY_V2_PREFIX = "v2:"
EVENT_IDENTITY_V3_PREFIX = "v3:"
EVENT_RESPECT_PREFIX = "respect:"
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
    """Exact current choices and conservative compatibility projections."""

    ignored_v2_keys: frozenset[str]
    ambiguous_legacy_keys: frozenset[str]
    ignored_event_keys: frozenset[str] = frozenset()
    ambiguous_v2_keys: frozenset[str] = frozenset()
    ambiguous_event_keys: frozenset[str] = frozenset()
    respected_event_keys: frozenset[str] = frozenset()


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


def remote_event_id(event: Mapping[str, object]) -> int | None:
    """Accept only an unambiguous positive ASIMUT identifier."""
    if not isinstance(event, Mapping):
        raise EventIdentityError('Event must be an object')
    value = event.get('eventId')
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and re.fullmatch(r'[1-9][0-9]*', value):
        return int(value)
    return None


def event_identity_v3(event: Mapping[str, object]) -> str:
    """Bind a dated remote event to every reviewed classification/tuple field."""
    payload = event_identity_payload(event)
    identifier = remote_event_id(event)
    if identifier is None:
        raise EventIdentityError('An exact event choice needs a positive remote event ID')
    payload['event_id'] = identifier
    return EVENT_IDENTITY_V3_PREFIX + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def event_identity(event: Mapping[str, object]) -> str:
    """Current read identity; ID-less historical data retains its v2 fallback.

    A fallback identifies cached data only, and cannot authorize a new choice.
    New writers must require ``event_choice_eligibility`` and write v3.
    """
    return event_identity_v3(event) if remote_event_id(event) is not None else event_identity_v2(event)


def event_respect_key(event: Mapping[str, object]) -> str:
    """Explicit exact Respect overrides any older allow for this identity only.

    Keep these tokens in ignored_events so every existing full preference guard,
    cache fingerprint and atomic settings editor includes both choice values.
    """
    return EVENT_RESPECT_PREFIX + event_identity_v3(event)


def event_choice_eligibility(event: Mapping[str, object], events: Iterable[Mapping[str, object]]) -> tuple[bool, str]:
    """Only a distinct, positively identified non-reservation may be edited."""
    payload = event_identity_payload(event)
    identifier = remote_event_id(event)
    if payload['isReservation']:
        return False, 'Room reservations keep their booking controls and cannot be ignored here.'
    if identifier is None:
        return False, 'This event has no verified remote ID. Refresh the agenda before choosing it.'
    matches = {event_identity(item) for item in events
               if remote_event_id(item) == identifier and event_identity_payload(item)['date'] == payload['date']}
    if len(matches) != 1:
        return False, 'This dated event has conflicting details. Refresh the agenda before choosing it.'
    return True, ''


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
        # Keep a conflicting same-ID card visible to the ambiguity resolver;
        # first-row-wins would silently grant permission to changed details.
        key = event_identity(event)
        if key in seen:
            continue
        seen.add(key)
        unique_events.append(event)
    return unique_events


def resolve_ignored_event_keys(
    ignored_keys: Iterable[str],
    events: Iterable[Mapping[str, object]],
) -> IgnoredEventResolution:
    """Resolve exact v3 and unambiguous older choices without changing storage.

    A legacy ``date_start_end`` key is applied only if it maps to exactly one
    distinct dated remote identity in the current scan/cache. Identical tuples
    with different remote IDs make both legacy and v2 permission ambiguous.
    An inconsistent same-ID/same-date card authorizes no version of its choice.
    """

    if isinstance(ignored_keys, (str, bytes)):
        raise EventIdentityError("Ignored event keys must be a collection")

    current_events: dict[str, Mapping[str, object]] = {}
    v2_matches: dict[str, set[str]] = defaultdict(set)
    legacy_matches: dict[str, set[str]] = defaultdict(set)
    remote_matches: dict[tuple[int, str], set[str]] = defaultdict(set)
    for event in events:
        current_key = event_identity(event)
        v2_key = event_identity_v2(event)
        current_events.setdefault(current_key, event)
        v2_matches[v2_key].add(current_key)
        legacy_matches[legacy_event_identity(event)].add(current_key)
        identifier = remote_event_id(event)
        if identifier is not None:
            remote_matches[(identifier, event_identity_payload(event)['date'])].add(current_key)

    resolved: set[str] = set()
    respected: set[str] = set()
    ambiguous: set[str] = set()
    ambiguous_v2: set[str] = set()
    ambiguous_current = {key for matches in remote_matches.values() if len(matches) > 1 for key in matches}
    for ignored_key in ignored_keys:
        if not isinstance(ignored_key, str):
            raise EventIdentityError("Ignored event keys must be text")
        if ignored_key.startswith(EVENT_RESPECT_PREFIX + EVENT_IDENTITY_V3_PREFIX):
            target = ignored_key[len(EVENT_RESPECT_PREFIX):]
            if target in current_events:
                respected.add(target)
            continue
        if ignored_key.startswith(EVENT_IDENTITY_V3_PREFIX):
            if ignored_key in current_events and ignored_key not in ambiguous_current:
                resolved.add(ignored_key)
            continue
        if ignored_key.startswith(EVENT_IDENTITY_V2_PREFIX):
            matches = v2_matches.get(ignored_key, set())
            if len(matches) == 1 and not matches & ambiguous_current:
                resolved.update(matches)
            elif len(matches) > 1 or matches & ambiguous_current:
                ambiguous_v2.add(ignored_key)
            continue
        if _LEGACY_KEY_RE.fullmatch(ignored_key) is None:
            continue
        matches = legacy_matches.get(ignored_key, set())
        if len(matches) == 1 and not matches & ambiguous_current:
            resolved.update(matches)
        elif len(matches) > 1 or matches & ambiguous_current:
            ambiguous.add(ignored_key)

    # An explicit negative choice remains authoritative if an old ambiguous
    # alias later becomes unique. Corrupt allow+respect pairs fail closed.
    resolved.difference_update(respected)
    return IgnoredEventResolution(
        # Old consumers must never broaden one exact v3 selection to both
        # remote rows. New consumers use ignored_event_keys + event_identity.
        ignored_v2_keys=frozenset(key for key, matches in v2_matches.items() if matches and matches <= resolved),
        ambiguous_legacy_keys=frozenset(ambiguous),
        ignored_event_keys=frozenset(resolved),
        ambiguous_v2_keys=frozenset(ambiguous_v2),
        ambiguous_event_keys=frozenset(ambiguous_current),
        respected_event_keys=frozenset(respected),
    )


__all__ = [
    "EVENT_IDENTITY_V2_PREFIX",
    "EVENT_IDENTITY_V3_PREFIX",
    "EVENT_RESPECT_PREFIX",
    "EventIdentityError",
    "IgnoredEventResolution",
    "deduplicate_events",
    "event_identity_payload",
    "event_identity",
    "event_identity_v3",
    "event_respect_key",
    "event_choice_eligibility",
    "remote_event_id",
    "event_identity_v2",
    "is_v2_event_identity_key",
    "legacy_event_identity",
    "resolve_ignored_event_keys",
]
