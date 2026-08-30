"""Tk-free, read-only health summaries for the AsimutBooker GUI.

The readers in this module never repair, normalize, lock, or rewrite their
source files.  A caller can therefore refresh health cards without changing
the evidence those cards describe.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

from mutation_receipts import load_journal


APP_DIR = Path(__file__).resolve().parent
HISTORY_FILE = APP_DIR / "data" / "booking_history.json"
SAVED_SESSION_FILE = APP_DIR / "data" / "browser_state" / "state.json"
AUTH_COOLDOWN_FILE = APP_DIR / "data" / "auth_recovery_state.json"
MUTATION_RECEIPTS_FILE = APP_DIR / "data" / "mutation_receipts.json"
PHYSICAL_WAKE_FILE = APP_DIR / "data" / "physical_wake_test.json"

HealthState = Literal["ok", "warn", "error", "unknown"]
Clock = Callable[[], datetime]
HealthCheck = tuple[str, str, Callable[[], "HealthItem"]]

_HEALTH_STATES = frozenset({"ok", "warn", "error", "unknown"})
_RECONCILIATION_PREFIX = "RECONCILIATION REQUIRED"
_AUTH_VERSION = 1
_WAKE_VERSION = 1
_WAKE_METHOD = "scheduled_wake_test"


class HealthStatusError(ValueError):
    """Raised when a health evidence file cannot be trusted."""


def _default_clock() -> datetime:
    return datetime.now().astimezone()


@dataclass(frozen=True)
class HealthItem:
    """One presentation-ready health card with no GUI dependency."""

    key: str
    label: str
    state: HealthState
    headline: str
    detail: str = ""
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.key or not self.label or not self.headline:
            raise ValueError("Health item key, label, and headline must be non-empty")
        if self.state not in _HEALTH_STATES:
            raise ValueError(f"Unsupported health state: {self.state!r}")
        if self.observed_at is not None and (
            self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None
        ):
            raise ValueError("Health item observed_at must include a timezone")


@dataclass(frozen=True)
class HealthSnapshot:
    """An immutable set of independently collected health cards."""

    items: tuple[HealthItem, ...]
    collected_at: datetime

    def __post_init__(self) -> None:
        if self.collected_at.tzinfo is None or self.collected_at.utcoffset() is None:
            raise ValueError("Health snapshot collected_at must include a timezone")
        keys = [item.key for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("Health snapshot keys must be unique")

    def get(self, key: str) -> HealthItem:
        """Return one card by its stable key."""

        for item in self.items:
            if item.key == key:
                return item
        raise KeyError(key)


def _clock_value(clock: Clock) -> datetime:
    value = clock()
    if not isinstance(value, datetime):
        raise TypeError("Health clock must return a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Health clock must return a timezone-aware datetime")
    return value


def _format_time(value: datetime) -> str:
    return f"{value.day} {value:%b %Y, %H:%M %Z}"


def _read_json(path: Path, description: str) -> Any:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HealthStatusError(f"{description} is unreadable or is not valid JSON") from exc


def _parse_timestamp(value: Any, field: str, *, default_tz: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise HealthStatusError(f"{field} must be a non-empty ISO timestamp")
    if "T" not in value and " " not in value:
        raise HealthStatusError(f"{field} must include a date and time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HealthStatusError(f"{field} is not a valid ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed


def _parse_aware_timestamp(value: Any, field: str) -> datetime:
    parsed = _parse_timestamp(value, field, default_tz=timezone.utc)
    if not isinstance(value, str) or not (
        value.endswith("Z") or parsed.tzinfo is not None and "+" in value[10:] or "-" in value[10:]
    ):
        # _parse_timestamp accepts legacy local history timestamps.  Evidence
        # files used for timers must instead carry an explicit timezone.
        try:
            original = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            original = None
        if original is None or original.tzinfo is None or original.utcoffset() is None:
            raise HealthStatusError(f"{field} must include a timezone")
    return parsed


def inspect_last_successful_run(
    path: Path = HISTORY_FILE,
    *,
    clock: Clock = _default_clock,
) -> HealthItem:
    """Read the most recent completed run without equating success with bookings."""

    now = _clock_value(clock)
    path = Path(path)
    if not path.exists():
        return HealthItem(
            "last_success",
            "Last successful run",
            "unknown",
            "No run history is available",
            "The booking-history file has not been created yet.",
        )

    document = _read_json(path, "Booking history")
    if not isinstance(document, dict) or set(document) != {"runs"}:
        raise HealthStatusError("Booking history must contain only a runs list")
    runs = document["runs"]
    if not isinstance(runs, list):
        raise HealthStatusError("Booking history runs must be a list")

    successful: list[tuple[datetime, int | None]] = []
    for entry in runs:
        if not isinstance(entry, dict):
            raise HealthStatusError("Each booking-history run must be an object")
        timestamp = _parse_timestamp(
            entry.get("timestamp"), "Booking-history timestamp", default_tz=now.tzinfo
        ).astimezone(now.tzinfo)

        booking_count = entry.get("bookings_made")
        if booking_count is not None and (
            isinstance(booking_count, bool)
            or not isinstance(booking_count, int)
            or booking_count < 0
        ):
            raise HealthStatusError("bookings_made must be a non-negative integer")

        if "outcome" in entry:
            outcome = entry["outcome"]
            if not isinstance(outcome, str) or not outcome.strip():
                raise HealthStatusError("Booking-history outcome must be non-empty text")
            is_success = outcome == "completed"
        else:
            details = entry.get("details")
            if not isinstance(details, str):
                raise HealthStatusError("Legacy booking-history runs require text details")
            is_success = not details.lstrip().startswith(_RECONCILIATION_PREFIX)

        if is_success:
            successful.append((timestamp, booking_count))

    if not successful:
        state: HealthState = "unknown" if not runs else "warn"
        return HealthItem(
            "last_success",
            "Last successful run",
            state,
            "No successful run is recorded",
            "Runs needing reconciliation or with a non-completed outcome are not counted.",
        )

    timestamp, booking_count = max(successful, key=lambda value: value[0])
    if booking_count == 0:
        detail = "The run completed with no bookings; that still counts as success."
    elif booking_count is None:
        detail = "The run completed successfully."
    else:
        noun = "booking" if booking_count == 1 else "bookings"
        detail = f"The run completed with {booking_count} {noun}."
    return HealthItem(
        "last_success",
        "Last successful run",
        "ok",
        f"Last successful run: {_format_time(timestamp)}",
        detail,
        timestamp,
    )


def _validate_cookie(cookie: Any) -> None:
    if not isinstance(cookie, dict):
        raise HealthStatusError("Saved-session cookies must be objects")
    string_fields = ("name", "value", "domain", "path")
    if any(not isinstance(cookie.get(field), str) for field in string_fields):
        raise HealthStatusError("Saved-session cookie text fields are invalid")
    expires = cookie.get("expires")
    if (
        isinstance(expires, bool)
        or not isinstance(expires, (int, float))
        or not math.isfinite(expires)
    ):
        raise HealthStatusError("Saved-session cookie expiry is invalid")
    if not isinstance(cookie.get("httpOnly"), bool) or not isinstance(cookie.get("secure"), bool):
        raise HealthStatusError("Saved-session cookie flags are invalid")
    if cookie.get("sameSite") not in {"Strict", "Lax", "None"}:
        raise HealthStatusError("Saved-session cookie sameSite is invalid")


def _validate_origin(origin: Any) -> None:
    if not isinstance(origin, dict) or not isinstance(origin.get("origin"), str):
        raise HealthStatusError("Saved-session origins must contain an origin string")
    local_storage = origin.get("localStorage")
    if not isinstance(local_storage, list):
        raise HealthStatusError("Saved-session localStorage must be a list")
    for item in local_storage:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "value"}
            or not isinstance(item["name"], str)
            or not isinstance(item["value"], str)
        ):
            raise HealthStatusError("Saved-session localStorage entries are invalid")
    if "indexedDB" in origin and not isinstance(origin["indexedDB"], list):
        raise HealthStatusError("Saved-session indexedDB must be a list")
    unknown = set(origin) - {"origin", "localStorage", "indexedDB"}
    if unknown:
        raise HealthStatusError("Saved-session origin contains unsupported fields")


def inspect_saved_session(
    path: Path = SAVED_SESSION_FILE,
    *,
    clock: Clock = _default_clock,
) -> HealthItem:
    """Report saved-state structure and mtime, not live authentication health."""

    now = _clock_value(clock)
    path = Path(path)
    if not path.exists():
        return HealthItem(
            "saved_session",
            "Saved session",
            "unknown",
            "No saved session state is available",
            "A live login check is required before authentication can be claimed.",
        )

    document = _read_json(path, "Saved session state")
    if not isinstance(document, dict) or set(document) != {"cookies", "origins"}:
        raise HealthStatusError("Saved session must contain cookies and origins lists")
    cookies = document["cookies"]
    origins = document["origins"]
    if not isinstance(cookies, list) or not isinstance(origins, list):
        raise HealthStatusError("Saved-session cookies and origins must be lists")
    for cookie in cookies:
        _validate_cookie(cookie)
    for origin in origins:
        _validate_origin(origin)

    try:
        refreshed_at = datetime.fromtimestamp(path.stat().st_mtime, tz=now.tzinfo)
    except OSError as exc:
        raise HealthStatusError("Saved session modification time is unavailable") from exc
    state: HealthState = "ok" if cookies else "warn"
    if cookies:
        detail = (
            "The Playwright state file is structurally valid. This does not prove "
            "that Asimut currently accepts the session."
        )
    else:
        detail = (
            "The file is structurally valid but contains no cookies; a live login check "
            "is required."
        )
    return HealthItem(
        "saved_session",
        "Saved session",
        state,
        f"Saved state last refreshed: {_format_time(refreshed_at)}",
        detail,
        refreshed_at,
    )


def inspect_auth_cooldown(
    path: Path = AUTH_COOLDOWN_FILE,
    *,
    clock: Clock = _default_clock,
) -> HealthItem:
    """Read auth retry state without clearing an expired transient block."""

    now = _clock_value(clock)
    path = Path(path)
    if not path.exists():
        return HealthItem(
            "auth_cooldown",
            "Authentication recovery",
            "ok",
            "Autonomous login retries are ready",
            "No cooldown artifact exists; the runtime treats this as ready.",
        )

    document = _read_json(path, "Authentication cooldown state")
    if not isinstance(document, dict) or document.get("version") != _AUTH_VERSION:
        raise HealthStatusError("Authentication cooldown state must use version 1")
    status = document.get("status")
    if status == "ready":
        if set(document) != {"version", "status"}:
            raise HealthStatusError("Ready authentication state has unexpected fields")
        return HealthItem(
            "auth_cooldown",
            "Authentication recovery",
            "ok",
            "Autonomous login retries are ready",
            "No authentication recovery cooldown is active.",
        )
    if status != "blocked" or document.get("category") not in {"credential", "transient"}:
        raise HealthStatusError("Authentication cooldown state is invalid")

    category = document["category"]
    if category == "credential":
        if set(document) != {"version", "status", "category"}:
            raise HealthStatusError("Credential authentication state has unexpected fields")
        return HealthItem(
            "auth_cooldown",
            "Authentication recovery",
            "error",
            "Secure login update or explicit repair is required",
            "Autonomous retries are paused after the stored login was rejected.",
        )

    if set(document) != {"version", "status", "category", "retry_after"}:
        raise HealthStatusError("Transient authentication state has unexpected fields")
    retry_after = document["retry_after"]
    if (
        isinstance(retry_after, bool)
        or not isinstance(retry_after, (int, float))
        or not math.isfinite(retry_after)
    ):
        raise HealthStatusError("Authentication retry_after must be a finite timestamp")
    retry_at = datetime.fromtimestamp(float(retry_after), tz=now.tzinfo)
    if float(retry_after) > now.timestamp():
        return HealthItem(
            "auth_cooldown",
            "Authentication recovery",
            "warn",
            "Authentication retry cooldown is active",
            f"Automatic retry becomes available after {_format_time(retry_at)}.",
            retry_at,
        )
    return HealthItem(
        "auth_cooldown",
        "Authentication recovery",
        "ok",
        "Authentication retry cooldown has expired",
        "The file still records the old block; this read-only check did not rewrite it.",
        retry_at,
    )


def inspect_pending_mutations(
    path: Path = MUTATION_RECEIPTS_FILE,
    *,
    clock: Clock = _default_clock,
) -> HealthItem:
    """Use the mutation journal's strict parser and count unresolved receipts."""

    now = _clock_value(clock)
    document = load_journal(Path(path))
    pending = [
        receipt for receipt in document["receipts"].values() if receipt["status"] == "pending"
    ]
    if not pending:
        return HealthItem(
            "pending_mutations",
            "Pending mutations",
            "ok",
            "No mutations need reconciliation",
            "The strict mutation journal contains no pending receipts.",
        )
    oldest_text = min(receipt["created_at"] for receipt in pending)
    oldest = _parse_aware_timestamp(oldest_text, "Receipt created_at").astimezone(now.tzinfo)
    count = len(pending)
    noun = "mutation" if count == 1 else "mutations"
    return HealthItem(
        "pending_mutations",
        "Pending mutations",
        "error",
        f"{count} pending {noun} need reconciliation",
        f"The oldest unresolved receipt was recorded at {_format_time(oldest)}.",
        oldest,
    )


def inspect_physical_wake(
    path: Path = PHYSICAL_WAKE_FILE,
    *,
    clock: Clock = _default_clock,
) -> HealthItem:
    """Read only dedicated physical-wake evidence, never scheduler settings."""

    now = _clock_value(clock)
    path = Path(path)
    if not path.exists():
        return HealthItem(
            "physical_wake",
            "Physical wake test",
            "unknown",
            "Physical wake has not been proven",
            "No wake-test artifact exists; scheduler settings alone do not prove a wake.",
        )

    document = _read_json(path, "Physical wake-test artifact")
    if not isinstance(document, dict) or document.get("version") != _WAKE_VERSION:
        raise HealthStatusError("Physical wake-test artifact must use version 1")
    if document.get("method") != _WAKE_METHOD:
        raise HealthStatusError("Physical wake-test artifact has an unsupported method")
    status = document.get("status")
    if status not in {"unproven", "passed", "failed"}:
        raise HealthStatusError("Physical wake-test artifact has an invalid status")
    expected = _parse_aware_timestamp(document.get("expected_wake_at"), "expected_wake_at")
    expected = expected.astimezone(now.tzinfo)

    base_keys = {"version", "status", "method", "expected_wake_at"}
    if status == "unproven":
        if set(document) != base_keys:
            raise HealthStatusError("Unproven wake-test artifact has unexpected fields")
        if expected < now:
            return HealthItem(
                "physical_wake",
                "Physical wake test",
                "warn",
                "Physical wake test is overdue",
                "The expected wake time passed without a pass or fail observation.",
                expected,
            )
        return HealthItem(
            "physical_wake",
            "Physical wake test",
            "unknown",
            "Physical wake test is not yet proven",
            f"A dedicated test is expected at {_format_time(expected)}.",
            expected,
        )

    if set(document) != base_keys | {"observed_at"}:
        raise HealthStatusError("Completed wake-test artifact has unexpected fields")
    observed = _parse_aware_timestamp(document["observed_at"], "observed_at").astimezone(
        now.tzinfo
    )
    if observed < expected:
        raise HealthStatusError("Wake-test observation cannot precede its expected wake time")
    if status == "passed":
        return HealthItem(
            "physical_wake",
            "Physical wake test",
            "ok",
            "Physical wake test passed",
            f"The dedicated wake-test artifact recorded a pass at {_format_time(observed)}.",
            observed,
        )
    return HealthItem(
        "physical_wake",
        "Physical wake test",
        "error",
        "Physical wake test failed",
        f"The dedicated wake-test artifact recorded a failure at {_format_time(observed)}.",
        observed,
    )


def combine_health(
    checks: Iterable[HealthCheck],
    *,
    clock: Clock = _default_clock,
) -> HealthSnapshot:
    """Run health readers independently so one corrupt source affects one card."""

    collected_at = _clock_value(clock)
    items: list[HealthItem] = []
    seen: set[str] = set()
    for key, label, check in checks:
        if key in seen:
            raise ValueError(f"Duplicate health check key: {key}")
        seen.add(key)
        try:
            item = check()
            if item.key != key:
                raise HealthStatusError("Health reader returned the wrong stable key")
        except Exception:
            item = HealthItem(
                key,
                label,
                "error",
                f"{label} could not be read",
                "This card's evidence is unreadable or invalid; other checks remain independent.",
            )
        items.append(item)
    return HealthSnapshot(tuple(items), collected_at)


def collect_local_health(
    *,
    history_path: Path = HISTORY_FILE,
    session_path: Path = SAVED_SESSION_FILE,
    auth_path: Path = AUTH_COOLDOWN_FILE,
    receipts_path: Path = MUTATION_RECEIPTS_FILE,
    wake_path: Path = PHYSICAL_WAKE_FILE,
    clock: Clock = _default_clock,
) -> HealthSnapshot:
    """Collect all local, file-backed health cards with injected paths and time."""

    collected_at = _clock_value(clock)
    fixed_clock = lambda: collected_at
    return combine_health(
        (
            (
                "last_success",
                "Last successful run",
                lambda: inspect_last_successful_run(history_path, clock=fixed_clock),
            ),
            (
                "saved_session",
                "Saved session",
                lambda: inspect_saved_session(session_path, clock=fixed_clock),
            ),
            (
                "auth_cooldown",
                "Authentication recovery",
                lambda: inspect_auth_cooldown(auth_path, clock=fixed_clock),
            ),
            (
                "pending_mutations",
                "Pending mutations",
                lambda: inspect_pending_mutations(receipts_path, clock=fixed_clock),
            ),
            (
                "physical_wake",
                "Physical wake test",
                lambda: inspect_physical_wake(wake_path, clock=fixed_clock),
            ),
        ),
        clock=fixed_clock,
    )


__all__ = [
    "AUTH_COOLDOWN_FILE",
    "HISTORY_FILE",
    "MUTATION_RECEIPTS_FILE",
    "PHYSICAL_WAKE_FILE",
    "SAVED_SESSION_FILE",
    "HealthItem",
    "HealthSnapshot",
    "HealthStatusError",
    "collect_local_health",
    "combine_health",
    "inspect_auth_cooldown",
    "inspect_last_successful_run",
    "inspect_pending_mutations",
    "inspect_physical_wake",
    "inspect_saved_session",
]
