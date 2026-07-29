"""Parser for Asimut's current SVG room overview.

The live RWCMD overview exposes stable semantic identity attributes:

* ``data-cy="display-date"`` for the observed date;
* ``data-location-id`` for room rows;
* ``data-event-id`` and ``data-category-id`` for occupied rectangles; and
* an SVG coordinate system shared by the time legend and event rectangles.

Availability is derived from those attributes and calibrated against the
rendered hour labels.  CSS colors, viewport Y proximity and fixed
``6.25%/hour`` assumptions are intentionally not used.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

from bs4 import BeautifulSoup, Tag

from .errors import PageContractError

_DISPLAY_DATE_FORMATS = ("%A, %d %B %Y", "%A, %e %B %Y")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_LEFT_RE = re.compile(r"(?:^|;)\s*left\s*:\s*(-?\d+(?:\.\d+)?)%")
_TIME_VALUE_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


@dataclass(frozen=True, order=True)
class ObservedInterval:
    """Half-open local-wall-clock interval in integer minutes."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if not (0 <= self.start < self.end <= 24 * 60):
            raise ValueError(f"invalid interval {self.start}-{self.end}")

    @property
    def duration(self) -> int:
        return self.end - self.start

    def contains(self, start: int, end: int) -> bool:
        return self.start <= start and end <= self.end

    def overlaps(self, other: "ObservedInterval") -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class GridCalibration:
    """Mapping between SVG x units and local minutes."""

    origin_minute: int
    minutes_per_unit: float
    width_units: float
    height_units: float
    row_height_units: float

    @property
    def end_minute(self) -> int:
        return int(round(self.origin_minute + self.width_units * self.minutes_per_unit))

    def x_to_minute(self, x_value: float) -> int:
        return int(round(self.origin_minute + x_value * self.minutes_per_unit))

    def minute_to_x(self, minute: int) -> float:
        return (minute - self.origin_minute) / self.minutes_per_unit


@dataclass(frozen=True)
class ObservedEventBlock:
    event_id: str
    location_id: str
    category_id: str | None
    interval: ObservedInterval


@dataclass(frozen=True)
class RoomOverview:
    name: str
    location_id: str
    description: str | None
    row_index: int
    closed: tuple[ObservedInterval, ...]
    events: tuple[ObservedEventBlock, ...]
    free: tuple[ObservedInterval, ...]

    def is_visually_free(self, start_minute: int, end_minute: int) -> bool:
        return any(slot.contains(start_minute, end_minute) for slot in self.free)


@dataclass(frozen=True)
class OverviewObservation:
    observed_date: date
    rooms: tuple[RoomOverview, ...]
    rolling_quota_remaining: int | None
    peak_quota_remaining: int | None
    calibration: GridCalibration
    contract_version: str = "asimut-svg-v1"

    @property
    def rooms_by_name(self) -> dict[str, RoomOverview]:
        return {room.name: room for room in self.rooms}

    @property
    def rooms_by_id(self) -> dict[str, RoomOverview]:
        return {room.location_id: room for room in self.rooms}


def _parse_float(value: str | None, field: str) -> float:
    if value is None:
        raise PageContractError(f"overview is missing {field}")
    match = _NUMBER_RE.search(value)
    if not match:
        raise PageContractError(f"overview has invalid {field}: {value!r}")
    parsed = float(match.group(0))
    if not math.isfinite(parsed):
        raise PageContractError(f"overview has non-finite {field}")
    return parsed


def _parse_display_date(text: str) -> date:
    clean = " ".join(text.split())
    # Windows does not support ``%e``; the first format accepts zero-padded and
    # non-padded days through Python's strptime implementation.
    for fmt in _DISPLAY_DATE_FORMATS[:1]:
        try:
            return datetime.strptime(clean, fmt).date()
        except ValueError:
            continue
    raise PageContractError(f"unrecognised overview date: {clean!r}")


def _parse_quota(root: BeautifulSoup, data_cy: str) -> int | None:
    nodes = root.select(f'[data-cy="{data_cy}"] .quota-value')
    if not nodes:
        return None
    values: set[int] = set()
    for node in nodes:
        match = _TIME_VALUE_RE.match(node.get_text(" ", strip=True))
        if not match:
            raise PageContractError(f"invalid {data_cy} value")
        values.add(int(match.group(1)) * 60 + int(match.group(2)))
    if len(values) != 1:
        raise PageContractError(f"conflicting responsive {data_cy} values: {values}")
    return values.pop()


def _merge(intervals: Iterable[ObservedInterval]) -> tuple[ObservedInterval, ...]:
    ordered = sorted(intervals)
    if not ordered:
        return ()
    merged: list[ObservedInterval] = [ordered[0]]
    for item in ordered[1:]:
        previous = merged[-1]
        if item.start <= previous.end:
            merged[-1] = ObservedInterval(previous.start, max(previous.end, item.end))
        else:
            merged.append(item)
    return tuple(merged)


def _complement(
    blocked: Iterable[ObservedInterval],
    start_minute: int,
    end_minute: int,
) -> tuple[ObservedInterval, ...]:
    cursor = start_minute
    free: list[ObservedInterval] = []
    for interval in _merge(blocked):
        if interval.end <= start_minute or interval.start >= end_minute:
            continue
        clipped_start = max(start_minute, interval.start)
        clipped_end = min(end_minute, interval.end)
        if cursor < clipped_start:
            free.append(ObservedInterval(cursor, clipped_start))
        cursor = max(cursor, clipped_end)
    if cursor < end_minute:
        free.append(ObservedInterval(cursor, end_minute))
    return tuple(free)


def _calibrate(svg: Tag, legend: Tag, room_count: int) -> GridCalibration:
    view_box = svg.get("viewBox") or svg.get("viewbox")
    if not view_box:
        raise PageContractError("SVG grid has no viewBox")
    parts = [float(part) for part in _NUMBER_RE.findall(str(view_box))]
    if len(parts) != 4:
        raise PageContractError(f"invalid SVG viewBox: {view_box!r}")
    _, _, width, height = parts
    if width <= 0 or height <= 0:
        raise PageContractError("SVG grid has non-positive dimensions")

    anchors: list[tuple[float, int]] = []
    for label in legend.find_all("p"):
        text = label.get_text(strip=True)
        if not text.isdigit():
            continue
        style = str(label.get("style") or "")
        match = _LEFT_RE.search(style)
        if not match:
            continue
        x_units = float(match.group(1)) / 100.0 * width
        anchors.append((x_units, int(text) * 60))

    if len(anchors) < 2:
        raise PageContractError("time legend has fewer than two calibrated hour labels")
    anchors.sort()
    slopes: list[float] = []
    for (x1, m1), (x2, m2) in zip(anchors, anchors[1:], strict=False):
        if x2 <= x1 or m2 <= m1:
            raise PageContractError("time legend labels are not strictly increasing")
        slopes.append((m2 - m1) / (x2 - x1))
    minutes_per_unit = sum(slopes) / len(slopes)
    if any(abs(value - minutes_per_unit) > 0.02 for value in slopes):
        raise PageContractError("time legend uses inconsistent SVG scaling")
    origins = [minute - x * minutes_per_unit for x, minute in anchors]
    origin = sum(origins) / len(origins)
    if any(abs(value - origin) > 1.0 for value in origins):
        raise PageContractError("time legend has inconsistent origins")
    origin_minute = int(round(origin))
    end_minute = int(round(origin_minute + width * minutes_per_unit))
    if not (0 <= origin_minute < end_minute <= 24 * 60):
        raise PageContractError(f"calibrated grid range is unsafe: {origin_minute}-{end_minute}")

    row_height = height / room_count
    if row_height < 5:
        raise PageContractError("room rows are implausibly small")
    return GridCalibration(
        origin_minute=origin_minute,
        minutes_per_unit=minutes_per_unit,
        width_units=width,
        height_units=height,
        row_height_units=row_height,
    )


def _rect_interval(rect: Tag, calibration: GridCalibration) -> ObservedInterval:
    x_value = _parse_float(rect.get("x"), "rectangle x")
    width_value = _parse_float(rect.get("width"), "rectangle width")
    if width_value <= 0:
        raise PageContractError("overview rectangle has non-positive width")
    start = calibration.x_to_minute(x_value)
    end = calibration.x_to_minute(x_value + width_value)
    start = max(calibration.origin_minute, start)
    end = min(calibration.end_minute, end)
    if start >= end:
        raise PageContractError("overview rectangle lies outside the calibrated grid")
    return ObservedInterval(start, end)


def parse_overview_html(
    html: str,
    *,
    expected_date: date | None = None,
    minimum_rooms: int = 10,
) -> OverviewObservation:
    """Parse and validate a complete Asimut overview HTML document.

    A failed invariant raises :class:`PageContractError`; it never returns an
    empty "available" calendar.
    """

    soup = BeautifulSoup(html, "html.parser")
    date_node = soup.select_one('[data-cy="display-date"]')
    if date_node is None:
        raise PageContractError("overview date marker is missing (possibly logged out)")
    observed_date = _parse_display_date(date_node.get_text(" ", strip=True))
    if expected_date is not None and observed_date != expected_date:
        raise PageContractError(
            f"requested {expected_date.isoformat()} but overview shows {observed_date.isoformat()}"
        )

    svg = soup.select_one("#svg-grid")
    legend_x = soup.select_one("#legend-x-inner")
    legend_y = soup.select_one("#legend-y-inner")
    if svg is None or legend_x is None or legend_y is None:
        raise PageContractError("overview SVG or legends are missing")

    room_nodes = legend_y.select("a[data-location-id]")
    if len(room_nodes) < minimum_rooms:
        raise PageContractError(
            f"overview exposes only {len(room_nodes)} rooms; expected at least {minimum_rooms}"
        )

    room_meta: list[tuple[str, str, str | None]] = []
    seen_names: set[str] = set()
    seen_ids: set[str] = set()
    for node in room_nodes:
        location_id = str(node.get("data-location-id") or "").strip()
        primary = node.find("span")
        name = primary.get_text(" ", strip=True) if primary else ""
        secondary = node.select_one(".secondary-name")
        description = None
        if secondary:
            description = (
                secondary.get_text(" ", strip=True).replace("\xa0", " ").strip().strip("()")
            )
        if not location_id or not name:
            raise PageContractError("room row is missing identity")
        if name in seen_names or location_id in seen_ids:
            raise PageContractError(f"duplicate room identity: {name}/{location_id}")
        seen_names.add(name)
        seen_ids.add(location_id)
        room_meta.append((name, location_id, description or None))

    calibration = _calibrate(svg, legend_x, len(room_meta))
    closed_by_room: dict[str, list[ObservedInterval]] = {
        location_id: [] for _, location_id, _ in room_meta
    }
    events_by_room: dict[str, list[ObservedEventBlock]] = {
        location_id: [] for _, location_id, _ in room_meta
    }

    closed_group = svg.select_one("#closed-hours")
    if closed_group is None:
        raise PageContractError("overview closed-hours layer is missing")
    for rect in closed_group.select("rect"):
        y_value = _parse_float(rect.get("y"), "closed rectangle y")
        height_value = _parse_float(rect.get("height"), "closed rectangle height")
        if height_value <= 0:
            raise PageContractError("closed-hours rectangle has non-positive height")
        top = max(0.0, y_value)
        bottom = min(calibration.height_units, y_value + height_value)
        if top >= bottom:
            raise PageContractError("closed-hours rectangle maps outside room rows")
        interval = _rect_interval(rect, calibration)
        tolerance = max(0.75, calibration.row_height_units * 0.02)
        covered_rows: list[int] = []
        for row_index in range(len(room_meta)):
            row_top = row_index * calibration.row_height_units
            row_bottom = row_top + calibration.row_height_units
            overlap = min(bottom, row_bottom) - max(top, row_top)
            if overlap > tolerance:
                covered_rows.append(row_index)
        if not covered_rows:
            raise PageContractError(
                "closed-hours rectangle does not significantly cover a room row"
            )
        for row_index in covered_rows:
            location_id = room_meta[row_index][1]
            closed_by_room[location_id].append(interval)

    overlays = svg.select("#event-overlays rect[data-event-id][data-location-id]")
    # Zero real events is valid, especially in vacations.  The semantic overlay
    # layer itself must still exist.
    if svg.select_one("#event-overlays") is None:
        raise PageContractError("overview event overlay layer is missing")
    seen_event_rectangles: set[tuple[str, str, int, int]] = set()
    for rect in overlays:
        event_id = str(rect.get("data-event-id") or "").strip()
        location_id = str(rect.get("data-location-id") or "").strip()
        category_id = str(rect.get("data-category-id") or "").strip() or None
        if location_id not in events_by_room:
            raise PageContractError(f"event {event_id} references unknown location {location_id}")
        interval = _rect_interval(rect, calibration)
        fingerprint = (event_id, location_id, interval.start, interval.end)
        if fingerprint in seen_event_rectangles:
            continue
        seen_event_rectangles.add(fingerprint)
        events_by_room[location_id].append(
            ObservedEventBlock(event_id, location_id, category_id, interval)
        )

    rooms: list[RoomOverview] = []
    for index, (name, location_id, description) in enumerate(room_meta):
        closed = _merge(closed_by_room[location_id])
        events = tuple(
            sorted(
                events_by_room[location_id],
                key=lambda item: (item.interval.start, item.interval.end, item.event_id),
            )
        )
        blocked = [*closed, *(item.interval for item in events)]
        free = _complement(blocked, calibration.origin_minute, calibration.end_minute)
        rooms.append(
            RoomOverview(
                name=name,
                location_id=location_id,
                description=description,
                row_index=index,
                closed=closed,
                events=events,
                free=free,
            )
        )

    return OverviewObservation(
        observed_date=observed_date,
        rooms=tuple(rooms),
        rolling_quota_remaining=_parse_quota(soup, "rolling-quota"),
        peak_quota_remaining=_parse_quota(soup, "peak-quota"),
        calibration=calibration,
    )
