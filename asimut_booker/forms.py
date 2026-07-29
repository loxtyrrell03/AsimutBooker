"""Validated snapshots of Asimut create/edit and arrangement pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from bs4 import BeautifulSoup

from .errors import PageContractError

_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")
_ARRANGEMENT_TIME_RE = re.compile(
    r"\b(\d{1,2}):(\d{2})\s*[-\u2012\u2013\u2014]\s*(\d{1,2}):(\d{2})\b"
)
_EVENT_ID_RE = re.compile(r"[?&]eventId=(\d+)")


def _minute_value(value: str, label: str) -> int:
    match = _TIME_RE.match(value)
    if not match:
        raise PageContractError(f"invalid {label}: {value!r}")
    minute = int(match.group(1)) * 60 + int(match.group(2))
    if not (0 <= minute <= 24 * 60):
        raise PageContractError(f"{label} lies outside a day")
    return minute


def _room_name(value: str) -> str:
    clean = " ".join(value.replace("\xa0", " ").split())
    return clean.split(" (", 1)[0].strip()


@dataclass(frozen=True)
class BookingFormSnapshot:
    event_date: date
    start_minute: int
    end_minute: int
    room_name: str
    save_enabled: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ArrangementSnapshot:
    event_id: str
    event_date: date
    start_minute: int
    end_minute: int
    room_name: str


def parse_booking_form_html(html: str) -> BookingFormSnapshot:
    soup = BeautifulSoup(html, "html.parser")
    date_input = soup.select_one('[data-cy="event-edit-date-input"]')
    start_input = soup.select_one('[data-cy="time-range-start-time"]')
    end_input = soup.select_one('[data-cy="time-range-end-time"]')
    location_input = soup.select_one('input[aria-label="Event location"]')
    save = soup.select_one("button.save-button")
    if None in (date_input, start_input, end_input, location_input, save):
        raise PageContractError("booking form is missing required semantic controls")
    try:
        event_date = datetime.strptime(
            str(date_input.get("value") or "").split()[-1], "%d/%m/%Y"
        ).date()
    except (ValueError, IndexError) as exc:
        raise PageContractError("booking form date is malformed") from exc
    start = _minute_value(str(start_input.get("value") or ""), "start time")
    end = _minute_value(str(end_input.get("value") or ""), "end time")
    if end <= start:
        raise PageContractError("booking form has a non-positive duration")
    room = _room_name(str(location_input.get("value") or ""))
    if not room:
        raise PageContractError("booking form has no room")
    warnings = tuple(
        " ".join((node.parent.get_text(" ", strip=True) if node.parent else "").split())
        for node in soup.select('[aria-label="Warning: Booking cannot be saved"]')
    )
    return BookingFormSnapshot(
        event_date=event_date,
        start_minute=start,
        end_minute=end,
        room_name=room,
        save_enabled=save.get("aria-label") == "Save event" and not warnings,
        warnings=warnings,
    )


def parse_arrangement_html(html: str, url: str) -> ArrangementSnapshot:
    event_match = _EVENT_ID_RE.search(url)
    if not event_match or event_match.group(1) == "0":
        raise PageContractError("arrangement URL has no persisted event ID")
    soup = BeautifulSoup(html, "html.parser")
    location = soup.select_one('a[href*="/agenda?locationIds="]')
    if location is None:
        raise PageContractError("arrangement has no location")
    text = soup.get_text(" ", strip=True)
    time_match = _ARRANGEMENT_TIME_RE.search(text)
    date_match = re.search(r"\b(\d{2})/(\d{2})/(\d{2})\b", text)
    if time_match is None or date_match is None:
        raise PageContractError("arrangement date/time is missing")
    year = 2000 + int(date_match.group(3))
    event_date = date(year, int(date_match.group(2)), int(date_match.group(1)))
    start = int(time_match.group(1)) * 60 + int(time_match.group(2))
    end = int(time_match.group(3)) * 60 + int(time_match.group(4))
    if end <= start:
        raise PageContractError("arrangement duration is invalid")
    return ArrangementSnapshot(
        event_id=event_match.group(1),
        event_date=event_date,
        start_minute=start,
        end_minute=end,
        room_name=_room_name(location.get_text(" ", strip=True)),
    )
