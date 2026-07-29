"""Semantic parser for the signed-in Asimut agenda."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

from .errors import PageContractError

_EVENT_ID_RE = re.compile(r"^event_(\d+)$")
_TIME_RANGE_RE = re.compile(
    r"^\s*(\d{1,2}):(\d{2})\s*[-\u2012\u2013\u2014]\s*"
    r"(\d{1,2}):(\d{2})\s*$"
)
_QUOTA_RE = re.compile(r"^\s*(\d{1,3}):(\d{2})\s*$")


@dataclass(frozen=True)
class AgendaEvent:
    event_id: str
    event_date: date
    start_minute: int
    end_minute: int
    title: str
    room_name: str | None
    location_id: str | None
    status: str
    arrangement_href: str | None

    @property
    def is_reservation(self) -> bool:
        return self.title.casefold() == "reservation"

    @property
    def is_cancelled(self) -> bool:
        return self.status == "cancelled"


@dataclass(frozen=True)
class AgendaObservation:
    first_date: date
    last_date: date
    day_count: int
    events: tuple[AgendaEvent, ...]
    rolling_quota_remaining: int | None
    contract_version: str = "asimut-agenda-v1"

    def find_exact(
        self,
        *,
        event_id: str | None = None,
        event_date: date | None = None,
        room_name: str | None = None,
        start_minute: int | None = None,
        end_minute: int | None = None,
    ) -> list[AgendaEvent]:
        matches: list[AgendaEvent] = []
        for item in self.events:
            if event_id is not None and item.event_id != str(event_id):
                continue
            if event_date is not None and item.event_date != event_date:
                continue
            if room_name is not None and item.room_name != room_name:
                continue
            if start_minute is not None and item.start_minute != start_minute:
                continue
            if end_minute is not None and item.end_minute != end_minute:
                continue
            if item.is_cancelled:
                continue
            matches.append(item)
        return matches


def _parse_quota(soup: BeautifulSoup) -> int | None:
    nodes = soup.select('[data-cy="rolling-quota"] .quota-value')
    if not nodes:
        return None
    values: set[int] = set()
    for node in nodes:
        match = _QUOTA_RE.match(node.get_text(" ", strip=True))
        if not match:
            raise PageContractError("agenda rolling quota is malformed")
        values.add(int(match.group(1)) * 60 + int(match.group(2)))
    if len(values) != 1:
        raise PageContractError(f"agenda contains conflicting quota values: {values}")
    return values.pop()


def _parse_day(value: str | None) -> date:
    if not value:
        raise PageContractError("agenda day is missing day-header")
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise PageContractError(f"invalid agenda day-header: {value!r}") from exc


def _minutes(hour: str, minute: str) -> int:
    result = int(hour) * 60 + int(minute)
    if not (0 <= result <= 24 * 60):
        raise PageContractError("agenda event time is outside a day")
    return result


def _status_from_event(root: Tag) -> tuple[str, str | None]:
    arrangement = root.select_one('a[href*="/arrangement?eventId="]')
    aria = (arrangement.get("aria-label") if arrangement else "") or ""
    folded = aria.casefold()
    classes = " ".join(root.get("class") or []).casefold()
    if "cancelled" in folded or "canceled" in folded or "cancelled" in classes:
        status = "cancelled"
    elif "not yet confirmed" in folded or "provisional" in folded:
        status = "provisional"
    elif "confirmed" in folded:
        status = "confirmed"
    else:
        status = "unknown"
    href = str(arrangement.get("href")) if arrangement and arrangement.get("href") else None
    return status, href


def _location_id(link: Tag | None) -> str | None:
    if link is None or not link.get("href"):
        return None
    query = parse_qs(urlparse(str(link.get("href"))).query)
    values = query.get("locationIds") or query.get("locationId")
    return values[0] if values else None


def _room_name(text: str) -> str | None:
    clean = " ".join(text.replace("\xa0", " ").split())
    if not clean:
        return None
    return clean.split(" (", 1)[0].strip() or None


def parse_agenda_html(
    html: str,
    *,
    required_start: date | None = None,
    required_end: date | None = None,
) -> AgendaObservation:
    """Parse events using their stable day and event identity attributes."""

    soup = BeautifulSoup(html, "html.parser")
    day_nodes = soup.select("[day-header]")
    if not day_nodes:
        raise PageContractError("agenda has no day-header nodes (possibly logged out)")

    parsed_days = [_parse_day(str(node.get("day-header") or "")) for node in day_nodes]
    unique_days = sorted(set(parsed_days))
    first_date = unique_days[0]
    last_date = unique_days[-1]
    if required_start is not None and first_date > required_start:
        raise PageContractError(f"agenda starts at {first_date}, after required {required_start}")
    if required_end is not None and last_date < required_end:
        raise PageContractError(f"agenda ends at {last_date}, before required {required_end}")

    events_by_id: dict[str, AgendaEvent] = {}
    for day_node, event_date in zip(day_nodes, parsed_days, strict=True):
        for root in day_node.select("[data-cy]"):
            match = _EVENT_ID_RE.match(str(root.get("data-cy") or ""))
            if not match:
                continue
            event_id = match.group(1)
            time_node = root.select_one('[data-cy="event-datetime"]')
            title_node = root.select_one('[data-cy="event-display-name"]')
            if time_node is None or title_node is None:
                raise PageContractError(f"agenda event {event_id} is incomplete")
            time_match = _TIME_RANGE_RE.match(time_node.get_text(" ", strip=True))
            if not time_match:
                raise PageContractError(f"agenda event {event_id} has invalid time range")
            start_minute = _minutes(time_match.group(1), time_match.group(2))
            end_minute = _minutes(time_match.group(3), time_match.group(4))
            if end_minute <= start_minute:
                raise PageContractError(f"agenda event {event_id} has a non-positive duration")
            title = " ".join(title_node.get_text(" ", strip=True).split())
            if not title:
                raise PageContractError(f"agenda event {event_id} has no title")
            location_link = root.select_one('[data-cy="event-location-link"]')
            status, arrangement_href = _status_from_event(root)
            item = AgendaEvent(
                event_id=event_id,
                event_date=event_date,
                start_minute=start_minute,
                end_minute=end_minute,
                title=title,
                room_name=(
                    _room_name(location_link.get_text(" ", strip=True)) if location_link else None
                ),
                location_id=_location_id(location_link),
                status=status,
                arrangement_href=arrangement_href,
            )
            previous = events_by_id.get(event_id)
            if previous is not None and previous != item:
                raise PageContractError(f"responsive agenda copies disagree for event {event_id}")
            events_by_id[event_id] = item

    return AgendaObservation(
        first_date=first_date,
        last_date=last_date,
        day_count=len(unique_days),
        events=tuple(
            sorted(
                events_by_id.values(),
                key=lambda item: (
                    item.event_date,
                    item.start_minute,
                    item.end_minute,
                    item.event_id,
                ),
            )
        ),
        rolling_quota_remaining=_parse_quota(soup),
    )
