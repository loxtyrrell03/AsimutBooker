"""Validated, display-only snapshots of the user's complete Asimut agenda."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app_settings import InterProcessFileLock, SettingsError, atomic_write_json


APP_DIR = Path(__file__).resolve().parent
AGENDA_SNAPSHOT_FILE = APP_DIR / "data" / "agenda_snapshot.json"
SCHEMA_VERSION = 1
DEFAULT_MAX_AGE = timedelta(minutes=30)
_SNAPSHOT_KEYS = {"version", "observed_at", "dates", "events"}
_EVENT_KEYS = {
    "date",
    "startTime",
    "endTime",
    "title",
    "isReservation",
    "room",
    "eventId",
}


class AgendaSnapshotError(RuntimeError):
    """Raised when agenda display evidence is malformed or cannot be persisted."""


@dataclass(frozen=True)
class AgendaEvent:
    date: date
    start_time: str
    end_time: str
    title: str
    is_reservation: bool
    room: str | None
    event_id: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "startTime": self.start_time,
            "endTime": self.end_time,
            "title": self.title,
            "isReservation": self.is_reservation,
            "room": self.room,
            "eventId": self.event_id,
        }


@dataclass(frozen=True)
class AgendaSnapshot:
    version: int
    observed_at: datetime
    dates: tuple[date, ...]
    events: tuple[AgendaEvent, ...]

    def event_dicts(self) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self.events]


@dataclass(frozen=True)
class AgendaSnapshotReadResult:
    snapshot: AgendaSnapshot | None
    stale: bool
    reason: str = ""


def _fail(message: str) -> AgendaSnapshotError:
    return AgendaSnapshotError(f"Agenda snapshot is invalid: {message}")


def _parse_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise _fail(f"{label} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise _fail(f"{label} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise _fail(f"{label} must use canonical YYYY-MM-DD format")
    return parsed


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise _fail("observed_at must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _fail("observed_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _fail("observed_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _parse_clock(value: Any, label: str) -> tuple[str, int]:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise _fail(f"{label} must use HH:MM format")
    try:
        hour = int(value[:2])
        minute = int(value[3:])
    except ValueError as exc:
        raise _fail(f"{label} must use HH:MM format") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise _fail(f"{label} is outside the clock range")
    canonical = f"{hour:02d}:{minute:02d}"
    if value != canonical:
        raise _fail(f"{label} must use canonical HH:MM format")
    return canonical, hour * 60 + minute


def _text(value: Any, label: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise _fail(f"{label} must be text")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise _fail(f"{label} must contain 1-{maximum} characters")
    return normalized


def _event_from_mapping(
    raw: Mapping[str, Any],
    label: str,
    allowed_dates: frozenset[date],
    *,
    strict_keys: bool,
) -> AgendaEvent:
    if not isinstance(raw, Mapping):
        raise _fail(f"{label} must be an object")
    if strict_keys and set(raw) != _EVENT_KEYS:
        raise _fail(f"{label} has unexpected or missing fields")

    event_date = _parse_date(raw.get("date"), f"{label}.date")
    if event_date not in allowed_dates:
        raise _fail(f"{label}.date is outside the proven agenda window")
    start_time, start_minutes = _parse_clock(
        raw.get("startTime"), f"{label}.startTime"
    )
    end_time, end_minutes = _parse_clock(raw.get("endTime"), f"{label}.endTime")
    if end_minutes <= start_minutes:
        raise _fail(f"{label} must end after it starts")

    is_reservation = raw.get("isReservation")
    if type(is_reservation) is not bool:
        raise _fail(f"{label}.isReservation must be a Boolean")
    title = _text(raw.get("title"), f"{label}.title", maximum=512)

    room_raw = raw.get("room")
    room = None if room_raw is None else _text(room_raw, f"{label}.room", maximum=128)
    if is_reservation and room is None:
        raise _fail(f"{label}.room is required for a reservation")

    event_id = raw.get("eventId")
    if event_id is not None and (
        type(event_id) is not int or event_id <= 0
    ):
        raise _fail(f"{label}.eventId must be a positive integer or null")

    return AgendaEvent(
        date=event_date,
        start_time=start_time,
        end_time=end_time,
        title=title,
        is_reservation=is_reservation,
        room=room,
        event_id=event_id,
    )


def _validated_dates(values: Sequence[Any]) -> tuple[date, ...]:
    if not isinstance(values, (list, tuple)) or not values:
        raise _fail("dates must be a non-empty list")
    dates = tuple(_parse_date(value, f"dates[{index}]") for index, value in enumerate(values))
    if dates != tuple(sorted(set(dates))):
        raise _fail("dates must be unique and strictly increasing")
    for previous, current in zip(dates, dates[1:]):
        if current != previous + timedelta(days=1):
            raise _fail("dates must describe one contiguous agenda window")
    return dates


def snapshot_from_dict(raw: Any) -> AgendaSnapshot:
    if not isinstance(raw, Mapping) or set(raw) != _SNAPSHOT_KEYS:
        raise _fail("document has unexpected or missing fields")
    if type(raw["version"]) is not int or raw["version"] != SCHEMA_VERSION:
        raise _fail(f"unsupported version: {raw['version']!r}")
    observed_at = _parse_timestamp(raw["observed_at"])
    dates = _validated_dates(raw["dates"])
    raw_events = raw["events"]
    if not isinstance(raw_events, list) or len(raw_events) > 1000:
        raise _fail("events must be a list containing at most 1000 entries")
    allowed_dates = frozenset(dates)
    events = tuple(
        _event_from_mapping(
            item,
            f"events[{index}]",
            allowed_dates,
            strict_keys=True,
        )
        for index, item in enumerate(raw_events)
    )
    keys = [
        (
            event.date,
            event.start_time,
            event.end_time,
            event.title,
            event.is_reservation,
            event.room or "",
            event.event_id or 0,
        )
        for event in events
    ]
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise _fail("events must be uniquely and canonically sorted")
    return AgendaSnapshot(SCHEMA_VERSION, observed_at, dates, events)


def publish_agenda_snapshot(
    events: Iterable[Mapping[str, Any]],
    dates: Sequence[date],
    *,
    observed_at: datetime | None = None,
    path: Path = AGENDA_SNAPSHOT_FILE,
) -> AgendaSnapshot:
    canonical_dates = _validated_dates([value.isoformat() for value in dates])
    observed = observed_at or datetime.now(timezone.utc)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise AgendaSnapshotError("observed_at must include a timezone")
    allowed_dates = frozenset(canonical_dates)
    normalized_events = [
        _event_from_mapping(
            event,
            f"events[{index}]",
            allowed_dates,
            strict_keys=False,
        )
        for index, event in enumerate(events)
    ]
    normalized_events.sort(
        key=lambda event: (
            event.date,
            event.start_time,
            event.end_time,
            event.title,
            event.is_reservation,
            event.room or "",
            event.event_id or 0,
        )
    )
    if len(set(normalized_events)) != len(normalized_events):
        raise _fail("events contain duplicate identities")
    snapshot = AgendaSnapshot(
        version=SCHEMA_VERSION,
        observed_at=observed.astimezone(timezone.utc),
        dates=canonical_dates,
        events=tuple(normalized_events),
    )
    document = {
        "version": snapshot.version,
        "observed_at": snapshot.observed_at.isoformat().replace("+00:00", "Z"),
        "dates": [value.isoformat() for value in snapshot.dates],
        "events": snapshot.event_dicts(),
    }
    path = Path(path)
    try:
        with InterProcessFileLock(path.with_suffix(path.suffix + ".lock")):
            atomic_write_json(path, document, backup=True)
    except SettingsError as exc:
        raise AgendaSnapshotError(f"Could not publish agenda snapshot: {exc}") from exc
    return snapshot_from_dict(document)


def read_agenda_snapshot(
    path: Path = AGENDA_SNAPSHOT_FILE,
    *,
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_MAX_AGE,
    expected_dates: Sequence[date] | None = None,
) -> AgendaSnapshotReadResult:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise AgendaSnapshotError("now must include a timezone")
    if max_age <= timedelta(0):
        raise AgendaSnapshotError("max_age must be positive")
    path = Path(path)
    if not path.exists():
        return AgendaSnapshotReadResult(None, True, "No agenda snapshot exists yet.")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            snapshot = snapshot_from_dict(json.load(handle))
    except AgendaSnapshotError as exc:
        return AgendaSnapshotReadResult(None, True, str(exc))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return AgendaSnapshotReadResult(None, True, f"Agenda snapshot is unreadable: {exc}")

    reasons: list[str] = []
    age = current.astimezone(timezone.utc) - snapshot.observed_at
    if age < -timedelta(minutes=5):
        reasons.append("agenda snapshot timestamp is in the future")
    elif age > max_age:
        reasons.append("agenda snapshot is older than the refresh interval")
    if expected_dates is not None and tuple(expected_dates) != snapshot.dates:
        reasons.append("agenda snapshot does not match the current booking window")
    return AgendaSnapshotReadResult(snapshot, bool(reasons), "; ".join(reasons))
