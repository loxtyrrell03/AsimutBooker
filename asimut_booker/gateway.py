"""Playwright boundary for the live Asimut application.

All selectors in this module are semantic contracts observed on the current
site.  The coordinator never performs browser interaction directly.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

from .agenda import AgendaEvent, AgendaObservation, parse_agenda_html
from .errors import (
    AmbiguousBookingResult,
    AuthenticationRequired,
    BookingRejected,
    NavigationError,
    PageContractError,
)
from .forms import BookingFormSnapshot, parse_arrangement_html
from .overview import OverviewObservation, RoomOverview, parse_overview_html

LOG = logging.getLogger(__name__)
_ARRANGEMENT_URL_RE = re.compile(r"/arrangement\?eventId=([1-9]\d*)")
_FORM_DATE_FORMAT = "%d/%m/%Y"
_FORM_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_HORIZON_WARNING_RE = re.compile(
    r"\bnot allowed to create or modify bookings\b.*\bthat end later than\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GatewaySettings:
    base_url: str = "https://rwcmd.asimut.net/"
    location_group_id: int = 10
    booking_category_id: int = 56
    storage_state_path: Path = Path("data/browser_state/state.json")
    profile_path: Path = Path("data/browser_state/profile")
    artifacts_dir: Path = Path("data/artifacts")
    executable_path: Path | None = None
    timezone: str = "Europe/London"
    viewport_width: int = 1920
    viewport_height: int = 1080
    timeout_ms: int = 15_000

    @property
    def overview_url(self) -> str:
        return urljoin(
            self.base_url,
            f"overview?locationGroupId={self.location_group_id}",
        )

    @property
    def agenda_url(self) -> str:
        return urljoin(self.base_url, "agenda")


@dataclass(frozen=True)
class BookingMutation:
    event: AgendaEvent
    action: str
    verified_at: datetime
    arrangement_observed: bool


def _only_horizon_release_warnings(warnings: tuple[str, ...]) -> bool:
    """Recognise the live server blocker that is expected before a snipe."""

    return bool(warnings) and all(_HORIZON_WARNING_RE.search(item) for item in warnings)


class AsimutGateway:
    """Validated, stateful browser session.

    The object is intentionally single-page and single-run.  The process-level
    coordinator lock prevents another worker from racing it.
    """

    def __init__(
        self,
        settings: GatewaySettings,
        *,
        headless: bool = True,
        require_saved_session: bool = True,
    ) -> None:
        self.settings = settings
        self.headless = headless
        self.require_saved_session = require_saved_session
        self.zone = ZoneInfo(settings.timezone)
        self._playwright: Any | None = None
        self.browser: Any | None = None
        self.context: Any | None = None
        self.page: Any | None = None
        self._authenticated = False
        self._persistent_context = False

    def __enter__(self) -> "AsimutGateway":
        self.open()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close(save_state=exc is None and self._authenticated)

    def open(self) -> None:
        if self.page is not None:
            return
        from playwright.sync_api import sync_playwright

        state_path = self.settings.storage_state_path
        profile_marker = self._profile_marker_path
        if self.require_saved_session and not state_path.exists() and not profile_marker.is_file():
            raise AuthenticationRequired(
                "No saved AsimutBooker browser profile exists. Run login setup once."
            )
        storage_state: dict[str, Any] | None = None
        if state_path.exists():
            try:
                parsed = json.loads(state_path.read_text(encoding="utf-8"))
                if not isinstance(parsed, dict) or not isinstance(parsed.get("cookies"), list):
                    raise ValueError("missing cookies array")
                storage_state = parsed
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                if self.require_saved_session and not profile_marker.is_file():
                    raise AuthenticationRequired(f"Saved Asimut session is invalid: {exc}") from exc
                LOG.warning("Ignoring invalid legacy browser state: %s", exc)

        self._playwright = sync_playwright().start()
        try:
            profile_was_authenticated = profile_marker.is_file()
            self.settings.profile_path.mkdir(parents=True, exist_ok=True)
            launch_options: dict[str, Any] = {
                "headless": self.headless,
                "viewport": {
                    "width": self.settings.viewport_width,
                    "height": self.settings.viewport_height,
                },
                "locale": "en-GB",
                "timezone_id": self.settings.timezone,
            }
            if self.settings.executable_path is not None:
                launch_options["executable_path"] = str(self.settings.executable_path)
            self.context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.settings.profile_path),
                **launch_options,
            )
            self._persistent_context = True
            self.browser = self.context.browser
            if storage_state is not None and not profile_was_authenticated:
                self._restore_legacy_storage_state(storage_state)
            self.context.set_default_timeout(self.settings.timeout_ms)
            pages = list(self.context.pages)
            self.page = pages[0] if pages else self.context.new_page()
            # Chromium can restore stale SSO tabs from a previous interrupted
            # run. Keep exactly one controlled tab so the page the user sees is
            # also the page the coordinator observes.
            for extra_page in pages[1:]:
                extra_page.close()
        except Exception:
            self.close()
            raise

    def close(self, *, save_state: bool = False) -> None:
        try:
            if save_state and self.context is not None:
                self.save_storage_state()
        finally:
            try:
                if self.context is not None:
                    self.context.close()
            finally:
                try:
                    if self.browser is not None and not self._persistent_context:
                        self.browser.close()
                finally:
                    try:
                        if self._playwright is not None:
                            self._playwright.stop()
                    finally:
                        self.page = None
                        self.context = None
                        self.browser = None
                        self._playwright = None
                        self._persistent_context = False

    @property
    def _profile_marker_path(self) -> Path:
        return self.settings.profile_path / ".asimut-booker-authenticated"

    def _restore_legacy_storage_state(self, state: dict[str, Any]) -> None:
        """Seed a new persistent profile from the previous cookie snapshot once."""

        if self.context is None:
            raise RuntimeError("gateway context is not open")
        cookies = state.get("cookies")
        if isinstance(cookies, list) and cookies:
            self.context.add_cookies(cookies)

        origin_storage: dict[str, dict[str, str]] = {}
        origins = state.get("origins")
        if isinstance(origins, list):
            for item in origins:
                if not isinstance(item, dict) or not isinstance(item.get("origin"), str):
                    continue
                values: dict[str, str] = {}
                for entry in item.get("localStorage", []):
                    if (
                        isinstance(entry, dict)
                        and isinstance(entry.get("name"), str)
                        and isinstance(entry.get("value"), str)
                    ):
                        values[entry["name"]] = entry["value"]
                if values:
                    origin_storage[item["origin"]] = values
        if origin_storage:
            serialized = json.dumps(origin_storage, ensure_ascii=False)
            self.context.add_init_script(
                script=(
                    "(() => {"
                    f"const saved = {serialized};"
                    "const values = saved[location.origin];"
                    "if (values) {"
                    "for (const [name, value] of Object.entries(values)) "
                    "localStorage.setItem(name, value);"
                    "}"
                    "})();"
                )
            )

    def _require_page(self) -> Any:
        if self.page is None:
            raise RuntimeError("gateway is not open")
        return self.page

    def _same_asimut_host(self, page: Any | None = None) -> bool:
        page = page or self._require_page()
        expected = urlparse(self.settings.base_url).hostname
        actual = urlparse(page.url).hostname
        return bool(expected and actual and expected.casefold() == actual.casefold())

    def verify_authenticated(self) -> None:
        """Positively verify agenda HTML before any booking decision."""

        page = self._require_page()
        page.goto(self.settings.agenda_url, wait_until="domcontentloaded")
        if not self._same_asimut_host():
            raise AuthenticationRequired(f"Asimut redirected to {page.url}; login is required.")
        try:
            page.locator("[day-header]").first.wait_for(
                state="attached", timeout=self.settings.timeout_ms
            )
        except Exception as exc:
            raise AuthenticationRequired(
                "Asimut did not expose the signed-in agenda. The saved Microsoft "
                "365 session may have expired."
            ) from exc
        # Parsing proves this is the expected complete application, rather than a
        # generic error page that happened to remain on the same host.
        parse_agenda_html(page.content())
        self._authenticated = True

    def _current_page_proves_authenticated(self) -> bool:
        current = self._require_page()
        candidates = [current]
        if self.context is not None:
            candidates.extend(page for page in self.context.pages if page is not current)
        for page in candidates:
            if page.is_closed() or not self._same_asimut_host(page):
                continue
            if page.locator('[data-cy="display-date"]:visible').count() == 1:
                parse_overview_html(page.content())
                self.page = page
                return True
            if page.locator("[day-header]").count() >= 1:
                parse_agenda_html(page.content())
                self.page = page
                return True
        return False

    def wait_for_interactive_login(
        self,
        *,
        timeout_seconds: int = 900,
        navigate: bool = True,
        on_progress: Callable[[int, str], None] | None = None,
    ) -> None:
        """Wait for a user to complete Microsoft SSO in a headed browser."""

        page = self._require_page()
        if navigate:
            page.goto(self.settings.overview_url, wait_until="domcontentloaded")
        started = time.monotonic()
        deadline = time.monotonic() + timeout_seconds
        next_progress = 0
        while time.monotonic() < deadline:
            live_pages = (
                [candidate for candidate in self.context.pages if not candidate.is_closed()]
                if self.context is not None
                else ([] if page.is_closed() else [page])
            )
            if not live_pages:
                raise AuthenticationRequired("The sign-in window was closed before login completed.")
            try:
                if self._current_page_proves_authenticated():
                    self._authenticated = True
                    self.save_storage_state()
                    return
            except PageContractError:
                pass
            elapsed = int(time.monotonic() - started)
            if on_progress is not None and elapsed >= next_progress:
                active_page = self.page if self.page is not None and not self.page.is_closed() else live_pages[0]
                host = urlparse(active_page.url).hostname or "unknown"
                on_progress(elapsed, host)
                next_progress = elapsed + 10
            time.sleep(1)
        raise AuthenticationRequired("Timed out waiting for a completed Asimut sign-in.")

    def save_storage_state(self) -> None:
        """Persist the full profile and atomically refresh the portable backup."""

        if not self._authenticated or self.context is None:
            return
        destination = self.settings.storage_state_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        backup = destination.with_suffix(destination.suffix + ".bak")
        # The persistent profile is authoritative for site storage. Export
        # cookies directly instead of calling BrowserContext.storage_state():
        # that API can block while Microsoft-owned origins are still settling
        # after SSO. Cookies are sufficient to recover the authenticated
        # profile, and writing them ourselves is bounded and atomic.
        cookies = self.context.cookies()
        temporary.write_text(
            json.dumps({"cookies": cookies, "origins": []}, ensure_ascii=False),
            encoding="utf-8",
        )
        # Validate the generated document before it can replace a usable state.
        parsed = json.loads(temporary.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict) or not isinstance(parsed.get("cookies"), list):
            temporary.unlink(missing_ok=True)
            raise AuthenticationRequired("Playwright generated invalid session state")
        if destination.exists():
            shutil.copy2(destination, backup)
        os.replace(temporary, destination)
        marker = self._profile_marker_path
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker_temporary = marker.with_suffix(".tmp")
        marker_temporary.write_text(
            datetime.now(self.zone).isoformat(timespec="seconds"),
            encoding="utf-8",
        )
        os.replace(marker_temporary, marker)

    def capture_diagnostics(self, label: str = "failure") -> tuple[Path, ...]:
        """Capture local-only HTML and screenshot artifacts best-effort.

        Artifacts may include personal calendar data and are therefore stored
        only in the configured ignored runtime directory.
        """

        page = self._require_page()
        if page.is_closed():
            return ()
        safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-")
        safe_label = safe_label[:60] or "failure"
        timestamp = datetime.now(self.zone).strftime("%Y%m%d-%H%M%S-%f")
        directory = self.settings.artifacts_dir / f"{timestamp}-{safe_label}"
        directory.mkdir(parents=True, exist_ok=False)
        written: list[Path] = []
        try:
            screenshot = directory / "page.png"
            page.screenshot(path=str(screenshot), full_page=True)
            written.append(screenshot)
        except Exception:
            LOG.exception("Could not capture Asimut screenshot")
        try:
            html_path = directory / "page.html"
            temporary = html_path.with_suffix(".html.tmp")
            temporary.write_text(page.content(), encoding="utf-8")
            os.replace(temporary, html_path)
            written.append(html_path)
        except Exception:
            LOG.exception("Could not capture Asimut HTML")
        try:
            metadata = directory / "metadata.json"
            temporary = metadata.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(
                    {
                        "captured_at": datetime.now(self.zone).isoformat(),
                        "url": page.url,
                        "label": label,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            os.replace(temporary, metadata)
            written.append(metadata)
        except Exception:
            LOG.exception("Could not capture Asimut diagnostic metadata")
        return tuple(written)

    @staticmethod
    def _displayed_date(page: Any) -> date:
        display = page.locator('[data-cy="display-date"]:visible')
        if display.count() != 1:
            raise PageContractError("visible overview date is not unique")
        text = display.inner_text().strip()
        try:
            return datetime.strptime(" ".join(text.split()), "%A, %d %B %Y").date()
        except ValueError as exc:
            raise PageContractError(f"unrecognised displayed date: {text!r}") from exc

    def _wait_for_overview_grid(self, page: Any, expected_date: date) -> None:
        """Wait for Angular's SVG rerender to finish after a date change.

        The visible date updates before the old SVG layers are replaced.  A
        single selector wait can therefore either read a half-built grid or
        accidentally accept the previous day's grid.  Require the semantic
        room/closed/event geometry to be present and unchanged across two
        polls before parsing it.
        """

        deadline = time.monotonic() + (self.settings.timeout_ms / 1000)
        previous_fingerprint: str | None = None
        stable_polls = 0
        last_state: dict[str, object] = {}
        while time.monotonic() < deadline:
            if self._displayed_date(page) != expected_date:
                previous_fingerprint = None
                stable_polls = 0
                page.wait_for_timeout(100)
                continue
            state = page.evaluate(
                """() => {
                    const svg = document.querySelector('#svg-grid');
                    const closed = svg && svg.querySelector('#closed-hours');
                    const events = svg && svg.querySelector('#event-overlays');
                    const rooms = [...document.querySelectorAll(
                        '#legend-y-inner a[data-location-id]'
                    )];
                    const spinnerVisible = [...document.querySelectorAll(
                        '.overview-render-spinner'
                    )].some(item => item.getClientRects().length > 0);
                    const rectSignature = root => root
                        ? [...root.querySelectorAll('rect')].map(rect => [
                            rect.getAttribute('x'),
                            rect.getAttribute('y'),
                            rect.getAttribute('width'),
                            rect.getAttribute('height'),
                            rect.getAttribute('data-event-id'),
                            rect.getAttribute('data-location-id'),
                        ])
                        : null;
                    const fingerprint = JSON.stringify({
                        viewBox: svg && svg.getAttribute('viewBox'),
                        rooms: rooms.map(item => [
                            item.getAttribute('data-location-id'),
                            item.textContent && item.textContent.trim(),
                        ]),
                        closed: rectSignature(closed),
                        events: rectSignature(events),
                    });
                    return {
                        hasSvg: Boolean(svg),
                        hasClosedLayer: Boolean(closed),
                        hasEventLayer: Boolean(events),
                        roomCount: rooms.length,
                        spinnerVisible,
                        fingerprint,
                    };
                }"""
            )
            last_state = state
            ready = (
                state["hasSvg"]
                and state["hasClosedLayer"]
                and state["hasEventLayer"]
                and int(state["roomCount"]) >= 10
                and not state["spinnerVisible"]
            )
            fingerprint = str(state["fingerprint"])
            if ready and fingerprint == previous_fingerprint:
                stable_polls += 1
                if stable_polls >= 2:
                    return
            elif ready:
                previous_fingerprint = fingerprint
                stable_polls = 1
            else:
                previous_fingerprint = None
                stable_polls = 0
            page.wait_for_timeout(100)

        raise PageContractError(
            f"overview grid did not settle for {expected_date.isoformat()}: {last_state}"
        )

    def navigate_overview(self, target_date: date) -> OverviewObservation:
        """Navigate by semantic date controls and prove the observed date."""

        page = self._require_page()
        if "/overview" not in urlparse(page.url).path:
            page.goto(self.settings.overview_url, wait_until="domcontentloaded")
        page.locator('[data-cy="display-date"]:visible').wait_for(
            state="visible", timeout=self.settings.timeout_ms
        )
        current = self._displayed_date(page)
        delta = (target_date - current).days
        if abs(delta) > 62:
            # Reloading the canonical overview reliably resets the calendar to
            # today, keeping navigation bounded.
            page.goto(self.settings.overview_url, wait_until="domcontentloaded")
            page.locator('[data-cy="display-date"]:visible').wait_for(state="visible")
            current = self._displayed_date(page)
            delta = (target_date - current).days
        if abs(delta) > 62:
            raise NavigationError(f"refusing to click through {abs(delta)} overview days")

        selector = (
            '[data-cy="increment-date-chevron"]:visible'
            if delta > 0
            else '[data-cy="decrement-date-chevron"]:visible'
        )
        for _ in range(abs(delta)):
            control = page.locator(selector)
            if control.count() != 1:
                raise PageContractError(f"date control {selector} is not unique")
            before = self._displayed_date(page)
            control.click()
            expected = before + timedelta(days=1 if delta > 0 else -1)
            try:
                page.wait_for_function(
                    """expected => {
                        const node = [...document.querySelectorAll(
                            '[data-cy="display-date"]'
                        )].find(item => item.getClientRects().length > 0);
                        return node && node.textContent &&
                            node.textContent.trim() !== expected;
                    }""",
                    arg=before.strftime("%A, %-d %B %Y")
                    if os.name != "nt"
                    else before.strftime("%A, %#d %B %Y"),
                    timeout=self.settings.timeout_ms,
                )
            except Exception:
                # The exact value check below is authoritative and produces a
                # clearer failure than the transition wait.
                pass
            observed = self._displayed_date(page)
            if observed != expected:
                raise NavigationError(f"date navigation expected {expected}, observed {observed}")

        self._wait_for_overview_grid(page, target_date)
        html = page.content()
        return parse_overview_html(html, expected_date=target_date)

    def observe_overview(self, target_date: date) -> OverviewObservation:
        return self.navigate_overview(target_date)

    def observe_agenda(
        self,
        required_start: date,
        required_end: date,
    ) -> AgendaObservation:
        """Read a complete bounded agenda snapshot.

        Asimut currently renders a long date range eagerly.  The bounded scroll
        loop is retained for virtualised variants and stops once the requested
        final day is present.
        """

        page = self._require_page()
        page.goto(self.settings.agenda_url, wait_until="domcontentloaded")
        try:
            page.locator("[day-header]").first.wait_for(
                state="attached", timeout=self.settings.timeout_ms
            )
        except Exception as exc:
            raise AuthenticationRequired("signed-in agenda is not available") from exc
        target_selector = f'[day-header^="{required_end.isoformat()}"]'
        previous_height = -1
        for _ in range(20):
            if page.locator(target_selector).count() > 0:
                break
            height = page.evaluate("() => document.documentElement.scrollHeight")
            if height == previous_height:
                break
            previous_height = height
            page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
            page.wait_for_timeout(150)
        observation = parse_agenda_html(
            page.content(),
            required_start=required_start,
            required_end=required_end,
        )
        self._authenticated = True
        return observation

    @staticmethod
    def _format_time(minute: int) -> str:
        return f"{minute // 60:02d}:{minute % 60:02d}"

    @staticmethod
    def _form_date(value: str) -> date:
        try:
            return datetime.strptime(value.split()[-1], _FORM_DATE_FORMAT).date()
        except (ValueError, IndexError) as exc:
            raise PageContractError(f"invalid form date: {value!r}") from exc

    def read_booking_form(self) -> BookingFormSnapshot:
        """Read live input properties, not merely their HTML attributes."""

        page = self._require_page()
        date_input = page.locator('[data-cy="event-edit-date-input"]:visible')
        start_input = page.locator('[data-cy="time-range-start-time"]:visible')
        end_input = page.locator('[data-cy="time-range-end-time"]:visible')
        location_input = page.locator('input[aria-label="Event location"]:visible')
        save = page.locator("button.save-button:visible")
        for name, locator in (
            ("date", date_input),
            ("start", start_input),
            ("end", end_input),
            ("location", location_input),
            ("save", save),
        ):
            if locator.count() != 1:
                raise PageContractError(f"booking form {name} control is not unique")
        event_date = self._form_date(date_input.input_value())
        start_text = start_input.input_value()
        end_text = end_input.input_value()
        start_match = _FORM_TIME_RE.fullmatch(start_text.strip())
        end_match = _FORM_TIME_RE.fullmatch(end_text.strip())
        if start_match is None or end_match is None:
            raise PageContractError("booking form time inputs are malformed")
        start_hour, start_part = map(int, start_match.groups())
        end_hour, end_part = map(int, end_match.groups())
        if (
            start_hour > 24
            or end_hour > 24
            or start_part >= 60
            or end_part >= 60
            or (start_hour == 24 and start_part)
            or (end_hour == 24 and end_part)
        ):
            raise PageContractError("booking form time inputs are malformed")
        start = start_hour * 60 + start_part
        end = end_hour * 60 + end_part
        if not (0 <= start < end <= 24 * 60):
            raise PageContractError("booking form times lie outside a day")
        location = " ".join(location_input.input_value().replace("\xa0", " ").split())
        room_name = location.split(" (", 1)[0].strip()
        if not room_name:
            raise PageContractError("booking form has no room identity")
        warnings = tuple(
            " ".join(text.split())
            for text in page.locator(
                '[aria-label="Warning: Booking cannot be saved"]'
            ).evaluate_all(
                """nodes => nodes.map(node =>
                    (node.parentElement?.textContent || node.textContent || '').trim()
                )"""
            )
            if text.strip()
        )
        return BookingFormSnapshot(
            event_date=event_date,
            start_minute=start,
            end_minute=end,
            room_name=room_name,
            save_enabled=(
                save.get_attribute("aria-label") == "Save event"
                and save.is_enabled()
                and not warnings
            ),
            warnings=warnings,
        )

    def _set_native_time(self, selector: str, target_minute: int) -> None:
        """Use Asimut's time picker so Angular receives real selection events."""

        page = self._require_page()
        if target_minute % 15:
            raise ValueError("Asimut times must use 15-minute increments")
        field = page.locator(f"{selector}:visible")
        if field.count() != 1:
            raise PageContractError(f"time field {selector} is not unique")
        target_text = self._format_time(target_minute)
        if field.input_value() == target_text:
            return
        field.click()
        picker = page.locator('[aria-label="Time picker container"]:visible')
        picker.first.wait_for(state="visible", timeout=self.settings.timeout_ms)
        if picker.count() != 1:
            raise PageContractError("visible time picker is not unique")
        hour = target_minute // 60
        minute = target_minute % 60
        hour_button = picker.get_by_role("button", name=f"{hour}h", exact=True)
        if hour_button.count() != 1 or not hour_button.is_enabled():
            raise BookingRejected(f"hour {hour:02d} is disabled by Asimut")
        hour_button.click()
        minute_button = picker.get_by_role("button", name=f"{minute}min", exact=True)
        if minute_button.count() != 1 or not minute_button.is_enabled():
            raise BookingRejected(f"minute {minute:02d} is disabled by Asimut")
        minute_button.click()
        picker.wait_for(state="hidden", timeout=self.settings.timeout_ms)
        actual = field.input_value()
        if actual != target_text:
            raise PageContractError(f"time picker selected {actual!r}, expected {target_text!r}")

    def _click_slot(
        self,
        observation: OverviewObservation,
        room: RoomOverview,
        start_minute: int,
    ) -> None:
        page = self._require_page()
        svg = page.locator("#svg-grid")
        if svg.count() != 1:
            raise PageContractError("overview SVG is not unique")
        page.locator(
            f'#legend-y-inner [data-location-id="{room.location_id}"]'
        ).scroll_into_view_if_needed()
        box = svg.bounding_box()
        if box is None:
            raise PageContractError("overview SVG has no rendered geometry")
        # Click inside the first 15-minute cell.  Asimut snaps this to its cell
        # start and the contextual menu is subsequently checked for exactness.
        click_minute = start_minute + 7
        x_units = observation.calibration.minute_to_x(click_minute)
        x = box["x"] + (x_units / observation.calibration.width_units) * box["width"]
        y_units = (room.row_index + 0.5) * observation.calibration.row_height_units
        y = box["y"] + (y_units / observation.calibration.height_units) * box["height"]
        viewport = page.viewport_size
        if (
            viewport is None
            or not (0 <= x < viewport["width"])
            or not (0 <= y < viewport["height"])
        ):
            raise PageContractError(
                f"calculated slot point lies outside viewport: ({x:.1f},{y:.1f})"
            )
        page.mouse.click(x, y)
        menu = page.locator("app-fab-menu-body:visible")
        menu.first.wait_for(state="visible", timeout=self.settings.timeout_ms)
        if menu.count() != 1:
            raise PageContractError("visible booking menu is not unique")
        heading = " ".join(menu.locator("h2").inner_text().split())
        expected_prefix = f"{self._format_time(start_minute)} in {room.name}"
        if heading != expected_prefix and not heading.startswith(expected_prefix + " "):
            page.keyboard.press("Escape")
            raise PageContractError(f"slot click opened {heading!r}, expected {expected_prefix!r}")
        option = menu.locator("mat-list-item", has_text="Student booking provisional")
        if option.count() != 1:
            raise PageContractError("standard student booking option is not unique")
        option.click()
        page.locator('[data-cy="time-range-end-time"]:visible').wait_for(
            state="visible", timeout=self.settings.timeout_ms
        )

    def prepare_create_form(
        self,
        *,
        target_date: date,
        room_name: str,
        start_minute: int,
        end_minute: int,
        allow_temporarily_blocked: bool = False,
    ) -> BookingFormSnapshot:
        observation = self.observe_overview(target_date)
        room = observation.rooms_by_name.get(room_name)
        if room is None:
            raise PageContractError(f"room {room_name!r} is absent from the overview")
        if not room.is_visually_free(start_minute, end_minute):
            raise BookingRejected(
                f"{room_name} is not visually free for "
                f"{self._format_time(start_minute)}-{self._format_time(end_minute)}"
            )
        self._click_slot(observation, room, start_minute)
        form = self.read_booking_form()
        if form.event_date != target_date:
            raise PageContractError(
                f"create form date is {form.event_date}, expected {target_date}"
            )
        if form.room_name != room_name or form.start_minute != start_minute:
            raise PageContractError("create form does not match the clicked room and start time")
        self._set_native_time('[data-cy="time-range-end-time"]', end_minute)
        form = self.read_booking_form()
        if (
            form.event_date != target_date
            or form.room_name != room_name
            or form.start_minute != start_minute
            or form.end_minute != end_minute
        ):
            raise PageContractError("create form changed away from the requested slot")
        if not form.save_enabled:
            if not (allow_temporarily_blocked and _only_horizon_release_warnings(form.warnings)):
                raise BookingRejected("; ".join(form.warnings) or "Save is not enabled")
        return form

    @staticmethod
    def _wait_until(instant: datetime) -> None:
        """Wait against a monotonic deadline while preserving wall-clock intent."""

        seconds = (instant - datetime.now(instant.tzinfo)).total_seconds()
        if seconds <= 0:
            return
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.1, remaining))

    def _activate_form_after_release(self, end_minute: int) -> BookingFormSnapshot:
        """Refresh Angular validation after a horizon release instant."""

        for attempt in range(10):
            form = self.read_booking_form()
            if form.save_enabled:
                return form
            # Re-selecting through the native picker triggers the server-rule
            # validation path; a plain text fill does not.
            current = form.end_minute
            if current == end_minute:
                # Temporarily choose another valid quarter where possible, then
                # return to the requested value.
                alternate = end_minute + 15 if end_minute + 15 <= 23 * 60 else end_minute - 15
                try:
                    self._set_native_time('[data-cy="time-range-end-time"]', alternate)
                except BookingRejected:
                    pass
            self._set_native_time('[data-cy="time-range-end-time"]', end_minute)
            form = self.read_booking_form()
            if form.save_enabled:
                return form
            time.sleep(0.25 + attempt * 0.1)
        raise BookingRejected(
            "; ".join(self.read_booking_form().warnings)
            or "Asimut did not enable Save after the release time"
        )

    def _event_from_arrangement(self) -> tuple[str | None, bool]:
        page = self._require_page()
        match = _ARRANGEMENT_URL_RE.search(page.url)
        if not match:
            return None, False
        event_id = match.group(1)
        try:
            parsed = parse_arrangement_html(page.content(), page.url)
            return parsed.event_id, True
        except PageContractError:
            return event_id, False

    def reconcile_event(
        self,
        *,
        event_id: str | None,
        target_date: date,
        room_name: str,
        start_minute: int,
        end_minute: int,
    ) -> AgendaEvent:
        today = datetime.now(self.zone).date()
        required_start = min(today, target_date)
        required_end = max(today + timedelta(days=7), target_date)
        last_detail = "the requested booking is not present"

        # Agenda state can trail a successful Save very briefly.  A few bounded,
        # fresh navigations make that normal propagation delay reliable without
        # ever retrying the mutation itself.
        for attempt in range(4):
            observation = self.observe_agenda(required_start, required_end)
            if event_id is not None:
                # Once an arrangement supplied an identity, natural-key
                # fallback is unsafe: it could silently adopt and later edit a
                # different reservation at the same time.
                identified = [
                    item
                    for item in observation.find_exact(event_id=event_id)
                    if item.is_reservation
                ]
                exact = [
                    item
                    for item in identified
                    if item.event_date == target_date
                    and item.room_name == room_name
                    and item.start_minute == start_minute
                    and item.end_minute == end_minute
                ]
                if len(identified) == 1 and len(exact) == 1:
                    return exact[0]
                if identified:
                    actual = identified[0]
                    last_detail = (
                        f"Asimut reservation {event_id} is "
                        f"{actual.event_date} {actual.room_name} "
                        f"{self._format_time(actual.start_minute)}-"
                        f"{self._format_time(actual.end_minute)}, not the "
                        "requested slot"
                    )
                else:
                    last_detail = (
                        f"Asimut reservation {event_id} is absent from the refreshed agenda"
                    )
            else:
                natural = [
                    item
                    for item in observation.find_exact(
                        event_date=target_date,
                        room_name=room_name,
                        start_minute=start_minute,
                        end_minute=end_minute,
                    )
                    if item.is_reservation
                ]
                if len(natural) == 1:
                    return natural[0]
                if len(natural) > 1:
                    last_detail = "multiple reservations match the requested booking"
                else:
                    last_detail = "the requested reservation is not present in the refreshed agenda"
            if attempt < 3:
                time.sleep(0.25 * (2**attempt))

        raise AmbiguousBookingResult(last_detail)

    def _submit_and_reconcile(
        self,
        *,
        action: str,
        target_date: date,
        room_name: str,
        start_minute: int,
        end_minute: int,
        known_event_id: str | None = None,
        before_submit: Callable[[], None] | None = None,
    ) -> BookingMutation:
        page = self._require_page()
        save = page.locator("button.save-button:visible")
        if save.count() != 1:
            raise PageContractError("Save button is not unique")
        form = self.read_booking_form()
        if (
            form.event_date,
            form.room_name,
            form.start_minute,
            form.end_minute,
        ) != (target_date, room_name, start_minute, end_minute):
            raise PageContractError("booking form identity changed immediately before Save")
        if not form.save_enabled:
            raise BookingRejected("; ".join(form.warnings) or "Save is not enabled")
        if before_submit is not None:
            # Persist the narrow "a write may have happened" boundary only
            # after every form assertion and immediately before the click.
            before_submit()
        click_error: Exception | None = None
        try:
            save.click()
            page.wait_for_url(
                re.compile(r".*/arrangement\?eventId=\d+"),
                timeout=self.settings.timeout_ms,
                wait_until="domcontentloaded",
            )
            progress = page.locator('[role="progressbar"]')
            if progress.count() == 1:
                try:
                    progress.wait_for(state="hidden", timeout=self.settings.timeout_ms)
                except Exception:
                    pass
        except Exception as exc:
            # A network response can be lost after Asimut commits the write.
            # Reconciliation, not a blind retry, determines the outcome.
            click_error = exc
        arrangement_id, arrangement_observed = self._event_from_arrangement()
        event_id = arrangement_id or known_event_id
        try:
            event = self.reconcile_event(
                event_id=event_id,
                target_date=target_date,
                room_name=room_name,
                start_minute=start_minute,
                end_minute=end_minute,
            )
        except Exception as exc:
            # Once Save has been clicked, every inability to prove the remote
            # result is ambiguous.  Promoting authentication, navigation and
            # page-contract failures here prevents the coordinator from
            # treating them as safe pre-write retries and creating duplicates.
            if isinstance(exc, AmbiguousBookingResult):
                ambiguity = exc
            else:
                ambiguity = AmbiguousBookingResult(
                    f"Save was attempted but the refreshed agenda could not "
                    f"prove the result: {type(exc).__name__}: {exc}"
                )
            if click_error is not None:
                raise AmbiguousBookingResult(
                    f"Save raised {type(click_error).__name__} and reconciliation "
                    f"did not prove the result: {ambiguity}"
                ) from click_error
            raise ambiguity from exc
        return BookingMutation(
            event=event,
            action=action,
            verified_at=datetime.now(self.zone),
            arrangement_observed=arrangement_observed,
        )

    def create_booking(
        self,
        *,
        target_date: date,
        room_name: str,
        start_minute: int,
        end_minute: int,
        not_before: datetime | None = None,
        before_submit: Callable[[], None] | None = None,
    ) -> BookingMutation:
        if not_before is not None:
            self.prepare_create_form(
                target_date=target_date,
                room_name=room_name,
                start_minute=start_minute,
                end_minute=end_minute,
                allow_temporarily_blocked=True,
            )
            self._wait_until(not_before)
            form = self._activate_form_after_release(end_minute)
        else:
            form = self.prepare_create_form(
                target_date=target_date,
                room_name=room_name,
                start_minute=start_minute,
                end_minute=end_minute,
            )
        if (
            form.event_date,
            form.room_name,
            form.start_minute,
            form.end_minute,
        ) != (target_date, room_name, start_minute, end_minute):
            raise PageContractError("form identity changed immediately before Save")
        return self._submit_and_reconcile(
            action="create",
            target_date=target_date,
            room_name=room_name,
            start_minute=start_minute,
            end_minute=end_minute,
            before_submit=before_submit,
        )

    def extend_booking(
        self,
        *,
        event_id: str,
        target_date: date,
        room_name: str,
        start_minute: int,
        current_end_minute: int,
        new_end_minute: int,
    ) -> BookingMutation:
        if new_end_minute <= current_end_minute:
            raise ValueError("extension end must be later than the current end")
        page = self._require_page()
        page.goto(
            urljoin(self.settings.base_url, f"event?eventId={event_id}"),
            wait_until="domcontentloaded",
        )
        page.locator('[data-cy="time-range-end-time"]:visible').wait_for(
            state="visible", timeout=self.settings.timeout_ms
        )
        form = self.read_booking_form()
        expected = (target_date, room_name, start_minute, current_end_minute)
        actual = (
            form.event_date,
            form.room_name,
            form.start_minute,
            form.end_minute,
        )
        if actual != expected:
            raise AmbiguousBookingResult(
                f"event {event_id} edit form is {actual}, expected {expected}"
            )
        self._set_native_time('[data-cy="time-range-end-time"]', new_end_minute)
        updated = self.read_booking_form()
        if (
            updated.event_date,
            updated.room_name,
            updated.start_minute,
            updated.end_minute,
        ) != (target_date, room_name, start_minute, new_end_minute):
            raise PageContractError("extension form changed unexpected fields")
        if not updated.save_enabled:
            raise BookingRejected("; ".join(updated.warnings) or "extension Save is disabled")
        return self._submit_and_reconcile(
            action="extend",
            target_date=target_date,
            room_name=room_name,
            start_minute=start_minute,
            end_minute=new_end_minute,
            known_event_id=event_id,
        )
