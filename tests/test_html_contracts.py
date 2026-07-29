from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from asimut_booker.agenda import parse_agenda_html
from asimut_booker.errors import PageContractError
from asimut_booker.forms import parse_arrangement_html, parse_booking_form_html
from asimut_booker.overview import parse_overview_html

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_current_svg_overview_uses_semantic_ids_and_calibrated_time() -> None:
    result = parse_overview_html(
        fixture("overview_current.html"),
        expected_date=date(2026, 7, 29),
        minimum_rooms=2,
    )

    assert result.contract_version == "asimut-svg-v1"
    assert result.rolling_quota_remaining == 12 * 60 + 30
    assert result.peak_quota_remaining == 75
    assert result.calibration.origin_minute == 7 * 60
    assert len(result.rooms) == 2

    b011 = result.rooms_by_name["B0.11"]
    assert b011.location_id == "82"
    assert b011.description == "Practice: Grand Piano x 2"
    assert [
        (event.event_id, event.interval.start, event.interval.end) for event in b011.events
    ] == [("3570001", 8 * 60, 9 * 60)]
    assert not b011.is_visually_free(8 * 60, 8 * 60 + 30)
    assert b011.is_visually_free(9 * 60, 9 * 60 + 30)


def test_overview_wrong_date_fails_closed() -> None:
    with pytest.raises(PageContractError, match="requested"):
        parse_overview_html(
            fixture("overview_current.html"),
            expected_date=date(2026, 7, 30),
            minimum_rooms=2,
        )


def test_closed_rectangle_spanning_rows_blocks_every_covered_room() -> None:
    html = fixture("overview_current.html").replace(
        '<rect x="0" y="0" width="30" height="30" class="closed-hours"></rect>',
        '<rect x="120" y="0" width="30" height="60" class="closed-hours"></rect>',
    )

    observation = parse_overview_html(html, minimum_rooms=2)

    for room in observation.rooms:
        assert not room.is_visually_free(9 * 60, 9 * 60 + 30)


def test_missing_grid_never_becomes_empty_availability() -> None:
    with pytest.raises(PageContractError, match="SVG"):
        parse_overview_html(
            '<span data-cy="display-date">Wednesday, 29 July 2026</span>',
            minimum_rooms=1,
        )


def test_agenda_preserves_local_bst_date_and_stable_event_identity() -> None:
    result = parse_agenda_html(
        fixture("agenda_current.html"),
        required_start=date(2026, 8, 5),
        required_end=date(2026, 8, 6),
    )

    assert result.first_date == date(2026, 8, 5)
    assert result.last_date == date(2026, 8, 6)
    assert result.rolling_quota_remaining == 27 * 60 + 15
    assert len(result.events) == 2
    event = result.find_exact(event_id="3570253")[0]
    assert event.event_date == date(2026, 8, 5)
    assert event.room_name == "B0.14"
    assert event.location_id == "49"
    assert event.start_minute == 600
    assert event.end_minute == 645
    assert event.status == "provisional"
    assert result.find_exact(event_id="3570300") == []


def test_agenda_requires_requested_date_coverage() -> None:
    with pytest.raises(PageContractError, match="before required"):
        parse_agenda_html(
            fixture("agenda_current.html"),
            required_end=date(2026, 8, 7),
        )


def test_booking_and_arrangement_contracts_match_exactly() -> None:
    form = parse_booking_form_html(fixture("booking_form_current.html"))
    assert form.event_date == date(2026, 8, 5)
    assert (form.start_minute, form.end_minute) == (600, 645)
    assert form.room_name == "B0.14"
    assert form.save_enabled

    arrangement = parse_arrangement_html(
        fixture("arrangement_current.html"),
        "https://rwcmd.asimut.net/arrangement?eventId=3570253",
    )
    assert arrangement.event_id == "3570253"
    assert arrangement.event_date == form.event_date
    assert arrangement.room_name == form.room_name
    assert (arrangement.start_minute, arrangement.end_minute) == (
        form.start_minute,
        form.end_minute,
    )
