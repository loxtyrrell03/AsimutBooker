"""Fresh, fail-closed discovery of Asimut room metadata and horizons.

The autonomous booker must build a complete :class:`RoomCatalog` from the
authenticated site on every run.  A cached catalog is deliberately marked as
display-only: it can make the GUI useful while offline, but it cannot authorize
a booking decision.

This module never calls Asimut's ``event/type=save`` endpoint.  The temporary
event-default object (including its participant arrays) exists only in memory
while exact ``event/type=check`` requests are made; it is never logged or
included in the cache schema.
"""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time as datetime_time, timedelta, tzinfo
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, unquote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app_settings import InterProcessFileLock, SettingsError, atomic_write_json


class _EuropeLondonFallback(tzinfo):
    """Modern UK timezone rules for Python installs without the tzdata wheel.

    Windows does not ship the IANA database consumed by :mod:`zoneinfo`, and
    the booker's minimal requirements historically have not included the
    optional ``tzdata`` package.  RWCMD policy is expressed in Europe/London
    wall time, so falling back to a fixed current offset would silently break
    horizon arithmetic across clock changes.  Since 1996, British Summer Time
    runs from the last Sunday in March at 01:00 UTC until the last Sunday in
    October at 01:00 UTC; this small fallback implements that exact rule.
    """

    _HOUR = timedelta(hours=1)
    _ZERO = timedelta(0)

    @staticmethod
    def _last_sunday(year: int, month: int) -> date:
        if month == 12:
            first_next = date(year + 1, 1, 1)
        else:
            first_next = date(year, month + 1, 1)
        last_day = first_next - timedelta(days=1)
        return last_day - timedelta(days=(last_day.weekday() + 1) % 7)

    @classmethod
    def _utc_boundaries(cls, year: int) -> tuple[datetime, datetime]:
        start_day = cls._last_sunday(year, 3)
        end_day = cls._last_sunday(year, 10)
        return (
            datetime.combine(start_day, datetime_time(1)),
            datetime.combine(end_day, datetime_time(1)),
        )

    def _is_dst_local(self, value: datetime) -> bool:
        naive = value.replace(tzinfo=None)
        start_utc, end_utc = self._utc_boundaries(value.year)
        # The spring change enters BST at 01:00 local wall time.  The autumn
        # hour 01:00-01:59 occurs twice; PEP 495's fold selects GMT on fold=1.
        spring_local = start_utc
        autumn_ambiguous_start = end_utc
        autumn_local_end = end_utc + self._HOUR
        if spring_local <= naive < autumn_ambiguous_start:
            return True
        if autumn_ambiguous_start <= naive < autumn_local_end:
            return not bool(value.fold)
        return False

    def utcoffset(self, value: datetime | None) -> timedelta:
        if value is None:
            return self._ZERO
        return self._HOUR if self._is_dst_local(value) else self._ZERO

    def dst(self, value: datetime | None) -> timedelta:
        return self.utcoffset(value)

    def tzname(self, value: datetime | None) -> str:
        return "BST" if value is not None and self._is_dst_local(value) else "GMT"

    def fromutc(self, value: datetime) -> datetime:
        if value.tzinfo is not self:
            raise ValueError("fromutc requires this timezone instance")
        naive_utc = value.replace(tzinfo=None)
        start_utc, end_utc = self._utc_boundaries(naive_utc.year)
        if start_utc <= naive_utc < end_utc:
            return (naive_utc + self._HOUR).replace(tzinfo=self)
        result = naive_utc.replace(tzinfo=self)
        if end_utc <= naive_utc < end_utc + self._HOUR:
            result = result.replace(fold=1)
        return result


def _load_site_timezone() -> tzinfo:
    try:
        return ZoneInfo("Europe/London")
    except (ZoneInfoNotFoundError, OSError):
        return _EuropeLondonFallback()


APP_DIR = Path(__file__).resolve().parent
ROOM_CATALOG_FILE = APP_DIR / "data" / "room_catalog.json"
CATALOG_VERSION = 1
ASIMUT_ORIGIN = "https://rwcmd.asimut.net"
LOCATION_GROUP_ID = 10
SITE_TIMEZONE = _load_site_timezone()

SESSION_CONTEXT_PATH = "/services/v2/session-context/me"
CATEGORIES_PATH = "/services/v2/categories"
EVENTDEFAULT_PATH = "/services/v2/eventdefault"
EVENT_CHECK_PATH = "/services/v2/event/type=check"

_CACHE_KEYS = {
    "version",
    "observed_at",
    "booking_horizon",
    "global_horizon_minutes",
    "minimum_booking_minutes",
    "maximum_booking_minutes",
    "minimum_booking_gap_minutes",
    "booking_category_id",
    "rooms",
}
_CACHE_ROOM_KEYS = {
    "location_id",
    "name",
    "secondary_name",
    "description",
    "inventory",
    "instrument_tags",
    "room_type_tags",
    "features",
    "horizon_minutes",
    "booking_cutoff",
}
_ISSUE_RE = re.compile(
    r"^You are not allowed to create or modify bookings"
    r"(?: in (?P<room>.+?))? that end later than "
    r"(?P<day>[0-9]{1,2})/(?P<month>[0-9]{1,2})/(?P<year>[0-9]{2}) "
    r"(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2})$"
)
_HORIZON_HINT_PATTERNS = (
    re.compile(
        r"\b(?:can|may)\s+(?:only\s+)?be\s+booked\s+"
        r"(?P<amount>[a-z]+(?:[- ][a-z]+)*|[0-9]+(?:\.[0-9]+)?)\s+"
        r"(?P<unit>minutes?|hours?|days?)\s+"
        r"(?:ahead|in\s+advance)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bbook(?:ing|able|ed)?(?:\s+horizon)?\s*(?:is|:)?\s*"
        r"(?P<amount>[a-z]+(?:[- ][a-z]+)*|[0-9]+(?:\.[0-9]+)?)\s+"
        r"(?P<unit>minutes?|hours?|days?)\s+"
        r"(?:ahead|in\s+advance)\b",
        re.IGNORECASE,
    ),
)
_SMALL_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS_NUMBER_WORDS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}


class RoomCatalogError(RuntimeError):
    """Raised when live or cached catalog evidence cannot be trusted."""


@dataclass(frozen=True)
class SessionPolicy:
    """The live booking limits exposed by ``session-context/me``."""

    observed_at: datetime
    booking_horizon: datetime
    global_horizon_minutes: int
    minimum_booking_minutes: int
    maximum_booking_minutes: int
    minimum_booking_gap_minutes: int


@dataclass(frozen=True)
class LocationMeta:
    """One location identity from the live location-group metadata."""

    location_id: int
    name: str
    secondary_name: str


@dataclass(frozen=True)
class LocationInfo:
    """Normalized non-secret descriptive metadata for one location."""

    location_id: int
    name: str
    secondary_name: str
    description: str
    inventory: str
    description_items: tuple[str, ...]
    inventory_items: tuple[str, ...]


@dataclass(frozen=True)
class HorizonEvidence:
    """Authoritative cutoff and derived duration from one check response."""

    booking_cutoff: datetime
    horizon_minutes: int


@dataclass(frozen=True)
class RoomCatalogRoom:
    """One complete room entry safe to persist and display."""

    location_id: int
    name: str
    secondary_name: str
    description: str
    inventory: str
    instrument_tags: tuple[str, ...]
    room_type_tags: tuple[str, ...]
    features: tuple[str, ...]
    horizon_minutes: int
    booking_cutoff: datetime

    @property
    def horizon_days(self) -> float:
        """Return the horizon as days without assuming it is a whole day."""

        return self.horizon_minutes / (24 * 60)


@dataclass(frozen=True)
class RoomCatalog:
    """A complete snapshot of live room metadata and booking policy."""

    version: int
    observed_at: datetime
    booking_horizon: datetime
    global_horizon_minutes: int
    minimum_booking_minutes: int
    maximum_booking_minutes: int
    minimum_booking_gap_minutes: int
    booking_category_id: int
    rooms: tuple[RoomCatalogRoom, ...]
    fresh: bool = field(default=False, compare=False, repr=False)

    @property
    def fetched_at(self) -> datetime:
        """Compatibility alias used by health and GUI views."""

        return self.observed_at

    @property
    def room_names(self) -> tuple[str, ...]:
        return tuple(room.name for room in self.rooms)

    @property
    def source(self) -> str:
        return "live" if self.fresh else "cache"

    @property
    def complete(self) -> bool:
        return bool(self.rooms) and len(self.room_names) == len(set(self.room_names))

    @property
    def minimum_booking_length(self) -> int:
        return self.minimum_booking_minutes

    @property
    def maximum_booking_length(self) -> int:
        return self.maximum_booking_minutes

    @property
    def minimum_booking_gap(self) -> int:
        return self.minimum_booking_gap_minutes

    def require_fresh(self) -> "RoomCatalog":
        """Reject a display-only cache before a runtime booking decision."""

        if not self.fresh:
            raise RoomCatalogError(
                "The room catalog came from cache and is display-only; "
                "this authenticated run must refresh it before booking"
            )
        if not self.complete:
            raise RoomCatalogError("The live room catalog is incomplete")
        return self

    def as_live_metadata(self) -> dict[str, dict[str, list[str]]]:
        """Return the exact metadata keys consumed by ``room_preferences``."""

        return {
            room.name: {
                "instrument_tags": list(room.instrument_tags),
                "room_type_tags": list(room.room_type_tags),
                "features": list(room.features),
            }
            for room in self.rooms
        }


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RoomCatalogError(f"{label} must be a JSON object")
    if any(not isinstance(key, str) for key in value):
        raise RoomCatalogError(f"{label} keys must be strings")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RoomCatalogError(f"{label} must be a JSON list")
    return value


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RoomCatalogError(f"{label} must be a positive integer")
    return value


def _minute_quantity(value: Any, label: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RoomCatalogError(f"{label} must be an integer number of minutes")
    if value < 0 or (value == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise RoomCatalogError(f"{label} must be {qualifier}")
    if value % 15:
        raise RoomCatalogError(f"{label} must use 15-minute increments")
    return value


def _text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise RoomCatalogError(f"{label} must be a string")
    if value != value.strip() or (not value and not allow_empty):
        requirement = "a trimmed string" if allow_empty else "a non-empty trimmed string"
        raise RoomCatalogError(f"{label} must be {requirement}")
    if any(ord(character) < 32 and character not in "\n\t" for character in value):
        raise RoomCatalogError(f"{label} contains control characters")
    return value


def _parse_aware_datetime(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RoomCatalogError(f"{label} must be a timezone-aware ISO timestamp")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise RoomCatalogError(f"{label} must be a timezone-aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RoomCatalogError(f"{label} must include a timezone offset")
    return parsed.astimezone(SITE_TIMEZONE)


def _normalize_observed_at(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RoomCatalogError("observed_at must be a timezone-aware datetime")
    return value.astimezone(SITE_TIMEZONE).replace(second=0, microsecond=0)


def _minute_key(value: datetime) -> tuple[int, int, int, int, int]:
    local = value.astimezone(SITE_TIMEZONE)
    return local.year, local.month, local.day, local.hour, local.minute


def _wall_clock_minutes(start: datetime, end: datetime, label: str) -> int:
    start_local = start.astimezone(SITE_TIMEZONE).replace(tzinfo=None)
    end_local = end.astimezone(SITE_TIMEZONE).replace(tzinfo=None)
    seconds = (end_local - start_local).total_seconds()
    if not math.isfinite(seconds) or seconds <= 0 or seconds % 60:
        raise RoomCatalogError(f"{label} must resolve to a positive whole-minute duration")
    minutes = int(seconds // 60)
    return _minute_quantity(minutes, label)


def _response_object(payload: Any, label: str) -> Mapping[str, Any]:
    root = _mapping(payload, label)
    return _mapping(root.get("response"), f"{label}.response")


def _require_success(response: Mapping[str, Any], label: str, expected: bool) -> None:
    if response.get("success") is not expected:
        word = "true" if expected else "false"
        raise RoomCatalogError(f"{label}.response.success must be {word}")


def parse_session_context(payload: Any, observed_at: datetime) -> SessionPolicy:
    """Strictly parse live session policy and derive its arbitrary horizon."""

    response = _response_object(payload, "session context")
    session_context = _mapping(
        response.get("session_context"), "session context.response.session_context"
    )
    me = _mapping(session_context.get("me"), "session context.response.session_context.me")
    observed = _normalize_observed_at(observed_at)
    booking_horizon = _parse_aware_datetime(
        me.get("booking_horizon"),
        "session context.response.session_context.me.booking_horizon",
    )
    if booking_horizon.second or booking_horizon.microsecond:
        raise RoomCatalogError("me.booking_horizon must be exact to the minute")
    global_minutes = _wall_clock_minutes(
        observed, booking_horizon, "global booking horizon"
    )
    minimum = _minute_quantity(
        me.get("minimum_booking_length"), "me.minimum_booking_length"
    )
    maximum = _minute_quantity(
        me.get("maximum_booking_length"), "me.maximum_booking_length"
    )
    gap = _minute_quantity(
        me.get("minimum_booking_gap"), "me.minimum_booking_gap", allow_zero=True
    )
    if maximum < minimum:
        raise RoomCatalogError(
            "me.maximum_booking_length must not be shorter than me.minimum_booking_length"
        )
    return SessionPolicy(
        observed_at=observed,
        booking_horizon=booking_horizon,
        global_horizon_minutes=global_minutes,
        minimum_booking_minutes=minimum,
        maximum_booking_minutes=maximum,
        minimum_booking_gap_minutes=gap,
    )


def parse_booking_category(payload: Any) -> int:
    """Select the one live, user-visible category governed by quota+horizon."""

    response = _response_object(payload, "categories")
    _require_success(response, "categories", True)
    categories = _list(response.get("categories"), "categories.response.categories")
    candidates: list[int] = []
    seen_ids: set[int] = set()
    for index, raw in enumerate(categories):
        item = _mapping(raw, f"categories.response.categories[{index}]")
        category_id = _positive_int(item.get("id"), f"categories[{index}].id")
        if category_id in seen_ids:
            raise RoomCatalogError(f"categories contains duplicate id {category_id}")
        seen_ids.add(category_id)

        def enabled(key: str) -> bool:
            value = item.get(key)
            if value is True or value == "true":
                return True
            if value is False or value == "false" or value is None:
                return False
            raise RoomCatalogError(f"categories[{index}].{key} must be boolean-like")

        if (
            enabled("my_category")
            and enabled("publicevent_usehorizons")
            and enabled("publicevent_usequotas")
        ):
            candidates.append(category_id)
    if len(candidates) != 1:
        raise RoomCatalogError(
            "Expected exactly one permitted quota-and-horizon booking category; "
            f"found {len(candidates)}"
        )
    return candidates[0]


def parse_location_group_meta(payload: Any) -> tuple[LocationMeta, ...]:
    """Parse the current ordered membership of AHC location group 10."""

    response = _response_object(payload, "location group metadata")
    _require_success(response, "location group metadata", True)
    locations = _list(response.get("locations"), "location group metadata.response.locations")
    if not locations:
        raise RoomCatalogError("Location group 10 returned no rooms")
    result: list[LocationMeta] = []
    seen_ids: set[int] = set()
    seen_names: set[str] = set()
    for index, raw in enumerate(locations):
        item = _mapping(raw, f"location group metadata.response.locations[{index}]")
        location_id = _positive_int(item.get("id"), f"locations[{index}].id")
        name = _text(item.get("name"), f"locations[{index}].name")
        secondary = _text(
            item.get("secondary_name"),
            f"locations[{index}].secondary_name",
            allow_empty=True,
        )
        if location_id in seen_ids:
            raise RoomCatalogError(f"Location group contains duplicate id {location_id}")
        if name in seen_names:
            raise RoomCatalogError(f"Location group contains duplicate room name {name}")
        seen_ids.add(location_id)
        seen_names.add(name)
        closed_hours = _list(item.get("closed_hours"), f"locations[{index}].closed_hours")
        for closed_index, raw_closed in enumerate(closed_hours):
            closed = _mapping(
                raw_closed, f"locations[{index}].closed_hours[{closed_index}]"
            )
            start = _parse_aware_datetime(
                closed.get("st"), f"locations[{index}].closed_hours[{closed_index}].st"
            )
            end = _parse_aware_datetime(
                closed.get("en"), f"locations[{index}].closed_hours[{closed_index}].en"
            )
            if end <= start:
                raise RoomCatalogError("Location closed-hours end must be after its start")
        result.append(LocationMeta(location_id, name, secondary))
    return tuple(result)


class _PlainTextHTMLParser(HTMLParser):
    _BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        if lowered in {"script", "style"}:
            self._ignored_depth += 1
        elif not self._ignored_depth and lowered in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth and lowered in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def normalize_html_items(value: Any, label: str = "HTML metadata") -> tuple[str, ...]:
    """Normalize HTML to distinct readable lines, decoding entities safely."""

    if not isinstance(value, str):
        raise RoomCatalogError(f"{label} must be an HTML string")
    parser = _PlainTextHTMLParser()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, TypeError) as exc:
        raise RoomCatalogError(f"{label} could not be normalized") from exc
    raw_lines = "".join(parser.parts).replace("\u200b", "").replace("\u00ad", "").splitlines()
    items: list[str] = []
    seen: set[str] = set()
    for raw in raw_lines:
        normalized = re.sub(r"\s+", " ", raw).strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key not in seen:
            seen.add(key)
            items.append(normalized)
    return tuple(items)


def normalize_html(value: Any, label: str = "HTML metadata") -> str:
    """Normalize HTML into newline-separated, entity-decoded plain text."""

    return "\n".join(normalize_html_items(value, label))


def parse_location_info(
    payload: Any,
    expected_locations: Sequence[LocationMeta],
) -> dict[int, LocationInfo]:
    """Parse and cross-check the one combined location-info response."""

    if not isinstance(expected_locations, Sequence) or isinstance(
        expected_locations, (str, bytes, bytearray)
    ):
        raise RoomCatalogError("expected_locations must be an ordered location sequence")
    expected_by_id: dict[int, LocationMeta] = {}
    for item in expected_locations:
        if not isinstance(item, LocationMeta):
            raise RoomCatalogError("expected_locations must contain LocationMeta values")
        if item.location_id in expected_by_id:
            raise RoomCatalogError("expected_locations contains a duplicate id")
        expected_by_id[item.location_id] = item
    if not expected_by_id:
        raise RoomCatalogError("expected_locations must not be empty")

    response = _response_object(payload, "location info")
    _require_success(response, "location info", True)
    entries = _list(response.get("locationsInfo"), "location info.response.locationsInfo")
    result: dict[int, LocationInfo] = {}
    for index, raw in enumerate(entries):
        item = _mapping(raw, f"location info.response.locationsInfo[{index}]")
        location_id = _positive_int(item.get("id"), f"locationsInfo[{index}].id")
        if location_id in result:
            raise RoomCatalogError(f"Location info contains duplicate id {location_id}")
        expected = expected_by_id.get(location_id)
        if expected is None:
            raise RoomCatalogError(f"Location info contains unexpected id {location_id}")
        name = _text(item.get("name"), f"locationsInfo[{index}].name")
        secondary = _text(
            item.get("secondary_name"),
            f"locationsInfo[{index}].secondary_name",
            allow_empty=True,
        )
        if name != expected.name or secondary != expected.secondary_name:
            raise RoomCatalogError(
                f"Location info identity disagrees with group metadata for id {location_id}"
            )
        _list(
            item.get("default_opening_hours"),
            f"locationsInfo[{index}].default_opening_hours",
        )
        _list(
            item.get("exceptional_opening_hours"),
            f"locationsInfo[{index}].exceptional_opening_hours",
        )
        description_items = normalize_html_items(
            item.get("description"), f"locationsInfo[{index}].description"
        )
        inventory_items = normalize_html_items(
            item.get("inventory"), f"locationsInfo[{index}].inventory"
        )
        result[location_id] = LocationInfo(
            location_id=location_id,
            name=name,
            secondary_name=secondary,
            description="\n".join(description_items),
            inventory="\n".join(inventory_items),
            description_items=description_items,
            inventory_items=inventory_items,
        )
    missing = set(expected_by_id) - set(result)
    if missing:
        raise RoomCatalogError(
            "Location info is incomplete; missing id(s): "
            + ", ".join(str(value) for value in sorted(missing))
        )
    return result


def _parse_number(value: str, label: str) -> Decimal:
    normalized = re.sub(r"[-\s]+", " ", value.casefold()).strip()
    tokens = normalized.split()
    if len(tokens) == 1 and tokens[0] in _SMALL_NUMBER_WORDS:
        return Decimal(_SMALL_NUMBER_WORDS[tokens[0]])
    if len(tokens) == 1 and tokens[0] in _TENS_NUMBER_WORDS:
        return Decimal(_TENS_NUMBER_WORDS[tokens[0]])
    if (
        len(tokens) == 2
        and tokens[0] in _TENS_NUMBER_WORDS
        and tokens[1] in _SMALL_NUMBER_WORDS
        and _SMALL_NUMBER_WORDS[tokens[1]] < 10
    ):
        return Decimal(
            _TENS_NUMBER_WORDS[tokens[0]] + _SMALL_NUMBER_WORDS[tokens[1]]
        )
    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise RoomCatalogError(f"{label} contains an unsupported number {value!r}") from exc


def parse_description_horizon_hint(description: str) -> int | None:
    """Return an explicit textual horizon hint, rejecting ambiguity."""

    if not isinstance(description, str):
        raise RoomCatalogError("Room description must be plain text")
    values: list[int] = []
    for pattern in _HORIZON_HINT_PATTERNS:
        for match in pattern.finditer(description):
            amount = _parse_number(match.group("amount"), "Room description horizon")
            unit = match.group("unit").casefold()
            multiplier = Decimal(1)
            if unit.startswith("hour"):
                multiplier = Decimal(60)
            elif unit.startswith("day"):
                multiplier = Decimal(24 * 60)
            minutes_decimal = amount * multiplier
            if minutes_decimal != minutes_decimal.to_integral_value():
                raise RoomCatalogError(
                    "Room description horizon must resolve to whole minutes"
                )
            minutes = _minute_quantity(
                int(minutes_decimal), "Room description horizon"
            )
            values.append(minutes)
    if not values:
        return None
    if len(set(values)) != 1:
        raise RoomCatalogError("Room description contains inconsistent horizon hints")
    return values[0]


def _parse_issue_cutoff(match: re.Match[str], label: str) -> datetime:
    try:
        naive = datetime(
            2000 + int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
        )
    except ValueError as exc:
        raise RoomCatalogError(f"{label} contains an invalid cutoff timestamp") from exc
    return naive.replace(tzinfo=SITE_TIMEZONE)


def parse_horizon_issues(
    payload: Any,
    room_name: str,
    expected_global_cutoff: datetime,
    observed_at: datetime,
    *,
    expected_global_horizon_minutes: int | None = None,
) -> HorizonEvidence:
    """Extract one authoritative room cutoff from a direct check response.

    Asimut's session context and event-check endpoints each calculate their
    absolute cutoff at request time.  A refresh can cross a minute boundary, so
    production callers validate the stable duration exposed by both endpoints
    against the minute in which this check was observed.  The exact-cutoff path
    remains available for pure fixtures and callers without observation data.
    """

    room_name = _text(room_name, "room_name")
    expected_global = expected_global_cutoff.astimezone(SITE_TIMEZONE)
    observed = _normalize_observed_at(observed_at)
    response = _response_object(payload, f"event check for {room_name}")
    _require_success(response, f"event check for {room_name}", False)
    bookingrules = _mapping(
        response.get("bookingrules"), f"event check for {room_name}.response.bookingrules"
    )
    issues = _list(
        bookingrules.get("issues"),
        f"event check for {room_name}.response.bookingrules.issues",
    )
    global_cutoffs: list[datetime] = []
    room_cutoffs: list[datetime] = []
    for index, raw in enumerate(issues):
        issue = _mapping(raw, f"event check for {room_name}.issues[{index}]")
        if set(issue) != {"class", "text", "type"}:
            raise RoomCatalogError(
                f"Event check issue {index} for {room_name} has an unsupported schema"
            )
        issue_class = _text(issue.get("class"), f"issues[{index}].class")
        issue_type = _text(issue.get("type"), f"issues[{index}].type")
        issue_text = _text(issue.get("text"), f"issues[{index}].text")
        if issue_type != "date-time":
            continue
        if issue_class != "message-warning":
            raise RoomCatalogError(
                f"Date-time issue for {room_name} did not use the authoritative warning class"
            )
        normalized_text = re.sub(r"\s+", " ", issue_text).strip()
        match = _ISSUE_RE.fullmatch(normalized_text)
        if match is None:
            raise RoomCatalogError(
                f"Event check for {room_name} returned an unrecognized date-time rule"
            )
        issue_room = match.group("room")
        cutoff = _parse_issue_cutoff(match, f"Event check for {room_name}")
        if issue_room is None:
            global_cutoffs.append(cutoff)
        elif issue_room == room_name:
            room_cutoffs.append(cutoff)
        else:
            raise RoomCatalogError(
                f"Event check for {room_name} returned a cutoff for a different room"
            )
    if len(global_cutoffs) != 1:
        raise RoomCatalogError(
            f"Event check for {room_name} must contain exactly one global cutoff"
        )
    if expected_global_horizon_minutes is None:
        if _minute_key(global_cutoffs[0]) != _minute_key(expected_global):
            raise RoomCatalogError(
                f"Event check global cutoff disagrees with session context for {room_name}: "
                f"check={global_cutoffs[0].isoformat(timespec='minutes')}, "
                f"session={expected_global.isoformat(timespec='minutes')}"
            )
    else:
        expected_global_minutes = _minute_quantity(
            expected_global_horizon_minutes,
            "expected global booking horizon",
        )
        actual_global_minutes = _wall_clock_minutes(
            observed,
            global_cutoffs[0],
            f"Event-check global horizon for {room_name}",
        )
        if actual_global_minutes != expected_global_minutes:
            raise RoomCatalogError(
                f"Event-check global horizon for {room_name} is "
                f"{actual_global_minutes} minutes; session context requires "
                f"{expected_global_minutes} minutes"
            )
    if len(room_cutoffs) > 1:
        raise RoomCatalogError(
            f"Event check for {room_name} contains ambiguous room-specific cutoffs"
        )
    cutoff = room_cutoffs[0] if room_cutoffs else global_cutoffs[0]
    if cutoff > global_cutoffs[0]:
        raise RoomCatalogError(
            f"Room-specific cutoff for {room_name} exceeds the global cutoff"
        )
    horizon_minutes = _wall_clock_minutes(
        observed, cutoff, f"Booking horizon for {room_name}"
    )
    return HorizonEvidence(cutoff, horizon_minutes)


def _dedupe_text(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key not in seen:
            seen.add(key)
            result.append(normalized)
    return tuple(result)


def _instrument_tags(info: LocationInfo) -> tuple[str, ...]:
    tags: list[str] = []
    for item in info.inventory_items:
        # Inventory commonly prefixes quantities ("1 x", "2 ×").  Removing
        # that mechanical prefix produces a stable, human-selectable tag while
        # preserving the site's full normalized inventory separately.
        tags.append(re.sub(r"^[0-9]+\s*(?:x|×)\s*", "", item, flags=re.I))
    return _dedupe_text(tags)


def build_catalog(
    *,
    session_payload: Any,
    location_meta_payload: Any,
    location_info_payload: Any,
    check_payloads: Mapping[int, Any],
    observed_at: datetime,
    booking_category_id: int,
    check_observed_ats: Mapping[int, datetime] | None = None,
) -> RoomCatalog:
    """Build one all-or-nothing fresh catalog from pure response values."""

    category_id = _positive_int(booking_category_id, "booking_category_id")
    session = parse_session_context(session_payload, observed_at)
    locations = parse_location_group_meta(location_meta_payload)
    info_by_id = parse_location_info(location_info_payload, locations)
    if not isinstance(check_payloads, Mapping):
        raise RoomCatalogError("check_payloads must map location ids to responses")
    if any(isinstance(key, bool) or not isinstance(key, int) for key in check_payloads):
        raise RoomCatalogError("check_payloads keys must be integer location ids")
    expected_ids = {location.location_id for location in locations}
    actual_ids = set(check_payloads)
    if actual_ids != expected_ids:
        missing = expected_ids - actual_ids
        extra = actual_ids - expected_ids
        details: list[str] = []
        if missing:
            details.append("missing " + ",".join(str(value) for value in sorted(missing)))
        if extra:
            details.append("unexpected " + ",".join(str(value) for value in sorted(extra)))
        raise RoomCatalogError("Room check set is incomplete: " + "; ".join(details))
    if check_observed_ats is None:
        observed_by_id = {
            location_id: session.observed_at for location_id in expected_ids
        }
    else:
        if not isinstance(check_observed_ats, Mapping):
            raise RoomCatalogError(
                "check_observed_ats must map location ids to observation times"
            )
        if set(check_observed_ats) != expected_ids:
            raise RoomCatalogError(
                "Room check observation set must exactly match the live room set"
            )
        observed_by_id = {
            location_id: _normalize_observed_at(check_observed_ats[location_id])
            for location_id in expected_ids
        }

    rooms: list[RoomCatalogRoom] = []
    for location in locations:
        info = info_by_id[location.location_id]
        evidence = parse_horizon_issues(
            check_payloads[location.location_id],
            location.name,
            session.booking_horizon,
            observed_by_id[location.location_id],
            expected_global_horizon_minutes=session.global_horizon_minutes,
        )
        description_hint = parse_description_horizon_hint(info.description)
        if description_hint is not None and description_hint != evidence.horizon_minutes:
            raise RoomCatalogError(
                f"Description horizon for {location.name} disagrees with its authoritative cutoff"
            )
        room_types = (location.secondary_name,) if location.secondary_name else ()
        features = _dedupe_text(info.description_items + info.inventory_items)
        # Each check endpoint reports a moving absolute cutoff.  Normalize the
        # proven duration back to the session snapshot so every cached room is
        # internally comparable and no request-order drift leaks into policy.
        normalized_cutoff = session.observed_at + timedelta(
            minutes=evidence.horizon_minutes
        )
        rooms.append(
            RoomCatalogRoom(
                location_id=location.location_id,
                name=location.name,
                secondary_name=location.secondary_name,
                description=info.description,
                inventory=info.inventory,
                instrument_tags=_instrument_tags(info),
                room_type_tags=room_types,
                features=features,
                horizon_minutes=evidence.horizon_minutes,
                booking_cutoff=normalized_cutoff,
            )
        )
    catalog = RoomCatalog(
        version=CATALOG_VERSION,
        observed_at=session.observed_at,
        booking_horizon=session.booking_horizon,
        global_horizon_minutes=session.global_horizon_minutes,
        minimum_booking_minutes=session.minimum_booking_minutes,
        maximum_booking_minutes=session.maximum_booking_minutes,
        minimum_booking_gap_minutes=session.minimum_booking_gap_minutes,
        booking_category_id=category_id,
        rooms=tuple(rooms),
        fresh=True,
    )
    return catalog.require_fresh()


def _validate_tag_tuple(value: Any, label: str, *, json_list: bool) -> tuple[str, ...]:
    if json_list:
        if not isinstance(value, list):
            raise RoomCatalogError(f"{label} must be a JSON list")
        values = value
    else:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise RoomCatalogError(f"{label} must be a string sequence")
        values = value
    result: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(values):
        item = _text(raw, f"{label}[{index}]")
        key = item.casefold()
        if key in seen:
            raise RoomCatalogError(f"{label} contains duplicate value {item!r}")
        seen.add(key)
        result.append(item)
    return tuple(result)


def _catalog_to_dict(catalog: RoomCatalog) -> dict[str, Any]:
    return {
        "version": catalog.version,
        "observed_at": catalog.observed_at.isoformat(),
        "booking_horizon": catalog.booking_horizon.isoformat(),
        "global_horizon_minutes": catalog.global_horizon_minutes,
        "minimum_booking_minutes": catalog.minimum_booking_minutes,
        "maximum_booking_minutes": catalog.maximum_booking_minutes,
        "minimum_booking_gap_minutes": catalog.minimum_booking_gap_minutes,
        "booking_category_id": catalog.booking_category_id,
        "rooms": [
            {
                "location_id": room.location_id,
                "name": room.name,
                "secondary_name": room.secondary_name,
                "description": room.description,
                "inventory": room.inventory,
                "instrument_tags": list(room.instrument_tags),
                "room_type_tags": list(room.room_type_tags),
                "features": list(room.features),
                "horizon_minutes": room.horizon_minutes,
                "booking_cutoff": room.booking_cutoff.isoformat(),
            }
            for room in catalog.rooms
        ],
    }


def _catalog_from_dict(raw: Any) -> RoomCatalog:
    document = _mapping(raw, "room catalog cache")
    if set(document) != _CACHE_KEYS:
        raise RoomCatalogError("Room catalog cache has an unsupported schema")
    if document.get("version") != CATALOG_VERSION:
        raise RoomCatalogError(
            f"Unsupported room catalog cache version {document.get('version')!r}"
        )
    observed = _parse_aware_datetime(document.get("observed_at"), "cache.observed_at")
    if observed.second or observed.microsecond:
        raise RoomCatalogError("cache.observed_at must be exact to the minute")
    booking_horizon = _parse_aware_datetime(
        document.get("booking_horizon"), "cache.booking_horizon"
    )
    global_minutes = _minute_quantity(
        document.get("global_horizon_minutes"), "cache.global_horizon_minutes"
    )
    if _wall_clock_minutes(observed, booking_horizon, "cache global horizon") != global_minutes:
        raise RoomCatalogError("Cached global horizon duration is inconsistent")
    minimum = _minute_quantity(
        document.get("minimum_booking_minutes"), "cache.minimum_booking_minutes"
    )
    maximum = _minute_quantity(
        document.get("maximum_booking_minutes"), "cache.maximum_booking_minutes"
    )
    gap = _minute_quantity(
        document.get("minimum_booking_gap_minutes"),
        "cache.minimum_booking_gap_minutes",
        allow_zero=True,
    )
    if maximum < minimum:
        raise RoomCatalogError("Cached maximum booking length is shorter than its minimum")
    category_id = _positive_int(document.get("booking_category_id"), "cache.booking_category_id")
    raw_rooms = _list(document.get("rooms"), "cache.rooms")
    if not raw_rooms:
        raise RoomCatalogError("Cached room catalog is empty")
    rooms: list[RoomCatalogRoom] = []
    seen_ids: set[int] = set()
    seen_names: set[str] = set()
    for index, raw_room in enumerate(raw_rooms):
        item = _mapping(raw_room, f"cache.rooms[{index}]")
        if set(item) != _CACHE_ROOM_KEYS:
            raise RoomCatalogError(f"cache.rooms[{index}] has an unsupported schema")
        location_id = _positive_int(item.get("location_id"), f"cache.rooms[{index}].location_id")
        name = _text(item.get("name"), f"cache.rooms[{index}].name")
        secondary = _text(
            item.get("secondary_name"), f"cache.rooms[{index}].secondary_name", allow_empty=True
        )
        if location_id in seen_ids or name in seen_names:
            raise RoomCatalogError("Cached room catalog contains duplicate identities")
        seen_ids.add(location_id)
        seen_names.add(name)
        description = _text(
            item.get("description"), f"cache.rooms[{index}].description", allow_empty=True
        )
        inventory = _text(
            item.get("inventory"), f"cache.rooms[{index}].inventory", allow_empty=True
        )
        horizon_minutes = _minute_quantity(
            item.get("horizon_minutes"), f"cache.rooms[{index}].horizon_minutes"
        )
        cutoff = _parse_aware_datetime(
            item.get("booking_cutoff"), f"cache.rooms[{index}].booking_cutoff"
        )
        if _wall_clock_minutes(observed, cutoff, f"cache horizon for {name}") != horizon_minutes:
            raise RoomCatalogError(f"Cached cutoff and horizon disagree for {name}")
        if cutoff > booking_horizon:
            raise RoomCatalogError(f"Cached cutoff for {name} exceeds the global horizon")
        rooms.append(
            RoomCatalogRoom(
                location_id=location_id,
                name=name,
                secondary_name=secondary,
                description=description,
                inventory=inventory,
                instrument_tags=_validate_tag_tuple(
                    item.get("instrument_tags"),
                    f"cache.rooms[{index}].instrument_tags",
                    json_list=True,
                ),
                room_type_tags=_validate_tag_tuple(
                    item.get("room_type_tags"),
                    f"cache.rooms[{index}].room_type_tags",
                    json_list=True,
                ),
                features=_validate_tag_tuple(
                    item.get("features"),
                    f"cache.rooms[{index}].features",
                    json_list=True,
                ),
                horizon_minutes=horizon_minutes,
                booking_cutoff=cutoff,
            )
        )
    return RoomCatalog(
        version=CATALOG_VERSION,
        observed_at=observed,
        booking_horizon=booking_horizon,
        global_horizon_minutes=global_minutes,
        minimum_booking_minutes=minimum,
        maximum_booking_minutes=maximum,
        minimum_booking_gap_minutes=gap,
        booking_category_id=category_id,
        rooms=tuple(rooms),
        fresh=False,
    )


def save_catalog(catalog: RoomCatalog, path: Path = ROOM_CATALOG_FILE) -> None:
    """Atomically cache a complete live snapshot under an interprocess lock."""

    if not isinstance(catalog, RoomCatalog):
        raise TypeError("catalog must be a RoomCatalog instance")
    catalog.require_fresh()
    # Round-trip validation proves the exact on-disk representation before any
    # replacement.  The loaded form is intentionally display-only.
    payload = _catalog_to_dict(catalog)
    _catalog_from_dict(payload)
    cache_path = Path(path)
    try:
        with InterProcessFileLock(cache_path.with_suffix(cache_path.suffix + ".lock")):
            atomic_write_json(cache_path, payload, backup=True)
    except SettingsError as exc:
        raise RoomCatalogError(f"Could not safely persist room catalog: {cache_path}") from exc


def load_cached_catalog(
    path: Path = ROOM_CATALOG_FILE,
    *,
    missing_ok: bool = True,
) -> RoomCatalog | None:
    """Load a strict cache as display-only; it is never considered fresh."""

    cache_path = Path(path)
    if not cache_path.exists():
        if missing_ok:
            return None
        raise RoomCatalogError(f"Room catalog cache does not exist: {cache_path}")
    try:
        with InterProcessFileLock(cache_path.with_suffix(cache_path.suffix + ".lock")):
            with open(cache_path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
    except RoomCatalogError:
        raise
    except (SettingsError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RoomCatalogError(f"Room catalog cache is unreadable: {cache_path}") from exc
    return _catalog_from_dict(raw)


# Clear aliases for callers that use the longer noun in their import names.
load_room_catalog = load_cached_catalog
save_room_catalog = save_catalog
load_catalog = load_cached_catalog


def as_live_metadata(catalog: RoomCatalog) -> dict[str, dict[str, list[str]]]:
    """Function-form adapter for callers that do not retain catalog methods."""

    if not isinstance(catalog, RoomCatalog):
        raise TypeError("catalog must be a RoomCatalog instance")
    return catalog.as_live_metadata()


def _format_site_iso(value: datetime) -> str:
    local = value.astimezone(SITE_TIMEZONE)
    return local.isoformat(timespec="milliseconds")


def _day_time(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, datetime_time(hour, minute), tzinfo=SITE_TIMEZONE)


def _group_meta_path(observed_at: datetime) -> str:
    day = observed_at.astimezone(SITE_TIMEZONE).date()
    return (
        f"/services/v2/locations/location_group_ids={LOCATION_GROUP_ID}"
        f";start_at={_format_site_iso(_day_time(day, 7))}"
        f";end_at={_format_site_iso(_day_time(day, 22, 59))}"
        f";current_date={_format_site_iso(_day_time(day, 0))}/meta"
    )


def _location_info_path(locations: Sequence[LocationMeta], observed_at: datetime) -> str:
    ids = ",".join(str(location.location_id) for location in locations)
    day = observed_at.astimezone(SITE_TIMEZONE).date()
    return (
        f"/services/v2/locations/location_ids={ids}"
        f";current_date={_format_site_iso(_day_time(day, 0))}/info"
    )


def _trusted_response_url(actual: Any, expected_path: str) -> bool:
    if not isinstance(actual, str):
        return False
    try:
        parsed = urlsplit(actual)
        expected = urlsplit(ASIMUT_ORIGIN + expected_path)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "rwcmd.asimut.net"
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and unquote(parsed.path) == unquote(expected.path)
        and parsed.query == expected.query
        and not parsed.fragment
    )


def _api_json(response: Any, expected_path: str, label: str) -> Any:
    if getattr(response, "ok", None) is not True:
        raise RoomCatalogError(f"{label} request did not return HTTP success")
    if not _trusted_response_url(getattr(response, "url", None), expected_path):
        raise RoomCatalogError(f"{label} request was redirected or answered by an unexpected URL")
    try:
        return response.json()
    except Exception as exc:
        # Deliberately do not read or interpolate response text: event-default
        # responses can contain participant objects.
        raise RoomCatalogError(f"{label} response was not valid JSON") from exc


def _get_json(page: Any, path: str, label: str) -> Any:
    try:
        response = page.request.get(ASIMUT_ORIGIN + path)
    except Exception as exc:
        raise RoomCatalogError(f"{label} request failed") from exc
    return _api_json(response, path, label)


def _post_json(page: Any, path: str, body: Mapping[str, Any], label: str) -> Any:
    if path == "/services/v2/event/type=save" or path.endswith("/type=save"):
        raise RoomCatalogError("Room-catalog discovery must never call event/type=save")
    try:
        response = page.request.post(ASIMUT_ORIGIN + path, data=body)
    except Exception as exc:
        raise RoomCatalogError(f"{label} request failed") from exc
    return _api_json(response, path, label)


def _ceil_quarter_hour(value: datetime) -> datetime:
    local = value.astimezone(SITE_TIMEZONE)
    minute_of_day = local.hour * 60 + local.minute
    if local.second or local.microsecond:
        minute_of_day += 1
    rounded = ((minute_of_day + 14) // 15) * 15
    next_day, minute_of_day = divmod(rounded, 24 * 60)
    target_day = local.date() + timedelta(days=next_day)
    return _day_time(target_day, minute_of_day // 60, minute_of_day % 60)


def _new_event_form_path(
    *, category_id: int, location_id: int, start: datetime
) -> str:
    query = urlencode(
        {
            "eventId": "0",
            "prefillTime": "true",
            "start": _format_site_iso(start),
            "categoryId": str(category_id),
            "locationId": str(location_id),
        }
    )
    return "/event?" + query


def _is_exact_no_save_form_url(
    value: Any, *, category_id: int, location_id: int, start: datetime
) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return False
    if (
        parsed.scheme != "https"
        or parsed.hostname != "rwcmd.asimut.net"
        or parsed.port is not None
        or parsed.path != "/event"
        or parsed.fragment
    ):
        return False
    if len(pairs) != 5 or len({key for key, _value in pairs}) != 5:
        return False
    values = dict(pairs)
    if values != {
        "eventId": "0",
        "prefillTime": "true",
        "start": _format_site_iso(start),
        "categoryId": str(category_id),
        "locationId": str(location_id),
    }:
        return False
    return True


def _parse_eventdefault_template(
    payload: Any,
    *,
    category_id: int,
    location: LocationMeta,
    expected_start: datetime,
) -> dict[str, Any]:
    response = _response_object(payload, "event default")
    _require_success(response, "event default", True)
    eventdefault = _mapping(response.get("eventdefault"), "event default.response.eventdefault")
    events = _list(eventdefault.get("events"), "event default.response.eventdefault.events")
    if len(events) != 1:
        raise RoomCatalogError("Event default must contain exactly one unsaved event")
    event = _mapping(events[0], "event default event")
    if event.get("id") != 0 or event.get("ca") != category_id:
        raise RoomCatalogError("Event default identity does not match the requested category")
    event_start = _parse_aware_datetime(event.get("st"), "event default event.st")
    if _minute_key(event_start) != _minute_key(expected_start):
        raise RoomCatalogError("Event default start does not match the requested form")
    rooms = _list(event.get("rs"), "event default event.rs")
    if len(rooms) != 1:
        raise RoomCatalogError("Event default must contain exactly one requested room")
    room = _mapping(rooms[0], "event default event.rs[0]")
    if room.get("id") != location.location_id:
        raise RoomCatalogError("Event default room does not match the requested form")
    # The live checker needs these participant-bearing arrays, but they remain
    # only in this local template and its short-lived per-room deep copies.
    _list(event.get("pe"), "event default event.pe")
    _list(event.get("ps"), "event default event.ps")
    return copy.deepcopy(dict(event))


def _capture_eventdefault(
    page: Any,
    *,
    category_id: int,
    location: LocationMeta,
    start: datetime,
) -> dict[str, Any]:
    form_path = _new_event_form_path(
        category_id=category_id, location_id=location.location_id, start=start
    )

    def is_eventdefault(response: Any) -> bool:
        return _trusted_response_url(getattr(response, "url", None), EVENTDEFAULT_PATH)

    try:
        with page.expect_response(is_eventdefault, timeout=15000) as pending:
            page.goto(ASIMUT_ORIGIN + form_path, wait_until="domcontentloaded")
        response = pending.value
    except Exception as exc:
        raise RoomCatalogError("Could not capture the no-Save event-default template") from exc
    if not _is_exact_no_save_form_url(
        getattr(page, "url", None),
        category_id=category_id,
        location_id=location.location_id,
        start=start,
    ):
        raise RoomCatalogError("Event-default capture did not remain on the exact unsaved form")
    payload = _api_json(response, EVENTDEFAULT_PATH, "event default")
    return _parse_eventdefault_template(
        payload,
        category_id=category_id,
        location=location,
        expected_start=start,
    )


def _probe_payload(
    template: Mapping[str, Any],
    *,
    category_id: int,
    location: LocationMeta,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    event = copy.deepcopy(dict(template))
    event["id"] = 0
    event["ca"] = category_id
    event["st"] = _format_site_iso(start)
    event["en"] = _format_site_iso(end)
    display_name = (
        f"{location.name} ({location.secondary_name})"
        if location.secondary_name
        else location.name
    )
    event["rs"] = [{"id": location.location_id, "dn": display_name}]
    return {
        "event": event,
        "booking_type": "single",
        "time_period_id": 0,
        "weekdays": [start.astimezone(SITE_TIMEZONE).weekday()],
    }


def _request_minute_candidates(
    started_at: datetime,
    finished_at: datetime,
) -> tuple[datetime, ...]:
    """Return the distinct local minute buckets crossed by one request."""

    started = _normalize_observed_at(started_at)
    finished = _normalize_observed_at(finished_at)
    if finished < started:
        raise RoomCatalogError("A live policy request finished before it started")
    minute_count = int(
        (
            finished.replace(tzinfo=None) - started.replace(tzinfo=None)
        ).total_seconds()
        // 60
    )
    if minute_count > 1:
        raise RoomCatalogError(
            "A live policy request crossed more than one minute boundary"
        )
    return tuple(
        started + timedelta(minutes=offset)
        for offset in range(minute_count + 1)
    )


def refresh_from_site(
    page: Any,
    *,
    path: Path = ROOM_CATALOG_FILE,
    save: bool = True,
    observed_at: datetime | None = None,
) -> RoomCatalog:
    """Build and optionally cache a complete catalog from an authenticated page.

    The function performs only read-only GETs, one navigation to an ``eventId=0``
    form, and direct ``event/type=check`` requests.  No partial snapshot is
    returned or persisted.
    """

    if observed_at is None:
        session_started_at = datetime.now(tz=SITE_TIMEZONE)
        session_payload = _get_json(page, SESSION_CONTEXT_PATH, "session context")
        session_finished_at = datetime.now(tz=SITE_TIMEZONE)
        session_candidates = _request_minute_candidates(
            session_started_at,
            session_finished_at,
        )
        parsed_sessions: list[SessionPolicy] = []
        session_errors: list[RoomCatalogError] = []
        for candidate in session_candidates:
            try:
                parsed_sessions.append(
                    parse_session_context(session_payload, candidate)
                )
            except RoomCatalogError as exc:
                session_errors.append(exc)
        if len(parsed_sessions) != 1:
            if not parsed_sessions and session_errors:
                raise RoomCatalogError(
                    "Session policy could not be aligned to its request minute: "
                    f"{session_errors[-1]}"
                ) from session_errors[-1]
            raise RoomCatalogError(
                "Session policy matched more than one request minute"
            )
        session = parsed_sessions[0]
    else:
        session_payload = _get_json(page, SESSION_CONTEXT_PATH, "session context")
        session = parse_session_context(session_payload, observed_at)
    category_payload = _get_json(page, CATEGORIES_PATH, "categories")
    category_id = parse_booking_category(category_payload)

    meta_path = _group_meta_path(session.observed_at)
    meta_payload = _get_json(page, meta_path, "location group metadata")
    locations = parse_location_group_meta(meta_payload)
    info_path = _location_info_path(locations, session.observed_at)
    info_payload = _get_json(page, info_path, "location info")
    # Validate the combined response before using a room identity in a form URL.
    parse_location_info(info_payload, locations)

    template_start = _ceil_quarter_hour(
        session.observed_at + timedelta(minutes=session.minimum_booking_minutes)
    )
    if template_start >= session.booking_horizon:
        raise RoomCatalogError("Global booking horizon is too short to load a safe event template")
    template = _capture_eventdefault(
        page,
        category_id=category_id,
        location=locations[0],
        start=template_start,
    )

    probe_start = _ceil_quarter_hour(session.booking_horizon + timedelta(days=1))
    if probe_start.date() <= session.booking_horizon.date():
        raise RoomCatalogError("Horizon probe must use a date beyond the global horizon")
    probe_end = probe_start + timedelta(minutes=session.maximum_booking_minutes)
    checks: dict[int, Any] = {}
    check_observed_ats: dict[int, datetime] = {}
    try:
        for location in locations:
            body = _probe_payload(
                template,
                category_id=category_id,
                location=location,
                start=probe_start,
                end=probe_end,
            )
            check_started_at = observed_at or datetime.now(tz=SITE_TIMEZONE)
            check_payload = _post_json(
                page,
                EVENT_CHECK_PATH,
                body,
                f"event check for {location.name}",
            )
            check_finished_at = observed_at or datetime.now(tz=SITE_TIMEZONE)
            checks[location.location_id] = check_payload
            matching_observations: list[datetime] = []
            check_errors: list[RoomCatalogError] = []
            for candidate in _request_minute_candidates(
                check_started_at,
                check_finished_at,
            ):
                try:
                    parse_horizon_issues(
                        check_payload,
                        location.name,
                        session.booking_horizon,
                        candidate,
                        expected_global_horizon_minutes=(
                            session.global_horizon_minutes
                        ),
                    )
                    matching_observations.append(candidate)
                except RoomCatalogError as exc:
                    check_errors.append(exc)
            if len(matching_observations) != 1:
                if not matching_observations and check_errors:
                    raise RoomCatalogError(
                        f"Event check for {location.name} could not be aligned "
                        f"to its request minute: {check_errors[-1]}"
                    ) from check_errors[-1]
                raise RoomCatalogError(
                    f"Event check for {location.name} matched more than one "
                    "request minute"
                )
            check_observed_ats[location.location_id] = matching_observations[0]
            # Do not retain the participant-bearing request copy after the call.
            del body
    finally:
        # Participant arrays from eventdefault are intentionally ephemeral.
        del template

    catalog = build_catalog(
        session_payload=session_payload,
        location_meta_payload=meta_payload,
        location_info_payload=info_payload,
        check_payloads=checks,
        observed_at=session.observed_at,
        booking_category_id=category_id,
        check_observed_ats=check_observed_ats,
    )
    if save:
        save_catalog(catalog, path)
    return catalog


__all__ = [
    "CATALOG_VERSION",
    "ROOM_CATALOG_FILE",
    "SITE_TIMEZONE",
    "HorizonEvidence",
    "LocationInfo",
    "LocationMeta",
    "RoomCatalog",
    "RoomCatalogError",
    "RoomCatalogRoom",
    "SessionPolicy",
    "as_live_metadata",
    "build_catalog",
    "load_catalog",
    "load_cached_catalog",
    "load_room_catalog",
    "normalize_html",
    "normalize_html_items",
    "parse_booking_category",
    "parse_description_horizon_hint",
    "parse_horizon_issues",
    "parse_location_group_meta",
    "parse_location_info",
    "parse_session_context",
    "refresh_from_site",
    "save_catalog",
    "save_room_catalog",
]
