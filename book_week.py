"""Book practice rooms throughout Asimut's current live booking window.

This script applies all RWCMD booking rules:
- Rolling quota of 28 reservation hours
- Maximum 2hr peak allowance (Mon-Fri 9am-4pm)
- Minimum 30 min, maximum 2hr per booking
- 60-minute gap between same room bookings
- Room-specific booking horizons freshly discovered from Asimut each run

Usage:
    python book_week.py              # Run with visible browser
    python book_week.py --headless   # Run without browser window (for scheduled tasks)
"""

import re
import sys
import json
import time
import argparse
import os
import copy
import urllib.request
import urllib.error
from dataclasses import dataclass, replace
from urllib.parse import urlsplit
from pathlib import Path
from datetime import date, datetime, timedelta
from playwright.sync_api import sync_playwright

from asimut_auth import (
    AutonomousLoginError,
    clear_auth_recovery_block,
    configure_windows_credential_interactive,
    ensure_asimut_session,
    windows_credential_is_configured,
)
from app_settings import (
    InterProcessFileLock,
    SettingsError,
    atomic_write_json,
    load_settings as load_settings_document,
    update_settings,
)
from agenda_snapshot import (
    AGENDA_SNAPSHOT_FILE,
    AgendaSnapshotError,
    publish_agenda_snapshot,
)
from booking_strategy import (
    BookingStrategyError,
    DailyPlanningPreferences,
    load_booking_strategy as load_booking_strategy_preferences,
)
from daily_planner import (
    BookingOpportunity,
    choose_horizon_opportunity,
    enumerate_gap_opportunities,
    interval_overlap_minutes,
    opportunity_rank,
    select_day_plan,
)
from booking_plan import (
    BookingPlanError,
    BookingPlanSnapshot,
    DayPlan,
    PlanCandidate,
    booking_plan_fingerprint,
    clear_booking_plan,
    write_booking_plan,
)
from event_identity import (
    deduplicate_events,
    event_identity_v2,
    resolve_ignored_event_keys,
)
from practice_plan import (
    PracticePlan,
    PracticePlanError,
    as_date,
    cap_duration_minutes,
    load_practice_plan,
    max_bookings_for_budget,
    remaining_target_hours,
)
from mutation_receipts import (
    MutationReceiptError,
    attach_event_url,
    list_pending as list_pending_mutation_receipts,
    mark_resolved as resolve_mutation_receipt,
    mark_verified as verify_mutation_receipt,
    record_pending_cancel,
    record_pending_create,
    record_pending_extension,
)
from live_room_policy import (
    LiveRoomPolicy,
    LiveRoomPolicyError,
    build_live_room_policy,
    canonical_overview_url,
    format_horizon_minutes,
)
from room_catalog import RoomCatalogError, refresh_from_site as refresh_room_catalog
from room_preferences import (
    DEFAULT_ORDERED_ROOMS,
    RoomPreferences,
    RoomPreferencesError,
    load_room_preferences,
    validate_room_name,
)
from runtime_guard import (
    SingleInstanceLock,
    booking_identity_matches,
    booking_summary_matches,
    booking_times_match,
    configure_utf8_stdio,
    is_confirmed_post_save_url,
    is_new_booking_form_url,
    normalize_date,
    parse_confirmed_event_id,
    parse_event_editor_id,
)

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

APP_DIR = Path(__file__).resolve().parent
state_file = APP_DIR / "data" / "browser_state" / "state.json"
history_file = APP_DIR / "data" / "booking_history.json"
settings_file = APP_DIR / "data" / "settings.json"

# ntfy.sh notification settings
# Change this to your own unique topic name (like "asimut-yourname-secret123")
NTFY_TOPIC = "asimut-loxty"
NTFY_ENABLED = True  # Set to False to disable notifications
MANUAL_RECONFIRMATION_REMINDER = (
    "Manual action: reconfirm provisional bookings on RWCMD Wi-Fi when Asimut "
    "enables it."
)

# Advanced College-rule overrides used only when no user config.yaml exists.
# Room order, exclusions, requirements, block length, and fragmentation are
# ordinary GUI settings.  Room horizons are never accepted from configuration;
# every authenticated run observes them afresh from Asimut.
_DEFAULT_CONFIG = {
    'rolling_quota': 28,
    'peak_start': 9,
    'peak_end': 16,
    'max_peak_hours': 2,
    'same_room_gap_minutes': 60,
}


class ConfigError(RuntimeError):
    """Raised when an existing configuration cannot be followed exactly."""


def _config_number(value, label, *, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{label} must be a number")
    number = float(value)
    if minimum is not None and number < minimum:
        raise ConfigError(f"{label} must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise ConfigError(f"{label} must be at most {maximum}")
    return number


def load_config():
    """Load the supported YAML schema; an existing bad file never falls back."""
    config_file = APP_DIR / "config" / "config.yaml"

    if not config_file.exists():
        return copy.deepcopy(_DEFAULT_CONFIG)

    if not YAML_AVAILABLE:
        raise ConfigError("config.yaml exists but PyYAML is not installed")

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"Could not read config.yaml: {exc}") from exc

    if not isinstance(cfg, dict):
        raise ConfigError("config.yaml must contain a YAML mapping")
    unknown_top = set(cfg) - {"rules"}
    if unknown_top:
        raise ConfigError(f"Unsupported config section(s): {', '.join(sorted(unknown_top))}")

    result = copy.deepcopy(_DEFAULT_CONFIG)
    rules = cfg.get("rules", {})
    if not isinstance(rules, dict):
        raise ConfigError("rules must be a mapping")
    unknown_rules = set(rules) - {"rolling_quota", "same_room_gap_minutes", "peak_hours"}
    if unknown_rules:
        raise ConfigError(f"Unsupported rules setting(s): {', '.join(sorted(unknown_rules))}")

    if "rolling_quota" in rules:
        result["rolling_quota"] = _config_number(
            rules["rolling_quota"], "rules.rolling_quota", minimum=0.5, maximum=168
        )
    if "same_room_gap_minutes" in rules:
        gap = _config_number(
            rules["same_room_gap_minutes"],
            "rules.same_room_gap_minutes",
            minimum=0,
            maximum=240,
        )
        if gap % 15:
            raise ConfigError("rules.same_room_gap_minutes must use 15-minute steps")
        result["same_room_gap_minutes"] = int(gap)

    peak = rules.get("peak_hours", {})
    if not isinstance(peak, dict):
        raise ConfigError("rules.peak_hours must be a mapping")
    unknown_peak = set(peak) - {"start", "end", "max_hours"}
    if unknown_peak:
        raise ConfigError(f"Unsupported peak_hours setting(s): {', '.join(sorted(unknown_peak))}")
    for key, result_key in (("start", "peak_start"), ("end", "peak_end")):
        if key in peak:
            value = peak[key]
            if not isinstance(value, str) or re.fullmatch(r"(?:[01][0-9]|2[0-3]):00", value) is None:
                raise ConfigError(f"rules.peak_hours.{key} must use whole-hour HH:00")
            result[result_key] = int(value[:2])
    if result["peak_end"] <= result["peak_start"]:
        raise ConfigError("rules.peak_hours.end must be later than start")
    if "max_hours" in peak:
        result["max_peak_hours"] = _config_number(
            peak["max_hours"], "rules.peak_hours.max_hours", minimum=0, maximum=24
        )

    return result


# Load config and set module-level constants. Keep imports testable while main
# still fails closed before Playwright if an existing configuration is invalid.
try:
    _CONFIG = load_config()
    _CONFIG_ERROR = None
except ConfigError as exc:
    _CONFIG = copy.deepcopy(_DEFAULT_CONFIG)
    _CONFIG_ERROR = exc
MAX_ROLLING_QUOTA_HOURS = _CONFIG['rolling_quota']
MAX_BOOKING_HOURS = 2
MIN_BOOKING_MINUTES = 30
LIVE_ACTION_MINUTES = tuple(range(MIN_BOOKING_MINUTES, MAX_BOOKING_HOURS * 60 + 1, 15))


def _action_maximum_minutes(max_action_minutes):
    """Return the per-mutation duration ceiling for a parsed live-test scope."""

    if max_action_minutes is None:
        return MAX_BOOKING_HOURS * 60
    if (
        isinstance(max_action_minutes, bool)
        or not isinstance(max_action_minutes, int)
        or max_action_minutes not in LIVE_ACTION_MINUTES
    ):
        raise ValueError("max_action_minutes must be 30-120 in 15-minute steps")
    return max_action_minutes


def _bounded_create_duration_minutes(
    requested_minutes,
    remaining_daily_hours,
    remaining_weekly_hours,
    max_action_minutes,
):
    """Apply every create budget, including the optional live-test ceiling."""

    return cap_duration_minutes(
        requested_minutes,
        remaining_daily_hours,
        remaining_weekly_hours,
        minimum_minutes=MIN_BOOKING_MINUTES,
        maximum_minutes=_action_maximum_minutes(max_action_minutes),
    )


def _extension_target_end_hour(start_hour, current_end_hour, max_possible_duration):
    """Return a real extension target, or None when the current booking is final."""

    proposed_end = start_hour + max_possible_duration / 60
    return proposed_end if current_end_hour + 1e-9 < proposed_end else None
PEAK_START = _CONFIG['peak_start']
PEAK_END = _CONFIG['peak_end']
MAX_PEAK_HOURS = _CONFIG['max_peak_hours']
SAME_ROOM_GAP_MINUTES = _CONFIG['same_room_gap_minutes']
CONFIGURED_SAME_ROOM_GAP_MINUTES = SAME_ROOM_GAP_MINUTES
DEFAULT_BOOKINGS_PER_DAY = 3  # Default limit per day, dynamically adjusted based on enabled days
# These run-scoped values are installed only after a fresh, complete catalog
# has been collected in the authenticated browser.  The default room order is
# available for non-horizon parsing and preference migration, but no horizon
# fallback exists.
ACTIVE_ROOM_POLICY: LiveRoomPolicy | None = None
PRIORITY_ROOMS = list(DEFAULT_ORDERED_ROOMS)
LIVE_CATALOG_ROOM_NAMES = list(DEFAULT_ORDERED_ROOMS)
LIVE_CATALOG_ROOM_LOCATION_IDS: dict[str, int] = {}
ROOM_HORIZON_MINUTES: dict[str, int] = {}
BOOKING_WINDOW_DATES: tuple = ()
MINIMUM_BLOCK_MINUTES = MIN_BOOKING_MINUTES
ALLOW_FRAGMENTED_SESSIONS = True
SITE_CLOCK_OFFSET_BOUNDS: tuple[float, float] | None = None
EXTENSION_PREP_WINDOW_SECONDS = 180
HORIZON_SAVE_SETTLE_SECONDS = 0.25
HORIZON_CLOCK_SAFETY_SECONDS = 0.05
HORIZON_SNIPE_GRACE_SECONDS = 180

ASIMUT_BASE_URL = "https://rwcmd.asimut.net"
ASIMUT_AGENDA_URL = f"{ASIMUT_BASE_URL}/agenda"
# A fresh policy replaces this inert import-time value before any real grid
# navigation.  Keeping it origin-only makes snapshot helpers import-safe while
# ensuring no stale location group can be booking authority.
ASIMUT_OVERVIEW_URL = f"{ASIMUT_BASE_URL}/overview"


def install_live_room_policy(policy, *, today=None):
    """Install one freshly observed immutable policy for this process run."""

    if not isinstance(policy, LiveRoomPolicy):
        raise LiveRoomPolicyError("policy must be a LiveRoomPolicy")
    policy_dates = policy.booking_dates(today)
    if policy.site_minimum_booking_minutes != MIN_BOOKING_MINUTES:
        raise LiveRoomPolicyError(
            "Asimut's live minimum booking length changed from the supported "
            f"{MIN_BOOKING_MINUTES} minutes to "
            f"{policy.site_minimum_booking_minutes} minutes"
        )
    if policy.site_maximum_booking_minutes != MAX_BOOKING_HOURS * 60:
        raise LiveRoomPolicyError(
            "Asimut's live maximum booking length changed from the supported "
            f"{MAX_BOOKING_HOURS * 60} minutes to "
            f"{policy.site_maximum_booking_minutes} minutes"
        )
    expected_overview_url = canonical_overview_url(
        policy.all_room_names,
        policy.all_room_location_ids,
    )
    if policy.overview_url != expected_overview_url:
        raise LiveRoomPolicyError(
            "The live room policy overview URL does not match its exact fresh "
            "room location IDs"
        )
    if any(room not in policy.all_room_location_ids for room in policy.room_order):
        raise LiveRoomPolicyError(
            "The eligible room order is not a subset of the fresh catalog identities"
        )

    global ACTIVE_ROOM_POLICY
    global PRIORITY_ROOMS
    global LIVE_CATALOG_ROOM_NAMES
    global LIVE_CATALOG_ROOM_LOCATION_IDS
    global ROOM_HORIZON_MINUTES
    global BOOKING_WINDOW_DATES
    global MINIMUM_BLOCK_MINUTES
    global ALLOW_FRAGMENTED_SESSIONS
    global SITE_CLOCK_OFFSET_BOUNDS
    global SAME_ROOM_GAP_MINUTES
    global ASIMUT_OVERVIEW_URL
    ACTIVE_ROOM_POLICY = policy
    PRIORITY_ROOMS = list(policy.room_order)
    LIVE_CATALOG_ROOM_NAMES = list(policy.all_room_names)
    LIVE_CATALOG_ROOM_LOCATION_IDS = dict(policy.all_room_location_ids)
    ROOM_HORIZON_MINUTES = dict(policy.room_horizon_minutes)
    BOOKING_WINDOW_DATES = policy_dates
    MINIMUM_BLOCK_MINUTES = policy.minimum_block_minutes
    ALLOW_FRAGMENTED_SESSIONS = policy.allow_fragmented_sessions
    SITE_CLOCK_OFFSET_BOUNDS = policy.site_clock_offset_bounds
    SAME_ROOM_GAP_MINUTES = max(
        CONFIGURED_SAME_ROOM_GAP_MINUTES,
        policy.site_minimum_booking_gap_minutes,
    )
    ASIMUT_OVERVIEW_URL = policy.overview_url
    return policy


def require_live_room_policy():
    """Return the current run policy or fail before any room timing decision."""

    if ACTIVE_ROOM_POLICY is None:
        raise LiveRoomPolicyError(
            "Room policy has not been freshly observed from Asimut in this run"
        )
    return ACTIVE_ROOM_POLICY


def room_horizon_minutes(room):
    """Return one exact fresh horizon; there is deliberately no default."""

    if not isinstance(room, str) or room not in ROOM_HORIZON_MINUTES:
        raise LiveRoomPolicyError(
            f"Room {room!r} has no freshly observed booking horizon"
        )
    return ROOM_HORIZON_MINUTES[room]


def booking_window_dates(today=None):
    """Return the installed live calendar dates, confirming the first is today."""

    today = today or datetime.now().date()
    if isinstance(today, datetime) or not isinstance(today, date):
        raise LiveRoomPolicyError("today must be a date")
    dates = tuple(BOOKING_WINDOW_DATES)
    if not dates or dates[0] != today or any(
        value != today + timedelta(days=index)
        for index, value in enumerate(dates)
    ):
        raise LiveRoomPolicyError(
            "The live booking-window dates are missing or stale for today"
        )
    return dates


def booking_window_day_offsets(today=None):
    """Return navigation offsets from the installed absolute live cutoff."""

    return tuple(range(len(booking_window_dates(today))))


def refresh_live_room_policy(page, preferences, *, today=None):
    """Refresh every room from Asimut and install the resulting run policy."""

    catalog = refresh_room_catalog(page)
    policy = build_live_room_policy(catalog, preferences)
    install_live_room_policy(policy, today=today)
    horizons = sorted(set(policy.room_horizon_minutes.values()), reverse=True)
    horizon_text = ", ".join(format_horizon_minutes(value) for value in horizons)
    print(
        "  Fresh room policy: "
        f"{len(policy.room_order)} eligible rooms; horizons {horizon_text}; "
        f"window ends {policy.booking_horizon.astimezone():%Y-%m-%d %H:%M %Z}"
    )
    if policy.site_clock_offset_bounds is not None:
        lower, upper = policy.site_clock_offset_bounds
        print(
            "  Site clock evidence: Asimut is between "
            f"{lower:+.3f}s and {upper:+.3f}s relative to this PC"
        )
    return policy


def is_horizon_snipe_timing_candidate(seconds_until_bookable):
    """Accept imminent and just-opened edges, but no stale normal slots."""

    return (
        -HORIZON_SNIPE_GRACE_SECONDS
        <= seconds_until_bookable
        <= HORIZON_SNIPE_GRACE_SECONDS
    )


def page_is_asimut_origin(page):
    """Return whether the page is on the exact HTTPS RWCMD Asimut origin."""

    try:
        parsed = urlsplit(page.url)
        return (
            not page.is_closed()
            and parsed.scheme == "https"
            and parsed.hostname == "rwcmd.asimut.net"
        )
    except Exception:
        return False


def page_is_authenticated(page):
    """Check for an authenticated Asimut shell, not merely an Asimut-domain URL."""

    try:
        if not page_is_asimut_origin(page):
            return False
        indicators = (
            "[data-cy='menu-item-locations']",
            "[data-cy='menu-item-agenda']",
            "[data-cy^='menu-item-']",
            "a[href='/agenda']",
            "app-root router-outlet",
            "app-wrapper",
        )
        return any(page.locator(selector).count() > 0 for selector in indicators)
    except Exception:
        return False


def restore_page_authentication(page):
    """Recover an expired Asimut session and persist the refreshed state."""

    result = ensure_asimut_session(page)
    persist_storage_state(page.context)
    if result.recovered:
        factors = "password and SMS" if result.used_sms else "stored password"
        print(f"  Asimut session restored autonomously using {factors}.")
    return result


def safe_goto(page, url, *, attempts=3, timeout=60000, require_authenticated=True):
    """Navigate with bounded retry and fail closed on an SSO redirect."""

    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            if page.is_closed():
                raise RuntimeError("Browser page is closed")
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            if require_authenticated and not page_is_authenticated(page):
                restore_page_authentication(page)
                page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                if not page_is_authenticated(page):
                    raise AutonomousLoginError(
                        "Asimut did not remain authenticated after recovery"
                    )
            return
        except AutonomousLoginError:
            raise
        except Exception as exc:
            last_error = exc
            if "not authenticated" in str(exc).lower() or attempt >= attempts:
                break
            print(f"  Navigation attempt {attempt}/{attempts} failed: {exc}")
            time.sleep(min(2 ** (attempt - 1), 4))
    raise RuntimeError(f"Could not load {url} after {attempts} attempts: {last_error}") from last_error


def safe_reload(page, *, attempts=3):
    """Reload the current authenticated page with bounded retry."""

    last_error = None
    target_url = page.url
    for attempt in range(1, attempts + 1):
        try:
            page.reload(wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            if not page_is_authenticated(page):
                restore_page_authentication(page)
                page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                if not page_is_authenticated(page):
                    raise AutonomousLoginError(
                        "Asimut did not remain authenticated after recovery"
                    )
            return
        except AutonomousLoginError:
            raise
        except Exception as exc:
            last_error = exc
            if "not authenticated" in str(exc).lower() or attempt >= attempts:
                break
            time.sleep(min(2 ** (attempt - 1), 4))
    raise RuntimeError(f"Could not reload {page.url}: {last_error}") from last_error


class BookingVerificationError(RuntimeError):
    """A Save may have mutated Asimut, but the exact result was not provable."""


class BookingCancellationError(RuntimeError):
    """An exact cancellation target or safe cancellation control was not proven."""


def page_booking_snapshot(page):
    """Extract one structured booking record from the active event component.

    This intentionally never reads ``document.body``.  Only controls, selected
    values, and labelled content inside the form/event component containing the
    booking time controls may contribute to confirmation.  Missing or ambiguous
    fields stay null so verification fails closed.
    """

    return page.evaluate(r"""(configuredRooms) => {
        const startSelectors = [
            '#startDate',
            "input[aria-label='Start time']",
            "input[data-cy='time-range-start-time']",
            "input[formcontrolname='startTime']",
            "input[data-cy*='start-time']",
            "input[name*='startTime' i]"
        ];
        const endSelectors = [
            '#endDate',
            "input[aria-label='End time']",
            "input[data-cy='time-range-end-time']",
            "input[formcontrolname='endTime']",
            "input[data-cy*='end-time']",
            "input[name*='endTime' i]"
        ];
        const roomSelectors = [
            "input[aria-label='Event location']",
            "input[data-cy*='event-location']",
            "input[formcontrolname*='location' i]"
        ];
        const dateSelectors = [
            "input[aria-label='Event date']",
            "input[data-cy*='event-date']",
            "input[formcontrolname*='date' i]"
        ];
        const componentSelector = [
            'app-event-form', 'app-event-editor', 'app-event-detail',
            'app-event', 'app-arrangement', 'mat-dialog-container',
            "[data-cy*='event-form']", "[data-cy*='event-detail']",
            "[data-cy*='arrangement']"
        ].join(',');

        const firstMatch = (root, selectors) => {
            for (const selector of selectors) {
                const match = root.querySelector(selector);
                if (match) return match;
            }
            return null;
        };
        const startControl = firstMatch(document, startSelectors);
        const endControl = firstMatch(document, endSelectors);
        const roots = [];
        const addRoot = (root) => {
            if (root && !roots.includes(root)) roots.push(root);
        };
        for (const control of [startControl, endControl]) {
            if (!control) continue;
            addRoot(control.closest('form'));
            addRoot(control.closest(componentSelector));
        }
        for (const selector of [
            'form', 'app-event-form', 'app-event-editor', 'app-event-detail',
            'app-event', 'app-arrangement', "[data-cy*='event-form']",
            "[data-cy*='event-detail']", "[data-cy*='arrangement']"
        ]) {
            for (const root of document.querySelectorAll(selector)) {
                const hasStart = firstMatch(root, startSelectors);
                const hasEnd = firstMatch(root, endSelectors);
                const isDetails = /detail|arrangement/i.test(
                    `${root.tagName || ''} ${root.id || ''} ${root.className || ''} ${root.getAttribute('data-cy') || ''}`
                );
                if ((hasStart && hasEnd) || isDetails) addRoot(root);
            }
        }
        // The current arrangement page renders one exact event card instead
        // of an app-event-detail component. Bind it to the positive event id
        // in this canonical arrangement URL; another card cannot confirm it.
        let expectedEventId = null;
        try {
            const current = new URL(window.location.href);
            const eventIds = current.searchParams.getAll('eventId');
            if (
                current.protocol === 'https:' &&
                current.hostname === 'rwcmd.asimut.net' &&
                current.port === '' &&
                current.pathname === '/arrangement' &&
                current.hash === '' &&
                eventIds.length === 1 &&
                /^[1-9][0-9]*$/.test(eventIds[0])
            ) expectedEventId = eventIds[0];
        } catch (_error) {
            expectedEventId = null;
        }
        if (expectedEventId) {
            // A persisted arrangement may contain other reusable event/form
            // components. Discard every generic candidate and bind extraction
            // exclusively to the card whose id is proven by the current URL.
            roots.length = 0;
            addRoot(document.querySelector(`[data-cy="event_${expectedEventId}"]`));
            addRoot(document.getElementById(`event_${expectedEventId}`));
        }

        const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
        const semanticName = (element) => clean([
            element.id,
            element.getAttribute && element.getAttribute('name'),
            element.getAttribute && element.getAttribute('formcontrolname'),
            element.getAttribute && element.getAttribute('data-cy'),
            element.getAttribute && element.getAttribute('aria-label'),
            element.getAttribute && element.getAttribute('placeholder'),
            typeof element.className === 'string' ? element.className : ''
        ].filter(Boolean).join(' ')).toLowerCase();
        const unique = (values) => [...new Set(values.filter(Boolean))];
        const roomTokens = (value) => {
            const text = clean(value);
            const matches = unique(configuredRooms.filter((room) => {
                const escaped = room.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                return new RegExp(
                    `(^|[^A-Za-z0-9.])${escaped}(?=$|[^A-Za-z0-9.])`,
                    'i'
                ).test(text);
            }));
            if (!matches.length) return [];
            const longest = Math.max(...matches.map(room => room.length));
            return matches.filter(room => room.length === longest);
        };
        const only = (values) => {
            const candidates = unique(values);
            return candidates.length === 1 ? candidates[0] : null;
        };
        const canonicalTime = (value) => {
            const match = clean(value).match(/^((?:[01][0-9]|2[0-3]):[0-5][0-9])(?::00)?$/);
            return match ? match[1] : null;
        };
        const canonicalDateParts = (year, month, day) => {
            const y = Number(year), m = Number(month), d = Number(day);
            const candidate = new Date(Date.UTC(y, m - 1, d));
            if (
                candidate.getUTCFullYear() !== y ||
                candidate.getUTCMonth() !== m - 1 ||
                candidate.getUTCDate() !== d
            ) return null;
            return `${String(y).padStart(4, '0')}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
        };
        const monthNumbers = {
            january: 1, february: 2, march: 3, april: 4, may: 5, june: 6,
            july: 7, august: 8, september: 9, october: 10, november: 11, december: 12,
            jan: 1, feb: 2, mar: 3, apr: 4, jun: 6, jul: 7, aug: 8,
            sep: 9, sept: 9, oct: 10, nov: 11, dec: 12
        };
        const dateTokens = (value) => {
            const text = clean(value);
            const results = [];
            let match;
            const iso = /(?<![0-9])([0-9]{4})-([0-9]{2})-([0-9]{2})(?![0-9])/g;
            while ((match = iso.exec(text)) !== null) {
                results.push(canonicalDateParts(match[1], match[2], match[3]));
            }
            const numeric = /(?<![0-9])([0-9]{1,2})[\/-]([0-9]{1,2})[\/-]([0-9]{4})(?![0-9])/g;
            while ((match = numeric.exec(text)) !== null) {
                results.push(canonicalDateParts(match[3], match[2], match[1]));
            }
            const numericShortYear = /(?<![0-9])([0-9]{1,2})[\/-]([0-9]{1,2})[\/-]([0-9]{2})(?![0-9])/g;
            while ((match = numericShortYear.exec(text)) !== null) {
                results.push(canonicalDateParts(`20${match[3]}`, match[2], match[1]));
            }
            const dayMonth = /(?<![A-Za-z0-9])([0-9]{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+([0-9]{4})(?![0-9])/gi;
            while ((match = dayMonth.exec(text)) !== null) {
                results.push(canonicalDateParts(match[3], monthNumbers[match[2].toLowerCase()], match[1]));
            }
            const monthDay = /(?<![A-Za-z0-9])(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+([0-9]{1,2}),?\s+([0-9]{4})(?![0-9])/gi;
            while ((match = monthDay.exec(text)) !== null) {
                results.push(canonicalDateParts(match[3], monthNumbers[match[1].toLowerCase()], match[2]));
            }
            return unique(results);
        };
        const controlValue = (control) => {
            if (!control) return '';
            if (control.tagName === 'SELECT' && control.selectedOptions.length === 1) {
                return clean(control.selectedOptions[0].value || control.selectedOptions[0].textContent);
            }
            return clean(control.value);
        };

        const snapshotFrom = (root) => {
            const rootStart = firstMatch(root, startSelectors);
            const rootEnd = firstMatch(root, endSelectors);
            let start = canonicalTime(controlValue(rootStart));
            let end = canonicalTime(controlValue(rootEnd));

            const selectedRooms = [];
            const labelledRoom = firstMatch(root, roomSelectors);
            if (labelledRoom) {
                selectedRooms.push(...roomTokens(controlValue(labelledRoom)));
                selectedRooms.push(...roomTokens(labelledRoom.textContent));
            }
            for (const element of root.querySelectorAll(
                "select option:checked, [aria-selected='true'], [data-selected='true'], mat-chip, .mat-chip, .mat-mdc-chip"
            )) {
                selectedRooms.push(...roomTokens(element.textContent));
                selectedRooms.push(...roomTokens(element.value));
            }
            let room = only(selectedRooms);
            if (!room) {
                const semanticRooms = [];
                for (const element of root.querySelectorAll('input, select, textarea, [data-cy], [aria-label], [id], [class]')) {
                    if (!/(?:^|[-_\s])(room|location)(?:$|[-_\s])/.test(semanticName(element))) continue;
                    semanticRooms.push(...roomTokens(element.value));
                    if (!element.matches('option, [role="option"], mat-option')) {
                        semanticRooms.push(...roomTokens(element.textContent));
                    }
                }
                room = only(semanticRooms);
            }
            if (!room) room = only(roomTokens(root.innerText));

            const directDates = [];
            const labelledDate = firstMatch(root, dateSelectors);
            if (labelledDate) {
                directDates.push(...dateTokens(controlValue(labelledDate)));
                directDates.push(...dateTokens(labelledDate.textContent));
            }
            for (const element of root.querySelectorAll('input, select, textarea, time, [datetime], [data-date]')) {
                if (!/(date|day|calendar)/.test(semanticName(element)) &&
                    !element.hasAttribute('datetime') && !element.hasAttribute('data-date')) continue;
                directDates.push(...dateTokens(element.value));
                directDates.push(...dateTokens(element.getAttribute('datetime')));
                directDates.push(...dateTokens(element.getAttribute('data-date')));
                directDates.push(...dateTokens(element.textContent));
            }
            let bookingDate = only(directDates);
            if (!bookingDate) bookingDate = only(dateTokens(root.innerText));

            if (!start || !end) {
                const ranges = [];
                const rangePattern = /(?<![0-9])((?:[01][0-9]|2[0-3]):[0-5][0-9])\s*(?:-|–|—|to)\s*((?:[01][0-9]|2[0-3]):[0-5][0-9])(?![0-9])/gi;
                let match;
                while ((match = rangePattern.exec(clean(root.innerText))) !== null) {
                    ranges.push(`${match[1]}|${match[2]}`);
                }
                const range = only(ranges);
                if (range) [start, end] = range.split('|');
            }
            return {room, date: bookingDate, start, end};
        };

        let partial = {room: null, date: null, start: null, end: null};
        for (const root of roots) {
            const snapshot = snapshotFrom(root);
            if (snapshot.room && snapshot.date && snapshot.start && snapshot.end) return snapshot;
            partial = snapshot;
        }
        return partial;
    }""", LIVE_CATALOG_ROOM_NAMES)


def verify_persisted_booking_page(page, room, target_date, start_time, end_time):
    """Reload and reconcile the complete saved reservation before counting it."""

    event_url = page.url
    if not is_confirmed_post_save_url(event_url):
        raise BookingVerificationError(
            "Persisted booking verification requires an exact positive arrangement event URL; "
            f"got {event_url!r}"
        )

    safe_reload(page)
    if page.url != event_url or not is_confirmed_post_save_url(page.url):
        raise BookingVerificationError(
            "Asimut assigned an event id, but the persisted event URL changed during verification"
        )

    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        snapshot = page_booking_snapshot(page)
        if booking_summary_matches(snapshot, room, target_date, start_time, end_time):
            return True
        page.wait_for_timeout(250)

    raise BookingVerificationError(
        "Asimut assigned an event id, but its persisted room/date/time did not match "
        f"{room} {normalize_date(target_date).isoformat()} {start_time}-{end_time}. "
        "The run stopped to avoid making duplicate bookings."
    )


def _visible_save_rejection(page):
    """Return a concrete post-Save rejection message, if one is visible."""

    selectors = (
        "text=conflicting events",
        "text=resolve the conflicts",
        "text=booking clashes",
        "text=You are not allowed",
        "text=not allowed to book",
        ".mat-error",
    )
    for selector in selectors:
        try:
            element = page.locator(selector).first
            if element.count() > 0 and element.is_visible():
                return (element.text_content() or selector).strip()[:200]
        except Exception:
            continue
    return None


def refresh_new_booking_validation(page, expected_end_time):
    """Force and prove one fresh no-Save validation at the horizon boundary."""

    end_input = page.locator(
        "#endDate, input[aria-label='End time'], "
        "input[data-cy='time-range-end-time'], input[formcontrolname='endTime']"
    ).first
    if end_input.count() == 0 or not end_input.is_visible():
        return False, "end-time control is unavailable"

    def is_exact_event_check(response):
        try:
            parsed = urlsplit(response.url)
            return (
                parsed.scheme == "https"
                and parsed.hostname == "rwcmd.asimut.net"
                and parsed.port is None
                and parsed.path == "/services/v2/event/type=check"
                and not parsed.query
                and not parsed.fragment
            )
        except Exception:
            return False

    try:
        with page.expect_response(is_exact_event_check, timeout=5000) as pending:
            # Playwright fill dispatches a fresh input event even when the text
            # is unchanged; Tab commits the Angular control and its debounced
            # server-side horizon check.
            end_input.fill(expected_end_time)
            end_input.press("Tab")
        response = pending.value
        if not is_exact_event_check(response) or not response.ok:
            return False, "event validation response was not trusted HTTP success"
        page.wait_for_timeout(300)
    except Exception as exc:
        return False, f"fresh event validation did not complete: {exc}"

    return True, "fresh event validation completed"


def wait_for_student_booking_option(page, *, timeout_ms=5000):
    """Wait for Asimut's delayed provisional-booking menu item."""

    deadline = time.monotonic() + timeout_ms / 1000
    selectors = (
        "mat-list-item:has-text('Student booking provisional')",
        "[role='menuitem']:has-text('Student booking provisional')",
        "text=Student booking provisional",
    )
    while time.monotonic() < deadline:
        if is_new_booking_form_url(page.url):
            return None
        for selector in selectors:
            try:
                option = page.locator(selector).first
                if option.count() > 0 and option.is_visible():
                    return option
            except Exception:
                continue
        page.wait_for_timeout(100)
    return None


def wait_for_new_booking_form(page, *, timeout_ms=10000):
    """Wait for an exact eventId=0 form whose time controls are usable."""

    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if is_new_booking_form_url(page.url):
            start_input = page.locator(
                "#startDate, input[aria-label='Start time'], "
                "input[data-cy='time-range-start-time'], "
                "input[formcontrolname='startTime']"
            ).first
            end_input = page.locator(
                "#endDate, input[aria-label='End time'], "
                "input[data-cy='time-range-end-time'], "
                "input[formcontrolname='endTime']"
            ).first
            try:
                if (
                    start_input.count() > 0
                    and end_input.count() > 0
                    and start_input.is_visible()
                    and end_input.is_visible()
                ):
                    return True
            except Exception:
                # Angular can replace the controls between locator reads.
                pass
        page.wait_for_timeout(100)
    return False


def wait_for_created_booking_outcome(
    page,
    receipt,
    room,
    target_date,
    start_time,
    end_time,
    *,
    timeout_seconds=30,
):
    """Classify Save as confirmed, rejected, or uncertain without duplicating."""

    receipt_id = receipt["id"]
    try:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            current_url = page.url
            if is_confirmed_post_save_url(current_url):
                try:
                    attach_event_url(receipt_id, current_url)
                except MutationReceiptError as exc:
                    raise BookingVerificationError(
                        f"Event {current_url} exists, but its crash-recovery receipt "
                        f"{receipt_id} could not be updated: {exc}"
                    ) from exc
                verify_persisted_booking_page(
                    page,
                    room,
                    target_date,
                    start_time,
                    end_time,
                )
                try:
                    verify_mutation_receipt(receipt_id, event_url=current_url)
                except MutationReceiptError as exc:
                    raise BookingVerificationError(
                        f"Booking {current_url} was verified, but receipt {receipt_id} "
                        f"could not be finalized: {exc}"
                    ) from exc
                return True

            rejection = _visible_save_rejection(page)
            if rejection:
                try:
                    resolve_mutation_receipt(
                        receipt_id,
                        resolution=f"Asimut rejected Save: {rejection}",
                    )
                except MutationReceiptError as exc:
                    raise BookingVerificationError(
                        f"Asimut rejected Save, but receipt {receipt_id} could not be closed: {exc}"
                    ) from exc
                print(f"  Save rejected: {rejection}")
                return False
            page.wait_for_timeout(250)

        raise BookingVerificationError(
            f"Save outcome is uncertain after {timeout_seconds}s (receipt {receipt_id}). "
            "No further mutations will be attempted until the agenda is reconciled."
        )
    except BookingVerificationError:
        raise
    except Exception as exc:
        raise BookingVerificationError(
            f"Save outcome is uncertain during verification (receipt {receipt_id}): {exc}. "
            "No further mutations will be attempted until the receipt is reconciled."
        ) from exc


def reconcile_pending_mutation_receipts(page, agenda_events):
    """Resolve crash leftovers against a complete agenda before new mutations."""

    pending = list_pending_mutation_receipts()
    if not pending:
        return

    agenda_records = [
        event for event in agenda_events if isinstance(event, dict)
    ]
    reservation_records = [
        event
        for event in agenda_records
        if event.get("room")
        and event.get("isReservation", True) is not False
    ]

    print(f"\nReconciling {len(pending)} interrupted booking mutation(s)...")
    for receipt in pending:
        event_url = receipt.get("event_url")
        if event_url is not None and not is_confirmed_post_save_url(event_url):
            raise BookingVerificationError(
                f"Interrupted mutation receipt {receipt['id']} contains a noncanonical "
                f"event URL; the receipt remains pending"
            )
        receipt_end = datetime.strptime(
            f"{receipt['date']} {receipt['end']}",
            "%Y-%m-%d %H:%M",
        )
        if receipt_end <= datetime.now():
            # Once the intended slot has ended, retrying it is impossible and
            # the receipt can no longer protect against a duplicate future
            # booking. Close it so a crash months ago cannot permanently jam
            # every autonomous run.
            resolve_mutation_receipt(
                receipt["id"],
                resolution="Interrupted mutation expired after its booking slot ended",
                event_url=receipt.get("event_url"),
            )
            print(f"  Closed expired receipt {receipt['id']}: booking slot has ended")
            continue

        if receipt.get("kind") == "cancel":
            outcome = classify_cancellation_agenda_outcome(
                agenda_records,
                receipt,
            )
            event_id = parse_confirmed_event_id(receipt["event_url"])
            if outcome == "not_applied":
                try:
                    safe_goto(page, receipt["event_url"])
                    verify_persisted_booking_page(
                        page,
                        receipt["room"],
                        receipt["date"],
                        receipt["start"],
                        receipt["end"],
                    )
                    resolve_mutation_receipt(
                        receipt["id"],
                        resolution=(
                            "Interrupted cancellation was not applied; exact "
                            "reservation remains"
                        ),
                        event_url=receipt["event_url"],
                    )
                except BookingVerificationError:
                    raise
                except Exception as exc:
                    raise BookingVerificationError(
                        f"Cancellation receipt {receipt['id']} still appears in the "
                        f"agenda but could not be proved unchanged: {exc}; the receipt "
                        "remains pending"
                    ) from exc
                print(
                    f"  Reconciled cancellation receipt {receipt['id']}: exact "
                    "reservation remains, so cancellation was not applied"
                )
                continue

            try:
                remove_extendable_booking_by_event_id(event_id)
            except SettingsError as exc:
                raise BookingVerificationError(
                    f"Cancellation of event {event_id} is proven by the complete "
                    f"agenda, but extension tracking could not be cleaned up: "
                    f"{exc}; receipt {receipt['id']} remains pending"
                ) from exc
            try:
                verify_mutation_receipt(
                    receipt["id"],
                    event_url=receipt["event_url"],
                )
            except Exception as exc:
                raise BookingVerificationError(
                    f"Cancellation of event {event_id} is proven by the complete "
                    f"agenda, but receipt {receipt['id']} could not be finalized: "
                    f"{exc}; the receipt remains pending"
                ) from exc
            try:
                clear_booking_plan()
            except BookingPlanError as exc:
                print(
                    "  WARNING: Reconciled cancellation, but the display-only "
                    f"booking plan could not be cleared: {exc}"
                )
            print(
                f"  Reconciled cancellation receipt {receipt['id']}: exact event "
                f"{event_id} is absent from the complete agenda"
            )
            continue

        intended = (
            receipt["room"],
            receipt["date"],
            receipt["start"],
            receipt["end"],
        )
        agenda_matches = [
            reservation
            for reservation in reservation_records
            if (
                reservation.get("room"),
                reservation.get("date"),
                normalize_time_string(reservation.get("startTime", "")),
                normalize_time_string(reservation.get("endTime", "")),
            )
            == intended
        ]
        if event_url is None:
            if len(agenda_matches) > 1:
                raise BookingVerificationError(
                    f"Interrupted mutation receipt {receipt['id']} matched multiple agenda "
                    "reservations. Autonomous mutations remain stopped."
                )
            if len(agenda_matches) == 0:
                raise BookingVerificationError(
                    f"Interrupted mutation receipt {receipt['id']} was not found exactly in the agenda. "
                    "Autonomous mutations remain stopped to prevent a duplicate or wrong booking."
                )
            agenda_event_id = agenda_matches[0].get("eventId")
            if (
                isinstance(agenda_event_id, bool)
                or not isinstance(agenda_event_id, int)
                or agenda_event_id <= 0
            ):
                raise BookingVerificationError(
                    f"The exact agenda reservation for receipt {receipt['id']} did not expose "
                    "a positive event id; the receipt remains pending"
                )
            event_url = f"{ASIMUT_BASE_URL}/arrangement?eventId={agenda_event_id}"

        expected_event_id = parse_confirmed_event_id(event_url)
        try:
            safe_goto(page, event_url)
            verify_persisted_booking_page(
                page,
                receipt["room"],
                receipt["date"],
                receipt["start"],
                receipt["end"],
            )
        except BookingVerificationError:
            raise
        except Exception as exc:
            raise BookingVerificationError(
                f"Exact event reconciliation failed for receipt {receipt['id']}: {exc}; "
                "the receipt remains pending"
            ) from exc
        if len(agenda_matches) != 1:
            raise BookingVerificationError(
                f"Recorded event {expected_event_id} verified, but the complete agenda "
                f"contained {len(agenda_matches)} exact reservations for receipt "
                f"{receipt['id']}; the receipt remains pending"
            )
        agenda_event_id = agenda_matches[0].get("eventId")
        if agenda_event_id != expected_event_id:
            raise BookingVerificationError(
                f"Recorded event {expected_event_id} verified, but the exact agenda "
                f"reservation reported event id {agenda_event_id!r}; the receipt remains pending"
            )
        try:
            verify_mutation_receipt(receipt["id"], event_url=event_url)
        except Exception as exc:
            raise BookingVerificationError(
                f"Exact event {expected_event_id} was verified, but receipt "
                f"{receipt['id']} could not be finalized: {exc}; the receipt remains pending"
            ) from exc
        print(
            f"  Reconciled receipt {receipt['id']}: persisted event and exact "
            "agenda reservation verified"
        )


def _agenda_records_for_mutation_proof(tracker):
    """Include every observed active event ID in mutation reconciliation proof."""

    records = [
        dict(event)
        for event in getattr(tracker, "agenda_events", [])
        if isinstance(event, dict)
    ]
    represented_ids = {
        event.get("eventId")
        for event in records
        if type(event.get("eventId")) is int and event.get("eventId") > 0
    }
    for event_id in getattr(tracker, "agenda_active_event_ids", []):
        if type(event_id) is int and event_id > 0 and event_id not in represented_ids:
            # Out-of-window cards are not planning input, but their IDs remain
            # relevant to a pending cancellation. Without a trusted in-window
            # tuple, their presence is ambiguous rather than verified absence.
            records.append({"eventId": event_id})
            represented_ids.add(event_id)
    return records


def validate_state_file():
    """Validate the local storage-state document before launching Chromium."""

    if not state_file.exists():
        raise RuntimeError(f"Browser session file not found: {state_file}")
    try:
        with open(state_file, "r", encoding="utf-8") as handle:
            state_data = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Browser session file is unreadable: {exc}") from exc
    if not isinstance(state_data, dict) or not isinstance(state_data.get("cookies"), list):
        raise RuntimeError("Browser session file is invalid (missing cookies list)")
    if not isinstance(state_data.get("origins"), list):
        raise RuntimeError("Browser session file is invalid (missing origins list)")


def persist_storage_state(context):
    """Persist session state through a validated atomic replacement."""

    state_data = context.storage_state()
    if not isinstance(state_data, dict) or not isinstance(state_data.get("cookies"), list):
        raise RuntimeError("Playwright returned an invalid browser storage state")
    if not isinstance(state_data.get("origins"), list):
        raise RuntimeError("Playwright returned an invalid browser storage state")
    atomic_write_json(state_file, state_data, backup=True)


def runtime_context_options(*, width=1920, height=1080):
    """Build context options while preserving any existing saved state."""

    options = {"viewport": {"width": width, "height": height}}
    if state_file.exists():
        validate_state_file()
        options["storage_state"] = str(state_file)
    return options


def authenticated_runtime_context_options(*, width=1920, height=1080):
    """Require a reusable state or an explicitly configured secure fallback.

    This check runs before Playwright starts.  A malformed existing state is
    never ignored or overwritten, and Credential Manager is not inspected when
    a structurally valid saved state is available.
    """

    try:
        options = runtime_context_options(width=width, height=height)
    except RuntimeError as exc:
        raise AutonomousLoginError(
            f"The saved Asimut browser session is unusable and was preserved: {exc}"
        ) from exc
    if "storage_state" in options:
        return options
    if windows_credential_is_configured():
        return options
    raise AutonomousLoginError(
        "No saved Asimut browser session or explicitly configured autonomous "
        "login is available. Use Login Setup once, or explicitly run "
        "--configure-autonomous-login."
    )


def login_only(*, headless=True):
    """Restore or verify the Asimut session without scanning or booking."""

    state_file.parent.mkdir(parents=True, exist_ok=True)
    context_options = authenticated_runtime_context_options()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(**context_options)
        page = context.new_page()
        result = ensure_asimut_session(page, explicit_retry=True)
        persist_storage_state(context)
        context.close()
        browser.close()
    if result.recovered:
        factors = "password and SMS" if result.used_sms else "stored password"
        print(f"Asimut login restored autonomously using {factors}.")
    else:
        print("Asimut login is already valid; refreshed the saved session state.")
    return 0


def setup_login(timeout_seconds=600):
    """Open one visible Playwright window and save a completed SSO session."""

    print("Opening Asimut login. Complete the Microsoft 365 sign-in in the browser window.")
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context_options = {"viewport": {"width": 1280, "height": 900}}
        if state_file.exists():
            try:
                validate_state_file()
                context_options["storage_state"] = str(state_file)
            except RuntimeError:
                pass
        context = browser.new_context(**context_options)
        page = context.new_page()
        page.goto(ASIMUT_BASE_URL, wait_until="domcontentloaded", timeout=60000)

        deadline = time.monotonic() + timeout_seconds
        returned_to_asimut_at = {}
        while time.monotonic() < deadline:
            live_pages = [candidate for candidate in context.pages if not candidate.is_closed()]
            if not live_pages:
                break
            now_monotonic = time.monotonic()
            for candidate in live_pages:
                candidate_key = id(candidate)
                if page_is_authenticated(candidate):
                    persist_storage_state(context)
                    clear_auth_recovery_block()
                    print(f"Login saved successfully: {state_file}")
                    context.close()
                    browser.close()
                    return 0
                if not page_is_asimut_origin(candidate):
                    returned_to_asimut_at.pop(candidate_key, None)
                    continue
                first_seen = returned_to_asimut_at.setdefault(
                    candidate_key,
                    now_monotonic,
                )
                if now_monotonic - first_seen >= 3:
                    # Asimut's custom element names can change independently of
                    # this client. A stable exact RWCMD Asimut HTTPS origin is
                    # sufficient to persist a candidate session; --check-only
                    # then proves the authenticated agenda and room grids before
                    # any mutation.
                    persist_storage_state(context)
                    print(f"Login returned to Asimut and was saved: {state_file}")
                    context.close()
                    browser.close()
                    return 0
            time.sleep(1)

        context.close()
        browser.close()
    print("ERROR: Login was not completed before the setup window closed or timed out.")
    return 2


def wait_until_target_time(target_time_str, page=None):
    """Wait until the specified time (HH:MM format) before proceeding.

    Keeps browser session alive with periodic small interactions.
    """
    target_hour, target_minute = map(int, target_time_str.split(':'))
    now = datetime.now()
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)

    # If target time already passed, proceed immediately
    if now >= target:
        print(f"Target time {target_time_str} already passed, proceeding immediately")
        return

    wait_seconds = (target - now).total_seconds()
    print(f"\n{'='*60}")
    print(f"READY - Waiting for target time: {target_time_str}")
    print(f"{'='*60}")
    print(f"Current time: {now.strftime('%H:%M:%S')}")
    print(f"Seconds to wait: {wait_seconds:.0f}")

    last_log_second = -1
    last_keepalive_second = -1

    # Wait with periodic status updates and session keepalive
    while datetime.now() < target:
        remaining = (target - datetime.now()).total_seconds()
        if remaining <= 0:
            break

        remaining_int = int(remaining)

        # Log countdown every 10 seconds (avoid duplicate logs)
        if remaining_int % 10 == 0 and remaining_int != last_log_second:
            print(f"  T-{remaining_int}s...")
            last_log_second = remaining_int

        # Keep browser session alive every 30 seconds
        if page and remaining_int % 30 == 0 and remaining_int != last_keepalive_second:
            try:
                page.evaluate("1")  # Minimal JS to keep session active
            except:
                pass
            last_keepalive_second = remaining_int

        time.sleep(0.5)

    print(f"\n>>> TARGET TIME REACHED: {datetime.now().strftime('%H:%M:%S')} <<<")
    print(f"{'='*60}\n")


def wait_until_datetime(
    target,
    page=None,
    *,
    label="HORIZON",
    settle_seconds=HORIZON_SAVE_SETTLE_SECONDS,
):
    """Wait for one exact local datetime while keeping the page responsive."""

    now = datetime.now()
    if now >= target:
        # Form preparation can legitimately consume the last fraction of the
        # lead window. Preserve the same small server-clock allowance even
        # when the local boundary was reached before this helper was entered.
        if settle_seconds > 0:
            time.sleep(settle_seconds)
        return 0.0

    initial_wait = (target - now).total_seconds()
    if initial_wait > EXTENSION_PREP_WINDOW_SECONDS + 1:
        raise BookingVerificationError(
            f"Refusing to hold a horizon editor for {initial_wait:.1f}s; "
            f"the preparation limit is {EXTENSION_PREP_WINDOW_SECONDS}s"
        )
    print(f"    [{label}] Form ready; Save unlocks in {initial_wait:.1f}s")
    monotonic_deadline = time.monotonic() + initial_wait + 5
    last_logged_second = None
    last_keepalive_second = None
    while True:
        if time.monotonic() > monotonic_deadline:
            raise BookingVerificationError(
                f"{label} boundary clock did not advance safely; no Save was attempted"
            )
        now = datetime.now()
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            break
        remaining_int = max(0, int(remaining))
        if remaining <= 10 and remaining_int != last_logged_second:
            print(f"    [{label}] T-{remaining:.1f}s")
            last_logged_second = remaining_int
        if (
            page is not None
            and remaining_int % 30 == 0
            and remaining_int != last_keepalive_second
        ):
            try:
                page.evaluate("1")
            except Exception:
                pass
            last_keepalive_second = remaining_int
        time.sleep(0.1 if remaining <= 10 else 0.5)

    # A very small server-clock allowance avoids firing on the wrong side of
    # the boundary without giving away the slot for the previous one-second
    # buffer used by the legacy path.
    if settle_seconds > 0:
        time.sleep(settle_seconds)
    return initial_wait


def wait_for_horizon_discovery_window(target_boundary, page=None):
    """Delay an early manual run until edge discovery is useful.

    No booking form is open during this wait.  Scheduled runs already start
    inside the three-minute window, while an explicitly launched test can be
    started earlier without exiting before its intended boundary exists.
    """

    discovery_at = target_boundary - timedelta(
        seconds=EXTENSION_PREP_WINDOW_SECONDS
    )
    now = datetime.now()
    if now >= discovery_at:
        return 0.0
    initial_wait = (discovery_at - now).total_seconds()
    print(
        f"\n  Horizon discovery opens at {discovery_at:%H:%M:%S}; "
        f"waiting {initial_wait:.0f}s before scanning"
    )
    last_keepalive = None
    while True:
        remaining = (discovery_at - datetime.now()).total_seconds()
        if remaining <= 0:
            break
        remaining_int = int(remaining)
        if (
            page is not None
            and remaining_int % 30 == 0
            and remaining_int != last_keepalive
        ):
            try:
                page.evaluate("1")
            except Exception:
                pass
            last_keepalive = remaining_int
        time.sleep(min(0.5, remaining))
    return initial_wait


def site_aligned_save_deadline(bookable_from):
    """Return a conservative local deadline for one Asimut wall-clock edge."""

    if SITE_CLOCK_OFFSET_BOUNDS is None:
        return bookable_from, None
    lower, upper = SITE_CLOCK_OFFSET_BOUNDS
    # ``lower`` is the smallest proven site-minus-local offset.  Waiting until
    # this deadline guarantees even the slowest clock allowed by the evidence
    # has crossed the site boundary.  A tiny additional margin covers scheduler
    # and float rounding without restoring the old multi-second lateness.
    deadline = bookable_from - timedelta(seconds=lower) + timedelta(
        seconds=HORIZON_CLOCK_SAFETY_SECONDS
    )
    return deadline, (lower, upper)


def estimated_site_latency_bounds(local_timestamp, site_boundary):
    """Estimate site-relative click latency from the run's clock evidence."""

    local_delta = (local_timestamp - site_boundary).total_seconds()
    if SITE_CLOCK_OFFSET_BOUNDS is None:
        return local_delta, local_delta
    lower, upper = SITE_CLOCK_OFFSET_BOUNDS
    return local_delta + lower, local_delta + upper


def is_room_available_to_book(room, target_date, slot_start_hour):
    """
    Check if a room is available to book based on its horizon.
    Rooms become available exactly X days later, by the minute.

    Args:
        room: Room name (e.g., "B1.09")
        target_date: The date we want to book (datetime object)
        slot_start_hour: The start hour of the slot (e.g., 9.5 for 9:30)

    Returns:
        Tuple of (is_available, reason_if_not)
    """
    horizon_minutes = room_horizon_minutes(room)
    now = datetime.now()

    # Calculate when this slot becomes available
    # The slot becomes bookable exactly the freshly observed horizon before it.
    # Handle both date and datetime objects
    if hasattr(target_date, 'date'):
        target_date_obj = target_date.date()
    else:
        target_date_obj = target_date
    slot_datetime = datetime.combine(target_date_obj, datetime.min.time())
    slot_datetime = slot_datetime.replace(
        hour=int(slot_start_hour),
        minute=int((slot_start_hour % 1) * 60)
    )

    # When does this slot become available?
    available_from = slot_datetime - timedelta(minutes=horizon_minutes)

    if now < available_from:
        time_until = available_from - now
        hours_until = time_until.total_seconds() / 3600
        if hours_until < 1:
            mins_until = int(time_until.total_seconds() / 60)
            return False, (
                f"Available in {mins_until}min "
                f"(live horizon: {format_horizon_minutes(horizon_minutes)})"
            )
        elif hours_until < 24:
            return False, (
                f"Available in {hours_until:.1f}h "
                f"(live horizon: {format_horizon_minutes(horizon_minutes)})"
            )
        else:
            days_until = hours_until / 24
            return False, (
                f"Available in {days_until:.1f}d "
                f"(live horizon: {format_horizon_minutes(horizon_minutes)})"
            )

    return True, ""


# Booking window opens at 7:30 AM
BOOKING_WINDOW_OPEN_HOUR = 7.5  # 7:30 AM


def is_any_room_bookable_on_day(target_date):
    """
    Check if ANY room from PRIORITY_ROOMS is bookable on a given day.

    Rooms become bookable at 7:30 AM on their horizon date. If no rooms
    are bookable yet (for example, before the furthest live cutoff opens),
    we should skip that day to avoid wasted navigation.

    Args:
        target_date: The date to check (date or datetime object)

    Returns:
        Tuple of (any_bookable, reason_if_none)
    """
    # Normalize to date object
    if hasattr(target_date, 'date'):
        target_date_obj = target_date.date()
    else:
        target_date_obj = target_date

    # Convert to datetime for is_room_available_to_book
    target_datetime = datetime.combine(target_date_obj, datetime.min.time())

    # Check if any priority room is bookable at the earliest slot time (7:30 AM)
    earliest_slot_hour = BOOKING_WINDOW_OPEN_HOUR

    for room in PRIORITY_ROOMS:
        is_available, _ = is_room_available_to_book(room, target_datetime, earliest_slot_hour)
        if is_available:
            return True, ""

    # Find the shortest wait time across all rooms
    min_wait = None
    for room in PRIORITY_ROOMS:
        horizon_minutes = room_horizon_minutes(room)
        slot_datetime = datetime.combine(target_date_obj, datetime.min.time())
        slot_datetime = slot_datetime.replace(hour=7, minute=30)
        available_from = slot_datetime - timedelta(minutes=horizon_minutes)

        now = datetime.now()
        if now < available_from:
            wait = available_from - now
            if min_wait is None or wait < min_wait:
                min_wait = wait

    if min_wait:
        hours_until = min_wait.total_seconds() / 3600
        if hours_until < 1:
            mins_until = int(min_wait.total_seconds() / 60)
            return False, f"No rooms bookable yet (opens in {mins_until}min)"
        elif hours_until < 24:
            return False, f"No rooms bookable yet (opens in {hours_until:.1f}h)"
        else:
            days_until = hours_until / 24
            return False, f"No rooms bookable yet (opens in {days_until:.1f}d)"

    return False, "No rooms bookable"


def fragmentation_allows_new_booking(tracker, target_date):
    """Enforce the user's single-contiguous-session choice for new creates."""

    if ALLOW_FRAGMENTED_SESSIONS:
        return True, ""
    date_key = as_date(target_date).isoformat()
    has_existing_reservation = bool(tracker.reservation_ranges.get(date_key))
    has_new_booking = any(
        as_date(booking_date) == as_date(target_date)
        for _room, booking_date, _start, _end in tracker.bookings
    )
    if has_existing_reservation or has_new_booking:
        return (
            False,
            "A reservation already exists on this date and fragmented sessions "
            "are disabled",
        )
    return True, ""


def adjust_slot_for_peak_quota(slot_start, slot_end, target_date, remaining_peak_mins):
    """
    Adjust a slot to avoid the peak zone (9am-4pm) when peak quota is exceeded or limited.

    This handles cases like:
    - 8:00-9:30 with no peak quota → trim to 8:00-9:00
    - 15:30-17:00 with no peak quota → trim to 16:00-17:00
    - 10:00-14:00 with no peak quota → skip entirely (fully in peak)
    - 8:00-10:00 with 30min peak left → trim to 8:00-9:30

    Args:
        slot_start: Start hour (e.g., 8.0 for 8am)
        slot_end: End hour (e.g., 9.5 for 9:30am)
        target_date: The date of the slot
        remaining_peak_mins: How many peak minutes are still available

    Returns:
        Tuple of (adjusted_start, adjusted_end, was_adjusted, reason)
        If the slot should be skipped entirely, returns (None, None, True, reason)
    """
    # Only applies to weekdays
    if target_date.weekday() >= 5:  # Weekend
        return slot_start, slot_end, False, ""

    # Calculate how much of this slot is in peak hours
    overlap_start = max(slot_start, PEAK_START)
    overlap_end = min(slot_end, PEAK_END)
    peak_overlap_mins = max(0, (overlap_end - overlap_start) * 60)

    # If no peak overlap, no adjustment needed
    if peak_overlap_mins == 0:
        return slot_start, slot_end, False, ""

    # If we have enough peak quota for this slot, no adjustment needed
    if peak_overlap_mins <= remaining_peak_mins:
        return slot_start, slot_end, False, ""

    # We need to adjust. Check if slot is fully within peak hours
    if slot_start >= PEAK_START and slot_end <= PEAK_END:
        # Slot is fully in peak zone - can we use partial quota?
        if remaining_peak_mins >= MIN_BOOKING_MINUTES:
            # Use remaining quota: book for remaining_peak_mins
            new_end = slot_start + remaining_peak_mins / 60
            if new_end <= slot_end and (new_end - slot_start) * 60 >= MIN_BOOKING_MINUTES:
                return slot_start, new_end, True, f"Trimmed to use {remaining_peak_mins:.0f}min peak quota"
        return None, None, True, "Fully in peak zone, quota exceeded"

    # Slot crosses peak boundary - adjust to avoid peak zone
    adjusted_start = slot_start
    adjusted_end = slot_end

    # If slot starts before peak and ends during/after peak
    if slot_start < PEAK_START and slot_end > PEAK_START:
        if remaining_peak_mins <= 0:
            # No peak quota left - trim to end at peak start
            adjusted_end = PEAK_START
        else:
            # Use remaining peak quota
            max_end = PEAK_START + remaining_peak_mins / 60
            if max_end < slot_end:
                adjusted_end = min(max_end, slot_end)

    # If slot starts during peak and ends after peak
    if slot_start >= PEAK_START and slot_start < PEAK_END and slot_end > PEAK_END:
        if remaining_peak_mins <= 0:
            # No peak quota left - start at peak end
            adjusted_start = PEAK_END
        else:
            # Can we fit some peak time at the start?
            peak_portion = min(PEAK_END - slot_start, remaining_peak_mins / 60)
            if peak_portion * 60 < MIN_BOOKING_MINUTES:
                # Not enough for a booking during peak, start after peak
                adjusted_start = PEAK_END

    # Validate adjusted slot
    if adjusted_end - adjusted_start < MIN_BOOKING_MINUTES / 60:
        return None, None, True, "Adjusted slot too short"

    if adjusted_start != slot_start or adjusted_end != slot_end:
        return adjusted_start, adjusted_end, True, f"Adjusted from {slot_start:.2f}-{slot_end:.2f} to avoid peak"

    return slot_start, slot_end, False, ""


def adjust_slot_for_weekly_quota(slot_start, slot_end, remaining_quota_hours):
    """
    Adjust a slot to fit within the remaining weekly quota.

    If the slot duration exceeds the remaining quota, trim the slot to use
    the remaining quota (similar to peak quota trimming).

    Args:
        slot_start: Start hour (e.g., 8.0 for 8am)
        slot_end: End hour (e.g., 10.0 for 10am)
        remaining_quota_hours: How many hours are left in the weekly quota

    Returns:
        Tuple of (adjusted_start, adjusted_end, was_adjusted, reason)
        If the slot should be skipped entirely, returns (None, None, True, reason)
    """
    slot_duration_hours = slot_end - slot_start
    slot_duration_mins = slot_duration_hours * 60

    # If we have enough quota, no adjustment needed
    if slot_duration_hours <= remaining_quota_hours:
        return slot_start, slot_end, False, ""

    # Not enough quota - can we trim?
    remaining_quota_mins = remaining_quota_hours * 60

    if remaining_quota_mins < MIN_BOOKING_MINUTES:
        return None, None, True, f"Weekly quota too low ({remaining_quota_hours:.1f}h remaining)"

    # Trim the slot to fit the remaining quota
    new_end = slot_start + remaining_quota_hours
    if (new_end - slot_start) * 60 >= MIN_BOOKING_MINUTES:
        return slot_start, new_end, True, f"Trimmed to {remaining_quota_hours:.1f}h (weekly quota)"

    return None, None, True, f"Cannot fit in remaining weekly quota ({remaining_quota_hours:.1f}h)"


def load_disabled_dates(settings=None):
    """Load list of disabled dates from settings file."""
    if settings is None:
        settings = load_settings_document(settings_file)
    disabled = settings.get("disabled_dates", [])
    if not isinstance(disabled, list) or not all(isinstance(value, str) for value in disabled):
        raise SettingsError("disabled_dates must be a JSON list of YYYY-MM-DD strings")
    return set(disabled)


def load_ignored_events(settings=None):
    """Load list of ignored event keys from settings file.

    Ignored events are events the user wants to allow booking over
    (they won't be treated as conflicts).

    Returns:
        Set of deterministic ``v2:`` event keys or legacy time-only keys.
    """
    if settings is None:
        settings = load_settings_document(settings_file)
    ignored = settings.get("ignored_events", [])
    if not isinstance(ignored, list) or not all(isinstance(value, str) for value in ignored):
        raise SettingsError("ignored_events must be a JSON list of event keys")
    return set(ignored)


def is_date_disabled(target_date, disabled_dates):
    """Check if a date is disabled for booking."""
    date_str = target_date.strftime('%Y-%m-%d')
    return date_str in disabled_dates


# Time preference presets: name -> (start_hour, end_hour)
TIME_PREFERENCE_PRESETS = {
    "morning": (7, 12),
    "peak_afternoon": (12, 16),
    "afternoon": (12, 18),
    "evening": (18, 22),
    "afternoon_evening": (14, 22),
}


def load_time_preferences(settings=None):
    """Load time preferences from settings file.

    Returns:
        dict with keys: enabled, start_hour, end_hour, strict_mode
        Hours are decimal (e.g., 14.5 = 14:30)
    """
    default = {"enabled": False, "start_hour": 14.0, "end_hour": 22.0, "strict_mode": False}

    if settings is None:
        settings = load_settings_document(settings_file)
    prefs = settings.get("time_preferences", {})
    if not isinstance(prefs, dict):
        raise SettingsError("time_preferences must be a JSON object")
    enabled = prefs.get("enabled", False)
    strict_mode = prefs.get("strict_mode", False)
    if not isinstance(enabled, bool):
        raise SettingsError("time_preferences.enabled must be true or false")
    if not isinstance(strict_mode, bool):
        raise SettingsError("time_preferences.strict_mode must be true or false")

    preset = prefs.get("preset", "afternoon_evening")
    supported_presets = {*TIME_PREFERENCE_PRESETS, "custom"}
    if not isinstance(preset, str) or preset not in supported_presets:
        raise SettingsError("time_preferences.preset is not a supported preset")

    def require_clock_part(field, default_value, maximum):
        value = prefs.get(field, default_value)
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsError(f"time_preferences.{field} must be a whole number")
        if not 0 <= value <= maximum:
            raise SettingsError(
                f"time_preferences.{field} must be between 0 and {maximum}"
            )
        return value

    # Validate the complete saved section even when preferences are disabled or
    # a preset is selected.  That keeps the headless runtime aligned with the
    # GUI and prevents malformed dormant values from becoming active later.
    start_h = require_clock_part("custom_start_hour", 14, 23)
    start_m = require_clock_part("custom_start_min", 0, 59)
    end_h = require_clock_part("custom_end_hour", 22, 23)
    end_m = require_clock_part("custom_end_min", 0, 59)
    if preset == "custom" and (end_h, end_m) <= (start_h, start_m):
        raise SettingsError("Custom preferred end time must be later than start time")

    if not enabled:
        return default

    if preset in TIME_PREFERENCE_PRESETS:
        start_hour, end_hour = TIME_PREFERENCE_PRESETS[preset]
    else:
        start_hour = start_h + start_m / 60.0
        end_hour = end_h + end_m / 60.0
    return {
        "enabled": True,
        "start_hour": start_hour,
        "end_hour": end_hour,
        "strict_mode": strict_mode,
    }


def load_booking_strategy(settings=None):
    """Load the shared strict date-order and daily-planning strategy."""
    if settings is None:
        settings = load_settings_document(settings_file)
    try:
        strategy = load_booking_strategy_preferences(settings)
    except BookingStrategyError as exc:
        raise SettingsError(str(exc)) from exc
    return {
        "reverse_date_order": strategy.reverse_date_order,
        "daily_planning": strategy.daily_planning,
    }


# =============================================================================
# EXTENDABLE BOOKINGS TRACKING
# =============================================================================
# When booking at the horizon edge, only 30 min can be booked initially.
# These functions track such bookings so they can be extended later.

def normalize_time_string(time_str):
    """Normalize time string to HH:MM format.

    Handles variations like "9:30" -> "09:30"

    Args:
        time_str: Time string like "9:30" or "09:30"

    Returns:
        Normalized time string "HH:MM"
    """
    parts = time_str.split(':')
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    return f"{hour:02d}:{minute:02d}"


def load_extendable_bookings(settings=None):
    """Load bookings that may be extendable from settings.

    Returns:
        List of dicts with: date, startTime, endTime, room, created_at,
        target_end, observed horizon evidence, plus eventId/event_url after
        identity migration. Historical horizon evidence is never current policy.
    """
    if settings is None:
        settings = load_settings_document(settings_file)
    bookings = settings.get("extendable_bookings", [])
    if not isinstance(bookings, list):
        raise SettingsError("extendable_bookings must be a JSON list")

    now = datetime.now()
    today = now.date()
    valid_bookings = []
    required_fields = ["date", "startTime", "endTime", "room", "created_at", "target_end"]
    for booking in bookings:
        if not isinstance(booking, dict):
            raise SettingsError("Every extendable booking must be a JSON object")
        missing_fields = [field for field in required_fields if field not in booking]
        if missing_fields:
            raise SettingsError(f"Extendable booking is missing fields: {', '.join(missing_fields)}")
        try:
            booking_date = datetime.strptime(booking["date"], '%Y-%m-%d').date()
            created_at = datetime.fromisoformat(booking["created_at"])
        except (TypeError, ValueError) as exc:
            raise SettingsError(f"Invalid extendable booking: {exc}") from exc
        room = booking["room"]
        try:
            validate_room_name(room, "Extendable booking room")
        except RoomPreferencesError as exc:
            raise SettingsError(str(exc)) from exc

        parsed_minutes = {}
        for field in ("startTime", "endTime", "target_end"):
            value = booking[field]
            if not isinstance(value, str) or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) is None:
                raise SettingsError(f"Extendable booking {field} must use canonical HH:MM")
            hour, minute = map(int, value.split(":"))
            if minute % 15:
                raise SettingsError(f"Extendable booking {field} must use 15-minute steps")
            parsed_minutes[field] = hour * 60 + minute
        if not (
            parsed_minutes["startTime"] < parsed_minutes["endTime"]
            <= parsed_minutes["target_end"]
            <= parsed_minutes["startTime"] + MAX_BOOKING_HOURS * 60
        ):
            raise SettingsError("Extendable booking times are out of order or exceed 2 hours")

        observed_horizon = booking.get("horizon_minutes")
        legacy_horizon_days = booking.get("horizon_days")
        if observed_horizon is None and legacy_horizon_days is None:
            raise SettingsError(
                f"Extendable booking has no observed horizon evidence for {room}"
            )
        if observed_horizon is not None and (
            isinstance(observed_horizon, bool)
            or not isinstance(observed_horizon, int)
            or observed_horizon <= 0
            or observed_horizon % 15
        ):
            raise SettingsError(f"Extendable booking horizon_minutes is invalid for {room}")
        if legacy_horizon_days is not None and (
            isinstance(legacy_horizon_days, bool)
            or not isinstance(legacy_horizon_days, int)
            or legacy_horizon_days <= 0
        ):
            raise SettingsError(f"Legacy extendable horizon_days is invalid for {room}")
        if (
            observed_horizon is not None
            and legacy_horizon_days is not None
            and observed_horizon != legacy_horizon_days * 24 * 60
        ):
            raise SettingsError(f"Extendable booking horizon evidence conflicts for {room}")

        has_event_id = "eventId" in booking
        has_event_url = "event_url" in booking
        if has_event_id != has_event_url:
            raise SettingsError(
                "Extendable booking eventId and event_url must either both be present or both be absent"
            )
        if has_event_id:
            event_id = booking["eventId"]
            event_url = booking["event_url"]
            if (
                isinstance(event_id, bool)
                or not isinstance(event_id, int)
                or event_id <= 0
                or not isinstance(event_url, str)
                or parse_confirmed_event_id(event_url) != event_id
            ):
                raise SettingsError(
                    "Extendable booking must use one exact positive Asimut event identity"
                )

        comparable_now = datetime.now(created_at.tzinfo) if created_at.tzinfo else now
        age_seconds = (comparable_now - created_at).total_seconds()
        if age_seconds < -300:
            raise SettingsError("Extendable booking creation timestamp is in the future")
        if booking_date >= today:
            valid_bookings.append(booking)
    return valid_bookings


def save_extendable_booking(
    room,
    target_date,
    start_hour,
    end_hour,
    target_end_hour,
    *,
    event_url,
):
    """Save a booking as potentially extendable.

    Args:
        room: Room name (e.g., "B0.29")
        target_date: Date of the booking (datetime or date object)
        start_hour: Start time as decimal (e.g., 9.5 for 9:30)
        end_hour: Current end time as decimal
        target_end_hour: Target end time we wanted but couldn't book
        event_url: Exact positive arrangement URL verified after Save
    """
    event_id = parse_confirmed_event_id(event_url)
    if event_id is None:
        raise SettingsError(
            "Cannot track an extendable booking without a verified positive event URL"
        )
    # Format times
    if hasattr(target_date, 'strftime'):
        date_str = target_date.strftime('%Y-%m-%d')
    else:
        date_str = str(target_date)

    start_h = int(start_hour)
    start_m = int((start_hour % 1) * 60)
    end_h = int(end_hour)
    end_m = int((end_hour % 1) * 60)
    target_h = int(target_end_hour)
    target_m = int((target_end_hour % 1) * 60)

    booking = {
        "date": date_str,
        "startTime": f"{start_h:02d}:{start_m:02d}",
        "endTime": f"{end_h:02d}:{end_m:02d}",
        "room": room,
        "created_at": datetime.now().isoformat(),
        "target_end": f"{target_h:02d}:{target_m:02d}",
        "horizon_minutes": room_horizon_minutes(room),
        "horizon_observed_at": require_live_room_policy().observed_at.isoformat(),
        "eventId": event_id,
        "event_url": event_url,
    }

    def mutate(settings):
        entries = settings.setdefault("extendable_bookings", [])
        if not isinstance(entries, list):
            raise SettingsError("extendable_bookings must be a JSON list")

        matches = [
            entry for entry in entries
            if isinstance(entry, dict)
            and entry.get("date") == booking["date"]
            and entry.get("room") == booking["room"]
            and entry.get("startTime") == booking["startTime"]
        ]
        if len(matches) > 1:
            raise SettingsError(
                "Duplicate extendable booking state exists for the verified date/room/start; "
                "nothing was updated"
            )
        if not matches:
            entries.append(booking)
        else:
            match = matches[0]
            if (
                match.get("eventId") != booking["eventId"]
                or match.get("event_url") != booking["event_url"]
            ):
                raise SettingsError(
                    "Existing extendable booking state has a different or unverified event "
                    "identity; nothing was updated"
                )
            match["endTime"] = booking["endTime"]
            match["target_end"] = booking["target_end"]
            match["horizon_minutes"] = booking["horizon_minutes"]
            match["horizon_observed_at"] = booking["horizon_observed_at"]
            match.pop("horizon_days", None)

        now = datetime.now()
        today = now.date()
        settings["extendable_bookings"] = [
            entry for entry in entries
            if isinstance(entry, dict)
            and datetime.strptime(entry["date"], '%Y-%m-%d').date() >= today
        ]

    update_settings(mutate, settings_file)

    print(f"  [EXTEND] Saved for extension: {room} {date_str} {booking['startTime']}->{booking['target_end']}")


def ensure_extendable_booking_event_identity(booking, agenda_reservations):
    """Return an event-bound extension record, migrating one legacy record safely."""

    event_id = booking.get("eventId")
    event_url = booking.get("event_url")
    if event_id is not None or event_url is not None:
        if (
            isinstance(event_id, bool)
            or not isinstance(event_id, int)
            or event_id <= 0
            or not isinstance(event_url, str)
            or parse_confirmed_event_id(event_url) != event_id
        ):
            raise SettingsError(
                "Extendable booking has an invalid or inconsistent event identity"
            )
        return dict(booking)

    if not isinstance(agenda_reservations, list):
        raise SettingsError(
            "Legacy extension identity cannot be migrated without a complete agenda scan"
        )
    try:
        start_time = normalize_time_string(booking.get("startTime", ""))
    except (AttributeError, IndexError, TypeError, ValueError) as exc:
        raise SettingsError(f"Legacy extension start time is invalid: {exc}") from exc
    matches = []
    for reservation in agenda_reservations:
        if not isinstance(reservation, dict):
            continue
        try:
            reservation_start = normalize_time_string(
                reservation.get("startTime", "")
            )
            reservation_end = normalize_time_string(
                reservation.get("endTime", "")
            )
        except (AttributeError, IndexError, TypeError, ValueError):
            continue
        if (
            reservation.get("room") == booking.get("room")
            and reservation.get("date") == booking.get("date")
            and reservation_start == start_time
        ):
            matches.append((reservation, reservation_end))
    if len(matches) != 1:
        raise SettingsError(
            "Legacy extension identity matched "
            f"{len(matches)} agenda reservations; exactly one is required"
        )
    migrated_reservation, migrated_end = matches[0]
    migrated_event_id = migrated_reservation.get("eventId")
    if (
        isinstance(migrated_event_id, bool)
        or not isinstance(migrated_event_id, int)
        or migrated_event_id <= 0
    ):
        raise SettingsError(
            "The unique agenda reservation has no positive event id for extension migration"
        )
    migrated_event_url = (
        f"{ASIMUT_BASE_URL}/arrangement?eventId={migrated_event_id}"
    )
    start_minutes = int(start_time[:2]) * 60 + int(start_time[3:])
    end_minutes = int(migrated_end[:2]) * 60 + int(migrated_end[3:])
    target_end = normalize_time_string(booking.get("target_end", ""))
    target_minutes = int(target_end[:2]) * 60 + int(target_end[3:])
    if not start_minutes < end_minutes <= target_minutes:
        raise SettingsError(
            "The unique agenda reservation has an end time outside the legacy extension target"
        )

    def mutate(settings):
        entries = settings.get("extendable_bookings", [])
        if not isinstance(entries, list):
            raise SettingsError("extendable_bookings must be a JSON list")
        local_matches = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise SettingsError(
                    "Legacy extension state changed during identity migration; "
                    "no edit was attempted"
                )
            try:
                entry_start = normalize_time_string(entry.get("startTime", ""))
            except (AttributeError, IndexError, TypeError, ValueError) as exc:
                raise SettingsError(
                    "Legacy extension state changed during identity migration; "
                    "no edit was attempted"
                ) from exc
            if (
                entry.get("date") == booking.get("date")
                and entry.get("room") == booking.get("room")
                and entry_start == start_time
            ):
                local_matches.append(entry)
        if len(local_matches) != 1:
            raise SettingsError(
                "Legacy extension state changed during identity migration; no edit was attempted"
            )
        if local_matches[0] != booking:
            raise SettingsError(
                "Legacy extension state changed during identity migration; no edit was attempted"
            )
        local_matches[0]["eventId"] = migrated_event_id
        local_matches[0]["event_url"] = migrated_event_url
        local_matches[0]["endTime"] = migrated_end

    update_settings(mutate, settings_file)
    migrated = dict(booking)
    migrated["eventId"] = migrated_event_id
    migrated["event_url"] = migrated_event_url
    migrated["endTime"] = migrated_end
    return migrated


def remove_extendable_booking(room, date_str, start_time):
    """Remove a booking from extendable list after successful extension.

    Args:
        room: Room name
        date_str: Date string (YYYY-MM-DD)
        start_time: Start time string (HH:MM)
    """
    if not settings_file.exists():
        return

    def mutate(settings):
        entries = settings.get("extendable_bookings", [])
        if not isinstance(entries, list):
            raise SettingsError("extendable_bookings must be a JSON list")
        settings["extendable_bookings"] = [
            entry for entry in entries
            if not (
                isinstance(entry, dict)
                and entry.get("date") == date_str
                and entry.get("room") == room
                and entry.get("startTime") == start_time
            )
        ]

    update_settings(mutate, settings_file)


def update_extendable_booking_end_time(room, date_str, start_time, new_end_time):
    """Update the end time of an extendable booking after successful extension.

    Args:
        room: Room name
        date_str: Date string (YYYY-MM-DD)
        start_time: Start time string (HH:MM)
        new_end_time: New end time string (HH:MM)

    Returns:
        bool: True if booking was found and updated, False otherwise
    """
    # Normalize time strings for comparison (ensure HH:MM format)
    start_time_normalized = normalize_time_string(start_time)
    new_end_normalized = normalize_time_string(new_end_time)

    def mutate(settings):
        entries = settings.get("extendable_bookings", [])
        if not isinstance(entries, list):
            raise SettingsError("extendable_bookings must be a JSON list")
        found_entry = None
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if (
                entry.get("date") == date_str
                and entry.get("room") == room
                and normalize_time_string(entry.get("startTime", "")) == start_time_normalized
            ):
                found_entry = entry
                break
        if found_entry is None:
            return False
        found_entry["endTime"] = new_end_normalized
        if new_end_normalized == normalize_time_string(found_entry["target_end"]):
            entries.remove(found_entry)
        return True

    found = update_settings(mutate, settings_file)
    if not found:
        print(f"  [WARN] Could not find extendable booking to update: {room} {date_str} {start_time}")
    return found


@dataclass(frozen=True)
class HorizonExtensionPlan:
    """Pure timing decision for one horizon-edge reservation."""

    can_extend: bool
    max_end_hour: float
    next_end_hour: float | None
    next_unlock_at: datetime | None
    wait_seconds: float | None
    reason: str


def plan_horizon_extension(
    room,
    date_str,
    start_hour,
    current_end_hour,
    target_end_hour,
    *,
    now=None,
):
    """Plan the latest unlocked extension and the next exact 15-minute edge.

    The horizon exposes another 15 minutes at each complete quarter-hour of
    elapsed time. A late run catches up in one verified edit to the latest
    completed boundary; it never rounds into a boundary that has not happened.
    """

    now = now or datetime.now()
    try:
        slot_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return HorizonExtensionPlan(
            False,
            current_end_hour,
            None,
            None,
            None,
            "Invalid booking date",
        )

    try:
        start_minutes = int(round(float(start_hour) * 60))
        current_end_minutes = int(round(float(current_end_hour) * 60))
        target_end_minutes = int(round(float(target_end_hour) * 60))
    except (TypeError, ValueError, OverflowError):
        return HorizonExtensionPlan(
            False,
            current_end_hour,
            None,
            None,
            None,
            "Invalid booking time",
        )

    current_duration_minutes = current_end_minutes - start_minutes
    target_duration_minutes = min(
        target_end_minutes - start_minutes,
        MAX_BOOKING_HOURS * 60,
    )
    if (
        not 0 <= start_minutes < 24 * 60
        or not 0 < current_end_minutes <= 24 * 60
        or not 0 < target_end_minutes <= 24 * 60
        or start_minutes % 15
        or current_end_minutes % 15
        or target_end_minutes % 15
        or current_duration_minutes < MIN_BOOKING_MINUTES
        or current_duration_minutes % 15
        or target_duration_minutes < current_duration_minutes
    ):
        return HorizonExtensionPlan(
            False,
            current_end_hour,
            None,
            None,
            None,
            "Invalid extension duration",
        )

    slot_datetime = datetime.combine(slot_date, datetime.min.time()) + timedelta(
        minutes=start_minutes
    )
    horizon_minutes = room_horizon_minutes(room)
    horizon_edge = slot_datetime - timedelta(minutes=horizon_minutes)
    try:
        elapsed_seconds = (now - horizon_edge).total_seconds()
    except (AttributeError, TypeError):
        return HorizonExtensionPlan(
            False,
            current_end_hour,
            None,
            None,
            None,
            "Invalid extension clock",
        )

    # Integer-second floor division makes the exact boundary contract explicit:
    # +44:59 is still 30 minutes, while +45:00 unlocks 45 minutes.
    completed_quarters = max(0, int(elapsed_seconds // (15 * 60)))
    unlocked_duration_minutes = min(
        completed_quarters * 15,
        target_duration_minutes,
        MAX_BOOKING_HOURS * 60,
    )
    unlocked_end_minutes = start_minutes + unlocked_duration_minutes

    if unlocked_end_minutes >= current_end_minutes + 15:
        max_end_hour = unlocked_end_minutes / 60
        return HorizonExtensionPlan(
            True,
            max_end_hour,
            None,
            None,
            0.0,
            f"Can extend to {unlocked_end_minutes // 60:02d}:{unlocked_end_minutes % 60:02d}",
        )

    if current_duration_minutes >= target_duration_minutes:
        return HorizonExtensionPlan(
            False,
            current_end_hour,
            None,
            None,
            None,
            "Already at target duration",
        )

    next_duration_minutes = current_duration_minutes + 15
    next_end_minutes = start_minutes + next_duration_minutes
    next_unlock_at = horizon_edge + timedelta(minutes=next_duration_minutes)
    wait_seconds = (next_unlock_at - now).total_seconds()
    if elapsed_seconds < 0:
        reason = f"Not yet in horizon window ({-elapsed_seconds / 60:.0f}min early)"
    else:
        reason = (
            "Next 15-minute extension unlocks at "
            f"{next_unlock_at.strftime('%H:%M:%S')} ({max(0.0, wait_seconds):.0f}s)"
        )
    return HorizonExtensionPlan(
        False,
        current_end_hour,
        next_end_minutes / 60,
        next_unlock_at,
        wait_seconds,
        reason,
    )


def calculate_max_extension(
    room,
    date_str,
    start_hour,
    current_end_hour,
    booking_timestamp,
    target_end_hour,
    *,
    now=None,
):
    """Calculate maximum possible extension for a booking made at horizon edge.

    When booking at the exact horizon time, only MIN_BOOKING_MINUTES can be booked.
    As time passes, more of the slot becomes available for booking.

    Args:
        room: Room name (e.g., "B0.29")
        date_str: Date string (YYYY-MM-DD)
        start_hour: Start time as decimal (e.g., 9.0 for 9:00)
        current_end_hour: Current end time as decimal (e.g., 9.5 for 9:30)
        booking_timestamp: ISO format timestamp when booking was created
        target_end_hour: The end time we ultimately want (e.g., 11.0 for 11:00)

    Returns:
        Tuple of (can_extend: bool, max_end_hour: float, reason: str)
    """
    try:
        # Validate timestamp format (kept for compatibility with saved data)
        datetime.fromisoformat(booking_timestamp)
    except (TypeError, ValueError):
        return False, current_end_hour, "Invalid booking timestamp"
    plan = plan_horizon_extension(
        room,
        date_str,
        start_hour,
        current_end_hour,
        target_end_hour,
        now=now,
    )
    return plan.can_extend, plan.max_end_hour, plan.reason


def is_preferred_time(start_hour, time_prefs):
    """Check if a slot's start time falls within the preferred range.

    Args:
        start_hour: Slot start time as decimal hour (e.g., 14.5 for 2:30 PM)
        time_prefs: Dict from load_time_preferences()

    Returns:
        True if the time is preferred (or if preferences are disabled)
    """
    if not time_prefs["enabled"]:
        return True  # All times preferred if disabled

    return time_prefs["start_hour"] <= start_hour < time_prefs["end_hour"]


def trim_to_strict_time_window(start_hour, end_hour, time_prefs):
    """Trim a slot to the full preferred interval when strict mode is enabled."""

    if not time_prefs["enabled"] or not time_prefs["strict_mode"]:
        return start_hour, end_hour
    trimmed_start = max(start_hour, time_prefs["start_hour"])
    trimmed_end = min(end_hour, time_prefs["end_hour"])
    if (trimmed_end - trimmed_start) * 60 < MIN_BOOKING_MINUTES:
        return None, None
    return trimmed_start, trimmed_end


def interval_is_strictly_preferred(start_hour, end_hour, time_prefs):
    """Return True only when the complete interval fits a strict preference."""

    if not time_prefs["enabled"] or not time_prefs["strict_mode"]:
        return True
    return (
        start_hour >= time_prefs["start_hour"]
        and end_hour <= time_prefs["end_hour"]
    )


# =============================================================================
# SMART SWAP FUNCTIONS (commented out for future use)
# =============================================================================
#
# def load_smart_swap_settings():
#     """Load smart swap settings from settings file."""
#     default = {"enabled": False}
#     if settings_file.exists():
#         try:
#             with open(settings_file, 'r') as f:
#                 settings = json.load(f)
#                 strategy = settings.get("booking_strategy", {})
#                 return {"enabled": strategy.get("smart_swap_enabled", False)}
#         except:
#             pass
#     return default
#
#
# def is_outside_preferred_time(start_hour, end_hour, time_prefs):
#     """Check if a reservation is outside the preferred time window."""
#     if not time_prefs["enabled"]:
#         return False
#     pref_start = time_prefs["start_hour"]
#     pref_end = time_prefs["end_hour"]
#     return end_hour <= pref_start or start_hour >= pref_end
#
#
# def get_swappable_reservations(reservations, time_prefs):
#     """Get list of reservations that are outside the preferred time window."""
#     if not time_prefs["enabled"]:
#         return []
#     swappable = []
#     for res in reservations:
#         start_parts = res['startTime'].split(':')
#         end_parts = res['endTime'].split(':')
#         start_hour = int(start_parts[0]) + int(start_parts[1]) / 60
#         end_hour = int(end_parts[0]) + int(end_parts[1]) / 60
#         if is_outside_preferred_time(start_hour, end_hour, time_prefs):
#             swappable.append({**res, 'start_hour': start_hour, 'end_hour': end_hour})
#     return swappable
#
#
def _expected_reservation_tuple(reservation):
    """Return the exact tuple used for cancellation matching and proof."""

    return (
        reservation.get("room"),
        reservation.get("date"),
        reservation.get("startTime"),
        reservation.get("endTime"),
    )


def resolve_exact_cancellation_target(
    reservations,
    *,
    event_id,
    room,
    date_str,
    start_time,
    end_time,
):
    """Resolve one positive agenda event ID and require its complete tuple."""

    if not isinstance(reservations, list):
        raise BookingCancellationError(
            "Cancellation requires a complete validated agenda reservation list"
        )
    if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id <= 0:
        raise BookingCancellationError("Cancellation requires a positive event ID")

    id_matches = [
        reservation
        for reservation in reservations
        if isinstance(reservation, dict)
        and type(reservation.get("eventId")) is int
        and reservation.get("eventId") == event_id
    ]
    if len(id_matches) != 1:
        raise BookingCancellationError(
            f"Cancellation event {event_id} matched {len(id_matches)} current "
            "agenda reservations; exactly one is required"
        )

    expected = (room, date_str, start_time, end_time)
    actual = _expected_reservation_tuple(id_matches[0])
    if actual != expected:
        raise BookingCancellationError(
            f"Cancellation event {event_id} no longer matches the expected "
            f"reservation {room} {date_str} {start_time}-{end_time}"
        )
    return dict(id_matches[0])


def _find_exact_cancellation_card(page, reservation):
    """Return one current ID-bound reservation card after structured proof."""

    event_id = reservation["eventId"]
    selector = f"[data-cy='event_{event_id}'], #event_{event_id}"
    exact_cards = page.locator(selector)
    for attempt in range(11):
        count = exact_cards.count()
        if count > 1:
            raise BookingCancellationError(
                f"Agenda contains multiple DOM cards for event {event_id}"
            )
        if count == 1:
            break
        if attempt == 10:
            raise BookingCancellationError(
                f"Exact agenda card for event {event_id} is not available"
            )
        page.evaluate("window.scrollBy(0, 2000)")
        page.wait_for_timeout(300)

    exact_card = exact_cards.first
    legacy_panel = exact_card.locator(
        "xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), "
        "' as-event-panel ')][1]"
    ).first
    card = legacy_panel if legacy_panel.count() == 1 else exact_card
    if card.count() != 1:
        raise BookingCancellationError(
            f"Exact agenda card for event {event_id} became ambiguous"
        )

    display_names = card.locator('[data-cy="event-display-name"]')
    if (
        display_names.count() != 1
        or not display_names.first.is_visible()
        or (display_names.first.text_content() or "").strip() != "Reservation"
    ):
        raise BookingCancellationError(
            f"Event {event_id} is not one exact visible reservation card"
        )

    card_text = " ".join((card.inner_text() or "").split())
    expected_range = f"{reservation['startTime']}-{reservation['endTime']}"
    compact_text = re.sub(r"\s+", "", card_text).replace("–", "-")
    if expected_range not in compact_text:
        raise BookingCancellationError(
            f"Event {event_id} card no longer shows the expected time range"
        )
    room_pattern = re.compile(
        rf"(?<![A-Za-z0-9.]){re.escape(reservation['room'])}(?![A-Za-z0-9.])",
        re.IGNORECASE,
    )
    if room_pattern.search(card_text) is None:
        raise BookingCancellationError(
            f"Event {event_id} card no longer shows the expected room"
        )
    return card


def _unique_visible_control(parent, selectors, label):
    """Resolve one visible enabled control through conservative fallbacks."""

    for selector in selectors:
        candidates = parent.locator(selector)
        visible = []
        for index in range(candidates.count()):
            candidate = candidates.nth(index)
            if candidate.is_visible():
                visible.append(candidate)
        if len(visible) > 1:
            raise BookingCancellationError(f"Multiple visible {label} controls were found")
        if len(visible) == 1:
            try:
                if not visible[0].is_enabled():
                    raise BookingCancellationError(f"The {label} control is disabled")
            except BookingCancellationError:
                raise
            except Exception as exc:
                raise BookingCancellationError(
                    f"The {label} control state could not be proven: {exc}"
                ) from exc
            return visible[0]
    raise BookingCancellationError(f"No exact visible {label} control was found")


_CANCELLATION_ACTION_LABELS = frozenset(
    {
        "cancel booking",
        "cancel reservation",
        "cancel event",
    }
)


def _normalized_control_label(value):
    """Normalize one visible or accessible control label for exact comparison."""

    return " ".join(str(value or "").split()).casefold()


def _cancellation_control_semantics(option):
    """Return the exact visible label and Material icons for one menu control."""

    raw_text = str(option.inner_text() or "").strip()
    icon_labels = []
    icons = option.locator("mat-icon")
    for index in range(icons.count()):
        icon_labels.append(
            _normalized_control_label(icons.nth(index).text_content())
        )

    visible_label = _normalized_control_label(raw_text)
    if len(icon_labels) == 1 and icon_labels[0]:
        # Material ligature text participates in innerText. Remove it only when
        # it is the exact leading child text; this handles both `cancel Cancel`
        # and renderer output without an inserted whitespace boundary.
        icon_text = str(icons.first.text_content() or "").strip()
        if raw_text.casefold().startswith(icon_text.casefold()):
            visible_label = _normalized_control_label(raw_text[len(icon_text) :])

    accessible_labels = set()
    if not visible_label:
        for attribute in ("aria-label", "title"):
            accessible_labels.add(
                _normalized_control_label(option.get_attribute(attribute))
            )
        accessible_labels.discard("")
    return visible_label, tuple(icon_labels), accessible_labels


def _cancel_menu_option(page):
    """Return one explicit Asimut cancellation action, never generic Cancel/Delete."""

    # The event-options popup is a CDK overlay. Scope every candidate to exactly
    # one visible overlay so unrelated page/dialog controls cannot authorize a
    # cancellation. Multiple overlays are ambiguous and therefore fail closed.
    overlays = page.locator(".cdk-overlay-pane:visible")
    if overlays.count() != 1:
        raise BookingCancellationError(
            f"Expected one visible event-options overlay; found {overlays.count()}"
        )
    options = overlays.first.locator(
        "[role='menuitem'], button[mat-menu-item], mat-list-item, "
        ".mat-menu-item, .mat-mdc-menu-item"
    )
    candidates = []
    for index in range(options.count()):
        option = options.nth(index)
        if not option.is_visible():
            continue
        visible_label, icon_labels, accessible_labels = (
            _cancellation_control_semantics(option)
        )
        explicit_label = (
            visible_label in _CANCELLATION_ACTION_LABELS
            or bool(accessible_labels & _CANCELLATION_ACTION_LABELS)
        )
        # Current Asimut renders the action as exactly:
        #   <mat-list-item><mat-icon>cancel</mat-icon>Cancel</mat-list-item>
        # Generic "Cancel" is accepted only with that one exact icon, element
        # type, and overlay scope. It is never accepted by text alone.
        try:
            tag_name = str(
                option.evaluate("element => element.tagName.toLowerCase()")
            ).casefold()
        except Exception as exc:
            raise BookingCancellationError(
                f"The cancellation menu item type could not be proven: {exc}"
            ) from exc
        native_cancel = (
            tag_name == "mat-list-item"
            and visible_label == "cancel"
            and icon_labels == ("cancel",)
        )
        if not explicit_label and not native_cancel:
            continue
        candidates.append(option)
    if len(candidates) != 1:
        raise BookingCancellationError(
            f"Expected one explicit cancellation menu item; found {len(candidates)}"
        )
    try:
        if not candidates[0].is_enabled():
            raise BookingCancellationError("The cancellation menu item is disabled")
    except BookingCancellationError:
        raise
    except Exception as exc:
        raise BookingCancellationError(
            f"The cancellation menu item state could not be proven: {exc}"
        ) from exc
    return candidates[0]


def _optional_cancel_confirmation(page):
    """Return a safe affirmative dialog button, or None when no dialog exists."""

    dialogs = page.locator("mat-dialog-container:visible, [role='dialog']:visible")
    if dialogs.count() == 0:
        return None
    if dialogs.count() != 1:
        raise BookingCancellationError("Cancellation opened an ambiguous confirmation dialog")
    dialog = dialogs.first
    dialog_text = " ".join((dialog.inner_text() or "").split()).casefold()
    if "cancel" not in dialog_text or not any(
        noun in dialog_text for noun in ("booking", "reservation", "event")
    ):
        raise BookingCancellationError(
            "Cancellation confirmation dialog did not explicitly identify the action"
        )

    allowed_labels = {
        "confirm",
        "yes",
        "yes, cancel booking",
        "cancel booking",
        "cancel reservation",
        "cancel event",
        "delete booking",
    }
    buttons = dialog.locator("button")
    affirmative = []
    for index in range(buttons.count()):
        button = buttons.nth(index)
        if not button.is_visible():
            continue
        label = " ".join((button.inner_text() or "").split()).casefold()
        if label in allowed_labels:
            affirmative.append(button)
    if len(affirmative) != 1:
        raise BookingCancellationError(
            f"Expected one explicit cancellation confirmation; found {len(affirmative)}"
        )
    try:
        if not affirmative[0].is_enabled():
            raise BookingCancellationError("Cancellation confirmation is disabled")
    except BookingCancellationError:
        raise
    except Exception as exc:
        raise BookingCancellationError(
            f"Cancellation confirmation state could not be proven: {exc}"
        ) from exc
    return affirmative[0]


def classify_cancellation_agenda_outcome(reservations, receipt):
    """Classify one complete post-action agenda without making assumptions."""

    event_id = parse_confirmed_event_id(receipt.get("event_url", ""))
    if event_id is None:
        raise BookingVerificationError(
            f"Cancellation receipt {receipt.get('id', 'unknown')} has no exact event ID"
        )
    expected = (
        receipt["room"],
        receipt["date"],
        receipt["start"],
        receipt["end"],
    )
    id_matches = [
        reservation
        for reservation in reservations
        if isinstance(reservation, dict)
        and type(reservation.get("eventId")) is int
        and reservation.get("eventId") == event_id
    ]
    if len(id_matches) > 1:
        raise BookingVerificationError(
            f"Cancellation event {event_id} appeared multiple times in the complete agenda"
        )
    if len(id_matches) == 1:
        if _expected_reservation_tuple(id_matches[0]) != expected:
            raise BookingVerificationError(
                f"Cancellation event {event_id} remains but its reservation tuple changed"
            )
        return "not_applied"

    tuple_matches = [
        reservation
        for reservation in reservations
        if isinstance(reservation, dict)
        and _expected_reservation_tuple(reservation) == expected
    ]
    if tuple_matches:
        raise BookingVerificationError(
            f"Cancellation event {event_id} disappeared, but the exact reservation "
            "tuple remains under a different or missing event ID"
        )
    return "cancelled"


def remove_extendable_booking_by_event_id(event_id):
    """Remove only extension state bound to one verified cancelled event ID."""

    if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id <= 0:
        raise SettingsError("Cancelled extension cleanup requires a positive event ID")

    def mutate(settings):
        entries = settings.get("extendable_bookings", [])
        if not isinstance(entries, list):
            raise SettingsError("extendable_bookings must be a JSON list")
        matches = [
            entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("eventId") == event_id
        ]
        if len(matches) > 1:
            raise SettingsError(
                f"Multiple extension records are bound to cancelled event {event_id}"
            )
        settings["extendable_bookings"] = [
            entry
            for entry in entries
            if not (isinstance(entry, dict) and entry.get("eventId") == event_id)
        ]

    update_settings(mutate, settings_file)


def cancel_reservation_exact(
    page,
    reservations,
    *,
    event_id,
    room,
    date_str,
    start_time,
    end_time,
    today,
    live_dates,
    ignored_events,
):
    """Cancel one exact reservation and prove its absence in a complete agenda."""

    target = resolve_exact_cancellation_target(
        reservations,
        event_id=event_id,
        room=room,
        date_str=date_str,
        start_time=start_time,
        end_time=end_time,
    )
    event_url = f"{ASIMUT_BASE_URL}/arrangement?eventId={event_id}"
    if parse_confirmed_event_id(event_url) != event_id:
        raise BookingCancellationError("Cancellation event URL could not be canonicalized")

    # The complete agenda established the event ID/tuple association. Reload the
    # exact persisted event as a second independent proof before opening its menu.
    # Everything in this block is non-destructive; distinguish a failed target
    # proof from uncertainty after a cancellation receipt has been written.
    try:
        safe_goto(page, event_url)
        verify_persisted_booking_page(
            page,
            room,
            date_str,
            start_time,
            end_time,
        )
        safe_goto(page, ASIMUT_AGENDA_URL)
        page.wait_for_timeout(2000)
        card = _find_exact_cancellation_card(page, target)
        card.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        more_button = _unique_visible_control(
            card,
            (
                "button[data-cy='button_more']",
                ".as-event-options-button",
                "button:has(mat-icon:has-text('more_vert'))",
            ),
            "reservation options",
        )
        more_button.click()
        page.wait_for_timeout(500)
        cancel_option = _cancel_menu_option(page)
    except BookingCancellationError:
        raise
    except Exception as exc:
        raise BookingCancellationError(
            f"Cancellation stopped before its receipt because the exact target "
            f"could not be proved: {exc}"
        ) from exc

    try:
        receipt = record_pending_cancel(
            room=room,
            booking_date=date_str,
            start=start_time,
            end=end_time,
            event_url=event_url,
        )
    except MutationReceiptError as exc:
        raise BookingCancellationError(
            f"Cancellation stopped before the destructive control because its "
            f"receipt could not be written: {exc}"
        ) from exc

    print(
        f"  Cancelling exact event {event_id}: {room} {date_str} "
        f"{start_time}-{end_time} (receipt {receipt['id']})"
    )
    try:
        cancel_option.click(no_wait_after=True, timeout=5000)
        page.wait_for_timeout(750)
        confirmation = _optional_cancel_confirmation(page)
        if confirmation is not None:
            confirmation.click(no_wait_after=True, timeout=5000)
            page.wait_for_timeout(1000)
    except BookingCancellationError as exc:
        raise BookingVerificationError(
            f"Cancellation outcome is uncertain (receipt {receipt['id']}): {exc}. "
            "No further mutations will be attempted until reconciliation."
        ) from exc
    except Exception as exc:
        raise BookingVerificationError(
            f"Cancellation click outcome is uncertain (receipt {receipt['id']}): {exc}. "
            "No further mutations will be attempted until reconciliation."
        ) from exc

    try:
        # The cancellation control is invoked exactly once above.  Asimut can
        # briefly rerender the agenda card after that click, so retry only the
        # read-only complete-agenda proof.  A retry must never revisit the menu
        # or issue another destructive action.
        for proof_attempt in range(2):
            try:
                proof_tracker = BookingTracker()
                events_detected, post_reservations = scan_agenda(
                    page,
                    proof_tracker,
                    today,
                    ignored_events=ignored_events,
                    window_dates=live_dates,
                    snapshot_path=AGENDA_SNAPSHOT_FILE,
                )
                break
            except Exception:
                if proof_attempt >= 1:
                    raise
                print(
                    "  Cancellation proof scan was temporarily inconsistent; "
                    "retrying the read-only agenda proof once"
                )
                page.wait_for_timeout(750)
        outcome = classify_cancellation_agenda_outcome(
            _agenda_records_for_mutation_proof(proof_tracker),
            receipt,
        )
        if outcome == "not_applied":
            raise BookingVerificationError(
                f"Cancellation event {event_id} still appears unchanged after the "
                f"click (receipt {receipt['id']}). The receipt remains pending so a "
                "later complete-agenda run can distinguish a delayed cancellation "
                "from an action that was not applied."
            )
        remove_extendable_booking_by_event_id(event_id)
        verify_mutation_receipt(receipt["id"], event_url=event_url)
    except BookingVerificationError:
        raise
    except Exception as exc:
        raise BookingVerificationError(
            f"Cancellation requires reconciliation (receipt {receipt['id']}): {exc}"
        ) from exc

    try:
        clear_booking_plan()
    except BookingPlanError as exc:
        print(
            f"  WARNING: Cancellation is verified, but the display-only booking "
            f"plan could not be cleared: {exc}"
        )
    print(
        f"CANCELLATION VERIFIED: event {event_id} {room} {date_str} "
        f"{start_time}-{end_time}"
    )
    return True, events_detected
# =============================================================================


# =============================================================================
# BOOKING EXTENSION FUNCTIONS
# =============================================================================

def edit_reservation_end_time(page, booking, new_end_time, *, save_not_before=None):
    """Edit an existing reservation to extend its end time.

    Uses Asimut's edit feature:
    1. Navigate to agenda
    2. Find the event using JavaScript (fast DOM search)
    3. Click more_vert button
    4. Click "Edit event"
    5. Update end time
    6. Click away to dismiss dropdown, then Save
    7. Navigate back to agenda

    Args:
        page: Playwright page object
        booking: Dict with date, startTime, endTime, room
        new_end_time: New end time string (e.g., "10:00")
        save_not_before: Optional exact horizon boundary. The edit form is
            prepared immediately, but no receipt or Save occurs before it.

    Returns:
        True if successfully edited, False otherwise
    """
    date_str = booking['date']
    start_time = normalize_time_string(booking['startTime'])
    current_end = booking['endTime']
    room = booking.get('room', 'Unknown')
    event_id = booking.get("eventId")
    event_url = booking.get("event_url")
    save_clicked = False
    save_receipt_id = None

    if (
        isinstance(event_id, bool)
        or not isinstance(event_id, int)
        or event_id <= 0
        or not isinstance(event_url, str)
        or parse_confirmed_event_id(event_url) != event_id
    ):
        print("    Extension state has no verified exact event identity")
        return False

    print(f"  Editing reservation: {room} on {date_str}")
    print(f"    Current: {start_time}-{current_end}")
    print(f"    New end: {new_end_time}")

    try:
        # 1. Navigate to agenda page
        safe_goto(page, ASIMUT_AGENDA_URL)
        page.wait_for_timeout(2000)

        # 2. Find the matching event card using JavaScript for efficiency
        def find_matching_panel():
            """Use JavaScript to search DOM for matching reservation panel by date, room, and time."""
            return page.evaluate("""(args) => {
                const { eventId, room, time, currentEnd, targetDate } = args;
                const targetMarker = 'data-asimutbooker-extension-target';
                document.querySelectorAll(`[${targetMarker}]`).forEach(
                    element => element.removeAttribute(targetMarker)
                );
                const exactSelector = `[data-cy="event_${eventId}"], #event_${eventId}`;
                const exactCards = Array.from(new Set(
                    document.querySelectorAll(exactSelector)
                ));
                if (exactCards.length > 1) return -2;
                let panels = Array.from(new Set(
                    exactCards.map(
                        card => card.closest('.as-event-panel') || card
                    )
                ));
                if (panels.length === 0) {
                    panels = Array.from(document.querySelectorAll('.as-event-panel')).filter(
                        panel => Array.from(panel.querySelectorAll('a[href]')).some(link => {
                            try {
                                const url = new URL(
                                    link.getAttribute('href'),
                                    window.location.origin
                                );
                                return url.origin === window.location.origin
                                    && url.pathname === '/arrangement'
                                    && url.hash === ''
                                    && url.search === `?eventId=${eventId}`;
                            } catch (_error) {
                                return false;
                            }
                        })
                    );
                }
                if (panels.length !== 1) return -2;

                // Parse target date for comparison (YYYY-MM-DD format)
                const [targetYear, targetMonth, targetDay] = targetDate.split('-').map(Number);

                const monthNames = ['January', 'February', 'March', 'April', 'May', 'June',
                                   'July', 'August', 'September', 'October', 'November', 'December'];

                // Helper to find which date section a panel belongs to
                function getPanelDate(panel) {
                    // Walk backwards through previous siblings and parent to find date header
                    let el = panel;
                    while (el) {
                        // Check previous siblings
                        let sibling = el.previousElementSibling;
                        while (sibling) {
                            const text = (sibling.textContent || '').trim();
                            // Match date headers like "Monday 3 February 2026"
                            const dateMatch = text.match(/^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\\s+(\\d{1,2})\\s+(January|February|March|April|May|June|July|August|September|October|November|December)\\s+(\\d{4})$/i);
                            if (dateMatch) {
                                const day = parseInt(dateMatch[2]);
                                const month = monthNames.indexOf(dateMatch[3]);
                                const year = parseInt(dateMatch[4]);
                                return { year, month, day };
                            }
                            sibling = sibling.previousElementSibling;
                        }
                        // Move up to parent and continue
                        el = el.parentElement;
                    }
                    return null;
                }

                // Search from end (future events) to beginning
                for (let i = panels.length - 1; i >= 0; i--) {
                    const panel = panels[i];
                    const text = panel.innerText || '';

                    // Must be the exact reservation card, not nearby text.
                    const displayNames = panel.querySelectorAll(
                        '[data-cy="event-display-name"]'
                    );
                    if (
                        displayNames.length !== 1
                        || (displayNames[0].textContent || '').trim() !== 'Reservation'
                    ) continue;

                    // Must match room (case insensitive)
                    if (!text.toLowerCase().includes(room.toLowerCase())) continue;

                    // Must match start time
                    const timeMatch = text.match(/(\\d{2}):(\\d{2})\\s*[-–]\\s*(\\d{2}):(\\d{2})/);
                    if (!timeMatch) continue;

                    const startTime = timeMatch[1] + ':' + timeMatch[2];
                    const endTime = timeMatch[3] + ':' + timeMatch[4];
                    if (startTime !== time || endTime !== currentEnd) continue;

                    // Must match date
                    const panelDate = getPanelDate(panel);
                    if (!panelDate) {
                        console.log('Could not determine date for panel', i);
                        continue;
                    }

                    // Compare dates (month is 0-indexed from monthNames, so targetMonth-1)
                    if (panelDate.year !== targetYear ||
                        panelDate.month !== (targetMonth - 1) ||
                        panelDate.day !== targetDay) {
                        continue;
                    }

                    // Check if cancelled (strikethrough)
                    const allElements = [panel, ...panel.querySelectorAll('*')];
                    let isCancelled = false;
                    for (const el of allElements) {
                        const style = window.getComputedStyle(el);
                        const className = String(el.className || '').toLowerCase();
                        if (style.textDecoration.includes('line-through') ||
                            style.textDecorationLine.includes('line-through') ||
                            className.includes('cancelled') ||
                            className.includes('canceled') ||
                            className.includes('deleted') ||
                            className.includes('removed')) {
                            isCancelled = true;
                            break;
                        }
                    }
                    if (isCancelled) continue;

                    // Found a valid match
                    panel.setAttribute(targetMarker, String(eventId));
                    return i;
                }
                return -1;
            }""", {
                "eventId": event_id,
                "room": room,
                "time": start_time,
                "currentEnd": normalize_time_string(current_end),
                "targetDate": date_str,
            })

        # First try without scrolling
        matching_index = find_matching_panel()

        # If not found, scroll down and try again (booking may be further in future)
        if matching_index == -2:
            print(f"    Exact event id {event_id} was missing or ambiguous")
            return False
        if matching_index < 0:
            print(f"    Not found in visible area, scrolling...")
            for scroll_attempt in range(10):
                page.evaluate("window.scrollBy(0, 2000)")
                page.wait_for_timeout(300)
                matching_index = find_matching_panel()
                if matching_index == -2:
                    print(f"    Exact event id {event_id} became ambiguous")
                    return False
                if matching_index >= 0:
                    break

        if matching_index < 0:
            print(f"    Could not find event card for {room} {date_str} {start_time}")
            # Already on agenda page, no need to navigate
            return False

        # Resolve the exact current card, expanding to its legacy panel only
        # when that ancestor contains the same proven event-id card. If the
        # current renderer exposes only legacy panels, use only the unique DOM
        # node marked by the same-origin canonical-link proof above.
        exact_cards = page.locator(
            f"[data-cy='event_{event_id}'], #event_{event_id}"
        )
        exact_card_count = exact_cards.count()
        if exact_card_count > 1:
            print(f"    Exact event {event_id} became ambiguous before editing")
            return False
        if exact_card_count == 1:
            exact_card = exact_cards.first
            legacy_panel = exact_card.locator(
                "xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), "
                "' as-event-panel ')][1]"
            ).first
            reservation_card = legacy_panel if legacy_panel.count() > 0 else exact_card
        else:
            marked_panels = page.locator(
                f'[data-asimutbooker-extension-target="{event_id}"]'
            )
            if marked_panels.count() != 1:
                print(
                    f"    Canonical legacy event {event_id} disappeared or became ambiguous "
                    "before editing"
                )
                return False
            reservation_card = marked_panels.first
        if reservation_card.count() != 1:
            print(f"    Exact event {event_id} disappeared before editing")
            return False
        print(f"    Found exact event card {event_id}")

        reservation_card.scroll_into_view_if_needed()
        page.wait_for_timeout(500)

        # 3. Click the more_vert button on this specific card
        more_btn = reservation_card.locator("button[data-cy='button_more']").first
        if more_btn.count() == 0:
            more_btn = reservation_card.locator(".as-event-options-button").first
        if more_btn.count() == 0:
            more_btn = reservation_card.locator("button:has(mat-icon:has-text('more_vert'))").first
        if more_btn.count() == 0:
            more_btn = reservation_card.locator("mat-icon:has-text('more_vert')").first

        if more_btn.count() > 0 and more_btn.is_visible():
            more_btn.click()
            page.wait_for_timeout(500)
        else:
            print(f"    Could not find more options button")
            # Navigate back to clean state
            safe_goto(page, ASIMUT_AGENDA_URL)
            return False

        # 4. Click "Edit event" from the menu
        edit_option = page.locator("mat-list-item").filter(has_text="Edit event").first
        if edit_option.count() == 0:
            edit_option = page.locator("mat-list-item:has(mat-icon:has-text('edit'))").first

        if edit_option.count() > 0 and edit_option.is_visible():
            edit_option.click()
            page.wait_for_timeout(1500)
        else:
            print(f"    Could not find 'Edit event' option")
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            # Navigate back to clean state
            safe_goto(page, ASIMUT_AGENDA_URL)
            return False

        if parse_event_editor_id(page.url) != event_id:
            print(
                "    Edit action did not open the exact tracked event "
                f"{event_id}; refusing to edit"
            )
            safe_goto(page, ASIMUT_AGENDA_URL)
            return False

        # 5. Find the end time input and update it
        extension_end_selector = (
            "#endDate, input[aria-label='End time'], "
            "input[data-cy='time-range-end-time'], "
            "input[formcontrolname='endTime']"
        )
        end_input = page.locator(extension_end_selector).first

        if end_input.count() > 0 and end_input.is_visible():
            if save_not_before is not None:
                # Open and prove the exact editor in advance, but wait until
                # the horizon boundary before changing the value. This avoids
                # relying on Asimut retaining a not-yet-valid future end time.
                wait_until_datetime(
                    save_not_before,
                    page,
                    label="EXTEND",
                )
                if end_input.count() == 0 or not end_input.is_visible():
                    print(
                        "    Extension editor closed while waiting for the "
                        "horizon boundary; cancelling"
                    )
                    safe_goto(page, ASIMUT_AGENDA_URL)
                    return False

            # Click and fill the new time
            end_input.click()
            end_input.fill(new_end_time)
            page.wait_for_timeout(300)
            actual_end = end_input.input_value()
            if not booking_times_match(start_time, new_end_time, start_time, actual_end):
                print(
                    f"    Asimut did not retain requested end time "
                    f"({actual_end} != {new_end_time}); cancelling"
                )
                safe_goto(page, ASIMUT_AGENDA_URL)
                return False
            if not booking_summary_matches(
                page_booking_snapshot(page),
                room,
                date_str,
                start_time,
                new_end_time,
            ):
                print("    Edit form room/date/time does not match the selected reservation")
                safe_goto(page, ASIMUT_AGENDA_URL)
                return False

            # Click on the right side of the page to dismiss any dropdown
            viewport = page.viewport_size
            if viewport:
                page.mouse.click(viewport['width'] - 100, viewport['height'] // 2)
            page.wait_for_timeout(500)

            # 6. Check for yellow warning boxes before saving
            # Look for warning/error/conflict elements that indicate extension isn't allowed
            warning_elements = page.locator(".warning, .error, [class*='conflict'], [class*='clash'], [class*='warning']").all()
            has_warning = False
            for warn in warning_elements:
                if warn.is_visible():
                    text = warn.text_content() or ""
                    if text.strip() and len(text.strip()) > 3:
                        print(f"    Warning found: {text.strip()[:80]}")
                        has_warning = True
                        break

            # Also check for disabled Save button (icon shows remove/block/error)
            save_btn = page.locator("button:has-text('Save')").first
            if save_btn.count() > 0 and save_btn.is_visible():
                save_icon = save_btn.locator("mat-icon").first
                if save_icon.count() > 0:
                    icon_text = save_icon.text_content() or ""
                    disabled_icons = ["remove", "block", "close", "cancel", "error", "warning"]
                    if any(disabled in icon_text.lower() for disabled in disabled_icons):
                        print(f"    Save disabled (icon: {icon_text})")
                        has_warning = True

            if has_warning:
                # Extension not allowed - cancel and return to agenda
                print(f"    Extension blocked by warning, cancelling...")
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
                safe_goto(page, ASIMUT_AGENDA_URL)
                return False

            # 7. Click Save button
            if save_btn.count() > 0 and save_btn.is_visible():
                try:
                    if not save_btn.is_enabled():
                        print("    Save is disabled; refusing to create a receipt")
                        safe_goto(page, ASIMUT_AGENDA_URL)
                        return False
                except Exception as exc:
                    print(f"    Could not prove Save is enabled: {exc}")
                    safe_goto(page, ASIMUT_AGENDA_URL)
                    return False
                current_event_url = page.url
                if parse_event_editor_id(current_event_url) != event_id:
                    print(
                        "    Extension editor is no longer on the exact tracked "
                        f"event {event_id}; refusing to edit"
                    )
                    safe_goto(page, ASIMUT_AGENDA_URL)
                    return False
                try:
                    receipt = record_pending_extension(
                        room=room,
                        booking_date=date_str,
                        start=start_time,
                        end=new_end_time,
                        event_url=event_url,
                    )
                    save_receipt_id = receipt["id"]
                except MutationReceiptError as exc:
                    raise BookingVerificationError(
                        f"Could not create an extension receipt before Save: {exc}"
                    ) from exc

                try:
                    save_btn.click(no_wait_after=True, timeout=5000)
                    save_clicked = True
                except Exception as exc:
                    raise BookingVerificationError(
                        f"Extension Save outcome is uncertain (receipt {receipt['id']}): {exc}"
                    ) from exc

                deadline = time.monotonic() + 30
                editor_closed = False
                while time.monotonic() < deadline:
                    rejection = _visible_save_rejection(page)
                    if rejection:
                        try:
                            resolve_mutation_receipt(
                                receipt["id"],
                                resolution=f"Asimut rejected extension Save: {rejection}",
                            )
                        except MutationReceiptError as exc:
                            raise BookingVerificationError(
                                f"Extension was rejected, but receipt {receipt['id']} "
                                f"could not be closed: {exc}"
                            ) from exc
                        print(f"    Extension rejected: {rejection}")
                        safe_goto(page, ASIMUT_AGENDA_URL)
                        return False
                    editing = page.locator(extension_end_selector).first
                    if editing.count() == 0 or not editing.is_visible():
                        editor_closed = True
                        break
                    page.wait_for_timeout(250)

                if not editor_closed:
                    raise BookingVerificationError(
                        f"Extension Save outcome is uncertain after 30s (receipt {receipt['id']}); "
                        "the editor never reached a settled post-Save state"
                    )

                if page.url != event_url:
                    safe_goto(page, event_url)
                try:
                    verify_persisted_booking_page(
                        page,
                        room,
                        date_str,
                        start_time,
                        new_end_time,
                    )
                    verify_mutation_receipt(
                        receipt["id"],
                        event_url=event_url,
                    )
                except (MutationReceiptError, BookingVerificationError) as exc:
                    raise BookingVerificationError(
                        f"Extension outcome requires reconciliation (receipt {receipt['id']}): {exc}"
                    ) from exc

                # Return the verified remote success before cleanup navigation.
                # The caller records the action, and the next phase performs its
                # own verified navigation. A navigation failure here must never
                # turn a confirmed mutation into an uncounted retry.
                print(f"    SUCCESS: Extended to {new_end_time}")
                return True
            else:
                print(f"    Could not find Save button")
                # Press Escape and navigate back to clean state
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
                safe_goto(page, ASIMUT_AGENDA_URL)
                return False
        else:
            print(f"    Could not find end time input")
            # Press Escape and navigate back to clean state
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            safe_goto(page, ASIMUT_AGENDA_URL)
            return False

    except BookingVerificationError:
        raise
    except Exception as e:
        if save_clicked:
            raise BookingVerificationError(
                "Extension Save outcome is uncertain after the click "
                f"(receipt {save_receipt_id or 'unknown'}): {e}. "
                "No further mutations will be attempted until reconciliation."
            ) from e
        print(f"    Error editing reservation: {e}")
        # Try to recover to a clean state
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            safe_goto(page, ASIMUT_AGENDA_URL)
        except:
            pass
        return False

    return False


def try_extend_booking(
    page,
    booking,
    tracker=None,
    remaining_daily_hours=None,
    time_prefs=None,
    max_action_minutes=None,
    agenda_reservations=None,
    now=None,
):
    """Attempt to extend an existing booking using Asimut's edit feature.

    Args:
        page: Playwright page object
        booking: Dict from extendable_bookings with:
            date, startTime, endTime, room, created_at, target_end, and
            historical horizon evidence
        tracker: BookingTracker instance for quota/gap validation (optional)

    Returns:
        Tuple of (success: bool, new_end_time: str or None, message: str)
    """
    room = booking["room"]
    date_str = booking["date"]
    start_time = booking["startTime"]
    current_end = booking["endTime"]
    target_end = booking["target_end"]
    created_at = booking["created_at"]

    # Parse times to decimals
    start_parts = start_time.split(':')
    start_hour = int(start_parts[0]) + int(start_parts[1]) / 60

    end_parts = current_end.split(':')
    current_end_hour = int(end_parts[0]) + int(end_parts[1]) / 60

    target_parts = target_end.split(':')
    target_end_hour = int(target_parts[0]) + int(target_parts[1]) / 60

    # A practice-plan target is authoritative.  If existing reservations have
    # already filled it (including after the user lowers a date override), this
    # local extension intent is obsolete and must not be retried every run.
    if remaining_daily_hours is not None and remaining_daily_hours <= 1e-9:
        try:
            remove_extendable_booking(room, date_str, start_time)
        except SettingsError as exc:
            return (
                False,
                None,
                f"Daily practice target is met, but extension state could not be cleared: {exc}",
            )
        return False, None, "Daily practice target is met; extension state cleared"

    try:
        booking = ensure_extendable_booking_event_identity(
            booking,
            agenda_reservations,
        )
    except SettingsError as exc:
        return False, None, f"Extension identity could not be proven: {exc}"

    # A legacy migration may update the authoritative current end time.
    current_end = booking["endTime"]
    end_parts = current_end.split(':')
    current_end_hour = int(end_parts[0]) + int(end_parts[1]) / 60

    # Reconcile stale local extension state against the authoritative agenda.
    if tracker is not None:
        matching_ranges = [
            (range_start, range_end)
            for range_start, range_end, range_room
            in tracker.reservation_ranges.get(date_str, [])
            if range_room == room and abs(range_start - start_hour) < 0.01
        ]
        if matching_ranges:
            actual_end_hour = max(range_end for _, range_end in matching_ranges)
            if abs(actual_end_hour - current_end_hour) >= 0.01:
                actual_h = int(actual_end_hour)
                actual_m = int((actual_end_hour % 1) * 60)
                actual_end = f"{actual_h:02d}:{actual_m:02d}"
                print(
                    f"  [RECONCILE] Agenda ends at {actual_end}; "
                    f"local extension state said {current_end}"
                )
                if actual_end_hour >= target_end_hour - 0.01:
                    try:
                        remove_extendable_booking(room, date_str, start_time)
                    except SettingsError as exc:
                        return False, None, f"Target reached, but stale local state could not be cleared: {exc}"
                    return False, None, "Extension target is already reached in the agenda"
                try:
                    update_extendable_booking_end_time(
                        room,
                        date_str,
                        start_time,
                        actual_end,
                    )
                except SettingsError as exc:
                    return False, None, f"Could not reconcile stale extension state: {exc}"
                booking = dict(booking)
                booking["endTime"] = actual_end
                current_end = actual_end
                current_end_hour = actual_end_hour

    # Calculate if extension is possible based on time elapsed. A scheduled
    # run that arrives within three minutes of the next quarter-hour prepares
    # the exact editor now and defers only the verified Save.
    extension_now = now or datetime.now()
    can_extend, max_end_hour, reason = calculate_max_extension(
        room,
        date_str,
        start_hour,
        current_end_hour,
        created_at,
        target_end_hour,
        now=extension_now,
    )

    save_not_before = None
    if not can_extend:
        plan = plan_horizon_extension(
            room,
            date_str,
            start_hour,
            current_end_hour,
            target_end_hour,
            now=extension_now,
        )
        if (
            plan.next_end_hour is None
            or plan.next_unlock_at is None
            or plan.wait_seconds is None
            or not 0 < plan.wait_seconds <= EXTENSION_PREP_WINDOW_SECONDS
        ):
            return False, None, reason
        max_end_hour = plan.next_end_hour
        save_not_before = plan.next_unlock_at
        print(
            f"  [EXTEND] Preparing {room} before its "
            f"{save_not_before.strftime('%H:%M:%S')} unlock"
        )

    if remaining_daily_hours is not None:
        max_end_hour = min(max_end_hour, current_end_hour + max(0.0, remaining_daily_hours))
    max_end_hour = min(
        max_end_hour,
        current_end_hour + _action_maximum_minutes(max_action_minutes) / 60,
    )
    if time_prefs and time_prefs.get("enabled") and time_prefs.get("strict_mode"):
        if not interval_is_strictly_preferred(
            start_hour,
            current_end_hour,
            time_prefs,
        ):
            return False, None, "Existing booking falls outside the strict time window"
        max_end_hour = min(max_end_hour, time_prefs["end_hour"])
    max_end_hour = int(max_end_hour * 4 + 1e-9) / 4
    if (max_end_hour - current_end_hour) * 60 < 15:
        return False, None, "Daily target or strict time window leaves less than 15min to extend"

    # Parse the booking date for tracker validation
    booking_date = datetime.strptime(date_str, '%Y-%m-%d')

    # If tracker is provided, validate against booking rules
    if tracker is not None:
        # Calculate the extension amount (what we're adding, not the total)
        extension_hours = max_end_hour - current_end_hour

        # Rule: Rolling weekly quota (28 hours per week)
        remaining_quota_hours = tracker.get_remaining_quota_hours()
        if extension_hours > remaining_quota_hours:
            if remaining_quota_hours < 0.25:  # Less than 15 minutes
                return False, None, f"Weekly quota full ({tracker.get_total_booking_hours():.1f}/{MAX_ROLLING_QUOTA_HOURS}h)"
            # Limit extension to stay within weekly quota
            max_end_hour = current_end_hour + remaining_quota_hours
            # Round down to 15-minute interval
            max_end_mins = int((max_end_hour % 1) * 60)
            max_end_mins = (max_end_mins // 15) * 15
            max_end_hour = int(max_end_hour) + max_end_mins / 60
            extension_hours = max_end_hour - current_end_hour
            if extension_hours < 0.25:  # Less than 15 minutes
                return False, None, f"Weekly quota would only allow {extension_hours * 60:.0f}min extension"

        # Rule: Peak hours limit (Mon-Fri 9am-4pm, max 2 hours per day)
        if booking_date.weekday() < 5:  # Monday-Friday
            # Calculate peak hours overlap for the EXTENSION portion only
            # (the current booking's peak hours are already accounted for)
            extension_peak_start = max(current_end_hour, PEAK_START)
            extension_peak_end = min(max_end_hour, PEAK_END)
            if extension_peak_end > extension_peak_start:
                new_peak_mins = (extension_peak_end - extension_peak_start) * 60
                day_peak_used = tracker.get_peak_used_for_day(booking_date)
                if day_peak_used + new_peak_mins > MAX_PEAK_HOURS * 60:
                    # Try to limit extension to stay within peak quota
                    remaining_peak_mins = MAX_PEAK_HOURS * 60 - day_peak_used
                    if remaining_peak_mins <= 0:
                        return False, None, f"Peak hours limit reached for {date_str} ({day_peak_used:.0f}/{MAX_PEAK_HOURS * 60} min)"

                    # Calculate max end hour that respects peak limit
                    # We can only extend into peak by remaining_peak_mins
                    max_peak_end = extension_peak_start + remaining_peak_mins / 60
                    if max_peak_end < max_end_hour and max_peak_end > current_end_hour:
                        # Limit extension to respect peak quota
                        max_end_hour = min(max_end_hour, max_peak_end)
                        # Round down to 15-minute interval
                        max_end_mins = int((max_end_hour % 1) * 60)
                        max_end_mins = (max_end_mins // 15) * 15
                        max_end_hour = int(max_end_hour) + max_end_mins / 60
                        # Check if extension is still worth it
                        if (max_end_hour - current_end_hour) * 60 < 15:
                            return False, None, f"Peak hours limit would only allow {(max_end_hour - current_end_hour) * 60:.0f}min extension"

        # Rule: Same-room gap (60 minutes between bookings in same room)
        # Check if extending would violate gap to another booking
        if date_str in tracker.reservation_ranges:
            for r_start, r_end, r_room in tracker.reservation_ranges[date_str]:
                if r_room and r_room == room:
                    # Skip if this is our own booking
                    if abs(r_start - start_hour) < 0.01:
                        continue
                    # Check if extension would violate gap before another booking
                    gap_before = (r_start - max_end_hour) * 60
                    if 0 <= gap_before < SAME_ROOM_GAP_MINUTES:
                        # Limit extension to maintain gap
                        safe_end = r_start - SAME_ROOM_GAP_MINUTES / 60
                        if safe_end <= current_end_hour:
                            return False, None, f"Same-room gap: another booking at {int(r_start):02d}:{int((r_start % 1) * 60):02d}"
                        max_end_hour = safe_end
                        # Round down to 15-minute interval
                        max_end_mins = int((max_end_hour % 1) * 60)
                        max_end_mins = (max_end_mins // 15) * 15
                        max_end_hour = int(max_end_hour) + max_end_mins / 60
                        if (max_end_hour - current_end_hour) * 60 < 15:
                            return False, None, f"Same-room gap limits extension to <15min"

        # Rule: extending must not cross any class or reservation conflict.
        for conflict_start, conflict_end in tracker.conflict_ranges.get(date_str, []):
            is_own_booking = (
                abs(conflict_start - start_hour) < 0.01
                and conflict_end <= current_end_hour + 0.01
            )
            if is_own_booking:
                continue
            if current_end_hour < conflict_end and max_end_hour > conflict_start:
                max_end_hour = min(max_end_hour, conflict_start)
                max_end_hour = int(max_end_hour * 4 + 1e-9) / 4
                if (max_end_hour - current_end_hour) * 60 < 15:
                    return False, None, "Another agenda event blocks this extension"

    # Format new end time
    new_end_h = int(max_end_hour)
    new_end_m = int((max_end_hour % 1) * 60)
    new_end_time = f"{new_end_h:02d}:{new_end_m:02d}"

    # Normalize target_end for comparison
    target_end_normalized = normalize_time_string(target_end)

    print(f"\n{'='*60}")
    print(f"EXTENDING BOOKING: {room} on {date_str}")
    print(f"  Current: {start_time} - {current_end}")
    print(f"  Target:  {start_time} - {new_end_time}")
    print(f"{'='*60}")

    # Perform the edit
    if save_not_before is None:
        success = edit_reservation_end_time(page, booking, new_end_time)
    else:
        success = edit_reservation_end_time(
            page,
            booking,
            new_end_time,
            save_not_before=save_not_before,
        )

    if success:
        # Update tracker with the extended hours
        if tracker is not None:
            # Add extension hours to existing reservation hours (for weekly quota)
            extension_hours = max_end_hour - current_end_hour
            tracker.existing_reservation_hours += extension_hours
            tracker.reservation_hours_by_day[date_str] = (
                tracker.reservation_hours_by_day.get(date_str, 0.0) + extension_hours
            )
            tracker.conflict_ranges.setdefault(date_str, []).append(
                (current_end_hour, max_end_hour)
            )
            print(f"  [Tracker] Added {extension_hours:.2f}h extension to quota ({tracker.get_total_booking_hours():.1f}/{MAX_ROLLING_QUOTA_HOURS}h)")

            for index, (b_room, b_date, b_start, b_end) in enumerate(tracker.bookings):
                if (
                    b_room == room
                    and as_date(b_date).isoformat() == date_str
                    and abs(b_start - start_hour) < 0.01
                ):
                    tracker.bookings[index] = (b_room, b_date, b_start, max_end_hour)
                    break

            # Update peak hours if applicable
            if booking_date.weekday() < 5:
                extension_peak_start = max(current_end_hour, PEAK_START)
                extension_peak_end = min(max_end_hour, PEAK_END)
                if extension_peak_end > extension_peak_start:
                    new_peak_mins = (extension_peak_end - extension_peak_start) * 60
                    if date_str not in tracker.peak_hours_by_day:
                        tracker.peak_hours_by_day[date_str] = 0
                    tracker.peak_hours_by_day[date_str] += new_peak_mins

            # Update reservation_ranges with the new end time (for same-room gap calculations)
            ranges = tracker.reservation_ranges.setdefault(date_str, [])
            updated = False
            for i, (r_start, r_end, r_room) in enumerate(ranges):
                if r_room == room and abs(r_start - start_hour) < 0.01:
                    ranges[i] = (r_start, max_end_hour, r_room)
                    updated = True
                    print(f"  [Tracker] Updated reservation_ranges: {room} now ends at {new_end_time}")
                    break
            if not updated:
                ranges.append((start_hour, max_end_hour, room))
                print(f"  [Tracker] Added to reservation_ranges: {room} {start_time}-{new_end_time}")

        # The remote extension and receipt are already verified. A local state
        # failure must not turn that confirmed mutation into an ordinary failure.
        try:
            if new_end_time == target_end_normalized:
                remove_extendable_booking(room, date_str, start_time)
            else:
                update_extendable_booking_end_time(room, date_str, start_time, new_end_time)
        except SettingsError as exc:
            print(
                f"  WARNING: Extension is confirmed, but local extension state "
                f"could not be updated: {exc}"
            )

        return True, new_end_time, "Extended successfully"

    return False, None, "Edit operation failed"


def calculate_max_bookings_per_day(remaining_quota_hours, enabled_days_count):
    """
    Calculate maximum bookings per day based on remaining quota and enabled days.

    This dynamically adjusts bookings per day so that if you have fewer days enabled,
    you can book more hours per day (up to the quota limit).

    Args:
        remaining_quota_hours: Hours left in the weekly quota (28h max)
        enabled_days_count: Number of live-window dates enabled for booking

    Returns:
        Maximum bookings per day (integer, minimum 1, maximum based on quota)
    """
    if enabled_days_count <= 0:
        return 0

    # Calculate target hours per day
    hours_per_day = remaining_quota_hours / enabled_days_count

    # Each booking is up to MAX_BOOKING_HOURS (2 hours)
    # We want to spread bookings across the day, so calculate how many 2-hour blocks we need
    bookings_per_day = hours_per_day / MAX_BOOKING_HOURS

    # Round up to ensure we fill the quota, but at least 1 booking per day
    bookings_per_day = max(1, int(bookings_per_day + 0.5))

    # Cap at a reasonable maximum (e.g., 6 bookings = 12 hours per day max)
    # This prevents trying to book unreasonably many slots on a single day
    return min(bookings_per_day, 6)


def calculate_target_hours_for_day(
    target_date,
    enabled_dates,
    tracker,
    total_quota=MAX_ROLLING_QUOTA_HOURS,
    practice_plan=None,
):
    """
    Calculate target hours for a specific day to achieve even distribution.

    This function considers existing bookings across all enabled days and calculates
    how many more hours should be booked on the target day to achieve a balanced week.

    Args:
        target_date: The date to calculate target hours for
        enabled_dates: List of enabled dates in the booking window
        tracker: BookingTracker with existing reservation data
        total_quota: Total weekly quota (default 28 hours)

    Returns:
        Tuple of (target_hours, max_bookings) for the day
    """
    if not enabled_dates:
        return 0.0, 0

    if practice_plan is not None and practice_plan.enabled:
        current_hours = tracker.get_hours_for_day(target_date)
        daily_remaining = remaining_target_hours(practice_plan, target_date, current_hours)
        target_hours = min(
            daily_remaining or 0.0,
            tracker.get_remaining_quota_hours(),
        )
        max_bookings = max_bookings_for_budget(target_hours) or 0
        return target_hours, min(max_bookings, 24)

    # Calculate ideal hours per day for even distribution
    ideal_per_day = total_quota / len(enabled_dates)

    # Get current hours for each enabled day
    hours_per_day = {}
    total_existing = 0.0
    for d in enabled_dates:
        day_hours = tracker.get_hours_for_day(d)
        hours_per_day[d.strftime('%Y-%m-%d')] = day_hours
        total_existing += day_hours

    remaining_quota = max(0, total_quota - total_existing)

    # If no remaining quota, nothing to book
    if remaining_quota <= 0:
        return 0.0, 0

    # Count days that still need more hours (below ideal)
    days_needing_hours = []
    for d in enabled_dates:
        day_key = d.strftime('%Y-%m-%d')
        day_hours = hours_per_day.get(day_key, 0.0)
        if day_hours < ideal_per_day:
            # Calculate how much this day is "behind" the ideal
            deficit = ideal_per_day - day_hours
            days_needing_hours.append((d, deficit))

    target_date_key = target_date.strftime('%Y-%m-%d')
    current_hours = hours_per_day.get(target_date_key, 0.0)

    if not days_needing_hours:
        # All days are at or above ideal - distribute remaining evenly
        target_hours = remaining_quota / len(enabled_dates)
    elif current_hours >= ideal_per_day:
        # This day is already at or above ideal - don't add more
        # Let the days with deficits catch up first
        target_hours = 0.0
    else:
        # This day is below ideal - calculate its fair share of remaining quota
        # Prioritize days with larger deficits proportionally
        total_deficit = sum(deficit for _, deficit in days_needing_hours)
        day_deficit = ideal_per_day - current_hours
        # Proportional share based on how much this day needs relative to total need
        proportion = day_deficit / total_deficit if total_deficit > 0 else 1.0 / len(days_needing_hours)
        target_hours = min(remaining_quota * proportion, day_deficit)

    # Cap at remaining quota and ensure positive
    target_hours = max(0, min(target_hours, remaining_quota))

    # Allow enough successes to fill the target even when availability is
    # fragmented into the 30-minute minimum.  This is a ceiling, not a goal;
    # the daily-hour budget still stops the loop at the requested total.
    if target_hours <= 0:
        max_bookings = 0
    else:
        max_bookings = max_bookings_for_budget(target_hours) or 0
        max_bookings = min(max_bookings, 24)

    return target_hours, max_bookings


def remaining_run_daily_budget(
    practice_plan,
    target_date,
    tracker,
    *,
    planned_additional_hours=None,
    starting_day_hours=None,
):
    """Return a budget that also works when availability is fragmented."""

    current_hours = tracker.get_hours_for_day(target_date)
    if practice_plan.enabled:
        return remaining_target_hours(practice_plan, target_date, current_hours)
    if planned_additional_hours is None or starting_day_hours is None:
        return None
    added_this_pass = max(0.0, current_hours - starting_day_hours)
    remaining = max(0.0, planned_additional_hours - added_this_pass)
    return int(remaining * 4 + 1e-9) / 4


class BookingTracker:
    """Tracks bookings and enforces rules."""

    def __init__(self):
        self.bookings = []  # List of (room, date, start_hour, end_hour)
        self.agenda_events = []  # Complete validated events from the latest scan.
        self.agenda_active_event_ids = []  # Includes valid out-of-window cards.
        self.conflict_ranges = {}  # Per-day conflict ranges: {date: [(start, end), ...]}
        self.reservation_ranges = {}  # Per-day reservation ranges (for same-room gap buffer): {date: [(start, end), ...]}
        self.peak_hours_by_day = {}  # Per-day peak minutes: {date_key: minutes}
        self.reservation_hours_by_day = {}  # Per-day reservation hours: {date_key: hours}
        self.existing_reservation_hours = 0.0  # Total hours from existing reservations

    def add_existing_event(
        self,
        date,
        start_hour,
        end_hour,
        is_reservation=False,
        room=None,
        blocks_conflict=True,
    ):
        """Record an existing event from agenda scan.

        Args:
            date: The date of the event
            start_hour: Start time as decimal hour (e.g., 9.5 for 9:30)
            end_hour: End time as decimal hour
            is_reservation: True if this is a practice room booking ("Reservation"),
                           False for classes/rehearsals/other events
            room: Room name (e.g., "B0.27") if available, for same-room gap enforcement
            blocks_conflict: Whether the event blocks generic overlap. Ignored
                             reservations set this false but still count toward
                             reservation limits and same-room spacing.

        Only reservations (practice room bookings) count toward peak hours quota.
        Non-ignored events are added as conflicts to avoid double-booking.
        """
        date_key = date.strftime('%Y-%m-%d')

        # Non-ignored classes and reservations block generic time overlap.
        if blocks_conflict:
            if date_key not in self.conflict_ranges:
                self.conflict_ranges[date_key] = []
            self.conflict_ranges[date_key].append((start_hour, end_hour))

        # Also track reservation ranges with room info for same-room gap enforcement
        if is_reservation:
            if date_key not in self.reservation_ranges:
                self.reservation_ranges[date_key] = []
            self.reservation_ranges[date_key].append((start_hour, end_hour, room))

        # Only track peak hours for reservations (practice room bookings)
        peak_info = ""
        if is_reservation and date.weekday() < 5:  # Monday-Friday
            overlap_start = max(start_hour, PEAK_START)
            overlap_end = min(end_hour, PEAK_END)
            if overlap_end > overlap_start:
                peak_mins = (overlap_end - overlap_start) * 60
                if date_key not in self.peak_hours_by_day:
                    self.peak_hours_by_day[date_key] = 0
                self.peak_hours_by_day[date_key] += peak_mins
                peak_info = f" (+{peak_mins:.0f}min peak)"

        # Track reservation hours for rolling quota (28 hours per week)
        if is_reservation:
            duration_hours = end_hour - start_hour
            self.existing_reservation_hours += duration_hours
            # Also track per-day for smart redistribution
            if date_key not in self.reservation_hours_by_day:
                self.reservation_hours_by_day[date_key] = 0.0
            self.reservation_hours_by_day[date_key] += duration_hours

        event_type = "reservation" if is_reservation else "conflict"
        print(f"  [Tracker] Added {event_type}: {date_key} {start_hour:.2f}-{end_hour:.2f}{peak_info}")

    def add_booking(self, room, date, start_hour, end_hour):
        """Record a successful booking."""
        self.bookings.append((room, date, start_hour, end_hour))
        date_key = date.strftime('%Y-%m-%d')

        # Update peak hours if applicable (only bookings count, not existing events)
        peak_mins_added = 0
        if date.weekday() < 5:  # Monday-Friday
            overlap_start = max(start_hour, PEAK_START)
            overlap_end = min(end_hour, PEAK_END)
            if overlap_end > overlap_start:
                peak_mins_added = (overlap_end - overlap_start) * 60
                if date_key not in self.peak_hours_by_day:
                    self.peak_hours_by_day[date_key] = 0
                self.peak_hours_by_day[date_key] += peak_mins_added

        # Also add to conflict ranges so we don't double-book
        if date_key not in self.conflict_ranges:
            self.conflict_ranges[date_key] = []
        self.conflict_ranges[date_key].append((start_hour, end_hour))

        # Track per-day reservation hours for smart redistribution
        booking_hours = end_hour - start_hour
        if date_key not in self.reservation_hours_by_day:
            self.reservation_hours_by_day[date_key] = 0.0
        self.reservation_hours_by_day[date_key] += booking_hours

        print(f"  [Tracker] Added booking: {room} {date_key} {start_hour:.2f}-{end_hour:.2f}")
        day_peak = self.peak_hours_by_day.get(date_key, 0)
        day_hours = self.reservation_hours_by_day.get(date_key, 0)
        print(f"  [Tracker] Total bookings: {len(self.bookings)}, Day hours: {day_hours:.1f}h, Peak: {day_peak:.0f}/{MAX_PEAK_HOURS * 60} min")

    def add_conflict(self, date, start_hour, end_hour):
        """Record a discovered conflict range (during booking, not from agenda)."""
        date_key = date.strftime('%Y-%m-%d')
        if date_key not in self.conflict_ranges:
            self.conflict_ranges[date_key] = []
        self.conflict_ranges[date_key].append((start_hour, end_hour))
        print(f"  [Tracker] Added conflict: {date_key} {start_hour:.2f}-{end_hour:.2f}")

    def get_remaining_peak_minutes(self, date):
        """Get remaining peak minutes available for a specific date.

        Returns unlimited (9999) for weekends since there's no daily peak quota.
        """
        # No peak quota restriction on weekends
        if date.weekday() >= 5:  # Saturday=5, Sunday=6
            return 9999  # Effectively unlimited
        date_key = date.strftime('%Y-%m-%d')
        used = self.peak_hours_by_day.get(date_key, 0)
        return max(0, MAX_PEAK_HOURS * 60 - used)

    def get_peak_used_for_day(self, date):
        """Get peak minutes used for a specific date.

        Returns 0 for weekends since peak quota doesn't apply.
        """
        # No peak quota tracking on weekends
        if date.weekday() >= 5:  # Saturday=5, Sunday=6
            return 0
        date_key = date.strftime('%Y-%m-%d')
        return self.peak_hours_by_day.get(date_key, 0)

    def is_peak_quota_exceeded(self, date):
        """Check if peak hours quota is exceeded for a specific date.

        Always returns False for weekends since there's no daily peak quota.
        """
        # No peak quota restriction on weekends
        if date.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        date_key = date.strftime('%Y-%m-%d')
        used = self.peak_hours_by_day.get(date_key, 0)
        return used >= MAX_PEAK_HOURS * 60

    def get_total_booking_hours(self):
        """Get total booking hours (existing reservations + new bookings this session)."""
        new_booking_hours = sum(end - start for _, _, start, end in self.bookings)
        return self.existing_reservation_hours + new_booking_hours

    def get_hours_for_day(self, date):
        """Get total reservation hours for a specific day."""
        date_key = date.strftime('%Y-%m-%d')
        return self.reservation_hours_by_day.get(date_key, 0.0)

    def get_remaining_quota_hours(self):
        """Get remaining rolling quota hours."""
        return max(0, MAX_ROLLING_QUOTA_HOURS - self.get_total_booking_hours())

    def is_quota_full(self):
        """Check if rolling quota is exceeded."""
        return self.get_total_booking_hours() >= MAX_ROLLING_QUOTA_HOURS

    def overlaps_conflict(self, date, start_hour, end_hour):
        """Check if a time overlaps with known conflicts."""
        date_key = date.strftime('%Y-%m-%d')
        if date_key not in self.conflict_ranges:
            return False
        for c_start, c_end in self.conflict_ranges[date_key]:
            if start_hour < c_end and end_hour > c_start:
                return True
        return False

    def can_book(self, room, date, start_hour, duration_minutes):
        """Check if a booking is allowed by all rules."""
        end_hour = start_hour + duration_minutes / 60
        duration_hours = duration_minutes / 60

        # Rule: Min duration
        if duration_minutes < MIN_BOOKING_MINUTES:
            return False, "Duration too short"

        # Rule: Max duration
        if duration_minutes > MAX_BOOKING_HOURS * 60:
            return False, "Duration too long"

        # Rule: Rolling quota (28 hours per week)
        remaining_hours = self.get_remaining_quota_hours()
        if duration_hours > remaining_hours:
            return False, f"Exceeds weekly quota ({remaining_hours:.1f}h remaining)"

        # Rule: Check conflict ranges
        if self.overlaps_conflict(date, start_hour, end_hour):
            return False, "Overlaps with your existing reservation"

        # Rule: Peak hours limit (per day)
        if date.weekday() < 5:  # Weekday
            overlap_start = max(start_hour, PEAK_START)
            overlap_end = min(end_hour, PEAK_END)
            if overlap_end > overlap_start:
                new_peak = (overlap_end - overlap_start) * 60
                day_peak_used = self.get_peak_used_for_day(date)
                if day_peak_used + new_peak > MAX_PEAK_HOURS * 60:
                    return False, f"Peak hours limit for {date.strftime('%Y-%m-%d')} ({day_peak_used:.0f}/{MAX_PEAK_HOURS * 60} min used)"

        # Rule: Same room gap (must have 60min gap before AND after existing bookings)
        # Check against bookings made during this session
        for b_room, b_date, b_start, b_end in self.bookings:
            if b_room == room and as_date(b_date) == as_date(date):
                # Forward gap: new booking starts after existing ends
                gap_after = (start_hour - b_end) * 60
                # Backward gap: new booking ends before existing starts
                gap_before = (b_start - end_hour) * 60

                if 0 <= gap_after < SAME_ROOM_GAP_MINUTES:
                    return False, f"Need {SAME_ROOM_GAP_MINUTES}min gap (only {gap_after:.0f}min after existing)"
                if 0 <= gap_before < SAME_ROOM_GAP_MINUTES:
                    return False, f"Need {SAME_ROOM_GAP_MINUTES}min gap (only {gap_before:.0f}min before existing)"

        # Also check against existing reservations from agenda (if room is known)
        date_key = date.strftime('%Y-%m-%d')
        if date_key in self.reservation_ranges:
            for r_start, r_end, r_room in self.reservation_ranges[date_key]:
                if r_room and r_room == room:
                    # Same room - check gaps
                    gap_after = (start_hour - r_end) * 60
                    gap_before = (r_start - end_hour) * 60

                    if 0 <= gap_after < SAME_ROOM_GAP_MINUTES:
                        return False, f"Need {SAME_ROOM_GAP_MINUTES}min gap from existing reservation (only {gap_after:.0f}min after)"
                    if 0 <= gap_before < SAME_ROOM_GAP_MINUTES:
                        return False, f"Need {SAME_ROOM_GAP_MINUTES}min gap from existing reservation (only {gap_before:.0f}min before)"

        return True, ""

    def get_same_room_blocked_ranges(self, date, room):
        """Get time ranges blocked for a specific room on a date.

        Returns list of (blocked_start, blocked_end) tuples representing times that
        cannot be booked due to:
        1. Existing reservation in the same room
        2. 60-minute same-room gap rule before/after reservations
        """
        date_key = date.strftime('%Y-%m-%d')
        blocked = []
        gap_hours = SAME_ROOM_GAP_MINUTES / 60

        # Check existing reservations from agenda
        if date_key in self.reservation_ranges:
            for r_start, r_end, r_room in self.reservation_ranges[date_key]:
                if r_room and r_room == room:
                    # Block the reservation time itself
                    blocked.append((r_start, r_end))
                    # Block the gap period after this reservation ends
                    blocked.append((r_end, r_end + gap_hours))
                    # Block the gap period before this reservation starts
                    blocked.append((r_start - gap_hours, r_start))

        # Check bookings made during this session
        for b_room, b_date, b_start, b_end in self.bookings:
            if b_room == room and as_date(b_date) == as_date(date):
                # Block the booking time itself
                blocked.append((b_start, b_end))
                blocked.append((b_end, b_end + gap_hours))
                blocked.append((b_start - gap_hours, b_start))

        return blocked

    def bookings_today(self, date):
        """Count bookings on a specific date."""
        target = as_date(date)
        return sum(1 for _, booking_date, _, _ in self.bookings if as_date(booking_date) == target)


def _normalize_practice_room_name(value, configured_rooms=None):
    """Resolve a renderer label to one exact live room name."""

    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if not text:
        return ""
    configured = tuple(configured_rooms or LIVE_CATALOG_ROOM_NAMES)
    matches = []
    for room in configured:
        try:
            validate_room_name(room, "configured room")
        except RoomPreferencesError:
            return ""
        pattern = re.compile(
            rf"(?<![A-Za-z0-9.]){re.escape(room)}(?![A-Za-z0-9.])",
            re.IGNORECASE,
        )
        if pattern.search(text):
            matches.append(room)
    if not matches:
        return ""
    longest = max(len(room) for room in matches)
    longest_matches = [room for room in matches if len(room) == longest]
    return longest_matches[0] if len(longest_matches) == 1 else ""


def get_practice_room_grid_snapshot(page, configured_rooms=None):
    """Return one normalized snapshot for either Asimut overview renderer."""

    configured_rooms = tuple(configured_rooms or LIVE_CATALOG_ROOM_NAMES)
    snapshot = page.evaluate(r"""(configuredRooms) => {
        const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
        const configuredRoomFromText = value => {
            const text = clean(value);
            const matches = configuredRooms.filter(room => {
                const escaped = room.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                return new RegExp(
                    `(^|[^A-Za-z0-9.])${escaped}(?=$|[^A-Za-z0-9.])`,
                    'i'
                ).test(text);
            });
            if (!matches.length) return null;
            const longest = Math.max(...matches.map(room => room.length));
            const longestMatches = matches.filter(room => room.length === longest);
            return longestMatches.length === 1 ? longestMatches[0] : null;
        };
        const number = value => {
            const parsed = Number(value);
            return Number.isFinite(parsed) ? parsed : null;
        };
        const visible = element => {
            if (!element) return false;
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            return rect.width > 0 && rect.height > 0
                && style.display !== 'none' && style.visibility !== 'hidden';
        };

        const svgRoot = Array.from(document.querySelectorAll('app-overview-svg'))
            .find(visible);
        if (svgRoot) {
            const rawLabels = Array.from(svgRoot.querySelectorAll('a[data-location-id]'));
            const svg = rawLabels[0]?.ownerSVGElement || svgRoot.querySelector('svg');
            if (!svg || !svg.getScreenCTM()) {
                return { renderer: 'svg', rooms: [] };
            }
            const seenLocations = new Set();
            const uniqueLabels = [];
            for (const label of rawLabels) {
                const id = label.getAttribute('data-location-id');
                if (!id || seenLocations.has(id)) continue;
                seenLocations.add(id);
                uniqueLabels.push({
                    label,
                    room: configuredRoomFromText(label.textContent),
                    rowIndex: uniqueLabels.length,
                });
            }
            const labels = uniqueLabels.filter(item => item.room);
            const blockers = Array.from(
                svgRoot.querySelectorAll('rect.closed-hours, rect.event-overlay')
            ).map(rect => {
                let box;
                try {
                    box = rect.getBBox();
                } catch (_) {
                    box = { x: 0, y: 0, width: 0, height: 0 };
                }
                return {
                    x: number(rect.getAttribute('x')) ?? number(box.x) ?? 0,
                    y: number(rect.getAttribute('y')) ?? number(box.y) ?? 0,
                    width: number(rect.getAttribute('width')) ?? number(box.width) ?? 0,
                    height: number(rect.getAttribute('height')) ?? number(box.height) ?? 0,
                    closed: rect.classList.contains('closed-hours'),
                };
            });
            const matrix = svg.getScreenCTM();
            const toScreen = (x, y) => {
                const point = svg.createSVGPoint();
                point.x = x;
                point.y = y;
                const screen = point.matrixTransform(matrix);
                return { x: screen.x, y: screen.y };
            };
            const rooms = labels.map(({label, room, rowIndex}) => {
                const rowTop = 30 * rowIndex;
                const rowBottom = rowTop + 30;
                const rowCenter = rowTop + 15;
                const origin = toScreen(0, rowCenter);
                const nextHour = toScreen(60, rowCenter);
                const blockedRanges = blockers
                    .filter(item => item.height > 0 && item.y < rowBottom
                        && item.y + item.height > rowTop)
                    .filter(item => item.width > 0)
                    .map(item => ({
                        startHour: 7 + item.x / 60,
                        endHour: 7 + (item.x + item.width) / 60,
                        closed: item.closed,
                    }));
                return {
                    room,
                    blockedRanges,
                    clickOriginX: origin.x,
                    clickPixelsPerHour: nextHour.x - origin.x,
                    clickY: origin.y,
                };
            });
            return { renderer: 'svg', rooms };
        }

        const rows = Array.from(document.querySelectorAll(
            "[data-cy='overview-location-row'], " +
            ".location-name-container .location-row"
        ));
        const locationDays = Array.from(document.querySelectorAll('.location-day'));
        const parseStylePct = (style, prop) => {
            const match = style.match(new RegExp(prop + ':\\s*calc\\(([\\d.]+)%'));
            return match ? parseFloat(match[1]) : null;
        };
        const pctToHour = pct => 7 + pct / 6.25;
        const rooms = rows.map(row => {
            const rowRect = row.getBoundingClientRect();
            const midY = (rowRect.top + rowRect.bottom) / 2;
            const day = locationDays.find(candidate => {
                const rect = candidate.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0
                    && Math.abs((rect.top + rect.bottom) / 2 - midY) < 30;
            });
            if (!day) return null;
            const dayRect = day.getBoundingClientRect();
            const eventRanges = Array.from(
                day.querySelectorAll('.location-event')
            ).map(element => {
                const style = element.getAttribute('style') || '';
                const left = parseStylePct(style, 'left') || 0;
                const width = parseStylePct(style, 'width') || 0;
                return { startHour: pctToHour(left), endHour: pctToHour(left + width), closed: false };
            });
            const closedRanges = Array.from(
                day.querySelectorAll('.location-closed')
            ).map(element => {
                const style = element.getAttribute('style') || '';
                const left = parseStylePct(style, 'left') || 0;
                const width = parseStylePct(style, 'width') || 0;
                return { startHour: pctToHour(left), endHour: pctToHour(left + width), closed: true };
            });
            return {
                room: configuredRoomFromText(row.textContent),
                blockedRanges: [...eventRanges, ...closedRanges],
                clickOriginX: dayRect.left,
                clickPixelsPerHour: dayRect.width / 16,
                clickY: midY,
            };
        }).filter(Boolean);
        return { renderer: 'legacy', rooms };
    }""", list(configured_rooms))
    if isinstance(snapshot, dict) and isinstance(snapshot.get("rooms"), list):
        normalized_rooms = []
        for item in snapshot["rooms"]:
            if not isinstance(item, dict):
                continue
            normalized = dict(item)
            normalized["room"] = _normalize_practice_room_name(
                item.get("room"),
                configured_rooms,
            )
            if normalized["room"]:
                normalized_rooms.append(normalized)
        snapshot = dict(snapshot)
        snapshot["rooms"] = normalized_rooms
    return snapshot


def _is_finite_grid_number(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and float("-inf") < float(value) < float("inf")
    )


def _available_slots_from_grid_snapshot(snapshot):
    """Calculate free intervals from a renderer-independent grid snapshot."""

    if not isinstance(snapshot, dict) or snapshot.get("renderer") not in {"legacy", "svg"}:
        raise RuntimeError("Practice-room overview returned an unknown grid renderer")
    rooms = snapshot.get("rooms")
    if not isinstance(rooms, list):
        raise RuntimeError("Practice-room overview returned invalid room data")

    result = []
    for room_data in rooms:
        if not isinstance(room_data, dict):
            continue
        room = room_data.get("room")
        origin_x = room_data.get("clickOriginX")
        pixels_per_hour = room_data.get("clickPixelsPerHour")
        click_y = room_data.get("clickY")
        if (
            not isinstance(room, str)
            or not room.strip()
            or not all(
                _is_finite_grid_number(value)
                for value in (origin_x, pixels_per_hour, click_y)
            )
            or pixels_per_hour <= 0
        ):
            continue

        blocked = []
        closing_hours = []
        for item in room_data.get("blockedRanges", []):
            if not isinstance(item, dict):
                continue
            start = item.get("startHour")
            end = item.get("endHour")
            if not (_is_finite_grid_number(start) and _is_finite_grid_number(end)):
                continue
            start = max(7.0, min(23.0, float(start)))
            end = max(7.0, min(23.0, float(end)))
            if end <= start:
                continue
            blocked.append((start, end))
            if item.get("closed") and start > 20:
                closing_hours.append(start)
        blocked.sort()

        day_end = min(closing_hours) if closing_hours else 22.25
        last_end = 7.2
        gaps = []
        for start, end in blocked:
            if start > last_end + 0.25:
                gap_start = int(last_end * 4 + 0.5) / 4
                gap_end = int(start * 4 + 0.5) / 4
                if gap_end > gap_start:
                    center = (last_end + start) / 2
                    gaps.append({
                        "startHour": gap_start,
                        "endHour": gap_end,
                        "clickX": origin_x + (center - 7) * pixels_per_hour,
                        "clickY": click_y,
                        "roomName": room.strip(),
                    })
            last_end = max(last_end, end)
        if last_end < day_end - 0.25:
            gap_start = int(last_end * 4 + 0.5) / 4
            gap_end = int(day_end * 4 + 0.5) / 4
            if gap_end > gap_start:
                center = (last_end + day_end) / 2
                gaps.append({
                    "startHour": gap_start,
                    "endHour": gap_end,
                    "clickX": origin_x + (center - 7) * pixels_per_hour,
                    "clickY": click_y,
                    "roomName": room.strip(),
                })
        if gaps:
            result.append({"room": room.strip(), "slots": gaps})
    return result


def get_available_slots(page):
    """Get free slots from either the legacy HTML or current SVG overview."""

    snapshot = get_practice_room_grid_snapshot(page)
    if not _practice_room_grid_has_exact_inventory(snapshot):
        raise RuntimeError(
            "Practice-room overview is missing one or more exact fresh-catalog rows"
        )
    return _available_slots_from_grid_snapshot(snapshot)


_CALENDAR_DATE_RE = re.compile(
    r"\b(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*,?\s*)?"
    r"([0-9]{1,2})\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"(?:\s+([0-9]{4}))?\b",
    re.IGNORECASE,
)


def get_visible_calendar_dates(page, *, reference_date=None):
    """Extract visible overview dates without trusting a generic page title."""

    reference_date = reference_date or datetime.now().date()
    visible_texts = page.evaluate("""() => {
        const candidates = new Set();
        const arrowIcons = Array.from(document.querySelectorAll('mat-icon')).filter(icon => {
            const text = (icon.textContent || '').trim();
            return text === 'chevron_left' || text === 'chevron_right';
        });
        for (const icon of arrowIcons) {
            let element = icon.parentElement;
            for (let depth = 0; element && depth < 5; depth++, element = element.parentElement) {
                candidates.add(element);
            }
        }
        for (const element of document.querySelectorAll(
            '[data-cy*="date"], [class*="date-selector"], [class*="calendar-date"], [class*="overview-date"]'
        )) {
            candidates.add(element);
        }

        const values = [];
        for (const element of candidates) {
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            if (!rect.width || !rect.height || style.display === 'none' || style.visibility === 'hidden') {
                continue;
            }
            const text = (element.innerText || element.textContent || '').trim();
            if (text && text.length <= 400) values.push(text);
        }
        return values;
    }""")

    found = set()
    for text_value in visible_texts:
        for match in _CALENDAR_DATE_RE.finditer(text_value):
            day_number = int(match.group(1))
            month_number = datetime.strptime(match.group(2).title(), "%B").month
            years = [int(match.group(3))] if match.group(3) else [
                reference_date.year - 1,
                reference_date.year,
                reference_date.year + 1,
            ]
            for year in years:
                try:
                    candidate = datetime(year, month_number, day_number).date()
                except ValueError:
                    continue
                if match.group(3) or abs((candidate - reference_date).days) <= 14:
                    found.add(candidate)
    return found


def assert_calendar_date(page, expected_date, *, timeout_ms=6000):
    """Wait until the overview visibly proves it is showing ``expected_date``."""

    expected_date = as_date(expected_date)
    deadline = time.monotonic() + timeout_ms / 1000
    last_seen = set()
    while time.monotonic() < deadline:
        last_seen = get_visible_calendar_dates(page, reference_date=expected_date)
        if expected_date in last_seen:
            return expected_date
        page.wait_for_timeout(150)
    seen_text = ", ".join(sorted(value.isoformat() for value in last_seen)) or "none"
    raise RuntimeError(
        f"Calendar date verification failed: expected {expected_date.isoformat()}, "
        f"visible dates were {seen_text}"
    )


def _is_exact_practice_room_overview_url(url):
    """Return whether ``url`` is this run's exact fresh-catalog overview."""

    return isinstance(url, str) and url == ASIMUT_OVERVIEW_URL


def _expected_live_overview_room_names():
    """Return the full fresh room inventory when a run policy is installed."""

    policy = ACTIVE_ROOM_POLICY
    if not isinstance(policy, LiveRoomPolicy):
        return ()
    return tuple(policy.all_room_names)


def _practice_room_grid_has_exact_inventory(snapshot, expected_rooms=None):
    """Return whether a snapshot contains every expected room exactly once.

    Asimut is free to render rows in an order different from the explicit
    ``locationIds`` query.  Identity therefore uses exact set equality while
    retaining duplicate detection through the row count.
    """

    expected = tuple(
        _expected_live_overview_room_names()
        if expected_rooms is None
        else expected_rooms
    )
    if not expected:
        return True
    if len(set(expected)) != len(expected):
        return False
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("rooms"), list):
        return False
    observed = [
        item.get("room") if isinstance(item, dict) else None
        for item in snapshot["rooms"]
    ]
    return (
        len(observed) == len(expected)
        and all(isinstance(room, str) and room for room in observed)
        and len(set(observed)) == len(observed)
        and set(observed) == set(expected)
    )


_SVG_POPULATED_GRID_STABLE_POLLS = 2
_SVG_EMPTY_GRID_STABLE_POLLS = 20


def _practice_room_grid_snapshot_is_complete(snapshot, *, allow_empty=False):
    """Return whether a grid snapshot has a complete, usable structure.

    A populated SVG grid is complete once event overlays exist anywhere in the
    structurally valid grid. A genuinely empty day has no event overlays, so it
    is admitted only by ``wait_for_practice_room_grid`` after a much longer
    unchanged quiet period. This avoids requiring every room to contain an
    event while still rejecting Asimut's transient labels-before-overlays frame.
    """

    if not isinstance(snapshot, dict):
        return False
    rooms = snapshot.get("rooms")
    if not isinstance(rooms, list) or not rooms:
        return False
    if snapshot.get("renderer") == "legacy":
        return True
    if snapshot.get("renderer") != "svg":
        return False
    if not all(
        isinstance(room, dict) and isinstance(room.get("blockedRanges"), list)
        for room in rooms
    ):
        return False
    has_event_overlay = any(
        isinstance(item, dict) and item.get("closed") is False
        for room in rooms
        for item in room["blockedRanges"]
    )
    return has_event_overlay or allow_empty


def _practice_room_grid_content_signature(snapshot):
    """Return a stable SVG content signature, excluding viewport coordinates."""

    if (
        not isinstance(snapshot, dict)
        or snapshot.get("renderer") not in {"legacy", "svg"}
        or not isinstance(snapshot.get("rooms"), list)
    ):
        return None
    signature_rooms = []
    seen_rooms = set()
    for room in snapshot["rooms"]:
        if not isinstance(room, dict) or not isinstance(room.get("blockedRanges"), list):
            return None
        room_name = room.get("room")
        if (
            not isinstance(room_name, str)
            or not room_name
            or room_name != room_name.strip()
            or room_name in seen_rooms
        ):
            return None
        seen_rooms.add(room_name)
        ranges = []
        for item in room["blockedRanges"]:
            if (
                not isinstance(item, dict)
                or not _is_finite_grid_number(item.get("startHour"))
                or not _is_finite_grid_number(item.get("endHour"))
                or float(item["endHour"]) <= float(item["startHour"])
                or not isinstance(item.get("closed"), bool)
            ):
                return None
            ranges.append(
                (
                    float(item["startHour"]),
                    float(item["endHour"]),
                    item["closed"],
                )
            )
        signature_rooms.append((room_name, tuple(ranges)))
    return snapshot.get("renderer"), tuple(signature_rooms)


def wait_for_practice_room_grid(page, expected_date, *, timeout_ms=20000):
    """Wait for a stable legacy or SVG practice-room overview."""

    expected_date = as_date(expected_date)
    deadline = time.monotonic() + timeout_ms / 1000
    last_snapshot = None
    last_error = None
    last_signature = None
    stable_polls = 0
    expected_rooms = _expected_live_overview_room_names()
    while time.monotonic() < deadline:
        if not _is_exact_practice_room_overview_url(page.url):
            raise RuntimeError(
                "Practice-room grid verification requires the exact "
                "fresh-catalog all-locations overview route"
            )
        if not page_is_authenticated(page):
            raise RuntimeError(
                "Asimut session is no longer authenticated while loading the practice-room grid"
            )
        try:
            last_snapshot = get_practice_room_grid_snapshot(page)
            rooms = (
                last_snapshot.get("rooms", [])
                if isinstance(last_snapshot, dict)
                and isinstance(last_snapshot.get("rooms"), list)
                else []
            )
            usable_rooms = [
                room
                for room in rooms
                if isinstance(room, dict)
                and _is_finite_grid_number(room.get("clickOriginX"))
                and _is_finite_grid_number(room.get("clickPixelsPerHour"))
                and room.get("clickPixelsPerHour", 0) > 0
                and _is_finite_grid_number(room.get("clickY"))
            ]
            structurally_usable = (
                isinstance(last_snapshot, dict)
                and last_snapshot.get("renderer") in {"legacy", "svg"}
                and len(usable_rooms) == len(rooms)
                and bool(rooms)
                and _practice_room_grid_has_exact_inventory(
                    last_snapshot,
                    expected_rooms,
                )
            )
            signature = (
                _practice_room_grid_content_signature(last_snapshot)
                if structurally_usable
                else None
            )
            if signature is not None and signature == last_signature:
                stable_polls += 1
            elif signature is not None:
                last_signature = signature
                stable_polls = 1
            else:
                last_signature = None
                stable_polls = 0

            renderer = last_snapshot.get("renderer") if structurally_usable else None
            if renderer == "legacy":
                required_stable_polls = 1
                complete = _practice_room_grid_snapshot_is_complete(last_snapshot)
            elif renderer == "svg":
                has_loaded_event_overlay = _practice_room_grid_snapshot_is_complete(
                    last_snapshot
                )
                required_stable_polls = (
                    _SVG_POPULATED_GRID_STABLE_POLLS
                    if has_loaded_event_overlay
                    else _SVG_EMPTY_GRID_STABLE_POLLS
                )
                complete = _practice_room_grid_snapshot_is_complete(
                    last_snapshot,
                    allow_empty=stable_polls >= required_stable_polls,
                )
            else:
                required_stable_polls = 1
                complete = False

            if complete and stable_polls >= required_stable_polls:
                remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
                assert_calendar_date(
                    page,
                    expected_date,
                    timeout_ms=min(6000, remaining_ms),
                )
                return last_snapshot
            last_error = None
        except RuntimeError:
            raise
        except Exception as exc:
            last_error = exc
        page.wait_for_timeout(250)

    renderer = (
        last_snapshot.get("renderer", "none")
        if isinstance(last_snapshot, dict)
        else "none"
    )
    room_count = (
        len(last_snapshot.get("rooms", []))
        if isinstance(last_snapshot, dict)
        and isinstance(last_snapshot.get("rooms"), list)
        else 0
    )
    observed_rooms = {
        item.get("room")
        for item in (
            last_snapshot.get("rooms", [])
            if isinstance(last_snapshot, dict)
            and isinstance(last_snapshot.get("rooms"), list)
            else []
        )
        if isinstance(item, dict) and isinstance(item.get("room"), str)
    }
    missing_rooms = [room for room in expected_rooms if room not in observed_rooms]
    inventory_detail = (
        "; missing expected live rooms: " + ", ".join(missing_rooms)
        if missing_rooms
        else ""
    )
    detail = f"; last probe error: {last_error}" if last_error else ""
    raise RuntimeError(
        f"Practice-room grid did not become usable within {timeout_ms}ms "
        f"(renderer={renderer}, rooms={room_count}){inventory_detail}{detail}"
    )


def open_practice_room_overview(page, expected_date, *, attempts=2):
    """Open and verify the fresh-catalog grid, retrying one incomplete SPA load."""

    last_error = None
    for attempt in range(1, attempts + 1):
        safe_goto(page, ASIMUT_OVERVIEW_URL)
        try:
            return wait_for_practice_room_grid(page, expected_date)
        except RuntimeError as exc:
            last_error = exc
            if attempt >= attempts:
                break
            print(
                f"  Practice-room grid load {attempt}/{attempts} was incomplete; "
                "retrying the canonical all-locations overview..."
            )
    raise RuntimeError(
        "Could not load a verified all-locations practice-room grid after "
        f"{attempts} attempts: "
        f"{last_error}"
    ) from last_error


def refresh_practice_room_overview(page, expected_date, *, base_date=None):
    """Reload the overview, then restore and prove its selected calendar date.

    Asimut keeps the selected overview day only in SPA state. Reloading the
    otherwise canonical overview URL therefore returns to today, even when the
    caller was viewing a later date. Treat today as the only trusted post-
    reload position, prove that complete grid first, then walk back to the
    requested live-window offset through the ordinary verified navigator.
    """

    expected_date = as_date(expected_date)
    today = as_date(base_date) if base_date is not None else datetime.now().date()
    days_ahead = (expected_date - today).days
    if days_ahead < 0:
        raise RuntimeError(
            "Cannot restore a practice-room overview date before the run's "
            f"base date ({expected_date} before {today})"
        )

    safe_reload(page)
    today_snapshot = wait_for_practice_room_grid(page, today)
    if days_ahead == 0:
        return today_snapshot

    navigate_to_day(page, days_ahead, 0, base_date=today)
    return wait_for_practice_room_grid(page, expected_date)


def get_practice_room_names(page):
    """Return visible room labels from either overview renderer."""

    snapshot = get_practice_room_grid_snapshot(page)
    if not isinstance(snapshot, dict) or snapshot.get("renderer") not in {"legacy", "svg"}:
        raise RuntimeError("Practice-room overview returned an unknown grid renderer")
    if not _practice_room_grid_has_exact_inventory(snapshot):
        raise RuntimeError(
            "Practice-room overview is missing one or more exact fresh-catalog rows"
        )
    return {
        item["room"].strip()
        for item in snapshot.get("rooms", [])
        if isinstance(item, dict)
        and isinstance(item.get("room"), str)
        and item["room"].strip()
    }


def get_room_slot_coordinates(page, room, start_hour, end_hour):
    """Return fresh screen coordinates for a room/time in either renderer."""

    room = _normalize_practice_room_name(room)
    if not room:
        return None
    center_hour = (float(start_hour) + float(end_hour)) / 2
    return page.evaluate(r"""([roomName, centerHour, configuredRooms]) => {
        const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
        const configuredRoomFromText = value => {
            const text = clean(value);
            const matches = configuredRooms.filter(candidate => {
                const escaped = candidate.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                return new RegExp(
                    `(^|[^A-Za-z0-9.])${escaped}(?=$|[^A-Za-z0-9.])`,
                    'i'
                ).test(text);
            });
            if (!matches.length) return null;
            const longest = Math.max(...matches.map(candidate => candidate.length));
            const longestMatches = matches.filter(
                candidate => candidate.length === longest
            );
            return longestMatches.length === 1 ? longestMatches[0] : null;
        };
        const svgRoot = Array.from(document.querySelectorAll('app-overview-svg'))
            .find(root => {
                const rect = root.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            });
        if (svgRoot) {
            const labels = Array.from(svgRoot.querySelectorAll('a[data-location-id]'));
            const label = labels.find(
                item => configuredRoomFromText(item.textContent) === roomName
            );
            const svg = label?.ownerSVGElement || svgRoot.querySelector('svg');
            if (!label || !svg) return null;
            label.scrollIntoView({ behavior: 'instant', block: 'center' });
            void label.getBoundingClientRect();
            const uniqueLabels = [];
            const seen = new Set();
            for (const item of labels) {
                const id = item.getAttribute('data-location-id');
                if (!id || seen.has(id)) continue;
                seen.add(id);
                uniqueLabels.push(item);
            }
            const rowIndex = uniqueLabels.indexOf(label);
            const matrix = svg.getScreenCTM();
            if (rowIndex < 0 || !matrix) return null;
            const point = svg.createSVGPoint();
            point.x = 60 * (centerHour - 7);
            point.y = 30 * rowIndex + 15;
            const screen = point.matrixTransform(matrix);
            return { x: screen.x, y: screen.y, renderer: 'svg' };
        }

        const rows = Array.from(document.querySelectorAll(
            "[data-cy='overview-location-row'], " +
            ".location-name-container .location-row"
        ));
        const row = rows.find(
            item => configuredRoomFromText(item.textContent) === roomName
        );
        if (!row) return null;
        row.scrollIntoView({ behavior: 'instant', block: 'center' });
        void row.getBoundingClientRect();
        const rowRect = row.getBoundingClientRect();
        const rowMidY = (rowRect.top + rowRect.bottom) / 2;
        const day = Array.from(document.querySelectorAll('.location-day')).find(item => {
            const rect = item.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0
                && Math.abs((rect.top + rect.bottom) / 2 - rowMidY) < 30;
        });
        if (!day) return null;
        const dayRect = day.getBoundingClientRect();
        return {
            x: dayRect.left + ((centerHour - 7) / 16) * dayRect.width,
            y: rowMidY,
            renderer: 'legacy',
        };
    }""", [room, center_hour, LIVE_CATALOG_ROOM_NAMES])


def try_book_slot(
    page,
    slot,
    target_date,
    tracker,
    days_ahead,
    remaining_daily_hours=None,
    max_action_minutes=None,
):
    """Attempt one slot and return its exact verified receipt, or False."""
    room = slot['room']
    start_hour = slot['start_hour']
    slot_duration = (slot['end_hour'] - start_hour) * 60

    # Calculate maximum bookable duration based on horizon constraint
    # At the horizon edge, you can only book time that has "passed" the edge
    # E.g., at 9:30 AM, a 9:00 AM slot has 30 min past the horizon, so you can book 9:00-9:30
    horizon_minutes = room_horizon_minutes(room)
    now = datetime.now()

    # Calculate when this slot became available
    if hasattr(target_date, 'date'):
        target_date_obj = target_date.date()
    else:
        target_date_obj = target_date
    slot_datetime = datetime.combine(target_date_obj, datetime.min.time())
    slot_datetime = slot_datetime.replace(
        hour=int(start_hour),
        minute=int((start_hour % 1) * 60)
    )
    available_from = slot_datetime - timedelta(minutes=horizon_minutes)

    # How much time has passed since the slot became available?
    time_since_available_mins = (now - available_from).total_seconds() / 60

    # The bookable duration is limited by:
    # 1. The slot's actual duration
    # 2. The max booking duration (2 hours)
    # 3. The time that has passed since horizon edge (for edge bookings)
    horizon_limited_duration = time_since_available_mins

    # Round down to nearest 15-minute interval for cleaner bookings
    horizon_limited_duration = (int(horizon_limited_duration) // 15) * 15

    max_possible_duration = _bounded_create_duration_minutes(
        slot_duration,
        remaining_daily_hours,
        tracker.get_remaining_quota_hours(),
        max_action_minutes,
    )

    # Evaluate the test ceiling inside the mutation path so a stale or overly
    # long calendar segment cannot escape the bounded live-test scope.
    requested_duration = min(max_possible_duration, horizon_limited_duration)
    booking_duration = _bounded_create_duration_minutes(
        requested_duration,
        remaining_daily_hours,
        tracker.get_remaining_quota_hours(),
        max_action_minutes,
    )

    # The College minimum and the user's chosen contiguous block minimum are
    # separate.  A larger preference waits for that complete block to unlock.
    if booking_duration < MINIMUM_BLOCK_MINUTES:
        mins_until_bookable = max(
            0,
            MINIMUM_BLOCK_MINUTES - horizon_limited_duration,
        )
        start_h = int(start_hour)
        start_m = int((start_hour % 1) * 60)
        book_start = f"{start_h:02d}:{start_m:02d}"
        print(
            f"  Skipping {room} {book_start}: Only "
            f"{horizon_limited_duration:.0f}min past live horizon "
            f"(need {MINIMUM_BLOCK_MINUTES}min, wait {mins_until_bookable:.0f}min)"
        )
        # Extra debug for horizon issues
        print(f"    [DEBUG] Horizon calc: now={now.strftime('%H:%M:%S')}, slot={slot_datetime.strftime('%Y-%m-%d %H:%M')}, "
              f"available_from={available_from.strftime('%Y-%m-%d %H:%M')}, mins_since={time_since_available_mins:.1f}")
        return False

    end_hour = start_hour + booking_duration / 60

    # Format times
    start_h = int(start_hour)
    start_m = int((start_hour % 1) * 60)
    end_h = int(end_hour)
    end_m = int((end_hour % 1) * 60)
    book_start = f"{start_h:02d}:{start_m:02d}"
    book_end = f"{end_h:02d}:{end_m:02d}"

    # Check rules
    can_book, reason = tracker.can_book(room, target_date, start_hour, booking_duration)
    if not can_book:
        print(f"  Skipping {room} {book_start}: {reason}")
        return False

    # Note: We no longer need is_room_available_to_book() here since we already
    # calculated horizon availability above. But we keep it for safety.
    is_available, horizon_reason = is_room_available_to_book(room, target_date, start_hour)
    if not is_available:
        print(f"  Skipping {room} {book_start}: {horizon_reason}")
        return False

    # Log if this is a horizon-limited booking
    is_horizon_limited = booking_duration < max_possible_duration
    if is_horizon_limited:
        target_end_h = int(start_hour + max_possible_duration / 60)
        target_end_m = int((start_hour + max_possible_duration / 60) % 1 * 60)
        print(f"\n{'='*60}")
        print(f"Trying: {room} at {book_start}-{book_end} (HORIZON LIMITED)")
        print(f"  Will extend later: target {book_start}-{target_end_h:02d}:{target_end_m:02d}")
        print(f"{'='*60}")
    else:
        print(f"\n{'='*60}")
        print(f"Trying: {room} at {book_start}-{book_end}")
        print(f"{'='*60}")

    # Step 1: Click on empty slot
    # Find the room row and scroll it into view, then get fresh coordinates
    room_name = slot['room']
    # Note: end_hour was already calculated above with horizon limits applied

    target_center = (start_hour + end_hour) / 2
    print(
        f"  [DEBUG] Click calculation: slot={start_hour:.2f}-{end_hour:.2f}, "
        f"center={target_center:.2f}"
    )

    # Find the room row and get fresh click coordinates
    coords = get_room_slot_coordinates(page, room_name, start_hour, end_hour)

    if not coords:
        print(f"  [DEBUG] Could not find room '{room_name}' in grid")
        print(f"  Could not find room '{room_name}' in grid - skipping")
        return False

    page.wait_for_timeout(300)  # Brief wait after scroll

    # Log viewport info
    viewport = page.evaluate("() => ({ width: window.innerWidth, height: window.innerHeight, scrollY: window.scrollY })")
    print(f"  [DEBUG] Viewport: {viewport['width']}x{viewport['height']}, scrollY={viewport['scrollY']:.0f}")
    print(
        f"  [DEBUG] Click target: x={coords['x']:.0f}, y={coords['y']:.0f} "
        f"({coords.get('renderer', 'unknown')}, target time: {start_hour:.2f}-{end_hour:.2f})"
    )

    # Check if click is within viewport
    if coords['y'] < 0 or coords['y'] > viewport['height']:
        print(f"  [DEBUG] WARNING: Click Y={coords['y']:.0f} is outside viewport (0-{viewport['height']})")

    # Try clicking at different positions if needed - start with center, then try offset positions
    click_positions = [
        (coords['x'], coords['y'], "center"),
        (coords['x'] + 15, coords['y'] + 5, "offset right-bottom"),
        (coords['x'] - 15, coords['y'] - 5, "offset left-top"),
    ]

    popup_found = False
    current_url = page.url

    for click_x, click_y, pos_desc in click_positions:
        print(f"  Clicking at ({click_x:.0f}, {click_y:.0f}) for {room_name} ({pos_desc})...")
        page.mouse.click(click_x, click_y)

        # Wait for popup or navigation with retry
        for wait_attempt in range(5):
            page.wait_for_timeout(1000)  # Wait 1s between checks

            # Check if clicking directly navigated to booking form (URL contains /event?eventId=0)
            current_url = page.url
            if '/event?eventId=0' in current_url:
                print(f"  [DEBUG] Click navigated directly to booking form")
                popup_found = True
                break

            # Check if popup with booking option appeared
            student_btn = page.locator("mat-list-item:has-text('Student booking provisional')").first
            if student_btn.count() > 0 and student_btn.is_visible():
                popup_found = True
                break

            # Check for overlay WITH "Student booking provisional" text
            overlays = page.locator(".cdk-overlay-pane, .mat-menu-panel, mat-bottom-sheet-container").all()
            for overlay in overlays:
                if overlay.is_visible():
                    text = overlay.text_content() or ""
                    if 'Student booking provisional' in text:
                        popup_found = True
                        break
            if popup_found:
                break

        if popup_found:
            break

        # If we got a tooltip but not the booking menu, dismiss it and try another position
        overlays = page.locator(".cdk-overlay-pane, .mat-menu-panel, mat-bottom-sheet-container").all()
        visible_overlays = [o for o in overlays if o.is_visible()]
        if visible_overlays:
            # Got a tooltip, not the booking menu - dismiss and try again
            tooltip_text = visible_overlays[0].text_content() or ""
            print(f"  [DEBUG] Got tooltip instead of menu: {tooltip_text[:80]}...")
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        else:
            print(f"  [DEBUG] No popup at {pos_desc} position")

    if '/event?eventId=0' in current_url:
        print(f"  [DEBUG] Click navigated directly to booking form")
        # Already on booking form, skip popup handling and go to Step 3
        pass  # Continue to Step 3 below
    elif not popup_found:
        # Check if we accidentally clicked on an existing reservation
        # The unique indicator is "Events where I participate" or "Choose which events to display"
        events_participate = page.locator("text=Events where I participate").first
        choose_events = page.locator("text=Choose which events to display").first

        ep_visible = events_participate.count() > 0 and events_participate.is_visible()
        ce_visible = choose_events.count() > 0 and choose_events.is_visible()

        if ep_visible or ce_visible:
            print(f"  [DEBUG] Clicked on existing reservation (events_participate={ep_visible}, choose_events={ce_visible})")
            print("  Clicked on existing reservation - clicking back button...")

            # Click the back button with aria-label="Back to previous page"
            back_btn = page.locator("button[aria-label='Back to previous page']").first
            if back_btn.count() > 0 and back_btn.is_visible():
                back_btn.click()
                page.wait_for_timeout(2000)
                print("    Clicked back button")
            else:
                # Fallback: use browser back
                page.go_back()
                page.wait_for_timeout(2000)
                print("    Used browser back")
            return False

        # No booking popup found after trying all positions
        print("  No booking popup found after trying multiple click positions - skipping")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        return False
    else:
        # Step 2: Click "Student booking provisional" - we know the popup is visible
        print(f"  [DEBUG] Looking for 'Student booking provisional' button...")
        student_btn = page.locator("mat-list-item:has-text('Student booking provisional')").first
        if student_btn.count() > 0 and student_btn.is_visible():
            print("  Found 'Student booking provisional' button")
            student_btn.click()
            page.wait_for_timeout(3000)
        else:
            # Try alternative locators
            alt_btn = page.locator("text=Student booking provisional").first
            if alt_btn.count() > 0 and alt_btn.is_visible():
                print("  Found alt 'Student booking provisional' text")
                alt_btn.click()
                page.wait_for_timeout(3000)
            else:
                # Check overlays for the button
                overlays = page.locator(".cdk-overlay-pane, .mat-menu-panel, mat-bottom-sheet-container").all()
                found_in_overlay = False
                for overlay in overlays:
                    if overlay.is_visible():
                        text = overlay.text_content() or ""
                        if 'Student booking provisional' in text:
                            overlay_btn = overlay.locator("text=Student booking provisional").first
                            if overlay_btn.count() > 0:
                                print("  Found 'Student booking provisional' in overlay, clicking...")
                                overlay_btn.click()
                                page.wait_for_timeout(3000)
                                found_in_overlay = True
                                break

                if not found_in_overlay:
                    print("  [DEBUG] Could not find 'Student booking provisional' button despite popup_found=True")
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
                    return False

    if not wait_for_new_booking_form(page, timeout_ms=10000):
        print(
            "  Refusing to save: the exact new-booking form and its time "
            f"controls did not become ready (URL: {page.url})"
        )
        go_back(page, days_ahead)
        return False

    # Step 3: Set times
    print(f"  [DEBUG] Setting times: {book_start} to {book_end}")
    start_input = page.locator(
        "#startDate, input[aria-label='Start time'], "
        "input[data-cy='time-range-start-time']"
    ).first
    end_input = page.locator(
        "#endDate, input[aria-label='End time'], "
        "input[data-cy='time-range-end-time']"
    ).first

    if start_input.count() > 0 and end_input.count() > 0:
        print(f"  [DEBUG] Found time inputs, filling start time...")
        start_input.click()
        start_input.fill(book_start)
        page.wait_for_timeout(300)

        print(f"  [DEBUG] Filling end time...")
        end_input.click()
        end_input.fill(book_end)
        page.wait_for_timeout(300)

        # Read back the values to verify
        actual_start = start_input.input_value()
        actual_end = end_input.input_value()
        print(f"  [DEBUG] Verified times: start={actual_start}, end={actual_end}")
        if not booking_times_match(book_start, book_end, actual_start, actual_end):
            print(
                f"  Error: Asimut did not retain the requested times "
                f"({actual_start}-{actual_end} != {book_start}-{book_end}); cancelling"
            )
            go_back(page, days_ahead)
            return False
        if not booking_summary_matches(
            page_booking_snapshot(page),
            room,
            target_date,
            book_start,
            book_end,
        ):
            print("  Error: booking form room/date/time does not match the selected slot")
            go_back(page, days_ahead)
            return False

        # Dismiss dropdown
        save_btn = page.locator("button:has-text('Save')").first
        if save_btn.count() > 0:
            box = save_btn.bounding_box()
            if box:
                page.mouse.click(box['x'] + 10, box['y'] - 20)
        page.wait_for_timeout(500)
    else:
        print(f"  Error: Time inputs not found; refusing to save an unverified booking")
        go_back(page, days_ahead)
        return False

    # Step 4: Check for errors - look for ANY conflict indicator
    # First, search for any reservation time patterns on the page (your existing bookings)
    print(f"  [DEBUG] Checking for errors/conflicts on booking form...")
    all_reservation_times = page.locator("text=/\\d{2}:\\d{2}\\s*-\\s*\\d{2}:\\d{2}/").all()
    print(f"  [DEBUG] Found {len(all_reservation_times)} time patterns on page")
    reservations_found = 0
    for res in all_reservation_times:
        try:
            text = res.text_content() or ""
            # Look for patterns like "09:30 - 11:00 Reservation" or similar
            if "Reservation" in text or "Student" in text:
                match = re.search(r'(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2})', text)
                if match:
                    c_start = int(match.group(1)) + int(match.group(2)) / 60
                    c_end = int(match.group(3)) + int(match.group(4)) / 60
                    # Add to conflicts if not already known
                    if not tracker.overlaps_conflict(target_date, c_start, c_end):
                        tracker.add_conflict(target_date, c_start, c_end)
                        print(f"  Found existing reservation: {match.group(1)}:{match.group(2)}-{match.group(3)}:{match.group(4)}")
                        reservations_found += 1
        except:
            pass
    if reservations_found > 0:
        print(f"  [DEBUG] Added {reservations_found} new conflict(s) from booking form")

    error_msg = page.locator("text=You are not allowed").first
    if error_msg.count() > 0 and error_msg.is_visible():
        # Get the full error text for debugging
        full_error = error_msg.text_content() or "Unknown error"
        print(f"  Error: Not allowed to book")
        print(f"  [DEBUG] Error message: {full_error[:100]}")

        # Extract conflict time
        conflict_text = page.locator("text=/\\d{2}:\\d{2}\\s*-\\s*\\d{2}:\\d{2}.*Reservation/").first
        if conflict_text.count() > 0:
            text = conflict_text.text_content() or ""
            print(f"  [DEBUG] Found conflict: {text[:80]}")
            match = re.search(r'(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2})', text)
            if match:
                c_start = int(match.group(1)) + int(match.group(2)) / 60
                c_end = int(match.group(3)) + int(match.group(4)) / 60
                tracker.add_conflict(target_date, c_start, c_end)

        go_back(page, days_ahead)
        return False

    # Check for conflicts
    conflict_msg = page.locator("text=conflicting events").first
    resolve_msg = page.locator("text=resolve the conflicts").first
    clash_msg = page.locator("text=booking clashes").first

    has_conflict = (conflict_msg.count() > 0 and conflict_msg.is_visible()) or \
                   (resolve_msg.count() > 0 and resolve_msg.is_visible()) or \
                   (clash_msg.count() > 0 and clash_msg.is_visible())

    if has_conflict:
        print("  Error: Conflicts with existing booking")

        # Extract conflict time from the booking list
        conflict_text = page.locator("text=/\\d{2}:\\d{2}\\s*-\\s*\\d{2}:\\d{2}.*Reservation/").first
        if conflict_text.count() > 0:
            text = conflict_text.text_content() or ""
            match = re.search(r'(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2})', text)
            if match:
                c_start = int(match.group(1)) + int(match.group(2)) / 60
                c_end = int(match.group(3)) + int(match.group(4)) / 60
                tracker.add_conflict(target_date, c_start, c_end)

        go_back(page, days_ahead)
        return False

    # Re-check if our slot now overlaps with conflicts we just discovered
    if tracker.overlaps_conflict(target_date, start_hour, end_hour):
        print(f"  Skipping - overlaps with newly discovered conflict")
        go_back(page, days_ahead)
        return False

    # Final conflict check before save - look for yellow/red warning areas
    warning_elements = page.locator(".warning, .error, [class*='conflict'], [class*='clash']").all()
    for warn in warning_elements:
        if warn.is_visible():
            text = warn.text_content() or ""
            if "conflict" in text.lower() or "clash" in text.lower() or "not allowed" in text.lower():
                print(f"  Warning found: {text[:50]}...")
                go_back(page, days_ahead)
                return False

    # Step 5: Click Save
    print(f"  [DEBUG] Looking for Save button...")
    save_btn = page.locator("button:has-text('Save')").first
    if save_btn.count() > 0 and save_btn.is_visible():
        # Check if enabled - look for disabled icons (remove, block, close, cancel, etc.)
        save_icon = save_btn.locator("mat-icon").first
        if save_icon.count() > 0:
            icon_text = save_icon.text_content() or ""
            print(f"  [DEBUG] Save button icon: '{icon_text}'")
            # Check for any "disabled" style icon (remove_circle, block, close, cancel, etc.)
            disabled_icons = ["remove", "block", "close", "cancel", "error", "warning"]
            if any(disabled in icon_text.lower() for disabled in disabled_icons):
                print(f"  Save disabled (icon: {icon_text})")

                # Try to capture the actual error message from the booking form
                error_reason = "Unknown reason"

                # Look for common Asimut error messages
                error_selectors = [
                    "text=You are not allowed",
                    "text=not allowed to book",
                    "text=maximum number",
                    "text=quota",
                    "text=peak hours",
                    "text=already booked",
                    "text=conflict",
                    "text=overlaps",
                    ".mat-error",
                    ".error-message",
                    "[class*='error']",
                    "[class*='warning']",
                    "mat-hint.mat-error",
                ]

                for selector in error_selectors:
                    try:
                        error_el = page.locator(selector).first
                        if error_el.count() > 0 and error_el.is_visible():
                            error_text = error_el.text_content() or ""
                            if error_text.strip() and len(error_text) > 5:
                                error_reason = error_text.strip()[:100]
                                break
                    except:
                        pass

                # Also check the page for any visible text containing error keywords
                if error_reason == "Unknown reason":
                    try:
                        page_text = page.locator("body").text_content() or ""
                        # Look for quota-related messages
                        if "maximum" in page_text.lower() and ("booking" in page_text.lower() or "hour" in page_text.lower()):
                            # Extract the relevant sentence
                            for line in page_text.split('\n'):
                                if "maximum" in line.lower() or "quota" in line.lower() or "limit" in line.lower():
                                    error_reason = line.strip()[:100]
                                    break
                    except:
                        pass

                print(f"  [DEBUG] Rejection reason: {error_reason}")
                go_back(page, days_ahead)
                return False

        # Get button state before clicking
        btn_box = save_btn.bounding_box()
        print(f"  [DEBUG] Save button at: x={btn_box['x']:.0f}, y={btn_box['y']:.0f}" if btn_box else "  [DEBUG] Save button box: None")

        try:
            receipt = record_pending_create(
                room=room,
                booking_date=as_date(target_date).isoformat(),
                start=book_start,
                end=book_end,
            )
        except MutationReceiptError as exc:
            raise BookingVerificationError(
                f"Could not create a crash-recovery receipt before Save: {exc}"
            ) from exc

        print(f"  Clicking Save (receipt {receipt['id']})...")
        try:
            save_btn.click(no_wait_after=True, timeout=5000)
        except Exception as exc:
            raise BookingVerificationError(
                f"Save click outcome is uncertain (receipt {receipt['id']}): {exc}"
            ) from exc

        if not wait_for_created_booking_outcome(
            page,
            receipt,
            room,
            target_date,
            book_start,
            book_end,
        ):
            go_back(page, days_ahead)
            return False

        try:
            tracker.add_booking(room, target_date, start_hour, end_hour)
            print(f"  SUCCESS: Booked {room} {book_start}-{book_end}")

            max_possible_end = _extension_target_end_hour(
                start_hour,
                end_hour,
                max_possible_duration,
            )
            if max_possible_end is not None:
                try:
                    save_extendable_booking(
                        room,
                        target_date,
                        start_hour,
                        end_hour,
                        max_possible_end,
                        event_url=page.url,
                    )
                except SettingsError as exc:
                    # The remote reservation and receipt are already verified. Agenda
                    # scanning on the next run remains authoritative.
                    print(
                        f"  WARNING: Booking is confirmed, but extension tracking "
                        f"could not be saved: {exc}"
                    )
            return {
                "receipt_id": receipt["id"],
                "event_url": page.url,
                "room": room,
                "date": as_date(target_date).isoformat(),
                "start": book_start,
                "end": book_end,
                "duration_minutes": booking_duration,
            }
        except BookingVerificationError:
            raise
        except Exception as exc:
            raise BookingVerificationError(
                f"Booking Save was verified (receipt {receipt['id']}), but local "
                f"post-Save accounting failed: {exc}. No further mutations will be attempted."
            ) from exc
    else:
        print(f"  [DEBUG] Save button not found: count={save_btn.count()}, visible={save_btn.is_visible() if save_btn.count() > 0 else 'N/A'}")
        print("  Save button not found")
        go_back(page, days_ahead)
        return False


def _calendar_navigation_button(page, direction):
    """Resolve one visible, enabled date control by semantic identity.

    The current Asimut shell also contains hidden drawer icons whose text is
    ``chevron_left``.  Selecting the first generic icon therefore targets the
    wrong control.  Prefer the date bar's stable data-cy hook, retain exact
    accessible/date-bar fallbacks, and reject ambiguous visible controls.
    """

    if direction not in {-1, 1}:
        raise ValueError("Calendar navigation direction must be -1 or 1")
    if direction > 0:
        selectors = (
            "button[data-cy='increment-date-chevron']",
            "button[aria-label='Change date forward']",
            ".date-bar button:has(mat-icon:text-is('chevron_right'))",
        )
        direction_name = "forward"
    else:
        selectors = (
            "button[data-cy='decrement-date-chevron']",
            "button[aria-label='Change date backward']",
            ".date-bar button:has(mat-icon:text-is('chevron_left'))",
        )
        direction_name = "backward"

    for selector in selectors:
        locator = page.locator(selector)
        visible = []
        for index in range(locator.count()):
            candidate = locator.nth(index)
            if candidate.is_visible():
                visible.append(candidate)
        if len(visible) > 1:
            raise RuntimeError(
                f"Calendar {direction_name} navigation is ambiguous "
                f"({len(visible)} visible controls matched {selector})"
            )
        if len(visible) == 1:
            button = visible[0]
            if not button.is_enabled():
                raise RuntimeError(
                    f"Calendar {direction_name} navigation control is disabled"
                )
            return button

    raise RuntimeError(
        f"Calendar {direction_name} navigation control is not visible"
    )


def _walk_calendar_days(page, target_day, current_day, *, today):
    """Walk between two verified calendar offsets without double-clicking."""

    if target_day == current_day:
        assert_calendar_date(page, today + timedelta(days=target_day))
        return current_day

    direction = 1 if target_day > current_day else -1
    clicks_needed = abs(target_day - current_day)
    direction_name = "forward" if direction > 0 else "backward"
    print(f"    Navigating {direction_name} {clicks_needed} day(s) to Day {target_day}...")

    position = current_day
    assert_calendar_date(page, today + timedelta(days=position))
    for step in range(clicks_needed):
        next_position = position + direction
        current_date = today + timedelta(days=position)
        next_date = today + timedelta(days=next_position)
        completed = False
        last_error = None
        for _attempt in range(1, 4):
            try:
                if not page_is_authenticated(page):
                    raise RuntimeError("Asimut session is no longer authenticated")

                visible_dates = get_visible_calendar_dates(
                    page,
                    reference_date=next_date,
                )
                current_visible = current_date in visible_dates
                next_visible = next_date in visible_dates
                if current_visible == next_visible:
                    seen = ", ".join(
                        sorted(value.isoformat() for value in visible_dates)
                    ) or "none"
                    raise RuntimeError(
                        "Calendar position became ambiguous before navigation "
                        f"(expected {current_date} or {next_date}; saw {seen})"
                    )

                if current_visible:
                    page.keyboard.press("Escape")
                    page.evaluate("window.scrollTo(0, 0)")
                    button = _calendar_navigation_button(page, direction)
                    button.click(timeout=5000)

                # If a prior click changed the date but grid verification
                # timed out, the next retry reaches this wait without clicking
                # again.  That prevents an accidental two-day jump.
                wait_for_practice_room_grid(page, next_date)
                position = next_position
                completed = True
                break
            except Exception as exc:
                last_error = exc
                page.wait_for_timeout(300)
        if not completed:
            raise RuntimeError(
                f"Calendar navigation stopped at Day {position}; "
                f"could not complete step {step + 1}/{clicks_needed}: {last_error}"
            )

    return position


def navigate_to_day(page, target_day, current_day, *, base_date=None):
    """
    Navigate from current_day to target_day using chevron clicks.

    Args:
        page: Playwright page object
        target_day: Live-window day offset (0 = today)
        current_day: Current calendar position

    Returns:
        The new current_day position
    """
    today = as_date(base_date) if base_date is not None else datetime.now().date()
    try:
        return _walk_calendar_days(
            page,
            target_day,
            current_day,
            today=today,
        )
    except Exception as primary_error:
        # A stale SPA date bar should not permanently strand a scheduled run.
        # Reset through the canonical today route once, then walk forward to
        # the exact target.  Every step still proves the visible date and full
        # grid, so this recovery cannot silently land on the wrong day.
        print(
            "    Calendar traversal became stale; reopening today's verified "
            "all-locations grid and retrying once..."
        )
        try:
            open_practice_room_overview(page, today)
            return _walk_calendar_days(page, target_day, 0, today=today)
        except Exception as recovery_error:
            raise RuntimeError(
                f"Calendar navigation to Day {target_day} failed before and "
                f"after canonical reset (initial: {primary_error}; "
                f"reset: {recovery_error})"
            ) from recovery_error


def _nearest_quarter_boundary(now):
    """Return the nearest quarter-hour boundary to one local datetime."""

    floor_minute = (now.minute // 15) * 15
    floor_boundary = now.replace(minute=floor_minute, second=0, microsecond=0)
    ceil_boundary = floor_boundary + timedelta(minutes=15)
    return min(
        (floor_boundary, ceil_boundary),
        key=lambda value: abs((value - now).total_seconds()),
    )


def target_boundary_datetime(target_time, *, base_date=None):
    """Build the exact same-day local boundary represented by ``HH:MM``."""

    parsed = datetime.strptime(target_time, "%H:%M")
    boundary_date = as_date(base_date) if base_date is not None else datetime.now().date()
    return datetime.combine(boundary_date, parsed.time())


def wait_before_runtime_target_window(target_time):
    """Wait outside the runtime lock before collecting live edge evidence."""

    target_boundary = target_boundary_datetime(target_time)
    ready_at = target_boundary - timedelta(seconds=EXTENSION_PREP_WINDOW_SECONDS)
    now = datetime.now()
    if now >= ready_at:
        return 0.0
    initial_wait = (ready_at - now).total_seconds()
    print(
        f"Target {target_time} is not yet in the live discovery window; "
        f"waiting {initial_wait:.0f}s before authentication"
    )
    while True:
        remaining = (ready_at - datetime.now()).total_seconds()
        if remaining <= 0:
            break
        time.sleep(min(1.0, remaining))
    return initial_wait


def plan_horizon_edge_slots(
    target_boundary,
    *,
    today=None,
    only_date=None,
    only_room=None,
    allowed_dates=None,
    room_names=None,
):
    """Derive the one exact newly bookable slot for every eligible live room.

    For a boundary ``T``, live room horizon ``H`` and selected minimum block
    ``M``, only the slot starting at ``T + H - M`` becomes newly bookable.  This
    avoids scanning every quarter-hour in every free gap and remains valid for
    arbitrary site-reported 15-minute horizons.
    """

    if not isinstance(target_boundary, date) or not hasattr(target_boundary, "hour"):
        raise TypeError("target_boundary must be a datetime")
    today = as_date(today) if today is not None else target_boundary.date()
    if allowed_dates is None:
        live_dates = set(booking_window_dates(today))
    else:
        live_dates = {as_date(value) for value in allowed_dates}
    if isinstance(only_date, str):
        selected_date = datetime.strptime(only_date, "%Y-%m-%d").date()
    else:
        selected_date = as_date(only_date) if only_date is not None else None
    if selected_date is not None:
        live_dates &= {selected_date}

    selected_rooms = set(room_names) if room_names is not None else None
    plans = []
    for room_priority, room_name in enumerate(PRIORITY_ROOMS):
        if selected_rooms is not None and room_name not in selected_rooms:
            continue
        if only_room is not None and room_name != only_room:
            continue
        horizon_minutes = room_horizon_minutes(room_name)
        slot_start = target_boundary + timedelta(
            minutes=horizon_minutes - MINIMUM_BLOCK_MINUTES
        )
        target_date = slot_start.date()
        if target_date not in live_dates:
            continue
        if slot_start.second or slot_start.microsecond or slot_start.minute % 15:
            raise LiveRoomPolicyError(
                f"The derived horizon edge for {room_name} is not quarter-hour aligned"
            )
        start_hour = slot_start.hour + slot_start.minute / 60
        plans.append(
            {
                "room": room_name,
                "room_priority": room_priority,
                "horizon_minutes": horizon_minutes,
                "booking_minutes": MINIMUM_BLOCK_MINUTES,
                "bookable_from": target_boundary,
                "target_date": target_date,
                "days_ahead": (target_date - today).days,
                "start_hour": start_hour,
                "end_hour": start_hour + MINIMUM_BLOCK_MINUTES / 60,
            }
        )
    return plans


def _candidate_for_planned_edge(room_data, plan):
    """Return a candidate when the exact edge block fits inside a free gap."""

    start_hour = plan["start_hour"]
    minimum_end = plan["end_hour"]
    for gap in room_data.get("slots", ()):
        gap_start = float(gap["startHour"])
        gap_end = float(gap["endHour"])
        if gap_start <= start_hour + 1e-9 and minimum_end <= gap_end + 1e-9:
            candidate = dict(plan)
            candidate.update(
                {
                    "end_hour": gap_end,
                    "duration": int(round((gap_end - start_hour) * 60)),
                    "click_x": gap.get("clickX"),
                    "click_y": gap.get("clickY"),
                }
            )
            return candidate
    return None


def _strict_window_minutes(time_prefs):
    """Return a strict HH:MM envelope in minutes, or no hard envelope."""

    if not time_prefs.get("enabled") or not time_prefs.get("strict_mode"):
        return None
    return (
        int(round(time_prefs["start_hour"] * 60)),
        int(round(time_prefs["end_hour"] * 60)),
    )


def _hours_to_quarter_minutes(value):
    if value is None:
        return None
    return max(0, int(float(value) * 4 + 1e-9) * 15)


def _split_slot_around_blocks(slot_start, slot_end, blocked_ranges):
    """Split one decimal-hour gap around all overlapping blocked ranges."""

    if not blocked_ranges:
        return [(slot_start, slot_end)]
    usable = []
    current_start = slot_start
    for blocked_start, blocked_end in sorted(blocked_ranges, key=lambda value: value[0]):
        if blocked_end <= current_start:
            continue
        if blocked_start >= slot_end:
            break
        if blocked_start > current_start:
            gap_end = min(blocked_start, slot_end)
            if (gap_end - current_start) * 60 >= MINIMUM_BLOCK_MINUTES:
                usable.append((current_start, gap_end))
        current_start = max(current_start, blocked_end)
        if current_start >= slot_end:
            break
    if (
        current_start < slot_end
        and (slot_end - current_start) * 60 >= MINIMUM_BLOCK_MINUTES
    ):
        usable.append((current_start, slot_end))
    return usable


def build_day_booking_opportunities(
    available_data,
    target_date,
    tracker,
    time_prefs,
    daily_planning,
    *,
    now=None,
    remaining_daily_hours=None,
    reserved_peak_minutes=0,
    reserved_weekly_minutes=0,
    only_room=None,
):
    """Build fresh, conflict-aware current and future opportunities for a day."""

    now = now or datetime.now()
    target_date = as_date(target_date)
    date_key = target_date.isoformat()
    daily_minutes = _hours_to_quarter_minutes(remaining_daily_hours)
    weekly_minutes = max(
        0,
        (_hours_to_quarter_minutes(tracker.get_remaining_quota_hours()) or 0)
        - reserved_weekly_minutes,
    )
    peak_minutes = max(
        0,
        int(tracker.get_remaining_peak_minutes(target_date) - reserved_peak_minutes),
    )
    strict_window = _strict_window_minutes(time_prefs)
    soft_preferred_window = (
        (
            int(round(time_prefs["start_hour"] * 60)),
            int(round(time_prefs["end_hour"] * 60)),
        )
        if time_prefs.get("enabled")
        else None
    )
    conflicts = tracker.conflict_ranges.get(date_key, [])
    today_minutes = now.hour * 60 + now.minute
    opportunities = []
    seen = set()

    for room_data in available_data:
        room_name = room_data.get("room")
        if room_name not in PRIORITY_ROOMS:
            continue
        if only_room and room_name != only_room:
            continue
        room_priority = PRIORITY_ROOMS.index(room_name)
        room_blocks = tracker.get_same_room_blocked_ranges(target_date, room_name)
        for raw_gap in room_data.get("slots", ()):
            raw_start = float(raw_gap["startHour"])
            raw_end = float(raw_gap["endHour"])
            if (raw_end - raw_start) * 60 < MINIMUM_BLOCK_MINUTES:
                continue
            segments = _split_slot_around_blocks(raw_start, raw_end, conflicts)
            usable_segments = []
            for segment_start, segment_end in segments:
                usable_segments.extend(
                    _split_slot_around_blocks(
                        segment_start,
                        segment_end,
                        room_blocks,
                    )
                )
            for segment_start, segment_end in usable_segments:
                for opportunity in enumerate_gap_opportunities(
                    room=room_name,
                    target_date=target_date,
                    gap_start_hour=segment_start,
                    gap_end_hour=segment_end,
                    horizon_minutes=room_horizon_minutes(room_name),
                    room_priority=room_priority,
                    planning=daily_planning,
                    now=now,
                    minimum_block_minutes=MINIMUM_BLOCK_MINUTES,
                    maximum_booking_minutes=int(MAX_BOOKING_HOURS * 60),
                    remaining_daily_minutes=daily_minutes,
                    remaining_weekly_minutes=weekly_minutes,
                    remaining_peak_minutes=peak_minutes,
                    strict_window=strict_window,
                    soft_preferred_window=soft_preferred_window,
                    peak_start_minutes=int(PEAK_START * 60),
                    peak_end_minutes=int(PEAK_END * 60),
                ):
                    if target_date == now.date() and opportunity.start_minutes <= today_minutes:
                        continue
                    key = (
                        opportunity.room,
                        opportunity.start_minutes,
                        opportunity.end_minutes,
                    )
                    if key in seen:
                        continue
                    can_book, _ = tracker.can_book(
                        opportunity.room,
                        target_date,
                        opportunity.start_hour,
                        opportunity.potential_minutes,
                    )
                    if not can_book:
                        continue
                    seen.add(key)
                    opportunities.append(opportunity)
    return opportunities


def current_opportunities_after_hold(
    available_data,
    target_date,
    tracker,
    time_prefs,
    daily_planning,
    decision,
    *,
    now,
    remaining_daily_hours,
    reserved_weekly_minutes=0,
    only_room=None,
):
    """Keep only genuine surplus capacity while a better session is held.

    Peak, daily-target, and rolling-week capacity are all reserved together.
    Rebuilding from the same fresh gaps lets a larger off-peak opportunity be
    safely trimmed to real surplus instead of either consuming the held target
    or being discarded unnecessarily.
    """

    if decision.action != "wait" or decision.selected is None:
        return None
    held_minutes = decision.selected.potential_minutes
    surplus_daily_hours = (
        None
        if remaining_daily_hours is None
        else max(0.0, remaining_daily_hours - held_minutes / 60)
    )
    protected = build_day_booking_opportunities(
        available_data,
        target_date,
        tracker,
        time_prefs,
        daily_planning,
        now=now,
        remaining_daily_hours=surplus_daily_hours,
        reserved_peak_minutes=decision.held_peak_minutes,
        reserved_weekly_minutes=held_minutes + reserved_weekly_minutes,
        only_room=only_room,
    )
    held = decision.selected
    return [
        item
        for item in protected
        if item.unlock_at <= now
        and not (
            item.start_minutes < held.end_minutes
            and item.end_minutes > held.start_minutes
        )
        and not (
            item.room == held.room
            and not (
                item.start_minutes
                >= held.end_minutes + SAME_ROOM_GAP_MINUTES
                or item.end_minutes + SAME_ROOM_GAP_MINUTES
                <= held.start_minutes
            )
        )
    ]


def _opportunity_to_horizon_candidate(opportunity, *, target_boundary):
    return {
        "room": opportunity.room,
        "room_priority": opportunity.room_priority,
        "horizon_minutes": room_horizon_minutes(opportunity.room),
        "booking_minutes": opportunity.initial_minutes,
        "bookable_from": target_boundary,
        "target_date": opportunity.target_date,
        "days_ahead": (opportunity.target_date - target_boundary.date()).days,
        "start_hour": opportunity.start_hour,
        "end_hour": opportunity.end_hour,
        "duration": opportunity.potential_minutes,
        "planned_target_end_hour": opportunity.end_hour,
        "planning_opportunity": opportunity,
    }


def _opportunity_to_normal_slot(opportunity):
    return {
        "room": opportunity.room,
        "start_hour": opportunity.start_hour,
        "end_hour": opportunity.end_hour,
        "duration": opportunity.potential_minutes / 60,
        "room_priority": opportunity.room_priority,
        "is_good": opportunity.potential_minutes >= 60,
        "planned_target_end_hour": opportunity.end_hour,
        "planning_opportunity": opportunity,
    }


def _aware_local_datetime(value):
    """Attach the host's local zone to planner wall-clock values."""

    if value.tzinfo is not None and value.utcoffset() is not None:
        return value
    return value.astimezone()


def _plan_candidate_from_opportunity(opportunity, *, state, reason):
    return PlanCandidate(
        room=opportunity.room,
        date=opportunity.target_date.isoformat(),
        start_time=opportunity.start_text,
        end_time=opportunity.end_text,
        unlock_at=_aware_local_datetime(opportunity.unlock_at),
        initial_minutes=opportunity.initial_minutes,
        potential_minutes=opportunity.potential_minutes,
        confirmed_minutes=0,
        state=state,
        reason=reason,
    )


def _plan_candidate_from_extension(booking, *, now):
    """Reconstruct one display-only extension target from validated state."""

    start_h, start_m = map(int, booking["startTime"].split(":"))
    end_h, end_m = map(int, booking["endTime"].split(":"))
    target_h, target_m = map(int, booking["target_end"].split(":"))
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    target_minutes = target_h * 60 + target_m
    plan = plan_horizon_extension(
        booking["room"],
        booking["date"],
        start_minutes / 60,
        end_minutes / 60,
        target_minutes / 60,
        now=now,
    )
    if plan.can_extend:
        unlock_at = now.replace(
            minute=(now.minute // 15) * 15,
            second=0,
            microsecond=0,
        )
        state = "ready"
        timing = "the next extension is ready for live revalidation"
    else:
        unlock_at = plan.next_unlock_at
        state = "waiting"
        timing = "waiting for the next exact 15-minute extension edge"
    if unlock_at is None:
        raise BookingPlanError(
            "An incomplete extension target cannot be displayed as complete"
        )
    return PlanCandidate(
        room=booking["room"],
        date=booking["date"],
        start_time=booking["startTime"],
        end_time=booking["target_end"],
        unlock_at=_aware_local_datetime(unlock_at),
        initial_minutes=MINIMUM_BLOCK_MINUTES,
        potential_minutes=target_minutes - start_minutes,
        confirmed_minutes=end_minutes - start_minutes,
        state=state,
        reason=(
            f"{end_minutes - start_minutes}/{target_minutes - start_minutes} "
            f"minutes are confirmed; {timing}"
        ),
    )


def attach_extension_progress(day_plan, planning_context, *, now):
    """Place tracked partial sessions ahead of new potential day sessions."""

    if not isinstance(planning_context, dict):
        return day_plan
    pending_extensions = [
        _plan_candidate_from_extension(booking, now=now)
        for booking in planning_context.get("extension_bookings", ())
        if booking["date"] == day_plan.date
    ]
    if not pending_extensions:
        return day_plan
    pending_extensions.sort(
        key=lambda candidate: (
            candidate.state != "ready",
            candidate.unlock_at,
            PRIORITY_ROOMS.index(candidate.room),
            candidate.start_time,
        )
    )
    ordinary_selected = (
        (() if day_plan.primary is None else (day_plan.primary,))
        + day_plan.additional
    )
    selected = pending_extensions + list(ordinary_selected)
    reason = (
        f"{len(pending_extensions)} tracked horizon session(s) are partially "
        "confirmed and retain capacity before new bookings"
    )
    extension_peak = planning_context.get("extension_peak_by_date", {}).get(
        day_plan.date, 0
    )
    return replace(
        day_plan,
        status="in_progress",
        primary=selected[0],
        additional=tuple(selected[1:]),
        held_peak_minutes=min(
            extension_peak,
            max(0, day_plan.peak_limit_minutes - day_plan.peak_used_minutes),
        ),
        reason=reason,
    )


def build_display_day_plan(
    target_date,
    opportunities,
    tracker,
    daily_planning,
    *,
    now,
    target_minutes,
    remaining_weekly_minutes=None,
):
    """Convert fresh opportunities into one clear display-only day plan."""

    target_date = as_date(target_date)
    existing_minutes = int(round(tracker.get_hours_for_day(target_date) * 60 / 15)) * 15
    target_minutes = min(
        1440,
        max(existing_minutes, int(target_minutes // 15) * 15),
    )
    remaining_minutes = max(0, target_minutes - existing_minutes)
    latest_useful_unlock = now + timedelta(
        minutes=daily_planning.foresight_minutes
    )
    opportunities = [
        item
        for item in opportunities
        if item.unlock_at <= now or item.unlock_at <= latest_useful_unlock
    ]
    selection_minutes = min(
        remaining_minutes,
        _hours_to_quarter_minutes(tracker.get_remaining_quota_hours()) or 0,
    )
    if remaining_weekly_minutes is not None:
        selection_minutes = min(
            selection_minutes,
            max(0, int(remaining_weekly_minutes // 15) * 15),
        )
    selected_plan = select_day_plan(
        opportunities,
        daily_planning,
        now=now,
        target_minutes=selection_minutes,
        allow_fragmented_sessions=ALLOW_FRAGMENTED_SESSIONS,
        remaining_peak_minutes=tracker.get_remaining_peak_minutes(target_date),
        same_room_gap_minutes=SAME_ROOM_GAP_MINUTES,
    )
    current = [item for item in opportunities if item.unlock_at <= now]
    future = [item for item in opportunities if item.unlock_at > now]
    decision = choose_horizon_opportunity(
        current,
        future,
        daily_planning,
        now=now,
    )
    if decision.action in {"book_now", "wait"} and decision.selected is not None:
        # The displayed primary must be the exact opportunity the mutation
        # decision selected. A stronger but insufficiently corroborated future
        # option must never be labelled ready while runtime books the fallback.
        forced_plan = select_day_plan(
            (decision.selected,),
            daily_planning,
            now=now,
            target_minutes=selection_minutes,
            allow_fragmented_sessions=False,
            remaining_peak_minutes=tracker.get_remaining_peak_minutes(target_date),
            same_room_gap_minutes=SAME_ROOM_GAP_MINUTES,
        )
        selected_plan = forced_plan
        forced = forced_plan[0] if forced_plan else None
        remaining_selection = max(
            0,
            selection_minutes - (forced.potential_minutes if forced else 0),
        )
        if (
            forced is not None
            and ALLOW_FRAGMENTED_SESSIONS
            and remaining_selection >= MINIMUM_BLOCK_MINUTES
        ):
            remaining_options = [
                item
                for item in opportunities
                if item != decision.selected
                and not (
                    item.start_minutes < forced.end_minutes
                    and item.end_minutes > forced.start_minutes
                )
                and not (
                    item.room == forced.room
                    and not (
                        item.start_minutes
                        >= forced.end_minutes + SAME_ROOM_GAP_MINUTES
                        or item.end_minutes + SAME_ROOM_GAP_MINUTES
                        <= forced.start_minutes
                    )
                )
            ]
            selected_plan += select_day_plan(
                remaining_options,
                daily_planning,
                now=now,
                target_minutes=remaining_selection,
                allow_fragmented_sessions=True,
                remaining_peak_minutes=max(
                    0,
                    tracker.get_remaining_peak_minutes(target_date)
                    - forced.peak_minutes,
                ),
                same_room_gap_minutes=SAME_ROOM_GAP_MINUTES,
            )

    primary_opportunity = None
    candidate_state = "potential"
    held_peak = 0
    reason = "No suitable free opportunity is currently visible"
    if remaining_minutes < MINIMUM_BLOCK_MINUTES:
        status = "complete"
        reason = "The configured daily target is already met"
    elif selected_plan:
        primary_opportunity = selected_plan[0]
        if decision.action == "wait" and decision.selected is not None:
            candidate_state = "waiting"
            held_peak = min(
                decision.held_peak_minutes,
                primary_opportunity.peak_minutes,
            )
            status = "waiting"
            reason = decision.reason
        elif decision.action == "book_now":
            candidate_state = "ready"
            status = "planned"
            reason = decision.reason
        else:
            candidate_state = (
                "waiting" if primary_opportunity.unlock_at > now else "ready"
            )
            status = "waiting" if candidate_state == "waiting" else "planned"
            reason = (
                f"Best visible opportunity is {primary_opportunity.start_text}-"
                f"{primary_opportunity.end_text} in {primary_opportunity.room}"
            )
    elif decision.action == "wait":
        # A held decision can be omitted only when an aggregate hard budget no
        # longer supports it. Expose that honestly instead of claiming a plan.
        primary_opportunity = None
        candidate_state = "waiting"
        status = "blocked"
        reason = "The preferred opportunity no longer fits the aggregate booking budgets"
    else:
        status = "unplanned"

    primary = (
        None
        if primary_opportunity is None
        else _plan_candidate_from_opportunity(
            primary_opportunity,
            state=candidate_state,
            reason=reason,
        )
    )
    additional = tuple(
        _plan_candidate_from_opportunity(
            item,
            state="waiting" if item.unlock_at > now else "ready",
            reason="Selected as another non-overlapping session in this day's plan",
        )
        for item in selected_plan[1:]
    )
    ranked = sorted(
        opportunities,
        key=lambda item: opportunity_rank(item, daily_planning, now=now),
    )
    backups = []
    seen = set()
    for selected in selected_plan:
        seen.add(
            (
                selected.room,
                selected.start_minutes,
                selected.end_minutes,
            )
        )
    for item in ranked:
        identity = (item.room, item.start_minutes, item.end_minutes)
        if identity in seen:
            continue
        seen.add(identity)
        backups.append(
            _plan_candidate_from_opportunity(
                item,
                state="potential",
                reason="Visible alternative from the same fresh room-grid scan",
            )
        )
        if len(backups) == 3:
            break

    peak_limit = int(MAX_PEAK_HOURS * 60) if target_date.weekday() < 5 else 0
    peak_used = (
        int(round(tracker.get_peak_used_for_day(target_date) / 15)) * 15
        if peak_limit
        else 0
    )
    return DayPlan(
        date=target_date.isoformat(),
        target_minutes=min(1440, target_minutes),
        existing_minutes=min(1440, existing_minutes),
        peak_used_minutes=min(peak_limit, peak_used),
        peak_limit_minutes=peak_limit,
        status=status,
        primary=primary,
        additional=additional if primary is not None else (),
        backups=tuple(backups) if primary is not None else (),
        held_peak_minutes=min(held_peak, max(0, peak_limit - peak_used)),
        reason=reason,
    )


def _legacy_opportunity_rank(opportunity, time_prefs):
    """Mirror the established non-foresight booking order for display."""

    return (
        0 if is_preferred_time(opportunity.start_hour, time_prefs) else 1,
        opportunity.potential_minutes < max(MINIMUM_BLOCK_MINUTES, 60),
        opportunity.start_minutes,
        opportunity.room_priority,
    )


def _legacy_current_opportunities(opportunities, *, now):
    """Keep the one gap-start candidate the legacy mutation path can choose.

    The intelligent planner deliberately enumerates interior quarter-hour
    starts. Legacy booking never did: after conflict/strict-window trimming it
    uses the first currently bookable start of each maximal gap. Reducing by
    the source-gap identity keeps a disabled planner's preview truthful.
    """

    by_gap = {}
    for opportunity in opportunities:
        if opportunity.unlock_at > now:
            continue
        key = (
            opportunity.room,
            opportunity.source_gap_start_minutes,
            opportunity.source_gap_end_minutes,
        )
        previous = by_gap.get(key)
        if previous is None or opportunity.start_minutes < previous.start_minutes:
            by_gap[key] = opportunity
    return tuple(by_gap.values())


def build_legacy_display_day_plan(
    target_date,
    opportunities,
    tracker,
    daily_planning,
    time_prefs,
    *,
    now,
    target_minutes,
    remaining_weekly_minutes=None,
):
    """Build a display plan that exactly follows disabled-planner ordering."""

    target_date = as_date(target_date)
    existing_minutes = (
        int(round(tracker.get_hours_for_day(target_date) * 60 / 15)) * 15
    )
    target_minutes = min(
        1440,
        max(existing_minutes, int(target_minutes // 15) * 15),
    )
    selection_minutes = min(
        max(0, target_minutes - existing_minutes),
        _hours_to_quarter_minutes(tracker.get_remaining_quota_hours()) or 0,
    )
    if remaining_weekly_minutes is not None:
        selection_minutes = min(
            selection_minutes,
            max(0, int(remaining_weekly_minutes // 15) * 15),
        )
    current = _legacy_current_opportunities(opportunities, now=now)
    rank_key = lambda item: _legacy_opportunity_rank(item, time_prefs)
    selected = select_day_plan(
        current,
        daily_planning,
        now=now,
        target_minutes=selection_minutes,
        allow_fragmented_sessions=ALLOW_FRAGMENTED_SESSIONS,
        remaining_peak_minutes=tracker.get_remaining_peak_minutes(target_date),
        same_room_gap_minutes=SAME_ROOM_GAP_MINUTES,
        rank_key=rank_key,
    )
    if selection_minutes < MINIMUM_BLOCK_MINUTES:
        status = "complete"
        reason = "The configured daily or rolling-week target is already met"
    elif selected:
        status = "planned"
        first = selected[0]
        reason = (
            "Daily foresight is disabled; legacy order selects "
            f"{first.start_text}-{first.end_text} in {first.room}"
        )
    else:
        status = "unplanned"
        reason = "No currently bookable legacy-order opportunity is visible"

    selected_keys = {
        (item.room, item.start_minutes, item.end_minutes) for item in selected
    }
    backups = []
    for item in sorted(current, key=rank_key):
        identity = (item.room, item.start_minutes, item.end_minutes)
        if identity in selected_keys:
            continue
        backups.append(
            _plan_candidate_from_opportunity(
                item,
                state="potential",
                reason="Alternative in the established non-foresight booking order",
            )
        )
        if len(backups) == 3:
            break
    selected_candidates = tuple(
        _plan_candidate_from_opportunity(
            item,
            state="ready",
            reason=reason if index == 0 else "Another legacy-order session",
        )
        for index, item in enumerate(selected)
    )
    peak_limit = int(MAX_PEAK_HOURS * 60) if target_date.weekday() < 5 else 0
    peak_used = (
        int(round(tracker.get_peak_used_for_day(target_date) / 15)) * 15
        if peak_limit
        else 0
    )
    return DayPlan(
        date=target_date.isoformat(),
        target_minutes=target_minutes,
        existing_minutes=min(1440, existing_minutes),
        peak_used_minutes=min(peak_limit, peak_used),
        peak_limit_minutes=peak_limit,
        status=status,
        primary=selected_candidates[0] if selected_candidates else None,
        additional=selected_candidates[1:],
        backups=tuple(backups) if selected_candidates else (),
        held_peak_minutes=0,
        reason=reason,
    )


def publish_booking_plan(days, policy, settings, *, summary, status="active", now=None):
    """Best-effort publication of display evidence; never booking authority."""

    now = _aware_local_datetime(now or datetime.now())
    try:
        # The runtime itself may have added, advanced, or removed an extension
        # record after loading its immutable planning preferences. Fold only
        # that runtime-owned field into the fingerprint. Any concurrent GUI
        # change to strategy, dates, rooms, targets, or ignored events therefore
        # still makes this snapshot visibly stale instead of falsely current.
        fingerprint_settings = dict(settings)
        latest_settings = load_settings_document(settings_file)
        if "extendable_bookings" in latest_settings:
            fingerprint_settings["extendable_bookings"] = latest_settings[
                "extendable_bookings"
            ]
        else:
            fingerprint_settings.pop("extendable_bookings", None)
        snapshot = BookingPlanSnapshot(
            version=1,
            generated_at=now,
            valid_until=now + timedelta(minutes=20),
            policy_observed_at=_aware_local_datetime(policy.observed_at),
            strategy_fingerprint=booking_plan_fingerprint(fingerprint_settings),
            status=status,
            summary=summary,
            days=tuple(sorted(days, key=lambda item: item.date)),
        )
        write_booking_plan(snapshot)
        return snapshot
    except (BookingPlanError, SettingsError) as exc:
        print(
            "  WARNING: Booking decision remains valid, but the display-only "
            f"plan could not be published: {exc}"
        )
        return None


def publish_planning_context(
    planning_context,
    policy,
    settings,
    *,
    summary,
    status="active",
    now=None,
):
    """Publish every freshly observed day accumulated in the current run."""

    days = tuple(
        planning_context.get("display_days", {}).values()
        if isinstance(planning_context, dict)
        else ()
    )
    if not days:
        return None
    return publish_booking_plan(
        days,
        policy,
        settings,
        summary=summary,
        status=status,
        now=now,
    )


def record_plan_create_progress(
    planning_context,
    policy,
    settings,
    tracker,
    candidate,
    confirmed_minutes,
    *,
    now=None,
):
    """Replace a just-verified create preview with explicit extension progress."""

    if not isinstance(planning_context, dict):
        return None
    target_date = as_date(candidate["target_date"])
    date_key = target_date.isoformat()
    day = planning_context.get("display_days", {}).get(date_key)
    if day is None or day.primary is None:
        return None
    primary = day.primary
    start_text = (
        f"{int(candidate['start_hour']):02d}:"
        f"{int(round(candidate['start_hour'] % 1 * 60)):02d}"
    )
    if primary.room != candidate["room"] or primary.start_time != start_text:
        return None
    confirmed = min(
        primary.potential_minutes,
        max(primary.confirmed_minutes, int(confirmed_minutes)),
    )
    existing_minutes = min(
        1440,
        int(round(tracker.get_hours_for_day(target_date) * 60 / 15)) * 15,
    )
    peak_limit = day.peak_limit_minutes
    peak_used = min(
        peak_limit,
        int(round(tracker.get_peak_used_for_day(target_date) / 15)) * 15,
    )
    additional = list(day.additional)
    if confirmed >= primary.potential_minutes:
        new_primary = additional.pop(0) if additional else None
        status = "planned" if new_primary is not None else (
            "complete" if existing_minutes >= day.target_minutes else "unplanned"
        )
        held_peak = 0
        reason = (
            "The selected session is fully confirmed in Asimut; remaining "
            "potential sessions will be replanned from a fresh grid"
        )
    else:
        new_primary = replace(
            primary,
            confirmed_minutes=confirmed,
            state="waiting",
            reason=(
                f"{confirmed}/{primary.potential_minutes} minutes are confirmed; "
                "the remainder requires separately verified horizon extensions"
            ),
        )
        status = "in_progress"
        start_minutes = int(primary.start_time[:2]) * 60 + int(primary.start_time[3:])
        end_minutes = int(primary.end_time[:2]) * 60 + int(primary.end_time[3:])
        remaining_peak = interval_overlap_minutes(
            start_minutes + confirmed,
            end_minutes,
            int(PEAK_START * 60),
            int(PEAK_END * 60),
        )
        held_peak = min(
            remaining_peak,
            max(0, peak_limit - peak_used),
        )
        reason = new_primary.reason
    updated = replace(
        day,
        existing_minutes=existing_minutes,
        peak_used_minutes=peak_used,
        status=status,
        primary=new_primary,
        additional=tuple(additional),
        held_peak_minutes=held_peak,
        reason=reason,
    )
    planning_context["display_days"][date_key] = updated
    planning_context.setdefault("held_peak_by_date", {}).pop(date_key, None)
    planning_context.setdefault("held_target_by_date", {}).pop(date_key, None)
    if new_primary is None:
        planning_context.setdefault("extension_peak_by_date", {}).pop(
            date_key, None
        )
        planning_context.setdefault("extension_target_by_date", {}).pop(
            date_key, None
        )
    else:
        planning_context.setdefault("extension_peak_by_date", {})[
            date_key
        ] = held_peak
        planning_context.setdefault("extension_target_by_date", {})[date_key] = (
            new_primary.potential_minutes - new_primary.confirmed_minutes
        )
    try:
        planning_context["extension_bookings"] = tuple(
            load_extendable_bookings(load_settings_document(settings_file))
        )
    except SettingsError as exc:
        print(
            "  WARNING: Booking progress is verified, but the display plan "
            f"could not refresh extension state: {exc}"
        )
    return publish_planning_context(
        planning_context,
        policy,
        settings,
        summary=reason,
        status="active" if new_primary is not None else "complete",
        now=now,
    )


def build_horizon_display_days(
    opportunities_by_date,
    planning_context,
    tracker,
    practice_plan,
    daily_planning,
    *,
    now,
    reverse_date_order=False,
):
    """Build horizon previews under one aggregate rolling-week allowance."""

    if not isinstance(planning_context, dict):
        raise TypeError("planning_context must be a dictionary")
    extension_targets = planning_context.get("extension_target_by_date", {})
    extension_weekly_minutes = sum(extension_targets.values())
    remaining_weekly_minutes = max(
        0,
        int(tracker.get_remaining_quota_hours() * 4 + 1e-9) * 15,
    )
    planned_weekly_minutes = 0
    ordered = sorted(
        opportunities_by_date.items(),
        reverse=bool(reverse_date_order),
    )
    display_days = []
    for date_key, date_opportunities in ordered:
        plan_date = date.fromisoformat(date_key)
        existing_minutes = (
            int(round(tracker.get_hours_for_day(plan_date) * 60 / 15)) * 15
        )
        if practice_plan.enabled:
            target_minutes = int(
                round((practice_plan.target_for(plan_date) or 0) * 60)
            )
        else:
            target_minutes = max(
                existing_minutes,
                existing_minutes + daily_planning.desired_peak_block_minutes,
            )
        target_minutes = max(
            target_minutes,
            existing_minutes + extension_targets.get(date_key, 0),
        )
        available_weekly_for_new = max(
            0,
            remaining_weekly_minutes
            - extension_weekly_minutes
            - planned_weekly_minutes,
        )
        display_day = build_display_day_plan(
            plan_date,
            date_opportunities,
            tracker,
            daily_planning,
            now=now,
            target_minutes=target_minutes,
            remaining_weekly_minutes=available_weekly_for_new,
        )
        selected = (
            (() if display_day.primary is None else (display_day.primary,))
            + display_day.additional
        )
        planned_weekly_minutes += sum(
            candidate.potential_minutes - candidate.confirmed_minutes
            for candidate in selected
        )
        display_day = attach_extension_progress(
            display_day,
            planning_context,
            now=now,
        )
        planning_context.setdefault("display_days", {})[
            display_day.date
        ] = display_day
        display_days.append(display_day)
    return tuple(display_days)


def find_horizon_snipe_candidate(available_data, target_date, tracker, time_prefs):
    """Compatibility helper for one loaded date using boundary-driven edges."""

    now = datetime.now()
    boundary = _nearest_quarter_boundary(now)
    target_date_obj = as_date(target_date)
    if not is_horizon_snipe_timing_candidate((boundary - now).total_seconds()):
        return None, None
    fragmentation_ok, _ = fragmentation_allows_new_booking(tracker, target_date_obj)
    if not fragmentation_ok:
        return None, None

    plans = plan_horizon_edge_slots(
        boundary,
        today=now.date(),
        allowed_dates=(target_date_obj,),
        only_date=target_date_obj,
        room_names={item.get("room") for item in available_data},
    )
    rooms = {item["room"]: item for item in available_data}
    candidates = []
    for plan in plans:
        room_data = rooms.get(plan["room"])
        if room_data is None:
            continue
        candidate = _candidate_for_planned_edge(room_data, plan)
        if candidate is None:
            continue
        start_hour = candidate["start_hour"]
        end_hour = start_hour + MINIMUM_BLOCK_MINUTES / 60
        if time_prefs["enabled"] and time_prefs["strict_mode"]:
            if not interval_is_strictly_preferred(start_hour, end_hour, time_prefs):
                continue
        can_book, _ = tracker.can_book(
            candidate["room"],
            target_date_obj,
            start_hour,
            MINIMUM_BLOCK_MINUTES,
        )
        if can_book:
            candidates.append(candidate)
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: item["room_priority"])
    wait_seconds = (boundary - now).total_seconds()
    return candidates[0], wait_seconds


def find_all_snipe_candidates_multi_day(
    page,
    tracker,
    time_prefs,
    current_calendar_day,
    disabled_dates=None,
    practice_plan=None,
    *,
    target_boundary=None,
    only_date=None,
    only_room=None,
    candidate_limit=None,
    daily_planning=None,
    planning_context=None,
):
    """Scan only exact live-derived horizon edges relevant to one boundary."""

    now = datetime.now()
    today = now.date()
    target_boundary = target_boundary or _nearest_quarter_boundary(now)
    seconds_until_boundary = (target_boundary - now).total_seconds()
    if not is_horizon_snipe_timing_candidate(seconds_until_boundary):
        print(
            "\n  Horizon boundary is outside the bounded three-minute "
            "discovery window; no room grid was scanned"
        )
        return [], current_calendar_day

    disabled_dates = disabled_dates or set()
    practice_plan = practice_plan or PracticePlan()
    # Callers predating daily planning retain the exact legacy candidate path;
    # production always passes the validated explicit strategy.
    daily_planning = daily_planning or DailyPlanningPreferences(enabled=False)
    if planning_context is not None and not isinstance(planning_context, dict):
        raise TypeError("planning_context must be a dictionary")
    plans = plan_horizon_edge_slots(
        target_boundary,
        today=today,
        only_date=only_date,
        only_room=only_room,
    )

    extension_keys = set()
    for booking in load_extendable_bookings() or ():
        ext_date = booking.get("date", "")
        ext_room = booking.get("room", "")
        ext_start = booking.get("startTime", "")
        if not (ext_date and ext_room and ext_start):
            continue
        try:
            hour, minute = map(int, ext_start.split(":"))
        except (TypeError, ValueError):
            continue
        extension_keys.add((ext_date, ext_room, hour + minute / 60))
    if extension_keys:
        print(
            f"\n  Found {len(extension_keys)} pending extension(s) - "
            "conflicting horizon creates remain excluded"
        )

    plans_by_day = {}
    for plan in plans:
        plans_by_day.setdefault(plan["days_ahead"], []).append(plan)
    if candidate_limit is not None:
        if isinstance(candidate_limit, bool) or candidate_limit != 1:
            raise ValueError("candidate_limit currently supports only one edge")
        # Visit the date containing the best-ranked remaining room first.  If
        # it is free, preparation begins immediately rather than traversing all
        # distinct horizon dates before returning to it.
        day_offsets = tuple(
            sorted(
                plans_by_day,
                key=lambda day: min(
                    plan["room_priority"] for plan in plans_by_day[day]
                ),
            )
        )
    else:
        day_offsets = tuple(sorted(plans_by_day))
    print(
        f"\n  Target-aware horizon scan: {len(plans)} room edge(s) across "
        f"{len(day_offsets)} exact live-derived date(s): {list(day_offsets)}"
    )

    candidates = []
    smart_current_opportunities = []
    smart_future_opportunities = []
    smart_decisions_by_date = {}
    for day_index, days_ahead in enumerate(day_offsets):
        day_plans = plans_by_day[days_ahead]
        target_date = day_plans[0]["target_date"]
        date_key = target_date.isoformat()
        if is_date_disabled(target_date, disabled_dates):
            print(f"\n    Skipping Day {days_ahead} ({target_date}): disabled by user")
            continue
        daily_remaining = remaining_target_hours(
            practice_plan,
            target_date,
            tracker.get_hours_for_day(target_date),
        )
        extension_target_minutes = (
            planning_context.get("extension_target_by_date", {}).get(date_key, 0)
            if planning_context is not None
            else 0
        )
        effective_daily_remaining = (
            None
            if daily_remaining is None
            else max(0.0, daily_remaining - extension_target_minutes / 60)
        )
        if (
            effective_daily_remaining is not None
            and effective_daily_remaining < MINIMUM_BLOCK_MINUTES / 60
        ):
            print(f"\n    Skipping Day {days_ahead} ({target_date}): daily practice target met")
            continue
        fragmentation_ok, fragmentation_reason = fragmentation_allows_new_booking(
            tracker,
            target_date,
        )
        if not fragmentation_ok:
            print(
                f"\n    Skipping Day {days_ahead} ({target_date}): "
                f"{fragmentation_reason}"
            )
            continue

        expected_rooms = {plan["room"] for plan in day_plans}
        print(
            f"\n    Scanning Day {days_ahead} ({target_date}) for "
            f"{len(expected_rooms)} exact room edge(s)..."
        )
        current_calendar_day = navigate_to_day(
            page,
            days_ahead,
            current_calendar_day,
            base_date=today,
        )
        available_by_room = {
            item["room"]: item
            for item in get_available_slots(page)
            if item.get("room") in expected_rooms
        }
        extension_weekly_minutes = (
            sum(planning_context.get("extension_target_by_date", {}).values())
            if planning_context is not None
            else 0
        )
        extension_peak_minutes = (
            planning_context.get("extension_peak_by_date", {}).get(date_key, 0)
            if planning_context is not None
            else 0
        )

        if daily_planning.enabled:
            cross_date_foresight_minutes = (
                sum(
                    minutes
                    for held_date, minutes in planning_context.get(
                        "held_target_by_date", {}
                    ).items()
                    if held_date != target_date.isoformat()
                )
                if planning_context is not None
                else 0
            )
            day_opportunities = build_day_booking_opportunities(
                list(available_by_room.values()),
                target_date,
                tracker,
                time_prefs,
                daily_planning,
                now=target_boundary,
                remaining_daily_hours=effective_daily_remaining,
                reserved_peak_minutes=extension_peak_minutes,
                reserved_weekly_minutes=(
                    extension_weekly_minutes + cross_date_foresight_minutes
                ),
                only_room=only_room,
            )
            day_current = []
            day_future = []
            for opportunity in day_opportunities:
                extension_key = (
                    target_date.isoformat(),
                    opportunity.room,
                    opportunity.start_hour,
                )
                if extension_key in extension_keys:
                    continue
                seconds_from_boundary = (
                    opportunity.unlock_at - target_boundary
                ).total_seconds()
                if abs(seconds_from_boundary) <= 1:
                    day_current.append(opportunity)
                elif seconds_from_boundary > 1:
                    day_future.append(opportunity)
            smart_current_opportunities.extend(day_current)
            smart_future_opportunities.extend(day_future)
            decision = choose_horizon_opportunity(
                day_current,
                day_future,
                daily_planning,
                now=target_boundary,
            )
            smart_decisions_by_date[date_key] = decision
            if planning_context is not None:
                planning_context.setdefault("opportunities_by_date", {})[
                    date_key
                ] = tuple(day_opportunities)
                planning_context.setdefault("decisions_by_date", {})[
                    date_key
                ] = decision
                if decision.action == "wait" and decision.selected is not None:
                    planning_context.setdefault("held_peak_by_date", {})[
                        date_key
                    ] = decision.held_peak_minutes
                    planning_context.setdefault("held_target_by_date", {})[
                        date_key
                    ] = decision.selected.potential_minutes
                else:
                    planning_context.setdefault("held_peak_by_date", {}).pop(
                        date_key, None
                    )
                    planning_context.setdefault("held_target_by_date", {}).pop(
                        date_key, None
                    )
            if day_current:
                print(
                    f"      {len(day_current)} exact edge(s) are free now; "
                    f"{len(day_future)} later visible option(s) were evaluated"
                )
            else:
                print(
                    f"      No exact edge is free now; {len(day_future)} "
                    "later visible option(s) were evaluated"
                )
            print(f"      Daily planner: {decision.reason}")

            # Peak allowance is per target date, so foresight is intentionally
            # decided within this one fresh day grid. If that day should wait,
            # an independent edge on another date may still be used. If the
            # selected current edge outranks every untested room, prepare it
            # immediately and preserve the boundary-reliability fast path.
            if decision.action == "book_now":
                candidates.append(
                    _opportunity_to_horizon_candidate(
                        decision.selected,
                        target_boundary=target_boundary,
                    )
                )
                if candidate_limit == 1:
                    selected_priority = decision.selected.room_priority
                    remaining_priorities = [
                        plan["room_priority"]
                        for remaining_day in day_offsets[day_index + 1 :]
                        for plan in plans_by_day[remaining_day]
                    ]
                    if (
                        not remaining_priorities
                        or selected_priority < min(remaining_priorities)
                    ):
                        print(
                            "      Best actionable room is proven; preparing "
                            "it without scanning lower-ranked horizon dates"
                        )
                        break
            continue

        day_candidates = 0
        for plan in day_plans:
            room_name = plan["room"]
            room_data = available_by_room.get(room_name)
            if room_data is None:
                continue
            candidate = _candidate_for_planned_edge(room_data, plan)
            if candidate is None:
                continue
            start_hour = candidate["start_hour"]
            end_hour = start_hour + MINIMUM_BLOCK_MINUTES / 60
            target_date_str = target_date.isoformat()
            if (target_date_str, room_name, start_hour) in extension_keys:
                print(
                    f"      Skipping {room_name} at {start_hour:.2f}: "
                    "pending extension takes priority"
                )
                continue
            if time_prefs["enabled"] and time_prefs["strict_mode"]:
                if not interval_is_strictly_preferred(start_hour, end_hour, time_prefs):
                    continue
            can_book, reason = tracker.can_book(
                room_name,
                target_date,
                start_hour,
                MINIMUM_BLOCK_MINUTES,
            )
            remaining_weekly_after_extensions = None
            if extension_weekly_minutes:
                remaining_weekly_after_extensions = max(
                    0,
                    int(tracker.get_remaining_quota_hours() * 4 + 1e-9) * 15
                    - extension_weekly_minutes,
                )
            initial_peak_minutes = (
                interval_overlap_minutes(
                    int(round(start_hour * 60)),
                    int(round(end_hour * 60)),
                    int(PEAK_START * 60),
                    int(PEAK_END * 60),
                )
                if target_date.weekday() < 5
                else 0
            )
            remaining_peak_after_extensions = None
            if extension_peak_minutes:
                remaining_peak_after_extensions = max(
                    0,
                    tracker.get_remaining_peak_minutes(target_date)
                    - extension_peak_minutes,
                )
            if (
                remaining_weekly_after_extensions is not None
                and remaining_weekly_after_extensions < MINIMUM_BLOCK_MINUTES
            ):
                can_book = False
                reason = "rolling quota is reserved for pending extensions"
            elif (
                remaining_peak_after_extensions is not None
                and initial_peak_minutes > remaining_peak_after_extensions
            ):
                can_book = False
                reason = "peak allowance is reserved for pending extensions"
            if not can_book:
                print(f"      Skipping {room_name} at {start_hour:.2f}: {reason}")
                continue
            candidate["seconds_until"] = (
                target_boundary - datetime.now()
            ).total_seconds()
            candidates.append(candidate)
            day_candidates += 1
            print(
                f"      Found exact edge: {room_name} "
                f"{start_hour:05.2f}-{end_hour:05.2f}"
            )
        if day_candidates == 0:
            print(f"      No exact edge block is free on Day {days_ahead}")
        if candidate_limit == 1 and candidates:
            best_priority = min(item["room_priority"] for item in candidates)
            remaining_priorities = [
                plan["room_priority"]
                for remaining_day in day_offsets[day_index + 1 :]
                for plan in plans_by_day[remaining_day]
            ]
            if not remaining_priorities or best_priority < min(remaining_priorities):
                print(
                    "      Highest-priority free edge found; preparing it "
                    "without scanning lower-ranked horizon dates"
                )
                break

    if daily_planning.enabled:
        if candidates:
            best_candidate = min(
                candidates,
                key=lambda item: (item["room_priority"], item["days_ahead"]),
            )
            best_opportunity = best_candidate["planning_opportunity"]
            decision = next(
                value
                for value in smart_decisions_by_date.values()
                if value.selected == best_opportunity
            )
        else:
            waiting = [
                value
                for value in smart_decisions_by_date.values()
                if value.action == "wait" and value.selected is not None
            ]
            if waiting:
                decision = min(
                    waiting,
                    key=lambda value: (
                        value.selected.room_priority,
                        value.selected.target_date,
                    ),
                )
            elif smart_decisions_by_date:
                decision = next(iter(smart_decisions_by_date.values()))
            else:
                decision = choose_horizon_opportunity(
                    (),
                    (),
                    daily_planning,
                    now=target_boundary,
                )
        if planning_context is not None:
            planning_context["decision"] = decision
            planning_context["current_opportunities"] = tuple(
                smart_current_opportunities
            )
            planning_context["future_opportunities"] = tuple(
                smart_future_opportunities
            )
        print(f"\n  Daily planner result: {decision.reason}")

    # A common boundary is attempted in the user's GUI room order.  Date
    # distance is only a deterministic tiebreaker, never a reason to demote a
    # preferred room.
    candidates.sort(
        key=lambda item: (
            item["bookable_from"],
            item["room_priority"],
            item["days_ahead"],
        )
    )
    if candidate_limit is not None:
        candidates = candidates[:candidate_limit]
    print(f"\n  Total exact horizon candidates found: {len(candidates)}")
    if candidates:
        print("  Snipe order (boundary, then GUI room preference):")
        for index, candidate in enumerate(candidates, 1):
            print(
                f"    {index}. Day {candidate['days_ahead']} - "
                f"{candidate['room']} at {candidate['start_hour']:.2f}"
            )
    return candidates, current_calendar_day


def try_horizon_snipe(
    page,
    slot,
    target_date,
    tracker,
    days_ahead,
    remaining_daily_hours=None,
    time_prefs=None,
    max_action_minutes=None,
):
    """
    Prepare a booking form for a slot that's about to become bookable,
    then click Save at the exact moment it becomes available.

    This "snipes" the slot by having everything ready before other users.

    Returns: True if successful, False otherwise.
    """
    room = slot['room']
    start_hour = slot['start_hour']
    bookable_from = slot['bookable_from']
    horizon_minutes = slot.get('horizon_minutes')
    booking_minutes = slot.get('booking_minutes')
    if horizon_minutes != room_horizon_minutes(room):
        print("  [SNIPE] Candidate horizon is stale; aborting")
        return False
    if booking_minutes != MINIMUM_BLOCK_MINUTES:
        print("  [SNIPE] Candidate minimum block is stale; aborting")
        return False
    if booking_minutes > _action_maximum_minutes(max_action_minutes):
        print("  [SNIPE] Controlled action ceiling is below the chosen minimum block")
        return False
    if SITE_CLOCK_OFFSET_BOUNDS is None:
        print(
            "  [SNIPE] Fresh Asimut clock evidence is unavailable or "
            "ambiguous; refusing an edge Save"
        )
        return False
    fragmentation_ok, fragmentation_reason = fragmentation_allows_new_booking(
        tracker,
        target_date,
    )
    if not fragmentation_ok:
        print(f"  [SNIPE] Candidate is no longer valid: {fragmentation_reason}")
        return False

    end_hour = start_hour + booking_minutes / 60

    # Candidates can become stale while earlier snipes are attempted.
    can_book, reason = tracker.can_book(
        room,
        target_date,
        start_hour,
        booking_minutes,
    )
    if not can_book:
        print(f"  [SNIPE] Candidate is no longer valid: {reason}")
        return False
    if remaining_daily_hours is not None and remaining_daily_hours < booking_minutes / 60:
        print("  [SNIPE] Daily practice target is already met")
        return False
    if time_prefs and not interval_is_strictly_preferred(start_hour, end_hour, time_prefs):
        print("  [SNIPE] Candidate falls outside the complete strict time window")
        return False

    # Format times
    start_h = int(start_hour)
    start_m = int((start_hour % 1) * 60)
    end_h = int(end_hour)
    end_m = int((end_hour % 1) * 60)
    book_start = f"{start_h:02d}:{start_m:02d}"
    book_end = f"{end_h:02d}:{end_m:02d}"

    save_deadline, clock_bounds = site_aligned_save_deadline(bookable_from)
    now = datetime.now()
    site_late_low, site_late_high = estimated_site_latency_bounds(
        now,
        bookable_from,
    )
    seconds_to_wait = (save_deadline - now).total_seconds()
    if site_late_low > HORIZON_SNIPE_GRACE_SECONDS:
        print(
            f"  [SNIPE] Candidate opened at least {site_late_low:.0f}s ago and "
            "is outside the bounded horizon-edge grace"
        )
        return False

    print(f"\n{'='*60}")
    print(f"HORIZON SNIPE: {room} at {book_start}-{book_end}")
    print(f"{'='*60}")
    print(f"  Slot becomes bookable at: {bookable_from.strftime('%H:%M:%S')}")
    print(f"  Current time: {now.strftime('%H:%M:%S')}")
    print(f"  Local safe-Save deadline: {save_deadline.strftime('%H:%M:%S.%f')[:-3]}")
    if clock_bounds is not None:
        print(
            "  Asimut clock bounds used: "
            f"site-local {clock_bounds[0]:+.3f}s to {clock_bounds[1]:+.3f}s"
        )
    print(f"  Seconds until safe Save: {seconds_to_wait:.1f}")
    print("  Strategy: Open form now, wait, re-prove identity, then Save")

    # Step 1: Click on the slot to open booking form
    room_name = slot['room']

    print(f"\n  [SNIPE] Step 1: Opening booking form...")

    # Find the room row and get click coordinates
    coords = get_room_slot_coordinates(page, room_name, start_hour, end_hour)

    if not coords:
        print(f"  [SNIPE] Could not find room '{room_name}' - aborting snipe")
        return False

    page.wait_for_timeout(200)

    # Click to open booking form
    print(f"  [SNIPE] Clicking at ({coords['x']:.0f}, {coords['y']:.0f})...")
    page.mouse.click(coords['x'], coords['y'])

    # The SVG overview can take 2-3.5s to reveal this menu.
    student_booking_btn = wait_for_student_booking_option(page, timeout_ms=5000)
    if student_booking_btn is not None:
        print(f"  [SNIPE] Found booking type button, clicking...")
        student_booking_btn.click()
    else:
        print(f"  [SNIPE] No booking type button found, checking if form opened...")

    if not wait_for_new_booking_form(page, timeout_ms=10000):
        print(
            "  [SNIPE] Exact new-booking form time controls did not become "
            f"ready at {page.url}; aborting"
        )
        go_back(page, days_ahead)
        return False

    # Step 2: Fill in the times
    print(f"  [SNIPE] Step 2: Pre-filling times ({book_start} to {book_end})...")

    start_input = page.locator(
        "#startDate, input[aria-label='Start time'], "
        "input[data-cy='time-range-start-time'], input[formcontrolname='startTime']"
    ).first
    end_input = page.locator(
        "#endDate, input[aria-label='End time'], "
        "input[data-cy='time-range-end-time'], input[formcontrolname='endTime']"
    ).first
    if start_input.count() > 0 and end_input.count() > 0:
        # Fill start time
        start_input.click()
        start_input.fill(book_start)
        page.wait_for_timeout(200)

        # Fill end time
        end_input.click()
        end_input.fill(book_end)
        page.wait_for_timeout(200)

        # Verify times
        actual_start = start_input.input_value()
        actual_end = end_input.input_value()
        print(f"  [SNIPE] Times set: {actual_start} to {actual_end}")
        if not booking_times_match(book_start, book_end, actual_start, actual_end):
            print("  [SNIPE] Requested times were not retained; aborting")
            go_back(page, days_ahead)
            return False
        if not booking_summary_matches(
            page_booking_snapshot(page),
            room,
            target_date,
            book_start,
            book_end,
        ):
            print("  [SNIPE] Booking form room/date/time does not match the candidate; aborting")
            go_back(page, days_ahead)
            return False
    else:
        print("  [SNIPE] Could not find labelled start/end time inputs - aborting")
        go_back(page, days_ahead)
        return False

    # Step 3: Locate Save button
    print(f"  [SNIPE] Step 3: Locating Save button...")
    save_btn = page.locator("button").filter(has_text="Save").first

    if save_btn.count() == 0 or not save_btn.is_visible():
        print(f"  [SNIPE] Save button not found - aborting")
        go_back(page, days_ahead)
        return False

    save_box = save_btn.bounding_box()
    if not save_box:
        print(f"  [SNIPE] Could not get Save button position - aborting")
        go_back(page, days_ahead)
        return False

    save_x = save_box['x'] + save_box['width'] / 2
    save_y = save_box['y'] + save_box['height'] / 2
    print(f"  [SNIPE] Save button located at ({save_x:.0f}, {save_y:.0f})")

    # Step 4: Wait until the exact moment, then click Save
    print(f"\n  [SNIPE] Step 4: Waiting for bookable moment...")

    # Update timing against the conservative local deadline derived from the
    # already-collected Asimut HTTPS clock evidence.
    now = datetime.now()
    seconds_to_wait = (save_deadline - now).total_seconds()

    if seconds_to_wait > 0:
        print(f"  [SNIPE] Waiting {seconds_to_wait:.1f} seconds...")
        wait_until_datetime(
            save_deadline,
            page,
            label="SNIPE",
            settle_seconds=(
                HORIZON_SAVE_SETTLE_SECONDS if clock_bounds is None else 0
            ),
        )
    else:
        if clock_bounds is None:
            print(
                f"  [SNIPE] Adding {HORIZON_SAVE_SETTLE_SECONDS:.2f}s "
                "fallback clock allowance"
            )
            time.sleep(HORIZON_SAVE_SETTLE_SECONDS)
        else:
            print("  [SNIPE] Safe site-aligned Save deadline already reached")

    # Everything above was preparation. Re-prove the exact new-event form and
    # obtain fresh controls immediately before the receipt and Save so a
    # rerender, navigation, authentication change, or delayed earlier action
    # cannot mutate a different booking.
    site_late_low, site_late_high = estimated_site_latency_bounds(
        datetime.now(),
        bookable_from,
    )
    if site_late_low > HORIZON_SNIPE_GRACE_SECONDS:
        print(
            f"  [SNIPE] Prepared form is now at least {site_late_low:.0f}s past its "
            "bounded horizon-edge grace; aborting"
        )
        go_back(page, days_ahead)
        return False
    if not is_new_booking_form_url(page.url):
        print(
            f"  [SNIPE] Exact new-booking form identity changed at {page.url}; "
            "aborting"
        )
        go_back(page, days_ahead)
        return False

    validation_ok, validation_detail = refresh_new_booking_validation(
        page,
        book_end,
    )
    if not validation_ok:
        print(
            "  [SNIPE] Could not prove a fresh boundary-time validation: "
            f"{validation_detail}; aborting"
        )
        go_back(page, days_ahead)
        return False
    print("  [SNIPE] Fresh boundary-time validation completed")

    start_input = page.locator(
        "#startDate, input[aria-label='Start time'], "
        "input[data-cy='time-range-start-time'], input[formcontrolname='startTime']"
    ).first
    end_input = page.locator(
        "#endDate, input[aria-label='End time'], "
        "input[data-cy='time-range-end-time'], input[formcontrolname='endTime']"
    ).first
    if (
        start_input.count() == 0
        or end_input.count() == 0
        or not start_input.is_visible()
        or not end_input.is_visible()
        or not booking_times_match(
            book_start,
            book_end,
            start_input.input_value(),
            end_input.input_value(),
        )
        or not booking_summary_matches(
            page_booking_snapshot(page),
            room,
            target_date,
            book_start,
            book_end,
        )
    ):
        print("  [SNIPE] Prepared form changed before Save; aborting")
        go_back(page, days_ahead)
        return False

    save_btn = page.locator("button").filter(has_text="Save").first
    if save_btn.count() == 0 or not save_btn.is_visible():
        print("  [SNIPE] Save button disappeared before the boundary; aborting")
        go_back(page, days_ahead)
        return False
    try:
        if not save_btn.is_enabled():
            print("  [SNIPE] Save button is disabled at the boundary; aborting")
            go_back(page, days_ahead)
            return False
    except Exception as exc:
        print(f"  [SNIPE] Could not prove Save is enabled: {exc}")
        go_back(page, days_ahead)
        return False
    rejection = _visible_save_rejection(page)
    if rejection:
        print(f"  [SNIPE] Booking form rejects the Save: {rejection}")
        go_back(page, days_ahead)
        return False
    save_box = save_btn.bounding_box()
    if not save_box:
        print("  [SNIPE] Save button has no current click target; aborting")
        go_back(page, days_ahead)
        return False
    save_x = save_box["x"] + save_box["width"] / 2
    save_y = save_box["y"] + save_box["height"] / 2

    try:
        receipt = record_pending_create(
            room=room,
            booking_date=as_date(target_date).isoformat(),
            start=book_start,
            end=book_end,
        )
    except MutationReceiptError as exc:
        raise BookingVerificationError(
            f"Could not create a crash-recovery receipt before snipe Save: {exc}"
        ) from exc

    # Click Save only after the durable receipt exists.  Record the actual
    # click time separately from the later reload/persistence confirmation.
    click_time = datetime.now()
    click_latency_low, click_latency_high = estimated_site_latency_bounds(
        click_time,
        bookable_from,
    )
    print(f"\n  >>> CLICKING SAVE NOW: {click_time.strftime('%H:%M:%S.%f')[:-3]} <<<")
    print(
        "  [SNIPE] Estimated Save-click latency vs Asimut edge: "
        f"{click_latency_low * 1000:.0f}ms to {click_latency_high * 1000:.0f}ms"
    )

    try:
        page.mouse.click(save_x, save_y)
    except Exception as exc:
        raise BookingVerificationError(
            f"Snipe Save outcome is uncertain (receipt {receipt['id']}): {exc}"
        ) from exc

    if not wait_for_created_booking_outcome(
        page,
        receipt,
        room,
        target_date,
        book_start,
        book_end,
    ):
        go_back(page, days_ahead)
        return False

    result_time = datetime.now()
    print(
        "  [SNIPE] SUCCESS! Exact booking persisted at "
        f"{result_time.strftime('%H:%M:%S.%f')[:-3]}"
    )
    print(
        "  [SNIPE] Persistence confirmation after click: "
        f"{(result_time - click_time).total_seconds() * 1000:.0f}ms"
    )

    tracker.add_booking(room, target_date, start_hour, end_hour)
    print(f"  SUCCESS: Booked {room} {book_start}-{book_end}")

    max_possible_duration = _bounded_create_duration_minutes(
        min(slot['duration'], MAX_BOOKING_HOURS * 60),
        remaining_daily_hours,
        tracker.get_remaining_quota_hours() + booking_minutes / 60,
        max_action_minutes,
    )
    if time_prefs and time_prefs.get("enabled") and time_prefs.get("strict_mode"):
        max_possible_duration = min(
            max_possible_duration,
            int(max(0.0, time_prefs["end_hour"] - start_hour) * 60),
        )
        max_possible_duration = (max_possible_duration // 15) * 15
    target_end_hour = _extension_target_end_hour(
        start_hour,
        end_hour,
        max_possible_duration,
    )
    if target_end_hour is not None:
        try:
            save_extendable_booking(
                room,
                target_date,
                start_hour,
                end_hour,
                target_end_hour,
                event_url=page.url,
            )
            target_end_h = int(target_end_hour)
            target_end_m = int((target_end_hour % 1) * 60)
            print(f"  [EXTEND] Marked for extension: target {book_start}-{target_end_h:02d}:{target_end_m:02d}")
        except SettingsError as exc:
            print(f"  WARNING: Snipe is confirmed, but extension tracking could not be saved: {exc}")

    return True


def go_back(page, days_ahead=None):
    """Return through a canonical overview URL and prove the selected day."""
    print(f"  [DEBUG] go_back() called")
    open_practice_room_overview(page, datetime.now().date())
    if days_ahead:
        navigate_to_day(page, days_ahead, 0)
    print("  [DEBUG] Back on verified practice-room calendar")


def _assert_complete_agenda_extraction(structure, window_dates, events_data):
    """Require exact live-window day coverage and event-card extraction."""

    window_dates = tuple(window_dates)
    if not window_dates or any(
        not isinstance(value, date) or isinstance(value, datetime)
        for value in window_dates
    ):
        raise RuntimeError("Agenda verification requires live booking-window dates")

    expected_headers = [
        value.strftime("%A %d %B %Y").replace(" 0", " ")
        for value in window_dates
    ]
    if (
        not isinstance(structure, dict)
        or not isinstance(structure.get("agendaDayStructure"), list)
    ):
        raise RuntimeError("Agenda structure could not be verified safely")
    relevant_days = [
        entry
        for entry in structure["agendaDayStructure"]
        if isinstance(entry, dict) and entry.get("header") in expected_headers
    ]
    headers = [entry.get("header") for entry in relevant_days]
    if sorted(headers) != sorted(expected_headers):
        missing_or_duplicate = [
            header for header in expected_headers if headers.count(header) != 1
        ]
        raise RuntimeError(
            "Agenda extraction did not cover each live-window date "
            f"exactly once (missing or duplicated: {missing_or_duplicate})"
        )
    unmapped_active_days = [
        entry
        for entry in structure["agendaDayStructure"]
        if isinstance(entry, dict)
        and entry.get("parsedDate") is None
        and (entry.get("activeEventIds") or entry.get("invalidCards"))
    ]
    if unmapped_active_days:
        raise RuntimeError(
            "Agenda contained active event cards whose owning day header could "
            "not be mapped exactly; agenda state cannot be trusted safely"
        )
    invalid_cards = [
        invalid
        for entry in relevant_days
        for invalid in entry.get("invalidCards", [])
    ]
    if invalid_cards:
        raise RuntimeError(
            f"Agenda contained {len(invalid_cards)} event card(s) with ambiguous "
            "identity, title, time, or reservation-location fields"
        )
    header_dates = {
        header: value.isoformat()
        for header, value in zip(expected_headers, window_dates)
    }
    dom_event_associations = sorted(
        (event_id, header_dates[entry["header"]])
        for entry in relevant_days
        for event_id in entry.get("eventIds", [])
        if isinstance(event_id, int)
        and not isinstance(event_id, bool)
        and event_id > 0
    )
    extracted_event_associations = sorted(
        (event.get("eventId"), event.get("date"))
        for event in events_data
        if isinstance(event.get("eventId"), int)
        and not isinstance(event.get("eventId"), bool)
        and event.get("eventId") > 0
    )
    if extracted_event_associations != dom_event_associations:
        raise RuntimeError(
            "Agenda event-card ID/date associations did not match the extracted "
            "events; agenda state cannot be trusted safely"
        )

    # Virtual scrolling may retain an exact duplicate of one card. Accept only
    # byte-for-byte-equivalent semantic copies; a positive remote ID attached to
    # different fields is ambiguous and must never be collapsed by deduplication.
    event_id_signatures = {}
    for event in events_data:
        event_id = event.get("eventId") if isinstance(event, dict) else None
        if type(event_id) is not int or event_id <= 0:
            continue
        signature = (
            event.get("date"),
            event.get("startTime"),
            event.get("endTime"),
            event.get("title"),
            event.get("isReservation"),
            event.get("room"),
        )
        previous = event_id_signatures.setdefault(event_id, signature)
        if previous != signature:
            raise RuntimeError(
                f"Agenda event {event_id} had conflicting active card copies; "
                "agenda state cannot be trusted safely"
            )


def scan_agenda(
    page,
    tracker,
    today,
    ignored_events=None,
    *,
    window_dates=None,
    snapshot_path=None,
):
    """Scan every date in the freshly observed live booking window."""
    window_dates = tuple(window_dates or booking_window_dates(today))
    if not window_dates or window_dates[0] != today:
        raise RuntimeError("Agenda scan requires a current live booking window")
    print("\n" + "="*60)
    print("SCANNING MY AGENDA FOR EXISTING EVENTS")
    print("="*60)

    # Navigate directly to the agenda page
    print("  [DEBUG] Navigating to agenda page...")
    safe_goto(page, ASIMUT_AGENDA_URL)
    page.wait_for_timeout(3000)
    print(f"  [DEBUG] Agenda page loaded, URL: {page.url[:50]}...")

    target_date = window_dates[-1]
    target_date_str = target_date.strftime("%d %B %Y").lstrip("0")  # e.g., "7 February 2026"
    print(f"  Scrolling until we reach {target_date_str}...")

    # Keep scrolling until we see the target date or reach the end
    max_scrolls = 50  # Safety limit
    scroll_count = 0
    last_height = 0
    stalled_count = 0  # Track consecutive scrolls with no height change
    max_stalled = 3  # Allow a few stalled scrolls before giving up (lazy loading)
    target_reached = False

    while scroll_count < max_scrolls:
        # Check if target date is visible on page
        page_text = page.evaluate("() => document.body.innerText")
        if target_date_str in page_text:
            print(f"  [DEBUG] Found target date '{target_date_str}' after {scroll_count} scrolls")
            target_reached = True
            break

        # Also check alternative date format (e.g., "7 February 2026" vs "07 February 2026")
        alt_date_str = target_date.strftime("%d %B %Y")  # With leading zero
        if alt_date_str in page_text:
            print(f"  [DEBUG] Found target date '{alt_date_str}' after {scroll_count} scrolls")
            target_reached = True
            break

        # Scroll down - use scrollTo for more reliable scrolling
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1000)  # Wait longer for lazy loading

        # Check if we've reached the bottom (no new content)
        new_height = page.evaluate("() => document.body.scrollHeight")
        if new_height == last_height:
            stalled_count += 1
            if stalled_count >= max_stalled:
                print(f"  [DEBUG] Reached end of agenda page after {scroll_count} scrolls (height: {new_height})")
                break
            # Try scrolling again - content might still be loading
            page.wait_for_timeout(500)
        else:
            stalled_count = 0  # Reset if we got new content
        last_height = new_height
        scroll_count += 1
        if scroll_count % 5 == 0:
            print(f"  [DEBUG] Still scrolling... ({scroll_count} scrolls, height: {new_height})")

    if not target_reached:
        raise RuntimeError(
            f"Agenda scan did not reach {target_date.isoformat()}; "
            "booking stopped rather than trusting a partial conflict scan"
        )

    # Scroll back to top to ensure we capture everything
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(1000)

    # Now scroll through the entire page once more to ensure all content is in DOM
    # Use a minimum of 10 scrolls to ensure we cover at least a week
    min_scrolls = max(scroll_count + 5, 10)
    print(f"  Final pass to capture all events ({min_scrolls} scrolls)...")
    for i in range(min_scrolls):
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        page.wait_for_timeout(400)
        if i % 5 == 4:
            # Extra wait every 5 scrolls for lazy loading
            page.wait_for_timeout(300)

    page.wait_for_timeout(1000)

    # Capture the day/card topology and semantic event fields in one atomic DOM
    # evaluation.  A newly cancelled Angular card can change between tasks; two
    # independent walks previously disagreed about descendant-only cancellation
    # styling and left a valid cancellation receipt permanently unreconciled.
    agenda_dom = page.evaluate(r"""(scanPolicy) => {
        const events = [];
        const agendaDayStructure = [];
        const configuredRooms = scanPolicy.configuredRooms;
        const allowedDateSet = new Set(scanPolicy.allowedDates);
        const allowedDateIndex = new Map(
            scanPolicy.allowedDates.map((value, index) => [value, index])
        );
        const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();

        const monthNames = ['January', 'February', 'March', 'April', 'May', 'June',
                          'July', 'August', 'September', 'October', 'November', 'December'];
        const datePattern = /^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})$/i;
        const timePattern = /^((?:[01][0-9]|2[0-3]):[0-5][0-9])\s*[-–]\s*((?:[01][0-9]|2[0-3]):[0-5][0-9])$/;

        function configuredRoomFromText(text) {
            const matches = [];
            for (const room of configuredRooms) {
                const escaped = room.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                const pattern = new RegExp(`(^|[^A-Za-z0-9.])${escaped}(?=$|[^A-Za-z0-9.])`, 'i');
                if (pattern.test(text || '')) matches.push(room);
            }
            if (!matches.length) return null;
            const longest = Math.max(...matches.map(room => room.length));
            const longestMatches = matches.filter(room => room.length === longest);
            return longestMatches.length === 1 ? longestMatches[0] : null;
        }

        function hasCancellationSignal(element) {
            const style = window.getComputedStyle(element);
            if (style.textDecoration.includes('line-through') ||
                style.textDecorationLine.includes('line-through')) {
                return true;
            }
            const className = String(element.className || '').toLowerCase();
            if (className.includes('cancelled') || className.includes('canceled') ||
                className.includes('deleted') || className.includes('removed')) {
                return true;
            }
            // Background colour is non-inherited. Generic computed text colour
            // is deliberately excluded: a red day/theme ancestor would otherwise
            // make every active descendant look cancelled.
            const red = (style.backgroundColor || '').match(
                /rgba?\((\d+),\s*(\d+),\s*(\d+)/
            );
            if (red) {
                const r = Number(red[1]);
                const g = Number(red[2]);
                const b = Number(red[3]);
                if (r > 180 && r > g * 1.5 && r > b * 1.5) return true;
            }
            return false;
        }

        // Inspect the exact card plus the time/title descendants Asimut styles
        // during cancellation. Ancestor traversal is bounded at the owning day
        // so an unrelated page or day style cannot cancel every event.
        function isCancelledCard(card, dayBody) {
            const roots = [
                card,
                ...card.querySelectorAll(
                    '[data-cy="event-datetime"], .event-datetime, '
                    + '[data-cy="event-display-name"]'
                ),
            ];
            for (const root of roots) {
                let el = root;
                while (el && el !== dayBody) {
                    if (hasCancellationSignal(el)) return true;
                    el = el.parentElement;
                }
            }
            return false;
        }

        function canonicalEventRoot(timeControl, dayBody) {
            let element = timeControl;
            let semanticRoot = null;
            while (element && element !== dayBody) {
                const dataCy = element.getAttribute
                    ? element.getAttribute('data-cy') || ''
                    : '';
                if (dataCy.startsWith('event_')) return element;
                if (!semanticRoot && (
                    element.tagName === 'APP-AS-EVENT'
                    || element.classList.contains('as-event-panel')
                    || element.classList.contains('event-details-expanded')
                )) {
                    semanticRoot = element;
                }
                element = element.parentElement;
            }
            return semanticRoot || timeControl;
        }

        for (const dayBody of document.querySelectorAll('.day-body')) {
            const container = dayBody.parentElement;
            const headers = container
                ? Array.from(container.children).filter(
                    (child) => child.classList.contains('day-header')
                  )
                : [];
            const header = headers.length === 1 ? headers[0] : null;
            const headerText = clean(header && header.textContent)
                .replace(/\s*·\s*no events$/i, '');
            const dateMatch = headerText.match(datePattern);
            let currentDate = null;
            let localDate = null;
            let daysDiff = null;
            if (dateMatch) {
                const day = Number(dateMatch[2]);
                const month = monthNames.indexOf(dateMatch[3]);
                const year = Number(dateMatch[4]);
                currentDate = new Date(year, month, day);
                localDate = [
                    currentDate.getFullYear().toString().padStart(4, '0'),
                    (currentDate.getMonth() + 1).toString().padStart(2, '0'),
                    currentDate.getDate().toString().padStart(2, '0'),
                ].join('-');
                daysDiff = allowedDateIndex.has(localDate)
                    ? allowedDateIndex.get(localDate)
                    : null;
            }

            const entry = {
                header: headerText,
                headerCount: headers.length,
                parsedDate: localDate,
                activeEventIds: [],
                eventIds: [],
                invalidCards: [],
            };
            const cards = new Set(
                dayBody.querySelectorAll('[data-cy^="event_"]')
            );
            for (const timeControl of dayBody.querySelectorAll(
                '[data-cy="event-datetime"], .event-datetime'
            )) {
                cards.add(canonicalEventRoot(timeControl, dayBody));
            }
            for (const card of cards) {
                if (isCancelledCard(card, dayBody)) continue;
                const dataCy = card.getAttribute('data-cy') || '';
                const idMatch = dataCy.match(/^event_([1-9][0-9]*)$/);
                if (idMatch) entry.activeEventIds.push(Number(idMatch[1]));
                const timeControls = card.querySelectorAll(
                    '[data-cy="event-datetime"], .event-datetime'
                );
                const displayNames = card.querySelectorAll(
                    '[data-cy="event-display-name"]'
                );
                const locationLinks = card.querySelectorAll(
                    '.location-link, [data-cy="event-location-link"]'
                );
                const timeText = timeControls.length === 1
                    ? clean(timeControls[0].textContent)
                    : '';
                const timeMatch = timeText.match(timePattern);
                const titleText = displayNames.length === 1
                    ? clean(displayNames[0].textContent)
                    : '';
                const titleValid = titleText.length > 0;
                const eventTitle = titleText === 'Reservation'
                    || titleText.includes('Reservation')
                    ? 'Reservation'
                    : titleText;
                const isReservation = eventTitle === 'Reservation';
                const eventRoom = locationLinks.length === 1
                    ? configuredRoomFromText(clean(locationLinks[0].textContent))
                    : null;
                const locationValid = !isReservation
                    || (locationLinks.length === 1 && eventRoom !== null);
                if (!idMatch || !timeMatch || !titleValid || !currentDate
                    || !locationValid) {
                    entry.invalidCards.push({
                        dataCy,
                        timeControls: timeControls.length,
                        displayNames: displayNames.length,
                        locationLinks: locationLinks.length,
                        timeValid: Boolean(timeMatch),
                        titleValid,
                        dateValid: Boolean(currentDate),
                        locationValid,
                    });
                    continue;
                }

                const eventId = Number(idMatch[1]);
                entry.eventIds.push(eventId);
                if (allowedDateSet.has(localDate)) {
                    events.push({
                        date: localDate,
                        daysDiff,
                        startTime: timeMatch[1],
                        endTime: timeMatch[2],
                        title: eventTitle,
                        isReservation,
                        room: eventRoom,
                        eventId,
                    });
                }
            }
            agendaDayStructure.push(entry);
        }

        return {events, agendaDayStructure};
    }""", {
        "configuredRooms": LIVE_CATALOG_ROOM_NAMES,
        "allowedDates": [value.isoformat() for value in window_dates],
    })

    if not isinstance(agenda_dom, dict) or not isinstance(agenda_dom.get("events"), list):
        raise RuntimeError("Agenda event snapshot could not be verified safely")
    events_data = agenda_dom["events"]
    _assert_complete_agenda_extraction(agenda_dom, window_dates, events_data)
    tracker.agenda_active_event_ids = [
        event_id
        for entry in agenda_dom.get("agendaDayStructure", [])
        if isinstance(entry, dict)
        for event_id in entry.get("activeEventIds", [])
        if type(event_id) is int and event_id > 0
    ]
    unknown_reservation_rooms = [
        event
        for event in events_data
        if isinstance(event, dict)
        and event.get("isReservation") is True
        and event.get("room") not in LIVE_CATALOG_ROOM_NAMES
    ]
    if unknown_reservation_rooms:
        raise RuntimeError(
            "Agenda contained a reservation whose room was not one exact live "
            "catalog name; agenda state cannot be trusted safely"
        )
    print(f"  [DEBUG] Raw events extracted: {len(events_data)}")

    # Remove only exact logical duplicates. Events sharing a time remain
    # distinct when title, room, or reservation type differs.
    unique_events = deduplicate_events(events_data)
    tracker.agenda_events = [dict(event) for event in unique_events]
    if snapshot_path is not None:
        try:
            publish_agenda_snapshot(
                unique_events,
                window_dates,
                observed_at=datetime.now().astimezone(),
                path=Path(snapshot_path),
            )
        except AgendaSnapshotError as exc:
            # This artifact is display-only and never booking authority. Keep
            # the authenticated run safe and leave the previous snapshot intact.
            print(f"  [WARN] Calendar snapshot was not refreshed: {exc}")

    print(f"\n  Found {len(unique_events)} unique events in agenda (deduplicated from {len(events_data)}):")

    # Load ignored events from settings
    if ignored_events is None:
        ignored_events = load_ignored_events()
    ignore_resolution = resolve_ignored_event_keys(ignored_events, unique_events)
    effective_ignored_events = ignore_resolution.ignored_v2_keys
    if ignored_events:
        print(
            f"  ({len(effective_ignored_events)} event(s) safely resolved as ignored "
            "- will allow booking over them)"
        )
    if ignore_resolution.ambiguous_legacy_keys:
        print(
            "  [SAFE] Older time-only ignore selection matched multiple events; "
            "none of those events will be ignored until reviewed in the GUI"
        )

    events_found = 0
    reservations_found = 0
    ignored_count = 0
    all_reservations = []  # Track all reservations for smart swap feature
    for event in unique_events:
        date_str = event['date']
        start_time = event['startTime']
        end_time = event['endTime']
        days_diff = event['daysDiff']
        is_reservation = event.get('isReservation', False)
        room = event.get('room')  # Room name if available (e.g., "B0.27")

        # Check if this event is ignored
        event_key = event_identity_v2(event)
        if event_key in effective_ignored_events:
            if is_reservation:
                # Ignoring a reservation permits overlap, but cannot erase it
                # from quota, daily-target, peak, or same-room-gap accounting.
                start_parts = start_time.split(':')
                end_parts = end_time.split(':')
                start_hour = int(start_parts[0]) + int(start_parts[1]) / 60
                end_hour = int(end_parts[0]) + int(end_parts[1]) / 60
                target_date = datetime.combine(
                    today + timedelta(days=days_diff), datetime.min.time()
                )
                tracker.add_existing_event(
                    target_date,
                    start_hour,
                    end_hour,
                    is_reservation=True,
                    room=room,
                    blocks_conflict=False,
                )
                all_reservations.append({
                    'date': date_str,
                    'startTime': start_time,
                    'endTime': end_time,
                    'room': room,
                    'daysDiff': days_diff,
                    'eventId': event.get('eventId'),
                })
            ignored_count += 1
            day_name = (today + timedelta(days=days_diff)).strftime("%A")
            room_info = f" in {room}" if room else ""
            print(f"  [IGNORED] {date_str} ({day_name}): {start_time} - {end_time}{room_info}")
            continue

        # Parse times to hours
        start_parts = start_time.split(':')
        end_parts = end_time.split(':')
        start_hour = int(start_parts[0]) + int(start_parts[1]) / 60
        end_hour = int(end_parts[0]) + int(end_parts[1]) / 60

        # Calculate the target date
        target_date = datetime.combine(today + timedelta(days=days_diff), datetime.min.time())

        # Add to conflict ranges (reservations also track peak hours and room for same-room gap)
        tracker.add_existing_event(target_date, start_hour, end_hour, is_reservation=is_reservation, room=room)
        events_found += 1
        if is_reservation:
            reservations_found += 1
            # Store reservation details for smart swap feature
            all_reservations.append({
                'date': date_str,
                'startTime': start_time,
                'endTime': end_time,
                'room': room,
                'daysDiff': days_diff,
                'eventId': event.get('eventId'),
            })

        day_name = target_date.strftime("%A")
        event_marker = "[R]" if is_reservation else "   "
        room_info = f" in {room}" if room else ""
        print(f"  {event_marker} {date_str} ({day_name}): {start_time} - {end_time}{room_info}")

    ignored_str = f", {ignored_count} ignored" if ignored_count > 0 else ""
    print(f"\n  Total events: {events_found} ({reservations_found} reservations, {events_found - reservations_found} other{ignored_str})")

    # Show rolling quota status (28 hours per week)
    used_hours = tracker.get_total_booking_hours()
    remaining_hours = tracker.get_remaining_quota_hours()
    print(f"\n  Rolling quota: {used_hours:.1f}/{MAX_ROLLING_QUOTA_HOURS} hours this week")
    if remaining_hours > 0:
        print(f"  [OK] Can book {remaining_hours:.1f} more hours")
    else:
        print(f"  [FULL] QUOTA FULL - cannot make new bookings until existing ones expire!")

    # Show peak hours summary per day
    if tracker.peak_hours_by_day:
        print("  Peak hours from existing reservations:")
        for date_key, mins in sorted(tracker.peak_hours_by_day.items()):
            print(f"    {date_key}: {mins:.0f}/{MAX_PEAK_HOURS * 60} min")
    print("="*60)
    return events_found, all_reservations


def send_notification(title, message, priority="default"):
    """Send a push notification via ntfy.sh."""
    if not NTFY_ENABLED:
        return

    try:
        url = f"https://ntfy.sh/{NTFY_TOPIC}"
        data = message.encode('utf-8')

        req = urllib.request.Request(url, data=data, method='POST')
        req.add_header('Title', title)
        req.add_header('Priority', priority)
        req.add_header('Tags', 'musical_keyboard,calendar')

        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                print(f"Notification sent: {title}")
            else:
                print(f"Notification failed: {response.status}")
    except urllib.error.URLError as e:
        print(f"Notification error (network): {e}")
    except Exception as e:
        print(f"Notification error: {e}")


def format_booking_notification_detail(detail):
    """Render one history detail while preserving exact multiword room names."""

    parts = detail.split()
    if len(parts) < 5:
        return detail

    try:
        date_obj = datetime.strptime(parts[0], "%Y-%m-%d")
        room = " ".join(parts[1:-3])
        start_time, end_time = parts[-3:-1]
        duration_mins = int(parts[-1])
        if not room:
            return detail

        if duration_mins == 60:
            duration_str = "1 hour"
        elif duration_mins < 60:
            duration_str = f"{duration_mins} minutes"
        elif duration_mins % 60 == 0:
            duration_str = f"{duration_mins // 60} hours"
        else:
            hours, mins = divmod(duration_mins, 60)
            duration_str = (
                f"{hours} hour{'s' if hours > 1 else ''} and {mins} minutes"
            )

        return (
            f"{date_obj.strftime('%a')} {date_obj.day} {date_obj.strftime('%b')}: "
            f"{room} {start_time}-{end_time} ({duration_str})"
        )
    except (TypeError, ValueError):
        return detail


def save_history(
    bookings_made,
    events_detected,
    booking_details,
    *,
    notify=True,
    outcome="completed",
):
    """Save run to booking history file."""
    try:
        if outcome not in {"completed", "reconciliation_required", "failed"}:
            raise ValueError(f"Unsupported booking history outcome: {outcome!r}")
        entry = {
            "timestamp": datetime.now().isoformat(),
            "outcome": outcome,
            "bookings_made": bookings_made,
            "events_detected": events_detected,
            "details": "; ".join(booking_details[:5]) if booking_details else "No bookings made"
        }

        history_lock = history_file.with_suffix(history_file.suffix + ".lock")
        with InterProcessFileLock(history_lock):
            history = {"runs": []}
            if history_file.exists():
                with open(history_file, "r", encoding="utf-8") as handle:
                    history = json.load(handle)
                if not isinstance(history, dict) or not isinstance(history.get("runs"), list):
                    raise ValueError("booking history must contain a runs list")

            history["runs"].insert(0, entry)
            history["runs"] = history["runs"][:100]  # Keep last 100
            atomic_write_json(history_file, history, backup=True)

        print(f"History saved: {bookings_made} bookings, {events_detected} events")

        # Send push notification
        if not notify:
            return
        if bookings_made > 0:
            title = f"Booked {bookings_made} room{'s' if bookings_made > 1 else ''}"
            # Parse the stable suffix from the right so exact site room names may
            # contain spaces (for example, "The Hopkins Studio").
            formatted = [
                format_booking_notification_detail(detail)
                for detail in booking_details[:5]
            ]
            message = "\n".join(formatted)
            if len(booking_details) > 5:
                message += f"\n+{len(booking_details) - 5} more"
            message += f"\n{MANUAL_RECONFIRMATION_REMINDER}"
            send_notification(title, message, priority="default")
        else:
            title = "AsimutBooker ran"
            message = f"No new bookings. {events_detected} existing events detected."
            send_notification(title, message, priority="low")

    except Exception as e:
        print(f"Warning: Could not save history: {e}")


def extension_processing_sort_key(booking, *, now=None):
    """Order due extensions first, then imminent edges, then future work."""

    now = now or datetime.now()
    start_h, start_m = map(int, booking["startTime"].split(":"))
    end_h, end_m = map(int, booking["endTime"].split(":"))
    target_h, target_m = map(int, booking["target_end"].split(":"))
    plan = plan_horizon_extension(
        booking["room"],
        booking["date"],
        start_h + start_m / 60,
        end_h + end_m / 60,
        target_h + target_m / 60,
        now=now,
    )
    if plan.can_extend:
        timing_class = 0
        timing_value = now
    elif (
        plan.wait_seconds is not None
        and 0 < plan.wait_seconds <= EXTENSION_PREP_WINDOW_SECONDS
    ):
        timing_class = 1
        timing_value = plan.next_unlock_at or now
    else:
        timing_class = 2
        timing_value = plan.next_unlock_at or datetime.max
    try:
        room_priority = PRIORITY_ROOMS.index(booking["room"])
    except ValueError:
        room_priority = len(PRIORITY_ROOMS)
    return (
        timing_class,
        timing_value,
        room_priority,
        booking["date"],
        booking["startTime"],
    )


def calculate_extension_capacity_holds(
    extendable_bookings,
    tracker,
    practice_plan,
    disabled_dates,
    *,
    time_prefs=None,
    now=None,
):
    """Reserve the exact remaining capacity of verified extension targets.

    A horizon create is only useful when its later 15-minute extensions retain
    enough daily-target, peak, and rolling-week capacity. These holds are local
    planning constraints, never mutation authority; every extension still
    revalidates its tracked event and live form before Save.
    """

    now = now or datetime.now()
    live_dates = {value.isoformat() for value in booking_window_dates(now.date())}
    remaining_weekly = max(
        0,
        int(tracker.get_remaining_quota_hours() * 4 + 1e-9) * 15,
    )
    target_by_date = {}
    peak_by_date = {}
    held_bookings = []

    def clock_minutes(value):
        hour, minute = map(int, value.split(":"))
        return hour * 60 + minute

    def cap_end_for_peak(target_date, start, end, remaining_peak):
        if target_date.weekday() >= 5 or end <= int(PEAK_START * 60) or start >= int(PEAK_END * 60):
            return end
        peak_start = int(PEAK_START * 60)
        peak_end = int(PEAK_END * 60)
        remaining_peak = max(0, int(remaining_peak // 15) * 15)
        if start < peak_start:
            if remaining_peak >= peak_end - peak_start:
                return end
            return min(end, peak_start + remaining_peak)
        if remaining_peak >= peak_end - start:
            return end
        return min(end, start + remaining_peak)

    strict_time_prefs = time_prefs or {
        "enabled": False,
        "strict_mode": False,
        "start_hour": 0.0,
        "end_hour": 24.0,
    }
    ordered = sorted(
        extendable_bookings,
        key=lambda booking: extension_processing_sort_key(booking, now=now),
    )
    for booking in ordered:
        date_key = booking["date"]
        if (
            booking["room"] not in PRIORITY_ROOMS
            or date_key not in live_dates
            or is_date_disabled(date.fromisoformat(date_key), disabled_dates)
        ):
            continue
        target_date = date.fromisoformat(date_key)
        start_time = clock_minutes(booking["startTime"])
        current_end = clock_minutes(booking["endTime"])
        target_end = clock_minutes(booking["target_end"])
        start_hour = start_time / 60
        current_end_hour = current_end / 60
        if not interval_is_strictly_preferred(
            start_hour,
            current_end_hour,
            strict_time_prefs,
        ):
            continue
        if strict_time_prefs["enabled"] and strict_time_prefs["strict_mode"]:
            target_end = min(
                target_end,
                int(round(strict_time_prefs["end_hour"] * 60)),
            )

        # An extension is contiguous. The first non-own conflict is therefore
        # a hard end, even when it appeared after the horizon create.
        for conflict_start, conflict_end in tracker.conflict_ranges.get(
            date_key, ()
        ):
            conflict_start_minutes = int(round(conflict_start * 60))
            conflict_end_minutes = int(round(conflict_end * 60))
            is_own_booking = (
                abs(conflict_start_minutes - start_time) <= 1
                and conflict_end_minutes <= current_end + 1
            )
            if is_own_booking or conflict_end_minutes <= current_end:
                continue
            if conflict_start_minutes <= current_end:
                target_end = current_end
                break
            target_end = min(target_end, conflict_start_minutes)

        # Keep the fresh same-room gap before every other reservation or
        # booking. Generic conflict clipping above prevents overlap; this
        # additional cap preserves the required clear interval before it.
        future_same_room_starts = []
        for other_start, _other_end, other_room in tracker.reservation_ranges.get(
            date_key, ()
        ):
            other_start_minutes = int(round(other_start * 60))
            if (
                other_room == booking["room"]
                and abs(other_start_minutes - start_time) > 1
                and other_start_minutes > current_end
            ):
                future_same_room_starts.append(other_start_minutes)
        for other_room, other_date, other_start, _other_end in tracker.bookings:
            other_start_minutes = int(round(other_start * 60))
            if (
                other_room == booking["room"]
                and as_date(other_date).isoformat() == date_key
                and abs(other_start_minutes - start_time) > 1
                and other_start_minutes > current_end
            ):
                future_same_room_starts.append(other_start_minutes)
        if future_same_room_starts:
            target_end = min(
                target_end,
                min(future_same_room_starts) - SAME_ROOM_GAP_MINUTES,
            )

        remaining = max(0, target_end - current_end)
        if remaining < 15 or remaining_weekly < 15:
            continue

        daily_remaining = remaining_target_hours(
            practice_plan,
            target_date,
            tracker.get_hours_for_day(target_date),
        )
        if daily_remaining is not None:
            daily_capacity = max(
                0,
                int(daily_remaining * 4 + 1e-9) * 15
                - target_by_date.get(date_key, 0),
            )
            remaining = min(remaining, daily_capacity)
        remaining = min(remaining, remaining_weekly)
        remaining -= remaining % 15
        if remaining < 15:
            continue

        remaining_peak = max(
            0,
            int(tracker.get_remaining_peak_minutes(target_date))
            - peak_by_date.get(date_key, 0),
        )
        held_end = cap_end_for_peak(
            target_date,
            current_end,
            current_end + remaining,
            remaining_peak,
        )
        held_minutes = max(0, held_end - current_end)
        held_minutes -= held_minutes % 15
        if held_minutes < 15:
            continue
        held_peak = (
            interval_overlap_minutes(
                current_end,
                current_end + held_minutes,
                int(PEAK_START * 60),
                int(PEAK_END * 60),
            )
            if target_date.weekday() < 5
            else 0
        )
        target_by_date[date_key] = target_by_date.get(date_key, 0) + held_minutes
        peak_by_date[date_key] = peak_by_date.get(date_key, 0) + held_peak
        remaining_weekly -= held_minutes
        planned_booking = dict(booking)
        planned_end = current_end + held_minutes
        planned_booking["target_end"] = (
            f"{planned_end // 60:02d}:{planned_end % 60:02d}"
        )
        held_bookings.append(planned_booking)

    return target_by_date, peak_by_date, tuple(held_bookings)


def refresh_extension_capacity_holds(
    planning_context,
    tracker,
    practice_plan,
    disabled_dates,
    *,
    settings=None,
    time_prefs=None,
    now=None,
):
    """Replace run-scoped extension holds from the latest validated state."""

    if not isinstance(planning_context, dict):
        raise TypeError("planning_context must be a dictionary")
    source_settings = (
        load_settings_document(settings_file) if settings is None else settings
    )
    bookings = load_extendable_bookings(source_settings)
    target_by_date, peak_by_date, held_bookings = calculate_extension_capacity_holds(
        bookings,
        tracker,
        practice_plan,
        disabled_dates,
        time_prefs=time_prefs,
        now=now,
    )
    planning_context["extension_target_by_date"] = target_by_date
    planning_context["extension_peak_by_date"] = peak_by_date
    planning_context["extension_bookings"] = held_bookings
    return target_by_date, peak_by_date, held_bookings


def process_pending_extensions(
    page,
    tracker,
    all_reservations,
    practice_plan,
    disabled_dates,
    time_prefs,
    args,
    total_booked,
    booking_details,
):
    """Process verified horizon extensions before any unrelated create."""

    extendable_bookings = load_extendable_bookings(
        load_settings_document(settings_file)
    )
    if not extendable_bookings:
        return total_booked, False

    only_date = getattr(args, "only_date", None)
    only_room = getattr(args, "only_room", None)
    live_window_date_strings = {
        value.isoformat() for value in booking_window_dates()
    }
    eligible_bookings = [
        booking
        for booking in extendable_bookings
        if booking["room"] in PRIORITY_ROOMS
        and booking["date"] in live_window_date_strings
        and (not only_date or booking["date"] == only_date)
        and (not only_room or booking["room"] == only_room)
    ]
    if not eligible_bookings:
        return total_booked, False

    ordering_now = datetime.now()
    eligible_bookings.sort(
        key=lambda booking: extension_processing_sort_key(
            booking,
            now=ordering_now,
        )
    )

    print("\n" + "=" * 60)
    print("PROCESSING BOOKING EXTENSIONS (PRIORITY PHASE)")
    print("=" * 60)
    print(f"Found {len(eligible_bookings)} scoped extendable booking(s)")

    extensions_made = 0
    for booking in eligible_bookings:
        max_actions = getattr(args, "max_actions", None)
        if max_actions is not None and total_booked >= max_actions:
            print("  Controlled action limit reached; ending extension phase")
            break
        booking_date = datetime.strptime(booking["date"], "%Y-%m-%d").date()
        if is_date_disabled(booking_date, disabled_dates):
            print(f"  Skipped {booking['room']} {booking['date']}: date is disabled")
            continue
        daily_remaining = remaining_target_hours(
            practice_plan,
            booking_date,
            tracker.get_hours_for_day(booking_date),
        )
        success, new_end, message = try_extend_booking(
            page,
            booking,
            tracker,
            remaining_daily_hours=daily_remaining,
            time_prefs=time_prefs,
            max_action_minutes=getattr(args, "max_action_minutes", None),
            agenda_reservations=all_reservations,
        )
        if success:
            extensions_made += 1
            total_booked += 1
            booking_details.append(
                f"EXTENDED: {booking['room']} {booking['date']} "
                f"{booking['startTime']}-{new_end}"
            )
            event_id = booking.get("eventId")
            matching_agenda_entries = [
                reservation
                for reservation in all_reservations
                if reservation.get("eventId") == event_id
            ]
            if event_id and len(matching_agenda_entries) == 1:
                matching_agenda_entries[0]["endTime"] = new_end
        else:
            print(
                f"  Skipped {booking['room']} {booking['date']} "
                f"{booking['startTime']}: {message}"
            )

    if extensions_made > 0:
        print(f"\nExtended {extensions_made} booking(s)")
    print("=" * 60)

    # Leave the page untouched after a verified mutation. The caller may be an
    # extension-only or action-capped run and must be able to return success
    # without risking a post-confirmation overview navigation failure.
    return total_booked, True


def validate_live_scope(args, policy, *, today=None):
    """Validate CLI room/date limits only after current site policy is known."""

    today = today or datetime.now().date()
    live_dates = set(policy.booking_dates(today))
    if _cancellation_requested(args):
        selected_date = datetime.strptime(args.cancel_date, "%Y-%m-%d").date()
        if selected_date not in live_dates:
            first = min(live_dates).isoformat()
            last = max(live_dates).isoformat()
            raise LiveRoomPolicyError(
                f"--cancel-date must be inside Asimut's current {first} to "
                f"{last} window"
            )
    if getattr(args, "only_room", None) and args.only_room not in policy.room_order:
        raise LiveRoomPolicyError(
            f"--only-room is not eligible in the current live room policy: {args.only_room}"
        )
    if getattr(args, "only_date", None):
        selected_date = datetime.strptime(args.only_date, "%Y-%m-%d").date()
        if selected_date not in live_dates:
            first = min(live_dates).isoformat()
            last = max(live_dates).isoformat()
            raise LiveRoomPolicyError(
                f"--only-date must be inside Asimut's current {first} to {last} window"
            )
    max_action_minutes = getattr(args, "max_action_minutes", None)
    if (
        max_action_minutes is not None
        and max_action_minutes < policy.minimum_block_minutes
        and not getattr(args, "extensions_only", False)
    ):
        raise LiveRoomPolicyError(
            f"--max-action-minutes is below the selected "
            f"{policy.minimum_block_minutes}-minute block minimum"
        )


def generate_read_only_booking_plan(
    page,
    policy,
    settings,
    practice_plan,
    tracker,
    time_prefs,
    daily_planning,
    disabled_dates,
    args,
    *,
    today,
    reverse_date_order=False,
    planning_context=None,
):
    """Scan fresh grids and publish a mutation-free, display-only day plan."""

    print("\nGENERATING READ-ONLY BOOKING PLAN")
    open_practice_room_overview(page, today)
    current_calendar_day = 0
    now = datetime.now()
    live_dates = booking_window_dates(today)
    planning_dates = tuple(reversed(live_dates)) if reverse_date_order else live_dates
    enabled_dates = [
        value for value in live_dates if not is_date_disabled(value, disabled_dates)
    ]
    planning_context = planning_context or {
        "extension_target_by_date": {},
        "extension_peak_by_date": {},
        "extension_bookings": (),
    }
    extension_target_by_date = planning_context.get(
        "extension_target_by_date", {}
    )
    extension_peak_by_date = planning_context.get("extension_peak_by_date", {})
    total_extension_minutes = sum(extension_target_by_date.values())
    remaining_weekly_minutes = max(
        0,
        int(tracker.get_remaining_quota_hours() * 4 + 1e-9) * 15,
    )
    planned_weekly_minutes = 0
    day_plans = []
    for target_date in planning_dates:
        days_ahead = (target_date - today).days
        if args.only_date and target_date.isoformat() != args.only_date:
            continue
        existing_minutes = int(
            round(tracker.get_hours_for_day(target_date) * 60 / 15)
        ) * 15
        peak_limit = int(MAX_PEAK_HOURS * 60) if target_date.weekday() < 5 else 0
        peak_used = (
            int(round(tracker.get_peak_used_for_day(target_date) / 15)) * 15
            if peak_limit
            else 0
        )
        if is_date_disabled(target_date, disabled_dates):
            day_plans.append(
                DayPlan(
                    date=target_date.isoformat(),
                    target_minutes=existing_minutes,
                    existing_minutes=existing_minutes,
                    peak_used_minutes=min(peak_limit, peak_used),
                    peak_limit_minutes=peak_limit,
                    status="skipped",
                    primary=None,
                    additional=(),
                    backups=(),
                    held_peak_minutes=0,
                    reason="This date is disabled in Booking Days",
                )
            )
            continue
        daily_remaining = remaining_target_hours(
            practice_plan,
            target_date,
            tracker.get_hours_for_day(target_date),
        )
        extension_target_minutes = extension_target_by_date.get(
            target_date.isoformat(), 0
        )
        extension_peak_minutes = extension_peak_by_date.get(
            target_date.isoformat(), 0
        )
        if practice_plan.enabled:
            target_minutes = int(
                round((practice_plan.target_for(target_date) or 0) * 60)
            )
        else:
            additional_hours, _ = calculate_target_hours_for_day(
                target_date,
                enabled_dates,
                tracker,
                practice_plan=practice_plan,
            )
            target_minutes = (
                existing_minutes + int(additional_hours * 4 + 1e-9) * 15
            )
            daily_remaining = additional_hours
        target_minutes = max(
            target_minutes,
            existing_minutes + extension_target_minutes,
        )
        effective_daily_remaining = (
            None
            if daily_remaining is None
            else max(0.0, daily_remaining - extension_target_minutes / 60)
        )
        fragmentation_ok, fragmentation_reason = fragmentation_allows_new_booking(
            tracker,
            target_date,
        )
        if not fragmentation_ok:
            day_plan = DayPlan(
                date=target_date.isoformat(),
                target_minutes=min(1440, max(existing_minutes, target_minutes)),
                existing_minutes=min(1440, existing_minutes),
                peak_used_minutes=min(peak_limit, peak_used),
                peak_limit_minutes=peak_limit,
                status="complete" if existing_minutes else "skipped",
                primary=None,
                additional=(),
                backups=(),
                held_peak_minutes=0,
                reason=fragmentation_reason,
            )
            day_plans.append(
                attach_extension_progress(
                    day_plan,
                    planning_context,
                    now=now,
                )
            )
            continue
        if current_calendar_day != days_ahead:
            current_calendar_day = navigate_to_day(
                page,
                days_ahead,
                current_calendar_day,
                base_date=today,
            )
        wait_for_practice_room_grid(page, target_date)
        available_data = get_available_slots(page)
        opportunity_planning = (
            daily_planning
            if daily_planning.enabled
            else replace(
                daily_planning,
                desired_peak_block_minutes=int(MAX_BOOKING_HOURS * 60),
                after_peak_mode="longest_first",
            )
        )
        opportunities = build_day_booking_opportunities(
            available_data,
            target_date,
            tracker,
            time_prefs,
            opportunity_planning,
            now=now,
            remaining_daily_hours=effective_daily_remaining,
            reserved_peak_minutes=extension_peak_minutes,
            reserved_weekly_minutes=(
                total_extension_minutes + planned_weekly_minutes
            ),
            only_room=args.only_room,
        )
        if daily_planning.enabled:
            latest_useful_unlock = now + timedelta(
                minutes=daily_planning.foresight_minutes
            )
            opportunities = [
                item
                for item in opportunities
                if item.unlock_at <= now or item.unlock_at <= latest_useful_unlock
            ]
        available_weekly_for_new = max(
            0,
            remaining_weekly_minutes
            - total_extension_minutes
            - planned_weekly_minutes,
        )
        if daily_planning.enabled:
            day_plan = build_display_day_plan(
                target_date,
                opportunities,
                tracker,
                daily_planning,
                now=now,
                target_minutes=target_minutes,
                remaining_weekly_minutes=available_weekly_for_new,
            )
        else:
            day_plan = build_legacy_display_day_plan(
                target_date,
                opportunities,
                tracker,
                opportunity_planning,
                time_prefs,
                now=now,
                target_minutes=target_minutes,
                remaining_weekly_minutes=available_weekly_for_new,
            )
        newly_selected = (() if day_plan.primary is None else (day_plan.primary,)) + day_plan.additional
        planned_weekly_minutes += sum(
            candidate.potential_minutes - candidate.confirmed_minutes
            for candidate in newly_selected
        )

        day_plan = attach_extension_progress(
            day_plan,
            planning_context,
            now=now,
        )
        day_plans.append(day_plan)

    primaries = [day.primary for day in day_plans if day.primary is not None]
    if primaries:
        next_candidate = min(primaries, key=lambda item: item.unlock_at)
        summary = (
            f"Next: {next_candidate.date} {next_candidate.start_time}-"
            f"{next_candidate.end_time} in {next_candidate.room}"
        )
        status = "active"
    elif day_plans and all(
        day.status in {"complete", "skipped"} for day in day_plans
    ):
        summary = "All enabled daily targets are complete or unavailable"
        status = "complete"
    else:
        summary = (
            "No suitable potential booking is visible in the current "
            "foresight window"
        )
        status = "idle"
    snapshot = publish_booking_plan(
        sorted(day_plans, key=lambda day: day.date),
        policy,
        settings,
        summary=summary,
        status=status,
        now=now,
    )
    print(f"PLAN READY: {summary}")
    return snapshot


def run_booking(args, settings, practice_plan, room_preferences=None):
    """Run one authenticated booking pass with already-validated inputs."""
    today = datetime.now().date()
    room_preferences = room_preferences or load_room_preferences(settings)

    print("="*60)
    print("ASIMUT WEEK BOOKER")
    print("="*60)
    print(f"Today: {today}")
    print("Booking window: refreshing from Asimut after authentication")
    print(f"Mode: {'Headless' if args.headless else 'Visible browser'}")
    print("="*60)

    tracker = BookingTracker()
    total_booked = 0
    events_detected = 0
    booking_details = []

    # Fail before Chromium starts when neither the primary saved state nor the
    # explicitly configured autonomous fallback is available.
    context_options = authenticated_runtime_context_options()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(**context_options)
        page = context.new_page()

        # Reuse the saved state in the common case. If Microsoft has expired it,
        # recover before any agenda scan or booking mutation is attempted.
        restore_page_authentication(page)

        print("\nREFRESHING LIVE ROOM POLICY (READ ONLY)")
        policy = refresh_live_room_policy(page, room_preferences, today=today)
        validate_live_scope(args, policy, today=today)
        live_dates = booking_window_dates(today)
        print(f"Booking for: {live_dates[0]} to {live_dates[-1]}")

        # First, scan the agenda to learn about existing events
        ignored_events = load_ignored_events(settings)
        events_detected, all_reservations = scan_agenda(
            page,
            tracker,
            today,
            ignored_events=ignored_events,
            window_dates=live_dates,
            snapshot_path=AGENDA_SNAPSHOT_FILE,
        )
        reconcile_pending_mutation_receipts(
            page,
            _agenda_records_for_mutation_proof(tracker),
        )

        if _cancellation_requested(args):
            try:
                cancelled, events_detected = cancel_reservation_exact(
                    page,
                    all_reservations,
                    event_id=args.cancel_event_id,
                    room=args.cancel_room,
                    date_str=args.cancel_date,
                    start_time=args.cancel_start,
                    end_time=args.cancel_end,
                    today=today,
                    live_dates=live_dates,
                    ignored_events=ignored_events,
                )
            except BookingCancellationError as exc:
                print(f"CANCELLATION NOT APPLIED: {exc}")
                persist_storage_state(context)
                context.close()
                browser.close()
                return 7

            persist_storage_state(context)
            context.close()
            browser.close()
            return 0 if cancelled else 7

        if args.check_only:
            print(
                f"\nChecking all {len(live_dates)} live-window practice-room "
                "calendar days (read only)..."
            )
            open_practice_room_overview(page, today)
            current_calendar_day = 0
            seen_configured_rooms = set()
            total_slot_count = 0
            daily_summaries = []
            for days_ahead in booking_window_day_offsets(today):
                target_date = today + timedelta(days=days_ahead)
                if days_ahead:
                    current_calendar_day = navigate_to_day(
                        page,
                        days_ahead,
                        current_calendar_day,
                        base_date=today,
                    )
                wait_for_practice_room_grid(page, target_date)

                row_names = get_practice_room_names(page)
                if not row_names:
                    raise RuntimeError(
                        f"Practice-room grid for {target_date.isoformat()} "
                        "loaded without any readable room rows"
                    )
                configured_on_day = row_names.intersection(PRIORITY_ROOMS)
                if not configured_on_day:
                    raise RuntimeError(
                        f"Practice-room grid for {target_date.isoformat()} did not "
                        "contain any configured room"
                    )
                seen_configured_rooms.update(configured_on_day)

                available_data = get_available_slots(page)
                slot_count = sum(
                    len(room.get("slots", [])) for room in available_data
                )
                total_slot_count += slot_count
                daily_summaries.append(
                    f"{target_date.isoformat()}: {len(row_names)} rooms, "
                    f"{slot_count} visible gaps"
                )

            for summary in daily_summaries:
                print(f"  {summary}")
            missing_rooms = [
                room for room in PRIORITY_ROOMS if room not in seen_configured_rooms
            ]
            if missing_rooms:
                print(
                    "  WARNING: configured rooms not visible in the current "
                    "all-locations grid: "
                    + ", ".join(missing_rooms)
                )
            persist_storage_state(context)
            print(
                "CHECK PASSED: authenticated session, complete agenda, date "
                f"navigation, and {len(live_dates)} room grids are usable "
                f"({len(seen_configured_rooms)} configured rooms, "
                f"{total_slot_count} visible gaps)."
            )
            context.close()
            browser.close()
            return 0

        # Load all user planning policy before deciding whether this is a
        # mutation-capable run or a read-only plan refresh.
        disabled_dates = load_disabled_dates(settings)
        time_prefs = load_time_preferences(settings)
        booking_strategy = load_booking_strategy(settings)
        reverse_date_order = booking_strategy["reverse_date_order"]
        daily_planning = booking_strategy.get(
            "daily_planning",
            DailyPlanningPreferences(),
        )
        if not isinstance(daily_planning, DailyPlanningPreferences):
            # Integration tests may replace the legacy loader with a minimal
            # dictionary. Production settings use the shared immutable schema;
            # an incomplete injected value cannot silently enable new logic.
            daily_planning = DailyPlanningPreferences(enabled=False)
        planning_context = {
            "held_peak_by_date": {},
            "held_target_by_date": {},
            "extension_peak_by_date": {},
            "extension_target_by_date": {},
            "extension_bookings": (),
            "display_days": {},
        }
        refresh_extension_capacity_holds(
            planning_context,
            tracker,
            practice_plan,
            disabled_dates,
            time_prefs=time_prefs,
        )

        if getattr(args, "plan_only", False):
            generate_read_only_booking_plan(
                page,
                policy,
                settings,
                practice_plan,
                tracker,
                time_prefs,
                daily_planning,
                disabled_dates,
                args,
                today=today,
                reverse_date_order=reverse_date_order,
                planning_context=planning_context,
            )
            persist_storage_state(context)
            context.close()
            browser.close()
            return 0

        # Check if we can make any bookings at all
        if tracker.is_quota_full():
            used_hours = tracker.get_total_booking_hours()
            print("\n" + "="*60)
            print("QUOTA FULL - SKIPPING ALL BOOKING ATTEMPTS")
            print("="*60)
            print(f"You have {used_hours:.1f}/{MAX_ROLLING_QUOTA_HOURS} hours booked this week.")
            print("Cannot make new bookings until existing ones expire.")
            print("="*60)

            save_history(
                0,
                events_detected,
                [f"Quota full ({used_hours:.1f}/{MAX_ROLLING_QUOTA_HOURS}h)"],
            )

            send_notification("AsimutBooker - Quota Full",
                            f"Cannot book: {used_hours:.1f}/{MAX_ROLLING_QUOTA_HOURS}h used this week")

            if not args.headless:
                print("\nKeeping browser open for 10 seconds...")
                page.wait_for_timeout(10000)

            persist_storage_state(context)
            context.close()
            browser.close()
            return 0

        # Load user policy before any booking-grid navigation. Pending horizon
        # extensions must be able to open their exact editor during the short
        # :13/:28/:43/:58 preparation lead.
        horizon_only = bool(getattr(args, "horizon_only", False))
        extensions_only = bool(getattr(args, "extensions_only", False))

        _extensions_considered = False
        if not horizon_only:
            total_booked, _extensions_considered = process_pending_extensions(
                page,
                tracker,
                all_reservations,
                practice_plan,
                disabled_dates,
                time_prefs,
                args,
                total_booked,
                booking_details,
            )
            refresh_extension_capacity_holds(
                planning_context,
                tracker,
                practice_plan,
                disabled_dates,
                time_prefs=time_prefs,
            )

        max_actions = getattr(args, "max_actions", None)
        action_limit_reached = (
            max_actions is not None and total_booked >= max_actions
        )
        if extensions_only or action_limit_reached:
            reason = (
                "extension-only scope complete"
                if extensions_only
                else "controlled action limit reached by priority extension"
            )
            print(f"\nStopping before room-grid discovery: {reason}.")
            save_history(total_booked, events_detected, booking_details)
            persist_storage_state(context)
            context.close()
            browser.close()
            return 0

        # Only continuing runs need the room grid. A verified extension above
        # can return early without this fallible post-success navigation.
        print("\nNavigating to the fresh all-locations room overview...")
        open_practice_room_overview(page, today)

        current_calendar_day = 0
        current_header_locator = page.locator("h1, h2, .title").first
        current_header = (
            current_header_locator.text_content()
            if current_header_locator.count() > 0
            else "Unknown"
        )
        print(f"  Currently showing: {current_header}")

        if disabled_dates:
            print(f"\n  Disabled dates (will skip): {sorted(disabled_dates)}")
        if time_prefs["enabled"]:
            print(f"\n  Time preferences: Prioritizing {int(time_prefs['start_hour']):02d}:00 - {int(time_prefs['end_hour']):02d}:00")
            if time_prefs["strict_mode"]:
                print(f"  Strict mode: ON (only booking preferred times)")
        if reverse_date_order:
            print(f"\n  Booking strategy: REVERSE ORDER (furthest dates first)")
        if daily_planning.enabled:
            print(
                "\n  Daily planning: prefer "
                f"{daily_planning.preferred_peak_start}-"
                f"{daily_planning.preferred_peak_end}, aim for "
                f"{daily_planning.desired_peak_block_minutes} continuous minutes, "
                f"look ahead {daily_planning.foresight_minutes} minutes"
            )

        # Build list of enabled dates for smart redistribution
        enabled_dates = []
        for d in booking_window_day_offsets(today):
            check_date = today + timedelta(days=d)
            if not is_date_disabled(check_date, disabled_dates):
                enabled_dates.append(check_date)

        remaining_quota = tracker.get_remaining_quota_hours()
        ideal_per_day = MAX_ROLLING_QUOTA_HOURS / len(enabled_dates) if enabled_dates else 0

        plan_label = "Practice plan" if practice_plan.enabled else "Smart redistribution"
        print(f"\n  {plan_label} calculation:")
        print(f"    Enabled days: {len(enabled_dates)}/{len(live_dates)}")
        print(f"    Remaining quota: {remaining_quota:.1f}h")
        if practice_plan.enabled:
            print(f"    Default daily target: {practice_plan.default_hours:.1f}h")
        else:
            print(f"    Ideal hours/day: {ideal_per_day:.1f}h")
        print(f"    Current hours per day:")
        for d in enabled_dates:
            day_hours = tracker.get_hours_for_day(d)
            target = practice_plan.target_for(d) if practice_plan.enabled else ideal_per_day
            deficit = max(0.0, target - day_hours)
            status = "OK" if deficit < 0.25 else f"needs +{deficit:.1f}h"
            print(f"      {d.strftime('%Y-%m-%d')} ({d.strftime('%a')}): {day_hours:.1f}h [{status}]")

        # Get current time for filtering today's slots
        current_time_hour = datetime.now().hour + datetime.now().minute / 60.0

        # Determine the order to process days based on strategy
        # Filter out disabled dates AND days with no bookable rooms (horizon not reached)
        enabled_day_order = []
        skipped_horizon_days = []
        for d in booking_window_day_offsets(today):
            check_date = today + timedelta(days=d)
            if is_date_disabled(check_date, disabled_dates):
                continue
            if args.only_date and check_date.isoformat() != args.only_date:
                continue
            daily_remaining = remaining_target_hours(
                practice_plan,
                check_date,
                tracker.get_hours_for_day(check_date),
            )
            if daily_remaining is not None and daily_remaining < MINIMUM_BLOCK_MINUTES / 60:
                continue
            fragmentation_ok, _ = fragmentation_allows_new_booking(
                tracker,
                check_date,
            )
            if not fragmentation_ok:
                continue
            # Check if any room is bookable on this day
            any_bookable, horizon_reason = is_any_room_bookable_on_day(check_date)
            if any_bookable:
                enabled_day_order.append(d)
            else:
                skipped_horizon_days.append((d, check_date.strftime('%Y-%m-%d'), horizon_reason))

        if skipped_horizon_days:
            print(f"\n  Days skipped (horizon not reached):")
            for d, date_str, reason in skipped_horizon_days:
                print(f"    Day {d} ({date_str}): {reason}")

        if reverse_date_order:
            # Process from furthest enabled day down to nearest (furthest first)
            day_order = sorted(enabled_day_order, reverse=True)
            if day_order and not args.target_time:
                first_day = day_order[0]
                print(f"\n  Navigating to day {first_day} first (reverse order strategy)...")
                current_calendar_day = navigate_to_day(
                    page,
                    first_day,
                    0,
                    base_date=today,
                )
            elif not day_order:
                print(f"\n  No bookable days available!")
                current_calendar_day = 0
        else:
            # Normal order: process enabled days from nearest to furthest
            day_order = sorted(enabled_day_order)
            current_calendar_day = 0  # Calendar starts on today

        # A target-aware horizon scan starts from today's verified overview.
        # Normal date ordering is applied only after the snipe phase, avoiding
        # an expensive trip to the furthest date and back before discovery.
        if args.target_time and not extensions_only:
            # =================================================================
            # HORIZON SNIPE: Check only exact site-derived room edges.
            # =================================================================
            target_boundary = target_boundary_datetime(
                args.target_time,
                base_date=today,
            )
            wait_for_horizon_discovery_window(target_boundary, page)

            print("\n" + "="*60)
            print("CHECKING FOR HORIZON SNIPE OPPORTUNITIES (MULTI-DAY)")
            print("="*60)

            # Each room's live horizon determines its exact candidate date and
            # start, so CLI scopes apply before any calendar navigation.
            all_snipe_candidates, current_calendar_day = find_all_snipe_candidates_multi_day(
                page,
                tracker,
                time_prefs,
                current_calendar_day,
                disabled_dates=disabled_dates,
                practice_plan=practice_plan,
                target_boundary=target_boundary,
                only_date=args.only_date,
                only_room=args.only_room,
                candidate_limit=1,
                daily_planning=daily_planning,
                planning_context=planning_context,
            )

            if daily_planning.enabled:
                opportunities_by_date = planning_context.get(
                    "opportunities_by_date", {}
                )
                build_horizon_display_days(
                    opportunities_by_date,
                    planning_context,
                    tracker,
                    practice_plan,
                    daily_planning,
                    now=target_boundary,
                    reverse_date_order=reverse_date_order,
                )
                if planning_context["display_days"]:
                    decision = planning_context.get("decision")
                    summary = (
                        decision.reason
                        if decision is not None
                        else "Fresh horizon opportunities evaluated"
                    )
                    publish_planning_context(
                        planning_context,
                        policy,
                        settings,
                        summary=summary,
                        status="active",
                        now=target_boundary,
                    )

            if all_snipe_candidates:
                # Navigate to the first candidate's day (highest priority)
                first_candidate = all_snipe_candidates[0]
                if current_calendar_day != first_candidate['days_ahead']:
                    print(f"\n  Positioning for first snipe...")
                    current_calendar_day = navigate_to_day(
                        page,
                        first_candidate['days_ahead'],
                        current_calendar_day,
                        base_date=today,
                    )
                    page.wait_for_timeout(500)

                print(f"\n  Ready to snipe {len(all_snipe_candidates)} candidate(s)")
                print(f"  First snipe: {first_candidate['room']} at {first_candidate['start_hour']:.2f} (Day {first_candidate['days_ahead']})")
                print(f"  Bookable at: {first_candidate['bookable_from'].strftime('%H:%M:%S')}")

                # =================================================================
                # SEQUENTIAL SNIPE PHASE
                # =================================================================
                snipes_attempted = 0
                snipes_successful = 0

                for idx, candidate in enumerate(all_snipe_candidates):
                    if args.max_actions is not None and total_booked >= args.max_actions:
                        print("  Controlled action limit reached; ending snipe phase")
                        break
                    snipes_attempted += 1
                    snipe_target_date = candidate['target_date']
                    days_ahead = candidate['days_ahead']

                    print(f"\n  [SNIPE {idx + 1}/{len(all_snipe_candidates)}] "
                          f"Attempting {candidate['room']} at {candidate['start_hour']:.2f} (Day {days_ahead})")

                    # Navigate to candidate's day if needed
                    if current_calendar_day != days_ahead:
                        print(f"    Navigating to Day {days_ahead}...")
                        current_calendar_day = navigate_to_day(
                            page,
                            days_ahead,
                            current_calendar_day,
                            base_date=today,
                        )
                        page.wait_for_timeout(500)

                    # Attempt the snipe
                    daily_remaining = remaining_target_hours(
                        practice_plan,
                        snipe_target_date,
                        tracker.get_hours_for_day(snipe_target_date),
                    )
                    snipe_success = try_horizon_snipe(
                        page,
                        candidate,
                        snipe_target_date,
                        tracker,
                        days_ahead,
                        remaining_daily_hours=daily_remaining,
                        time_prefs=time_prefs,
                        max_action_minutes=args.max_action_minutes,
                    )

                    if snipe_success:
                        snipes_successful += 1
                        total_booked += 1
                        snipe_minutes = candidate["booking_minutes"]
                        snipe_end_hour = candidate["start_hour"] + snipe_minutes / 60
                        booking_details.append(
                            f"{snipe_target_date.strftime('%Y-%m-%d')} {candidate['room']} "
                            f"{int(candidate['start_hour']):02d}:{int((candidate['start_hour'] % 1) * 60):02d} "
                            f"{int(snipe_end_hour):02d}:{int((snipe_end_hour % 1) * 60):02d} "
                            f"{snipe_minutes}"
                        )
                        record_plan_create_progress(
                            planning_context,
                            policy,
                            settings,
                            tracker,
                            candidate,
                            snipe_minutes,
                            now=datetime.now(),
                        )
                        print(f"    [SNIPE {idx + 1}] SUCCESS!")

                        max_actions = getattr(args, "max_actions", None)
                        action_limit_reached = (
                            max_actions is not None
                            and total_booked >= max_actions
                        )
                        if not horizon_only and not action_limit_reached:
                            # Only continuing work may risk cleanup navigation.
                            # A verified bounded mutation returns as success
                            # from the post-loop branch without touching it.
                            go_back(page, days_ahead)
                            page.wait_for_timeout(500)
                    else:
                        print(f"    [SNIPE {idx + 1}] FAILED, continuing to next candidate...")

                print(f"\n  Snipe phase complete: {snipes_successful}/{snipes_attempted} successful")

                # Navigate back to furthest enabled day for normal booking flow
                max_actions = getattr(args, "max_actions", None)
                action_limit_reached = (
                    max_actions is not None and total_booked >= max_actions
                )
                if (
                    not horizon_only
                    and not action_limit_reached
                    and day_order
                    and current_calendar_day != day_order[0]
                ):
                    print(f"\n  Returning to Day {day_order[0]} for normal booking flow...")
                    current_calendar_day = navigate_to_day(
                        page,
                        day_order[0],
                        current_calendar_day,
                        base_date=today,
                    )
                    page.wait_for_timeout(500)
            else:
                print(f"  No snipe candidates found (no slots becoming bookable within 3 minutes)")

            print("="*60)

            max_actions = getattr(args, "max_actions", None)
            action_limit_reached = (
                max_actions is not None and total_booked >= max_actions
            )
            if horizon_only or action_limit_reached:
                reason = (
                    "horizon-only scope complete"
                    if horizon_only
                    else "controlled action limit reached by horizon snipe"
                )
                print(f"\nStopping before normal room scanning: {reason}.")
                save_history(total_booked, events_detected, booking_details)
                persist_storage_state(context)
                context.close()
                browser.close()
                return 0

            # Now wait for remaining time (if any) and refresh for normal booking
            wait_until_target_time(args.target_time, page)

            # =================================================================
            # REFRESH PAGE FOR UPDATED SLOT AVAILABILITY
            # =================================================================
            print("\n" + "="*60)
            print("REFRESHING PAGE FOR UPDATED SLOT AVAILABILITY")
            print("="*60)
            pre_refresh_time = datetime.now()
            print(f"  [DEBUG] Pre-refresh time: {pre_refresh_time.strftime('%H:%M:%S.%f')[:-3]}")
            print(f"  [DEBUG] Current URL: {page.url[:80]}...")

            # Store current calendar day position before refresh
            pre_refresh_calendar_day = current_calendar_day
            print(f"  [DEBUG] Calendar position before refresh: day {pre_refresh_calendar_day}")

            # Refresh the current page and wait for its complete renderer. The
            # SVG view exposes labels before event overlays, so a fixed delay is
            # not sufficient evidence that availability is safe to scan.
            refresh_practice_room_overview(
                page,
                today + timedelta(days=current_calendar_day),
                base_date=today,
            )

            post_refresh_time = datetime.now()
            refresh_duration = (post_refresh_time - pre_refresh_time).total_seconds()
            print(f"  [DEBUG] Post-refresh time: {post_refresh_time.strftime('%H:%M:%S.%f')[:-3]}")
            print(f"  [DEBUG] Refresh took: {refresh_duration:.2f}s")
            print(f"  [DEBUG] Post-refresh URL: {page.url[:80]}...")

            # Verify we're still on the correct day after refresh
            print(f"  [DEBUG] Calendar position after refresh: day {current_calendar_day} (unchanged)")

            # Debug: Sample what slots look like now
            try:
                sample_slots = page.evaluate("""() => {
                    const locationDays = document.querySelectorAll('.location-day');
                    if (!locationDays.length) return 'No location-day elements found';

                    // Find first room with gaps
                    for (const day of locationDays) {
                        const roomName = day.closest('.location-row')?.querySelector('.room-name-line')?.textContent?.trim();
                        const gaps = day.querySelectorAll('.gap');
                        if (gaps.length > 0 && roomName) {
                            const firstGap = gaps[0];
                            const style = firstGap.getAttribute('style') || '';
                            const leftMatch = style.match(/left:\\s*([\\d.]+)%/);
                            const widthMatch = style.match(/width:\\s*([\\d.]+)%/);
                            if (leftMatch && widthMatch) {
                                const left = parseFloat(leftMatch[1]);
                                const startHour = 7.5 + (left / 100) * 15;  // 7:30 to 22:30 = 15 hours
                                const startH = Math.floor(startHour);
                                const startM = Math.round((startHour % 1) * 60);
                                return `First slot: ${roomName} starts at ${startH}:${startM.toString().padStart(2, '0')} (left=${left.toFixed(1)}%)`;
                            }
                        }
                    }
                    return 'Could not extract slot times';
                }""")
                print(f"  [DEBUG] {sample_slots}")
            except Exception as e:
                print(f"  [DEBUG] Could not sample slots: {e}")

            print("="*60)

        # Process each day in the determined order
        if horizon_only or extensions_only:
            day_order = []
        for day_index, days_ahead in enumerate(day_order):
            if args.max_actions is not None and total_booked >= args.max_actions:
                print("\nControlled action limit reached; no further booking changes will be attempted.")
                break
            target_date = datetime.combine(today + timedelta(days=days_ahead), datetime.min.time())
            day_name = target_date.strftime("%A")

            print(f"\n{'#'*60}")
            print(f"# DAY {days_ahead}: {target_date.strftime('%Y-%m-%d')} ({day_name})")
            print(f"{'#'*60}")

            # Calculate dynamic target for this specific day based on current distribution
            target_hours, max_bookings_per_day = calculate_target_hours_for_day(
                target_date,
                enabled_dates,
                tracker,
                practice_plan=practice_plan,
            )
            current_day_hours = tracker.get_hours_for_day(target_date)
            # Fragmented mode can use multiple complete preferred blocks; single-
            # block mode attempts at most one new reservation on the date.
            max_bookings_per_day = (
                (
                    min(
                        24,
                        int(
                            (target_hours * 60 + MINIMUM_BLOCK_MINUTES - 1)
                            // MINIMUM_BLOCK_MINUTES
                        ),
                    )
                    if ALLOW_FRAGMENTED_SESSIONS
                    else 1
                )
                if target_hours >= MINIMUM_BLOCK_MINUTES / 60
                else 0
            )
            print(f"  [Smart redistribution] Current: {current_day_hours:.1f}h, Target: +{target_hours:.1f}h, Max bookings: {max_bookings_per_day}")

            day_peak_used = tracker.get_peak_used_for_day(target_date)
            print(f"  [DEBUG] Session stats: {total_booked} bookings so far, peak for this day: {day_peak_used:.0f}/{MAX_PEAK_HOURS * 60}min")

            if days_ahead != current_calendar_day:
                current_calendar_day = navigate_to_day(
                    page,
                    days_ahead,
                    current_calendar_day,
                    base_date=today,
                )
                print(f"  [DEBUG] Navigation complete, now on day {current_calendar_day}")
                page.wait_for_timeout(700)
            else:
                print(f"  Already on correct day (day {days_ahead}) - no navigation needed")

            # Debug: Log current time when scanning slots for this day
            scan_time = datetime.now()
            print(f"  [DEBUG] Slot scan time: {scan_time.strftime('%H:%M:%S')}")

            # Verify the actual displayed date
            displayed_date = page.evaluate("""() => {
                const header = document.querySelector('h1, h2, .date-header, [class*="title"]');
                if (header) return header.textContent;
                // Look for date pattern in page
                const bodyText = document.body.innerText;
                const match = bodyText.match(/(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\\s+(\\d{1,2})\\s+(January|February|March|April|May|June|July|August|September|October|November|December)/i);
                return match ? match[0] : null;
            }""")
            print(f"  Displayed date: {displayed_date}")

            # Get available slots
            available_data = get_available_slots(page)

            # Debug the rooms with the furthest current site-derived horizon.
            furthest_horizon = max(ROOM_HORIZON_MINUTES.values())
            furthest_rooms = [
                room
                for room, horizon in ROOM_HORIZON_MINUTES.items()
                if horizon == furthest_horizon
            ]
            earliest_by_room = {}
            for room_data in available_data:
                room_name = room_data["room"]
                if room_name in furthest_rooms and room_data["slots"]:
                    earliest_start = min(s['startHour'] for s in room_data["slots"])
                    earliest_h = int(earliest_start)
                    earliest_m = int((earliest_start % 1) * 60)
                    earliest_by_room[room_name] = f"{earliest_h:02d}:{earliest_m:02d}"
            if earliest_by_room:
                print(
                    "  [DEBUG] Earliest slots for furthest live-horizon rooms "
                    f"({format_horizon_minutes(furthest_horizon)}): {earliest_by_room}"
                )
                expected_earliest = scan_time - timedelta(minutes=furthest_horizon)
                expected_earliest_str = expected_earliest.strftime('%H:%M')
                print(
                    "  [DEBUG] Expected earliest from the live horizon: "
                    f"~{expected_earliest_str} on target date"
                )

            # Build sorted slot list - prefer longer slots
            all_slots = []
            horizon_skipped = {}  # Track rooms skipped due to horizon
            for room_data in available_data:
                room_name = room_data["room"]
                if room_name not in PRIORITY_ROOMS:
                    continue
                if args.only_room and room_name != args.only_room:
                    continue

                room_priority = PRIORITY_ROOMS.index(room_name)

                for slot in room_data["slots"]:
                    slot_duration = slot['endHour'] - slot['startHour']
                    if slot_duration * 60 < MINIMUM_BLOCK_MINUTES:
                        continue  # Too short

                    # For today (days_ahead == 0), skip slots that have already started
                    if days_ahead == 0 and slot['startHour'] <= current_time_hour:
                        continue  # Slot is in the past

                    # Check if room is available based on booking horizon
                    is_available, horizon_reason = is_room_available_to_book(room_name, target_date, slot['startHour'])
                    if not is_available:
                        if room_name not in horizon_skipped:
                            horizon_skipped[room_name] = horizon_reason
                        continue

                    is_good_slot = (
                        slot_duration * 60
                        >= max(MINIMUM_BLOCK_MINUTES, 60)
                    )

                    all_slots.append({
                        "room": room_name,
                        "start_hour": slot['startHour'],
                        "end_hour": slot['endHour'],
                        "duration": slot_duration,
                        "room_priority": room_priority,
                        "is_good": is_good_slot,
                        "click_x": slot["clickX"],
                        "click_y": slot["clickY"]
                    })

            # Log which rooms were skipped due to horizon
            if horizon_skipped:
                print(f"  [DEBUG] Rooms skipped (not yet available): {', '.join(f'{r} ({reason})' for r, reason in horizon_skipped.items())}")

            # Sort: preferred times first, then good slots (longer), then by time, then by room priority
            all_slots.sort(key=lambda s: (
                0 if is_preferred_time(s['start_hour'], time_prefs) else 1,
                not s['is_good'],
                s['start_hour'],
                s['room_priority']
            ))

            if not all_slots and not daily_planning.enabled:
                print("  No available slots found")
                continue

            print(f"  Found {len(all_slots)} potential slots")
            # Show first 10 slots for debugging
            if all_slots:
                slot_info = [(s['room'], f"{s['start_hour']:.2f}-{s['end_hour']:.2f}") for s in all_slots[:10]]
                print(f"  [DEBUG] First 10 slots: {slot_info}")

            # Try to book slots
            day_booked = 0
            attempts = 0
            max_attempts = max(15, max_bookings_per_day * 3)

            # Log peak quota status for this specific day
            actual_remaining_peak = tracker.get_remaining_peak_minutes(target_date)
            date_key = as_date(target_date).isoformat()
            foresight_peak = planning_context["held_peak_by_date"].get(date_key, 0)
            extension_peak = planning_context.get(
                "extension_peak_by_date", {}
            ).get(date_key, 0)
            extension_target = planning_context.get(
                "extension_target_by_date", {}
            ).get(date_key, 0)
            extension_weekly = sum(
                planning_context.get("extension_target_by_date", {}).values()
            )
            reserved_peak = foresight_peak + extension_peak
            remaining_peak = max(0, actual_remaining_peak - reserved_peak)
            day_peak_used = tracker.get_peak_used_for_day(target_date)
            print(f"  [DEBUG] Peak quota for {target_date.strftime('%Y-%m-%d')}: {day_peak_used:.0f}/{MAX_PEAK_HOURS * 60} min used, {remaining_peak:.0f} min remaining")
            if reserved_peak:
                print(
                    f"  [Planner] Holding {reserved_peak} peak minutes for "
                    "preferred or partially confirmed sessions"
                )

            # Adjust slots to avoid conflicts AND peak zone if quota exceeded
            adjusted_slots = []
            conflicts = tracker.conflict_ranges.get(target_date.strftime('%Y-%m-%d'), [])

            if conflicts:
                print(f"  [DEBUG] Known conflicts for this day: {[(f'{c[0]:.2f}-{c[1]:.2f}') for c in conflicts]}")

            slots_removed = 0
            slots_adjusted = 0
            peak_adjusted = 0
            weekly_adjusted = 0
            conflict_removed = 0

            def split_slot_around_blocks(slot_start, slot_end, blocked_ranges):
                """Split a slot into valid sub-slots that don't overlap with blocked ranges.

                Returns list of (start, end) tuples representing usable time segments.
                """
                if not blocked_ranges:
                    return [(slot_start, slot_end)]

                # Sort blocked ranges by start time
                sorted_blocks = sorted(blocked_ranges, key=lambda x: x[0])

                # Find all usable gaps
                usable = []
                current_start = slot_start

                for b_start, b_end in sorted_blocks:
                    # If there's usable time before this block
                    if b_start > current_start:
                        gap_start = current_start
                        gap_end = min(b_start, slot_end)
                        if gap_end > gap_start and (gap_end - gap_start) * 60 >= MINIMUM_BLOCK_MINUTES:
                            usable.append((gap_start, gap_end))

                    # Move past this block
                    current_start = max(current_start, b_end)

                    # If we've moved past the slot end, stop
                    if current_start >= slot_end:
                        break

                # Check for usable time after all blocks
                if current_start < slot_end and (slot_end - current_start) * 60 >= MINIMUM_BLOCK_MINUTES:
                    usable.append((current_start, slot_end))

                return usable

            for s in all_slots:
                slot_start = s['start_hour']
                slot_end = s['end_hour']
                original_start = slot_start
                original_end = slot_end

                # Step 1: Split slot around conflicts (get all usable sub-slots)
                conflict_blocks = [(c_start, c_end) for c_start, c_end in conflicts
                                   if slot_start < c_end and slot_end > c_start]
                usable_segments = split_slot_around_blocks(slot_start, slot_end, conflict_blocks)

                if not usable_segments:
                    conflict_removed += 1
                    continue

                # Step 1.5: For each usable segment, also split around same-room gap blocks
                room_blocked = tracker.get_same_room_blocked_ranges(target_date, s['room'])
                final_segments = []
                for seg_start, seg_end in usable_segments:
                    room_blocks = [(b_start, b_end) for b_start, b_end in room_blocked
                                   if seg_start < b_end and seg_end > b_start]
                    sub_segments = split_slot_around_blocks(seg_start, seg_end, room_blocks)
                    final_segments.extend(sub_segments)

                if not final_segments:
                    conflict_removed += 1
                    continue

                # Add ALL valid segments as separate slots (not just the longest one)
                # This ensures we don't skip earlier bookable time
                for seg_start, seg_end in final_segments:
                    seg_start, seg_end = trim_to_strict_time_window(
                        seg_start,
                        seg_end,
                        time_prefs,
                    )
                    if seg_start is None:
                        slots_removed += 1
                        continue

                    # Step 2: Adjust each segment for peak hours quota (only on weekdays)
                    adj_start, adj_end, was_adjusted, reason = adjust_slot_for_peak_quota(
                        seg_start, seg_end, target_date, remaining_peak
                    )

                    if adj_start is None:
                        # Slot should be skipped (fully in peak zone with no quota)
                        slots_removed += 1
                        continue

                    if was_adjusted:
                        seg_start = adj_start
                        seg_end = adj_end
                        peak_adjusted += 1

                    # Step 3: Adjust for weekly quota (trim if exceeds remaining hours)
                    remaining_weekly = max(
                        0.0,
                        tracker.get_remaining_quota_hours()
                        - extension_weekly / 60,
                    )
                    adj_start, adj_end, was_weekly_adj, weekly_reason = adjust_slot_for_weekly_quota(
                        seg_start, seg_end, remaining_weekly
                    )

                    if adj_start is None:
                        # Not enough weekly quota remaining
                        slots_removed += 1
                        continue

                    if was_weekly_adj:
                        seg_start = adj_start
                        seg_end = adj_end
                        weekly_adjusted += 1

                    daily_remaining = remaining_run_daily_budget(
                        practice_plan,
                        target_date,
                        tracker,
                        planned_additional_hours=target_hours,
                        starting_day_hours=current_day_hours,
                    )
                    if daily_remaining is not None:
                        daily_remaining = max(
                            0.0,
                            daily_remaining - extension_target / 60,
                        )
                    allowed_minutes = cap_duration_minutes(
                        (seg_end - seg_start) * 60,
                        daily_remaining,
                        remaining_weekly,
                        minimum_minutes=MINIMUM_BLOCK_MINUTES,
                        maximum_minutes=_action_maximum_minutes(args.max_action_minutes),
                    )
                    if allowed_minutes == 0:
                        slots_removed += 1
                        continue
                    seg_end = seg_start + allowed_minutes / 60

                    # Final validation and add as new slot
                    if seg_start < seg_end and (seg_end - seg_start) * 60 >= MINIMUM_BLOCK_MINUTES:
                        new_slot = s.copy()
                        new_slot['start_hour'] = seg_start
                        new_slot['duration'] = seg_end - seg_start
                        new_slot['end_hour'] = seg_end
                        adjusted_slots.append(new_slot)
                        if seg_start != original_start or seg_end != original_end:
                            slots_adjusted += 1
                    else:
                        slots_removed += 1

            if conflict_removed > 0 or slots_removed > 0 or slots_adjusted > 0 or peak_adjusted > 0 or weekly_adjusted > 0:
                print(f"  [DEBUG] Slot adjustment: {conflict_removed} conflict-removed, {slots_removed} removed, {slots_adjusted} adjusted, {peak_adjusted} peak-adjusted, {weekly_adjusted} weekly-adjusted")

            all_slots = adjusted_slots

            if daily_planning.enabled:
                planning_remaining = remaining_run_daily_budget(
                    practice_plan,
                    target_date,
                    tracker,
                    planned_additional_hours=target_hours,
                    starting_day_hours=current_day_hours,
                )
                extension_target_minutes = planning_context.get(
                    "extension_target_by_date", {}
                ).get(date_key, 0)
                effective_planning_remaining = (
                    None
                    if planning_remaining is None
                    else max(
                        0.0,
                        planning_remaining - extension_target_minutes / 60,
                    )
                )
                cross_date_foresight_minutes = sum(
                    minutes
                    for held_date, minutes in planning_context.get(
                        "held_target_by_date", {}
                    ).items()
                    if held_date != date_key
                )
                extension_weekly_minutes = sum(
                    planning_context.get("extension_target_by_date", {}).values()
                )
                day_opportunities = build_day_booking_opportunities(
                    available_data,
                    target_date,
                    tracker,
                    time_prefs,
                    daily_planning,
                    now=scan_time,
                    remaining_daily_hours=effective_planning_remaining,
                    reserved_peak_minutes=extension_peak,
                    reserved_weekly_minutes=(
                        extension_weekly_minutes
                        + cross_date_foresight_minutes
                    ),
                    only_room=args.only_room,
                )
                current_opportunities = [
                    item
                    for item in day_opportunities
                    if item.unlock_at <= scan_time
                ]
                future_opportunities = [
                    item
                    for item in day_opportunities
                    if item.unlock_at > scan_time
                ]
                day_decision = choose_horizon_opportunity(
                    current_opportunities,
                    future_opportunities,
                    daily_planning,
                    now=scan_time,
                )
                print(f"  [Planner] {day_decision.reason}")
                if day_decision.action == "wait":
                    planning_context["held_peak_by_date"][date_key] = (
                        day_decision.held_peak_minutes
                    )
                    planning_context.setdefault("held_target_by_date", {})[
                        date_key
                    ] = day_decision.selected.potential_minutes
                    current_opportunities = current_opportunities_after_hold(
                        available_data,
                        target_date,
                        tracker,
                        time_prefs,
                        daily_planning,
                        day_decision,
                        now=scan_time,
                        remaining_daily_hours=effective_planning_remaining,
                        reserved_weekly_minutes=(
                            extension_weekly_minutes
                            + cross_date_foresight_minutes
                        ),
                        only_room=args.only_room,
                    )
                else:
                    planning_context["held_peak_by_date"].pop(date_key, None)
                    planning_context.setdefault("held_target_by_date", {}).pop(
                        date_key, None
                    )
                current_opportunities.sort(
                    key=lambda item: opportunity_rank(
                        item,
                        daily_planning,
                        now=scan_time,
                    )
                )
                display_day = build_display_day_plan(
                    target_date,
                    day_opportunities,
                    tracker,
                    daily_planning,
                    now=scan_time,
                    target_minutes=int(
                        (current_day_hours + target_hours) * 4 + 1e-9
                    )
                    * 15,
                    remaining_weekly_minutes=max(
                        0,
                        int(tracker.get_remaining_quota_hours() * 4 + 1e-9)
                        * 15
                        - extension_weekly_minutes
                        - cross_date_foresight_minutes,
                    ),
                )
                display_day = attach_extension_progress(
                    display_day,
                    planning_context,
                    now=scan_time,
                )
                planning_context["display_days"][display_day.date] = display_day
                publish_planning_context(
                    planning_context,
                    policy,
                    settings,
                    summary=display_day.reason,
                    now=scan_time,
                )
                all_slots = [
                    _opportunity_to_normal_slot(item)
                    for item in current_opportunities
                ]

            # Filter out non-preferred slots if strict mode is enabled
            if time_prefs["enabled"] and time_prefs["strict_mode"]:
                before_count = len(all_slots)
                all_slots = [
                    s for s in all_slots
                    if interval_is_strictly_preferred(
                        s['start_hour'],
                        s['end_hour'],
                        time_prefs,
                    )
                ]
                filtered_count = before_count - len(all_slots)
                if filtered_count > 0:
                    print(f"  [DEBUG] Strict mode: Filtered out {filtered_count} non-preferred slots")

            if not daily_planning.enabled:
                all_slots.sort(key=lambda s: (
                    0 if is_preferred_time(s['start_hour'], time_prefs) else 1,
                    not s.get('is_good', False),
                    s['start_hour'],
                    s['room_priority']
                ))

            print(f"  [DEBUG] Starting booking attempts. Max per day: {max_bookings_per_day}, slots available: {len(all_slots)}")

            while day_booked < max_bookings_per_day and attempts < max_attempts:
                if args.max_actions is not None and total_booked >= args.max_actions:
                    print("  Controlled action limit reached")
                    break
                # Check if we have valid slots to try
                valid_slots = [s for s in all_slots if not tracker.overlaps_conflict(
                    target_date, s['start_hour'], s['start_hour'] + min(s['duration'], MAX_BOOKING_HOURS)
                )]

                if not valid_slots:
                    print(f"  [DEBUG] No more valid slots (all_slots={len(all_slots)}, conflicts filtered all)")
                    print("  No more valid slots available")
                    break

                slot = valid_slots[0]  # Try the best remaining slot
                all_slots.remove(slot)  # Remove from list so we don't retry
                attempts += 1

                daily_remaining = remaining_run_daily_budget(
                    practice_plan,
                    target_date,
                    tracker,
                    planned_additional_hours=target_hours,
                    starting_day_hours=current_day_hours,
                )
                if daily_remaining is not None:
                    daily_remaining = max(
                        0.0,
                        daily_remaining - extension_target / 60,
                    )
                allowed_minutes = cap_duration_minutes(
                    slot['duration'] * 60,
                    daily_remaining,
                    max(
                        0.0,
                        tracker.get_remaining_quota_hours()
                        - extension_weekly / 60,
                    ),
                    minimum_minutes=MINIMUM_BLOCK_MINUTES,
                    maximum_minutes=_action_maximum_minutes(args.max_action_minutes),
                )
                if allowed_minutes == 0:
                    print("  Daily practice target or weekly quota is already filled")
                    break
                slot = slot.copy()
                slot['duration'] = allowed_minutes / 60
                slot['end_hour'] = slot['start_hour'] + slot['duration']

                print(f"  [DEBUG] Attempt {attempts}/{max_attempts}: {slot['room']} {slot['start_hour']:.2f}-{slot['end_hour']:.2f} (duration: {slot['duration']:.2f}h)")

                try:
                    booked = try_book_slot(
                        page,
                        slot,
                        target_date,
                        tracker,
                        days_ahead,
                        remaining_daily_hours=daily_remaining,
                        max_action_minutes=args.max_action_minutes,
                    )
                except BookingVerificationError:
                    raise
                except Exception as e:
                    try:
                        pending_receipts = list_pending_mutation_receipts()
                    except Exception as receipt_error:
                        raise BookingVerificationError(
                            "A booking attempt failed and the mutation journal could not be "
                            f"checked safely: {receipt_error}. No further mutations will be attempted."
                        ) from e
                    if pending_receipts:
                        raise BookingVerificationError(
                            "A booking attempt failed while a mutation receipt remains pending. "
                            "No same-pass retry is allowed until the receipt is reconciled."
                        ) from e
                    print(f"  Error booking slot: {e}")
                    # Check if browser is still open
                    try:
                        page.evaluate("1+1")  # Simple check
                    except:
                        print("  Browser was closed - exiting")
                        break
                    continue

                if booked:
                    day_booked += 1
                    total_booked += 1
                    print(f"  [DEBUG] Booking successful! Day total: {day_booked}, session total: {total_booked}")

                    booking_details.append(
                        f"{booked['date']} {booked['room']} {booked['start']} "
                        f"{booked['end']} {booked['duration_minutes']}"
                    )

                    # After successful booking, navigate back to calendar and refresh slots
                    page.wait_for_timeout(1000)

                    # Navigate back to calendar (we're on the booking confirmation page)
                    print("  [DEBUG] Navigating back to calendar...")
                    go_back(page, days_ahead)
                    page.wait_for_timeout(1000)

                    # Re-fetch available slots since the calendar has changed
                    print("  [DEBUG] Refreshing slot data after successful booking...")
                    available_data = get_available_slots(page)
                    total_raw_slots = sum(len(r["slots"]) for r in available_data)
                    print(f"  [DEBUG] Raw slots from page: {total_raw_slots} (from {len(available_data)} rooms)")

                    # Recalculate remaining peak quota after booking for this day
                    remaining_peak = tracker.get_remaining_peak_minutes(target_date)
                    print(f"  [DEBUG] Updated peak quota for {target_date.strftime('%Y-%m-%d')}: {remaining_peak:.0f} min remaining")

                    # Get updated conflict list (includes the booking we just made)
                    conflicts = tracker.conflict_ranges.get(target_date.strftime('%Y-%m-%d'), [])

                    # Rebuild slot list with conflict and peak adjustment
                    all_slots = []
                    for room_data in available_data:
                        room_name = room_data["room"]
                        if room_name not in PRIORITY_ROOMS:
                            continue
                        if args.only_room and room_name != args.only_room:
                            continue

                        room_priority = PRIORITY_ROOMS.index(room_name)

                        for slot_data in room_data["slots"]:
                            raw_start = slot_data['startHour']
                            raw_end = slot_data['endHour']
                            if (raw_end - raw_start) * 60 < MINIMUM_BLOCK_MINUTES:
                                continue

                            is_available, _ = is_room_available_to_book(
                                room_name,
                                target_date,
                                raw_start,
                            )
                            if not is_available:
                                continue

                            usable_segments = split_slot_around_blocks(
                                raw_start,
                                raw_end,
                                conflicts,
                            )
                            room_blocks = tracker.get_same_room_blocked_ranges(
                                target_date,
                                room_name,
                            )
                            refreshed_segments = []
                            for segment_start, segment_end in usable_segments:
                                refreshed_segments.extend(
                                    split_slot_around_blocks(
                                        segment_start,
                                        segment_end,
                                        room_blocks,
                                    )
                                )

                            for slot_start, slot_end in refreshed_segments:
                                slot_start, slot_end = trim_to_strict_time_window(
                                    slot_start,
                                    slot_end,
                                    time_prefs,
                                )
                                if slot_start is None:
                                    continue

                                slot_start, slot_end, _, _ = adjust_slot_for_peak_quota(
                                    slot_start,
                                    slot_end,
                                    target_date,
                                    remaining_peak,
                                )
                                if slot_start is None:
                                    continue

                                daily_remaining = remaining_run_daily_budget(
                                    practice_plan,
                                    target_date,
                                    tracker,
                                    planned_additional_hours=target_hours,
                                    starting_day_hours=current_day_hours,
                                )
                                if daily_remaining is not None:
                                    daily_remaining = max(
                                        0.0,
                                        daily_remaining - extension_target / 60,
                                    )
                                allowed_minutes = cap_duration_minutes(
                                    (slot_end - slot_start) * 60,
                                    daily_remaining,
                                    max(
                                        0.0,
                                        tracker.get_remaining_quota_hours()
                                        - extension_weekly / 60,
                                    ),
                                    minimum_minutes=MINIMUM_BLOCK_MINUTES,
                                    maximum_minutes=_action_maximum_minutes(args.max_action_minutes),
                                )
                                if allowed_minutes == 0:
                                    continue
                                slot_end = slot_start + allowed_minutes / 60
                                slot_duration = allowed_minutes / 60

                                all_slots.append({
                                    "room": room_name,
                                    "start_hour": slot_start,
                                    "end_hour": slot_end,
                                    "duration": slot_duration,
                                    "room_priority": room_priority,
                                    "is_good": (
                                        slot_duration * 60
                                        >= max(MINIMUM_BLOCK_MINUTES, 60)
                                    ),
                                    "click_x": slot_data["clickX"],
                                    "click_y": slot_data["clickY"]
                                })

                    if daily_planning.enabled:
                        refreshed_now = datetime.now()
                        planning_remaining = remaining_run_daily_budget(
                            practice_plan,
                            target_date,
                            tracker,
                            planned_additional_hours=target_hours,
                            starting_day_hours=current_day_hours,
                        )
                        date_key = as_date(target_date).isoformat()
                        extension_target_minutes = planning_context.get(
                            "extension_target_by_date", {}
                        ).get(date_key, 0)
                        extension_peak = planning_context.get(
                            "extension_peak_by_date", {}
                        ).get(date_key, 0)
                        effective_planning_remaining = (
                            None
                            if planning_remaining is None
                            else max(
                                0.0,
                                planning_remaining - extension_target_minutes / 60,
                            )
                        )
                        cross_date_foresight_minutes = sum(
                            minutes
                            for held_date, minutes in planning_context.get(
                                "held_target_by_date", {}
                            ).items()
                            if held_date != date_key
                        )
                        extension_weekly_minutes = sum(
                            planning_context.get(
                                "extension_target_by_date", {}
                            ).values()
                        )
                        refreshed_opportunities = build_day_booking_opportunities(
                            available_data,
                            target_date,
                            tracker,
                            time_prefs,
                            daily_planning,
                            now=refreshed_now,
                            remaining_daily_hours=effective_planning_remaining,
                            reserved_peak_minutes=extension_peak,
                            reserved_weekly_minutes=(
                                extension_weekly_minutes
                                + cross_date_foresight_minutes
                            ),
                            only_room=args.only_room,
                        )
                        current_opportunities = [
                            item
                            for item in refreshed_opportunities
                            if item.unlock_at <= refreshed_now
                        ]
                        future_opportunities = [
                            item
                            for item in refreshed_opportunities
                            if item.unlock_at > refreshed_now
                        ]
                        refreshed_decision = choose_horizon_opportunity(
                            current_opportunities,
                            future_opportunities,
                            daily_planning,
                            now=refreshed_now,
                        )
                        print(f"  [Planner] {refreshed_decision.reason}")
                        if refreshed_decision.action == "wait":
                            planning_context["held_peak_by_date"][date_key] = (
                                refreshed_decision.held_peak_minutes
                            )
                            planning_context.setdefault("held_target_by_date", {})[
                                date_key
                            ] = refreshed_decision.selected.potential_minutes
                            current_opportunities = current_opportunities_after_hold(
                                available_data,
                                target_date,
                                tracker,
                                time_prefs,
                                daily_planning,
                                refreshed_decision,
                                now=refreshed_now,
                                remaining_daily_hours=effective_planning_remaining,
                                reserved_weekly_minutes=(
                                    extension_weekly_minutes
                                    + cross_date_foresight_minutes
                                ),
                                only_room=args.only_room,
                            )
                        else:
                            planning_context["held_peak_by_date"].pop(date_key, None)
                            planning_context.setdefault(
                                "held_target_by_date", {}
                            ).pop(date_key, None)
                        current_opportunities.sort(
                            key=lambda item: opportunity_rank(
                                item,
                                daily_planning,
                                now=refreshed_now,
                            )
                        )
                        display_day = build_display_day_plan(
                            target_date,
                            refreshed_opportunities,
                            tracker,
                            daily_planning,
                            now=refreshed_now,
                            target_minutes=int(
                                (current_day_hours + target_hours) * 4 + 1e-9
                            )
                            * 15,
                            remaining_weekly_minutes=max(
                                0,
                                int(
                                    tracker.get_remaining_quota_hours()
                                    * 4
                                    + 1e-9
                                )
                                * 15
                                - extension_weekly_minutes
                                - cross_date_foresight_minutes,
                            ),
                        )
                        display_day = attach_extension_progress(
                            display_day,
                            planning_context,
                            now=refreshed_now,
                        )
                        planning_context["display_days"][display_day.date] = display_day
                        publish_planning_context(
                            planning_context,
                            policy,
                            settings,
                            summary=display_day.reason,
                            now=refreshed_now,
                        )
                        all_slots = [
                            _opportunity_to_normal_slot(item)
                            for item in current_opportunities
                        ]

                    # Apply strict mode filter if enabled
                    if time_prefs["enabled"] and time_prefs["strict_mode"]:
                        all_slots = [
                            s for s in all_slots
                            if interval_is_strictly_preferred(
                                s['start_hour'],
                                s['end_hour'],
                                time_prefs,
                            )
                        ]

                    if not daily_planning.enabled:
                        all_slots.sort(key=lambda s: (
                            0 if is_preferred_time(s['start_hour'], time_prefs) else 1,
                            not s['is_good'],
                            s['start_hour'],
                            s['room_priority']
                        ))
                    print(f"  [DEBUG] Rebuilt slot list: {len(all_slots)} slots remaining")

            print(f"\n  Day summary: {day_booked} bookings made, {attempts} attempts total")

        # Final summary
        print("\n" + "="*60)
        print("BOOKING COMPLETE")
        print("="*60)
        print(f"Total bookings made: {total_booked}")
        if tracker.peak_hours_by_day:
            print("Peak hours used (per day):")
            for date_key, mins in sorted(tracker.peak_hours_by_day.items()):
                print(f"  {date_key}: {mins:.0f}/{MAX_PEAK_HOURS * 60} min")
        else:
            print("Peak hours used: 0 minutes")
        print("\nAll bookings:")
        for room, date, start, end in tracker.bookings:
            start_str = f"{int(start):02d}:{int((start % 1) * 60):02d}"
            end_str = f"{int(end):02d}:{int((end % 1) * 60):02d}"
            print(f"  {date.strftime('%Y-%m-%d')} {room} {start_str}-{end_str}")
        print("="*60)

        # Save run to history
        save_history(total_booked, events_detected, booking_details)

        # Refresh cookies/local storage after every authenticated pass.
        persist_storage_state(context)

        if not args.headless:
            print("\nKeeping browser open for 30 seconds to verify...")
            page.wait_for_timeout(30000)

        context.close()
        browser.close()
        return 0


def _scheduled_target_time(now=None):
    """Return the imminent quarter-hour target for a :13/:28/:43/:58 task."""

    now = now or datetime.now()
    minutes_to_add = 15 - (now.minute % 15)
    target = now.replace(second=0, microsecond=0) + timedelta(minutes=minutes_to_add)
    seconds_until = (target - now).total_seconds()
    if target.date() == now.date() and 0 < seconds_until <= 180:
        return target.strftime("%H:%M")
    return None


def build_argument_parser():
    parser = argparse.ArgumentParser(description="Book RWCMD practice rooms safely")
    parser.add_argument("--headless", action="store_true", help="Run without a browser window")
    parser.add_argument(
        "--target-time",
        metavar="HH:MM",
        help="Pre-scan and wait until this local time before booking",
    )
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="Scheduled-task mode: headless and snipe the imminent quarter hour",
    )
    parser.add_argument(
        "--setup-login",
        action="store_true",
        help="Open one visible Playwright window and save Microsoft 365 login",
    )
    parser.add_argument(
        "--configure-autonomous-login",
        action="store_true",
        help="Securely store the RWCMD login in Windows Credential Manager",
    )
    parser.add_argument(
        "--login-only",
        action="store_true",
        help="Restore or verify Asimut login without scanning or booking",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Verify login, agenda, and room grid without changing bookings or history",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help=(
            "Read fresh agenda/room grids and publish the display-only booking "
            "plan without changing any Asimut booking"
        ),
    )
    parser.add_argument(
        "--only-date",
        metavar="YYYY-MM-DD",
        help="Controlled live-test scope: attempt changes only on this date",
    )
    parser.add_argument(
        "--only-room",
        metavar="ROOM",
        help="Controlled live-test scope: attempt changes only in this configured room",
    )
    parser.add_argument(
        "--max-actions",
        type=int,
        metavar="N",
        help="Stop after N successful creates/extensions during this run",
    )
    parser.add_argument(
        "--max-action-minutes",
        type=int,
        metavar="MINUTES",
        help=(
            "Controlled live-test scope: cap each create and each extension "
            "increment to 30-120 minutes in 15-minute steps"
        ),
    )
    parser.add_argument(
        "--horizon-only",
        action="store_true",
        help=(
            "Controlled live-test mode: attempt only imminent horizon-edge "
            "minimum-block creates, never extensions or ordinary bookings"
        ),
    )
    parser.add_argument(
        "--extensions-only",
        action="store_true",
        help=(
            "Controlled live-test mode: attempt only tracked horizon "
            "extensions, never new bookings; requires an explicit "
            "--max-action-minutes cap"
        ),
    )
    parser.add_argument(
        "--cancel-event-id",
        type=int,
        metavar="N",
        help=(
            "Isolated cancellation: exact positive agenda event ID; requires "
            "the complete expected reservation tuple"
        ),
    )
    parser.add_argument(
        "--cancel-date",
        metavar="YYYY-MM-DD",
        help="Isolated cancellation: exact expected reservation date",
    )
    parser.add_argument(
        "--cancel-room",
        metavar="ROOM",
        help="Isolated cancellation: exact expected reservation room",
    )
    parser.add_argument(
        "--cancel-start",
        metavar="HH:MM",
        help="Isolated cancellation: exact expected reservation start time",
    )
    parser.add_argument(
        "--cancel-end",
        metavar="HH:MM",
        help="Isolated cancellation: exact expected reservation end time",
    )
    return parser


def _cancellation_requested(args):
    """Return whether any exact-cancellation argument was supplied."""

    return any(
        getattr(args, field, None) is not None
        for field in (
            "cancel_event_id",
            "cancel_date",
            "cancel_room",
            "cancel_start",
            "cancel_end",
        )
    )


def _validate_cli_args(parser, args):
    cancellation_fields = {
        "--cancel-event-id": args.cancel_event_id,
        "--cancel-date": args.cancel_date,
        "--cancel-room": args.cancel_room,
        "--cancel-start": args.cancel_start,
        "--cancel-end": args.cancel_end,
    }
    cancellation_requested = any(
        value is not None for value in cancellation_fields.values()
    )
    if cancellation_requested:
        missing = [
            name for name, value in cancellation_fields.items() if value is None
        ]
        if missing:
            parser.error(
                "exact cancellation requires all of --cancel-event-id, "
                "--cancel-date, --cancel-room, --cancel-start, and --cancel-end; "
                f"missing: {', '.join(missing)}"
            )
        if args.cancel_event_id <= 0:
            parser.error("--cancel-event-id must be a positive integer")
        try:
            validate_room_name(args.cancel_room, "--cancel-room")
        except RoomPreferencesError as exc:
            parser.error(str(exc))
        try:
            selected_date = datetime.strptime(args.cancel_date, "%Y-%m-%d").date()
        except ValueError:
            parser.error("--cancel-date must use a valid YYYY-MM-DD date")
        if selected_date.isoformat() != args.cancel_date:
            parser.error("--cancel-date must use zero-padded YYYY-MM-DD")

        parsed_cancel_times = []
        for name, value in (
            ("--cancel-start", args.cancel_start),
            ("--cancel-end", args.cancel_end),
        ):
            try:
                parsed_time = datetime.strptime(value, "%H:%M")
            except ValueError:
                parser.error(f"{name} must use a valid 24-hour HH:MM value")
            if parsed_time.strftime("%H:%M") != value:
                parser.error(f"{name} must use zero-padded HH:MM")
            if parsed_time.minute % 15:
                parser.error(f"{name} must use a 15-minute boundary")
            parsed_cancel_times.append(parsed_time.hour * 60 + parsed_time.minute)
        if parsed_cancel_times[1] <= parsed_cancel_times[0]:
            parser.error("--cancel-end must be after --cancel-start")

    maintenance_modes = sum(
        bool(value)
        for value in (
            args.setup_login,
            args.configure_autonomous_login,
            args.login_only,
        )
    )
    if maintenance_modes > 1:
        parser.error(
            "--setup-login, --configure-autonomous-login, and --login-only "
            "are mutually exclusive"
        )
    if cancellation_requested and maintenance_modes:
        parser.error("exact cancellation cannot be combined with a maintenance mode")

    isolated_modes = sum(
        bool(value)
        for value in (
            args.horizon_only,
            args.extensions_only,
            args.plan_only,
            cancellation_requested,
        )
    )
    if isolated_modes > 1:
        parser.error(
            "--horizon-only, --extensions-only, --plan-only, and exact "
            "cancellation are mutually exclusive"
        )

    if cancellation_requested and (
        args.check_only
        or args.target_time
        or args.scheduled
        or args.only_date
        or args.only_room
        or args.max_actions is not None
        or args.max_action_minutes is not None
    ):
        parser.error(
            "exact cancellation is isolated and cannot be combined with check, "
            "scheduling, target timing, or booking mutation limits"
        )

    if args.target_time:
        try:
            parsed_time = datetime.strptime(args.target_time, "%H:%M")
        except ValueError:
            parser.error("--target-time must use a valid 24-hour HH:MM value")
        if parsed_time.strftime("%H:%M") != args.target_time:
            parser.error("--target-time must use zero-padded HH:MM")

    if args.max_actions is not None and args.max_actions < 1:
        parser.error("--max-actions must be at least 1")
    if (
        args.max_action_minutes is not None
        and args.max_action_minutes not in LIVE_ACTION_MINUTES
    ):
        parser.error("--max-action-minutes must be 30-120 in 15-minute steps")
    if args.only_room:
        try:
            validate_room_name(args.only_room, "--only-room")
        except RoomPreferencesError as exc:
            parser.error(str(exc))
    if args.only_date:
        try:
            selected_date = datetime.strptime(args.only_date, "%Y-%m-%d").date()
        except ValueError:
            parser.error("--only-date must use a valid YYYY-MM-DD date")
        if selected_date.isoformat() != args.only_date:
            parser.error("--only-date must use zero-padded YYYY-MM-DD")

    if args.check_only and (
        args.only_date
        or args.only_room
        or args.max_actions
        or args.max_action_minutes is not None
        or args.horizon_only
        or args.extensions_only
        or args.plan_only
    ):
        parser.error("--check-only cannot be combined with booking mutation limits")
    if args.plan_only and (
        args.target_time
        or args.scheduled
        or args.max_actions is not None
        or args.max_action_minutes is not None
    ):
        parser.error(
            "--plan-only cannot be scheduled, timed, or combined with mutation limits"
        )
    if args.max_action_minutes is not None and not (args.only_date or args.only_room):
        parser.error("--max-action-minutes requires --only-date or --only-room")
    if args.horizon_only or args.extensions_only:
        if not (args.only_date or args.only_room):
            parser.error(
                "isolated lifecycle modes require --only-date or --only-room"
            )
        if args.max_actions is None:
            parser.error("isolated lifecycle modes require --max-actions")
    if args.extensions_only and args.max_action_minutes is None:
        parser.error("--extensions-only requires --max-action-minutes")
    if args.horizon_only and not (args.target_time or args.scheduled):
        parser.error("--horizon-only requires --target-time or --scheduled")
    if args.horizon_only and args.max_actions != 1:
        parser.error(
            "--horizon-only requires --max-actions 1 so a verified Save can "
            "return without risky post-mutation navigation"
        )


def _load_and_validate_runtime_settings():
    """Load every safety-relevant block once and fail closed before Playwright."""

    settings = load_settings_document(settings_file)
    disabled_dates = load_disabled_dates(settings)
    for value in disabled_dates:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise SettingsError(f"Invalid disabled date: {value}") from exc
        if parsed.isoformat() != value:
            raise SettingsError(f"Disabled dates must use YYYY-MM-DD: {value}")
    load_ignored_events(settings)
    load_time_preferences(settings)
    load_booking_strategy(settings)
    load_extendable_bookings(settings)
    list_pending_mutation_receipts()
    try:
        room_preferences = load_room_preferences(settings)
    except RoomPreferencesError as exc:
        raise SettingsError(str(exc)) from exc
    try:
        practice_plan = load_practice_plan(settings)
    except PracticePlanError as exc:
        raise SettingsError(str(exc)) from exc
    return settings, practice_plan, room_preferences


def main(argv=None):
    configure_utf8_stdio()
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    _validate_cli_args(parser, args)

    if args.configure_autonomous_login:
        try:
            configure_windows_credential_interactive()
            print("Autonomous login saved securely in Windows Credential Manager.")
            return 0
        except (AutonomousLoginError, KeyboardInterrupt) as exc:
            if isinstance(exc, KeyboardInterrupt):
                print("Credential setup cancelled; the saved login was not changed.")
                return 130
            print(f"ERROR: {exc}")
            return 2

    if args.setup_login:
        login_lock = SingleInstanceLock(APP_DIR / "data" / "booker-runtime.lock")
        try:
            if not login_lock.acquire():
                print("Another login or booking run is active; Login Setup did not start.")
                return 6
            try:
                return setup_login()
            except KeyboardInterrupt:
                print("Login Setup cancelled; no session was replaced.")
                return 130
            except Exception as exc:
                print(f"ERROR: Login Setup failed safely: {exc}")
                return 1
        finally:
            login_lock.release()

    if args.login_only:
        login_lock = SingleInstanceLock(APP_DIR / "data" / "booker-runtime.lock")
        try:
            if not login_lock.acquire():
                print("Another login or booking run is active; login check did not start.")
                return 6
            return login_only(headless=args.headless)
        except KeyboardInterrupt:
            print("Autonomous login check cancelled.")
            return 130
        except AutonomousLoginError as exc:
            print(f"ERROR: Autonomous login could not complete: {exc}")
            return 2
        finally:
            login_lock.release()

    if _CONFIG_ERROR is not None:
        print(f"ERROR: {_CONFIG_ERROR}")
        print("Autonomous booking stopped because config.yaml cannot be followed safely.")
        return 4

    if args.scheduled:
        args.headless = True
        if not args.target_time:
            args.target_time = _scheduled_target_time()

    if args.horizon_only and not args.target_time:
        print(
            "Horizon-only scheduled run is outside the bounded edge window; "
            "exiting without ordinary booking."
        )
        return 0
    if args.target_time:
        wait_before_runtime_target_window(args.target_time)

    try:
        settings, practice_plan, room_preferences = _load_and_validate_runtime_settings()
    except SettingsError as exc:
        print(f"ERROR: {exc}")
        print("Autonomous booking stopped because the saved settings cannot be trusted.")
        return 3

    runtime_lock = SingleInstanceLock(APP_DIR / "data" / "booker-runtime.lock")
    try:
        if not runtime_lock.acquire():
            print("Another AsimutBooker run is already active; this run will exit cleanly.")
            return 0
        return run_booking(args, settings, practice_plan, room_preferences) or 0
    except KeyboardInterrupt:
        print("Booking run cancelled.")
        return 130
    except AutonomousLoginError as exc:
        print(f"ERROR: Autonomous login could not complete: {exc}")
        return 2
    except (RoomCatalogError, LiveRoomPolicyError) as exc:
        print(f"ERROR: Live room policy could not be verified: {exc}")
        print("Autonomous booking stopped before any room mutation.")
        return 4
    except BookingVerificationError as exc:
        message = f"RECONCILIATION REQUIRED: {exc}"
        print(f"ERROR: {message}")
        save_history(
            0,
            0,
            [message],
            notify=False,
            outcome="reconciliation_required",
        )
        send_notification(
            "AsimutBooker needs attention",
            str(exc),
            priority="high",
        )
        return 5
    except Exception as exc:
        print(f"ERROR: Booking run failed safely: {exc}")
        return 1
    finally:
        runtime_lock.release()


if __name__ == "__main__":
    sys.exit(main())
