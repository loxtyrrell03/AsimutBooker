from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from asimut_booker.agenda import AgendaEvent, AgendaObservation
from asimut_booker.errors import AmbiguousBookingResult, AuthenticationRequired
from asimut_booker.forms import BookingFormSnapshot
from asimut_booker.gateway import (
    AsimutGateway,
    GatewaySettings,
    _only_horizon_release_warnings,
)

DAY = date(2026, 8, 5)


def event(
    event_id: str,
    *,
    title: str = "Reservation",
    start: int = 600,
    end: int = 630,
) -> AgendaEvent:
    return AgendaEvent(
        event_id=event_id,
        event_date=DAY,
        start_minute=start,
        end_minute=end,
        title=title,
        room_name="B0.14",
        location_id="49",
        status="provisional",
        arrangement_href=f"/arrangement?eventId={event_id}",
    )


def agenda(*events: AgendaEvent) -> AgendaObservation:
    return AgendaObservation(
        first_date=DAY,
        last_date=DAY,
        day_count=1,
        events=events,
        rolling_quota_remaining=27 * 60,
    )


def gateway_with_observations(
    monkeypatch: pytest.MonkeyPatch,
    *observations: AgendaObservation,
) -> AsimutGateway:
    gateway = AsimutGateway(GatewaySettings())
    remaining = iter(observations)
    last = observations[-1]

    def observe(_start: date, _end: date) -> AgendaObservation:
        return next(remaining, last)

    gateway.observe_agenda = observe  # type: ignore[method-assign]
    monkeypatch.setattr("asimut_booker.gateway.time.sleep", lambda _seconds: None)
    return gateway


def test_reconcile_never_treats_an_unrelated_class_as_a_booking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = gateway_with_observations(
        monkeypatch,
        agenda(event("500", title="Keyboard class")),
    )

    with pytest.raises(AmbiguousBookingResult, match="reservation is not present"):
        gateway.reconcile_event(
            event_id=None,
            target_date=DAY,
            room_name="B0.14",
            start_minute=600,
            end_minute=630,
        )


def test_known_event_id_never_falls_back_to_a_different_exact_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = gateway_with_observations(
        monkeypatch,
        agenda(
            event("123", end=645),
            event("999", end=630),
        ),
    )

    with pytest.raises(AmbiguousBookingResult, match="reservation 123 is"):
        gateway.reconcile_event(
            event_id="123",
            target_date=DAY,
            room_name="B0.14",
            start_minute=600,
            end_minute=630,
        )


def test_reconcile_polls_fresh_agenda_until_extension_is_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = gateway_with_observations(
        monkeypatch,
        agenda(event("123", end=630)),
        agenda(event("123", end=645)),
    )

    result = gateway.reconcile_event(
        event_id="123",
        target_date=DAY,
        room_name="B0.14",
        start_minute=600,
        end_minute=645,
    )

    assert result.event_id == "123"
    assert result.end_minute == 645


def test_submit_marker_runs_after_form_validation_and_immediately_before_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions: list[str] = []

    class Locator:
        def __init__(self, kind: str) -> None:
            self.kind = kind

        def count(self) -> int:
            return 1 if self.kind == "save" else 0

        def click(self) -> None:
            actions.append("click")

    class Page:
        url = "https://rwcmd.asimut.net/event?eventId=0"

        def locator(self, selector: str) -> Locator:
            return Locator("save" if selector.startswith("button.save-button") else "other")

        def wait_for_url(self, *_args: object, **_kwargs: object) -> None:
            return None

    gateway = AsimutGateway(GatewaySettings())
    page = Page()
    monkeypatch.setattr(gateway, "_require_page", lambda: page)
    monkeypatch.setattr(
        gateway,
        "read_booking_form",
        lambda: BookingFormSnapshot(
            event_date=DAY,
            start_minute=600,
            end_minute=630,
            room_name="B0.14",
            save_enabled=True,
            warnings=(),
        ),
    )
    monkeypatch.setattr(gateway, "_event_from_arrangement", lambda: (None, False))
    monkeypatch.setattr(
        gateway,
        "reconcile_event",
        lambda **_kwargs: event("123"),
    )

    gateway._submit_and_reconcile(
        action="create",
        target_date=DAY,
        room_name="B0.14",
        start_minute=600,
        end_minute=630,
        before_submit=lambda: actions.append("submitting"),
    )

    assert actions == ["submitting", "click"]


def test_snipe_prefill_accepts_only_the_observed_horizon_warning_contract() -> None:
    assert _only_horizon_release_warnings(
        (
            "You are not allowed to create or modify bookings in B0.29 "
            "that end later than 3/8/26 23:30",
        )
    )
    assert not _only_horizon_release_warnings(())
    assert not _only_horizon_release_warnings(("The room is already occupied at this time",))


def test_storage_state_backup_is_nonblocking_and_marks_persistent_profile(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    class Context:
        def cookies(self) -> list[dict[str, object]]:
            calls.append("cookies")
            return [{"name": "session", "value": "opaque", "domain": "example.test"}]

    settings = GatewaySettings(
        storage_state_path=tmp_path / "state.json",
        profile_path=tmp_path / "profile",
    )
    gateway = AsimutGateway(settings)
    gateway.context = Context()
    gateway._authenticated = True

    gateway.save_storage_state()

    assert calls == ["cookies"]
    assert settings.storage_state_path.is_file()
    saved = json.loads(settings.storage_state_path.read_text(encoding="utf-8"))
    assert [cookie["name"] for cookie in saved["cookies"]] == ["session"]
    assert saved["origins"] == []
    assert (settings.profile_path / ".asimut-booker-authenticated").is_file()


def test_legacy_state_seeds_new_persistent_context() -> None:
    cookies_added: list[dict[str, object]] = []
    scripts: list[str] = []

    class Context:
        def add_cookies(self, cookies: list[dict[str, object]]) -> None:
            cookies_added.extend(cookies)

        def add_init_script(self, *, script: str) -> None:
            scripts.append(script)

    gateway = AsimutGateway(GatewaySettings())
    gateway.context = Context()
    gateway._restore_legacy_storage_state(
        {
            "cookies": [{"name": "session", "value": "opaque", "domain": "example.test"}],
            "origins": [
                {
                    "origin": "https://rwcmd.asimut.net",
                    "localStorage": [{"name": "preference", "value": "grid"}],
                }
            ],
        }
    )

    assert [cookie["name"] for cookie in cookies_added] == ["session"]
    assert len(scripts) == 1
    assert "localStorage.setItem" in scripts[0]


def test_auth_verification_detects_microsoft_redirect_without_waiting() -> None:
    class Locator:
        @property
        def first(self) -> "Locator":
            return self

        def wait_for(self, **_kwargs: object) -> None:
            raise AssertionError("the agenda wait must not run on the Microsoft host")

    class Page:
        url = "about:blank"

        def goto(self, _url: str, **_kwargs: object) -> None:
            self.url = "https://login.microsoftonline.com/example"

        def locator(self, _selector: str) -> Locator:
            return Locator()

    gateway = AsimutGateway(GatewaySettings())
    gateway.page = Page()

    with pytest.raises(AuthenticationRequired, match="redirected"):
        gateway.verify_authenticated()


def test_interactive_wait_preserves_an_existing_mfa_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Page:
        def goto(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("the active MFA page must not be replaced")

        def is_closed(self) -> bool:
            return False

    gateway = AsimutGateway(GatewaySettings())
    gateway.page = Page()
    saved: list[bool] = []
    monkeypatch.setattr(gateway, "_current_page_proves_authenticated", lambda: True)
    monkeypatch.setattr(gateway, "save_storage_state", lambda: saved.append(True))

    gateway.wait_for_interactive_login(timeout_seconds=30, navigate=False)

    assert gateway._authenticated is True
    assert saved == [True]
