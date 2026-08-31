"""Strict, display-only snapshots of the booker's current forward plan.

The planner may replace this artifact whenever fresh Asimut evidence or user
preferences change.  Consumers must inspect :class:`BookingPlanReadResult` and
must never use a stale snapshot as booking authority.
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from app_settings import InterProcessFileLock, SettingsError, atomic_write_json


APP_DIR = Path(__file__).resolve().parent
PLAN_FILE = APP_DIR / "data" / "booking_plan.json"
SCHEMA_VERSION = 1

CandidateState = Literal["potential", "waiting", "ready"]
DayPlanStatus = Literal[
    "unplanned",
    "planned",
    "waiting",
    "in_progress",
    "complete",
    "blocked",
    "skipped",
]
PlanStatus = Literal["idle", "planning", "active", "complete", "blocked"]

_CANDIDATE_STATES = {"potential", "waiting", "ready"}
_DAY_STATUSES = {
    "unplanned",
    "planned",
    "waiting",
    "in_progress",
    "complete",
    "blocked",
    "skipped",
}
_PLAN_STATUSES = {"idle", "planning", "active", "complete", "blocked"}

_SNAPSHOT_KEYS = {
    "version",
    "generated_at",
    "valid_until",
    "policy_observed_at",
    "strategy_fingerprint",
    "status",
    "summary",
    "days",
}
_DAY_KEYS = {
    "date",
    "target_minutes",
    "existing_minutes",
    "peak_used_minutes",
    "peak_limit_minutes",
    "status",
    "primary",
    "additional",
    "backups",
    "held_peak_minutes",
    "reason",
}
_CANDIDATE_KEYS = {
    "room",
    "date",
    "start_time",
    "end_time",
    "unlock_at",
    "initial_minutes",
    "potential_minutes",
    "confirmed_minutes",
    "state",
    "reason",
}

_FINGERPRINT_SETTINGS_KEYS = (
    "booking_strategy",
    "time_preferences",
    "practice_plan",
    "rebooking_blackouts",
    "disabled_dates",
    "room_preferences",
    "ignored_events",
    "extendable_bookings",
)

ADVANCED_CONFIG_FILE = APP_DIR / "config" / "config.yaml"


class BookingPlanError(SettingsError):
    """Raised when a booking-plan snapshot cannot be safely written."""


@dataclass(frozen=True)
class PlanCandidate:
    room: str
    date: str
    start_time: str
    end_time: str
    unlock_at: datetime
    initial_minutes: int
    potential_minutes: int
    confirmed_minutes: int
    state: CandidateState
    reason: str


@dataclass(frozen=True)
class DayPlan:
    date: str
    target_minutes: int
    existing_minutes: int
    peak_used_minutes: int
    peak_limit_minutes: int
    status: DayPlanStatus
    primary: PlanCandidate | None
    additional: tuple[PlanCandidate, ...]
    backups: tuple[PlanCandidate, ...]
    held_peak_minutes: int
    reason: str


@dataclass(frozen=True)
class BookingPlanSnapshot:
    version: int
    generated_at: datetime
    valid_until: datetime
    policy_observed_at: datetime
    strategy_fingerprint: str
    status: PlanStatus
    summary: str
    days: tuple[DayPlan, ...]


@dataclass(frozen=True)
class BookingPlanReadResult:
    snapshot: BookingPlanSnapshot | None
    stale: bool
    reason: str


def booking_plan_fingerprint(
    settings: Mapping[str, Any],
    *,
    config_path: Path = ADVANCED_CONFIG_FILE,
) -> str:
    """Hash every local preference/rule input that can change a day plan."""

    if not isinstance(settings, Mapping):
        raise BookingPlanError("Settings must be a JSON object")
    relevant = {
        key: settings.get(key)
        for key in _FINGERPRINT_SETTINGS_KEYS
        if key in settings
    }
    config_path = Path(config_path)
    try:
        config_bytes = config_path.read_bytes() if config_path.exists() else b""
    except OSError as exc:
        raise BookingPlanError(
            f"Could not fingerprint advanced booking rules: {exc}"
        ) from exc
    relevant["advanced_config_sha256"] = hashlib.sha256(config_bytes).hexdigest()
    try:
        canonical = json.dumps(
            relevant,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BookingPlanError(
            f"Could not fingerprint booking preferences: {exc}"
        ) from exc
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _fail(message: str) -> BookingPlanError:
    return BookingPlanError(f"Invalid booking plan: {message}")


def _require_exact_keys(raw: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(raw)
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise _fail(f"{label} is missing fields: {sorted(missing)}")
    if unknown:
        raise _fail(f"{label} has unknown fields: {sorted(unknown)}")


def _require_text(value: Any, label: str, *, maximum: int = 1000) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise _fail(f"{label} must be a non-empty trimmed string")
    if len(value) > maximum:
        raise _fail(f"{label} is too long")
    return value


def _parse_date(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise _fail(f"{label} must use YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise _fail(f"{label} must use YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise _fail(f"{label} must use canonical YYYY-MM-DD")
    return value


def _time_minutes(value: Any, label: str) -> tuple[str, int]:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise _fail(f"{label} must use HH:MM")
    try:
        hour = int(value[:2])
        minute = int(value[3:])
    except ValueError as exc:
        raise _fail(f"{label} must use HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise _fail(f"{label} must use HH:MM")
    if minute % 15:
        raise _fail(f"{label} must fall on a 15-minute boundary")
    if f"{hour:02d}:{minute:02d}" != value:
        raise _fail(f"{label} must use canonical HH:MM")
    return value, hour * 60 + minute


def _canonical_timestamp(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _fail(f"{label} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _format_timestamp(value: datetime, label: str) -> str:
    canonical = _canonical_timestamp(value, label)
    return canonical.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise _fail(f"{label} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _fail(f"{label} must be a canonical UTC timestamp") from exc
    canonical = _canonical_timestamp(parsed, label)
    if _format_timestamp(canonical, label) != value:
        raise _fail(f"{label} must use canonical UTC form YYYY-MM-DDTHH:MM:SSZ")
    return canonical


def _minutes(value: Any, label: str, *, allow_zero: bool) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(f"{label} must be an integer number of minutes")
    minimum = 0 if allow_zero else 15
    if not minimum <= value <= 1440:
        raise _fail(f"{label} must be between {minimum} and 1440 minutes")
    if value % 15:
        raise _fail(f"{label} must use 15-minute increments")
    return value


def _candidate_from_dict(raw: Any, label: str) -> PlanCandidate:
    if not isinstance(raw, Mapping):
        raise _fail(f"{label} must be a JSON object")
    _require_exact_keys(raw, _CANDIDATE_KEYS, label)
    room = _require_text(raw["room"], f"{label}.room", maximum=200)
    booking_date = _parse_date(raw["date"], f"{label}.date")
    start_time, start_minutes = _time_minutes(raw["start_time"], f"{label}.start_time")
    end_time, end_minutes = _time_minutes(raw["end_time"], f"{label}.end_time")
    if end_minutes <= start_minutes:
        raise _fail(f"{label}.end_time must be after start_time on the same date")
    initial_minutes = _minutes(
        raw["initial_minutes"], f"{label}.initial_minutes", allow_zero=False
    )
    potential_minutes = _minutes(
        raw["potential_minutes"], f"{label}.potential_minutes", allow_zero=False
    )
    confirmed_minutes = _minutes(
        raw["confirmed_minutes"], f"{label}.confirmed_minutes", allow_zero=True
    )
    if initial_minutes > potential_minutes:
        raise _fail(f"{label}.initial_minutes cannot exceed potential_minutes")
    if confirmed_minutes >= potential_minutes:
        raise _fail(
            f"{label}.confirmed_minutes must be less than potential_minutes; "
            "fully confirmed reservations belong in the agenda"
        )
    if end_minutes - start_minutes != potential_minutes:
        raise _fail(f"{label} start/end duration must equal potential_minutes")
    state = raw["state"]
    if not isinstance(state, str) or state not in _CANDIDATE_STATES:
        raise _fail(f"{label}.state is invalid: {state!r}")
    unlock_at = _parse_timestamp(raw["unlock_at"], f"{label}.unlock_at")
    if unlock_at.minute % 15 or unlock_at.second or unlock_at.microsecond:
        raise _fail(f"{label}.unlock_at must fall on a 15-minute boundary")
    return PlanCandidate(
        room=room,
        date=booking_date,
        start_time=start_time,
        end_time=end_time,
        unlock_at=unlock_at,
        initial_minutes=initial_minutes,
        potential_minutes=potential_minutes,
        confirmed_minutes=confirmed_minutes,
        state=state,
        reason=_require_text(raw["reason"], f"{label}.reason"),
    )


def _candidate_to_dict(candidate: PlanCandidate, label: str) -> dict[str, Any]:
    # Reparse the produced primitive representation so hand-constructed
    # dataclasses receive exactly the same validation as disk data.
    if not isinstance(candidate, PlanCandidate):
        raise _fail(f"{label} must be a PlanCandidate")
    raw = {
        "room": candidate.room,
        "date": candidate.date,
        "start_time": candidate.start_time,
        "end_time": candidate.end_time,
        "unlock_at": _format_timestamp(candidate.unlock_at, f"{label}.unlock_at"),
        "initial_minutes": candidate.initial_minutes,
        "potential_minutes": candidate.potential_minutes,
        "confirmed_minutes": candidate.confirmed_minutes,
        "state": candidate.state,
        "reason": candidate.reason,
    }
    _candidate_from_dict(raw, label)
    return raw


def _day_from_dict(raw: Any, label: str) -> DayPlan:
    if not isinstance(raw, Mapping):
        raise _fail(f"{label} must be a JSON object")
    _require_exact_keys(raw, _DAY_KEYS, label)
    booking_date = _parse_date(raw["date"], f"{label}.date")
    target_minutes = _minutes(raw["target_minutes"], f"{label}.target_minutes", allow_zero=True)
    existing_minutes = _minutes(
        raw["existing_minutes"], f"{label}.existing_minutes", allow_zero=True
    )
    peak_used_minutes = _minutes(
        raw["peak_used_minutes"], f"{label}.peak_used_minutes", allow_zero=True
    )
    peak_limit_minutes = _minutes(
        raw["peak_limit_minutes"], f"{label}.peak_limit_minutes", allow_zero=True
    )
    held_peak_minutes = _minutes(
        raw["held_peak_minutes"], f"{label}.held_peak_minutes", allow_zero=True
    )
    if peak_used_minutes > existing_minutes:
        raise _fail(f"{label}.peak_used_minutes cannot exceed existing_minutes")
    if peak_used_minutes > peak_limit_minutes:
        raise _fail(f"{label}.peak_used_minutes cannot exceed peak_limit_minutes")
    if held_peak_minutes > peak_limit_minutes - peak_used_minutes:
        raise _fail(f"{label}.held_peak_minutes exceeds the remaining peak allowance")
    status = raw["status"]
    if not isinstance(status, str) or status not in _DAY_STATUSES:
        raise _fail(f"{label}.status is invalid: {status!r}")
    primary_raw = raw["primary"]
    primary = None if primary_raw is None else _candidate_from_dict(primary_raw, f"{label}.primary")
    additional_raw = raw["additional"]
    if not isinstance(additional_raw, list):
        raise _fail(f"{label}.additional must be a JSON array")
    additional = tuple(
        _candidate_from_dict(candidate, f"{label}.additional[{index}]")
        for index, candidate in enumerate(additional_raw)
    )
    backups_raw = raw["backups"]
    if not isinstance(backups_raw, list):
        raise _fail(f"{label}.backups must be a JSON array")
    backups = tuple(
        _candidate_from_dict(candidate, f"{label}.backups[{index}]")
        for index, candidate in enumerate(backups_raw)
    )
    candidates = (() if primary is None else (primary,)) + additional + backups
    for candidate in candidates:
        if candidate.date != booking_date:
            raise _fail(f"{label} candidates must use the day date {booking_date}")
    identities = [
        (candidate.room, candidate.date, candidate.start_time, candidate.end_time)
        for candidate in candidates
    ]
    if len(identities) != len(set(identities)):
        raise _fail(f"{label} contains a duplicate candidate")
    if (additional or backups) and primary is None:
        raise _fail(
            f"{label} cannot have additional candidates or backups without a primary"
        )
    if held_peak_minutes and primary is None:
        raise _fail(f"{label}.held_peak_minutes requires a primary candidate")
    selected = (() if primary is None else (primary,)) + additional
    selected_ranges = []
    for candidate in selected:
        _, start = _time_minutes(candidate.start_time, "candidate.start_time")
        _, end = _time_minutes(candidate.end_time, "candidate.end_time")
        if any(start < other_end and end > other_start for other_start, other_end in selected_ranges):
            raise _fail(f"{label} selected candidates cannot overlap")
        selected_ranges.append((start, end))
    planned_minutes = sum(
        candidate.potential_minutes - candidate.confirmed_minutes
        for candidate in selected
    )
    if held_peak_minutes > planned_minutes:
        raise _fail(
            f"{label}.held_peak_minutes cannot exceed unconfirmed selected minutes"
        )
    if existing_minutes + planned_minutes > target_minutes:
        raise _fail(f"{label} selected candidates exceed the daily target")
    return DayPlan(
        date=booking_date,
        target_minutes=target_minutes,
        existing_minutes=existing_minutes,
        peak_used_minutes=peak_used_minutes,
        peak_limit_minutes=peak_limit_minutes,
        status=status,
        primary=primary,
        additional=additional,
        backups=backups,
        held_peak_minutes=held_peak_minutes,
        reason=_require_text(raw["reason"], f"{label}.reason"),
    )


def _day_to_dict(day: DayPlan, label: str) -> dict[str, Any]:
    if not isinstance(day, DayPlan):
        raise _fail(f"{label} must be a DayPlan")
    if not isinstance(day.backups, (tuple, list)):
        raise _fail(f"{label}.backups must be a sequence")
    if not isinstance(day.additional, (tuple, list)):
        raise _fail(f"{label}.additional must be a sequence")
    raw = {
        "date": day.date,
        "target_minutes": day.target_minutes,
        "existing_minutes": day.existing_minutes,
        "peak_used_minutes": day.peak_used_minutes,
        "peak_limit_minutes": day.peak_limit_minutes,
        "status": day.status,
        "primary": None
        if day.primary is None
        else _candidate_to_dict(day.primary, f"{label}.primary"),
        "additional": [
            _candidate_to_dict(candidate, f"{label}.additional[{index}]")
            for index, candidate in enumerate(day.additional)
        ],
        "backups": [
            _candidate_to_dict(candidate, f"{label}.backups[{index}]")
            for index, candidate in enumerate(day.backups)
        ],
        "held_peak_minutes": day.held_peak_minutes,
        "reason": day.reason,
    }
    _day_from_dict(raw, label)
    return raw


def snapshot_from_dict(raw: Any) -> BookingPlanSnapshot:
    """Strictly parse one v1 JSON-compatible snapshot."""

    if not isinstance(raw, Mapping):
        raise _fail("document must be a JSON object")
    _require_exact_keys(raw, _SNAPSHOT_KEYS, "document")
    if type(raw["version"]) is not int or raw["version"] != SCHEMA_VERSION:
        raise _fail(f"unsupported version: {raw['version']!r}")
    generated_at = _parse_timestamp(raw["generated_at"], "generated_at")
    valid_until = _parse_timestamp(raw["valid_until"], "valid_until")
    policy_observed_at = _parse_timestamp(raw["policy_observed_at"], "policy_observed_at")
    if valid_until <= generated_at:
        raise _fail("valid_until must be after generated_at")
    if policy_observed_at > generated_at:
        raise _fail("policy_observed_at cannot be after generated_at")
    fingerprint = _require_text(
        raw["strategy_fingerprint"], "strategy_fingerprint", maximum=256
    )
    status = raw["status"]
    if not isinstance(status, str) or status not in _PLAN_STATUSES:
        raise _fail(f"status is invalid: {status!r}")
    days_raw = raw["days"]
    if not isinstance(days_raw, list):
        raise _fail("days must be a JSON array")
    days = tuple(_day_from_dict(day, f"days[{index}]") for index, day in enumerate(days_raw))
    dates = [day.date for day in days]
    if dates != sorted(dates):
        raise _fail("days must be ordered by ascending date")
    if len(dates) != len(set(dates)):
        raise _fail("days must contain unique dates")
    return BookingPlanSnapshot(
        version=SCHEMA_VERSION,
        generated_at=generated_at,
        valid_until=valid_until,
        policy_observed_at=policy_observed_at,
        strategy_fingerprint=fingerprint,
        status=status,
        summary=_require_text(raw["summary"], "summary"),
        days=days,
    )


def snapshot_to_dict(snapshot: BookingPlanSnapshot) -> dict[str, Any]:
    """Validate and serialize a snapshot to its exact v1 JSON shape."""

    if not isinstance(snapshot, BookingPlanSnapshot):
        raise _fail("value must be a BookingPlanSnapshot")
    if not isinstance(snapshot.days, (tuple, list)):
        raise _fail("days must be a sequence")
    raw = {
        "version": snapshot.version,
        "generated_at": _format_timestamp(snapshot.generated_at, "generated_at"),
        "valid_until": _format_timestamp(snapshot.valid_until, "valid_until"),
        "policy_observed_at": _format_timestamp(
            snapshot.policy_observed_at, "policy_observed_at"
        ),
        "strategy_fingerprint": snapshot.strategy_fingerprint,
        "status": snapshot.status,
        "summary": snapshot.summary,
        "days": [_day_to_dict(day, f"days[{index}]") for index, day in enumerate(snapshot.days)],
    }
    snapshot_from_dict(raw)
    return raw


def write_booking_plan(
    snapshot: BookingPlanSnapshot,
    path: Path = PLAN_FILE,
) -> BookingPlanSnapshot:
    """Validate and atomically replace the display-only plan artifact."""

    path = Path(path)
    try:
        document = snapshot_to_dict(snapshot)
        canonical = snapshot_from_dict(document)
        with InterProcessFileLock(path.with_suffix(path.suffix + ".lock")):
            atomic_write_json(path, document)
        return canonical
    except BookingPlanError:
        raise
    except SettingsError as exc:
        raise BookingPlanError(f"Could not safely write booking plan: {exc}") from exc


def read_booking_plan(
    path: Path = PLAN_FILE,
    *,
    now: datetime | None = None,
    expected_strategy_fingerprint: str | None = None,
) -> BookingPlanReadResult:
    """Read a plan for display, explicitly classifying absent/invalid/old data.

    An expired or settings-mismatched snapshot is returned for optional stale
    display, but ``stale`` remains true.  Structurally invalid data is never
    exposed as a snapshot.
    """

    path = Path(path)
    current = datetime.now(timezone.utc) if now is None else now
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        raise BookingPlanError("now must be a timezone-aware datetime")
    current = current.astimezone(timezone.utc)
    if expected_strategy_fingerprint is not None:
        _require_text(
            expected_strategy_fingerprint,
            "expected_strategy_fingerprint",
            maximum=256,
        )
    try:
        with InterProcessFileLock(path.with_suffix(path.suffix + ".lock")):
            if not path.exists():
                return BookingPlanReadResult(None, True, "No booking plan has been generated yet.")
            try:
                with path.open("r", encoding="utf-8") as handle:
                    raw = json.load(handle)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                return BookingPlanReadResult(None, True, f"Booking plan is unreadable: {exc}")
    except SettingsError as exc:
        return BookingPlanReadResult(None, True, f"Booking plan is unavailable: {exc}")

    try:
        snapshot = snapshot_from_dict(raw)
    except BookingPlanError as exc:
        return BookingPlanReadResult(None, True, str(exc))
    if current >= snapshot.valid_until:
        return BookingPlanReadResult(
            snapshot,
            True,
            f"Booking plan expired at {_format_timestamp(snapshot.valid_until, 'valid_until')}.",
        )
    if (
        expected_strategy_fingerprint is not None
        and snapshot.strategy_fingerprint != expected_strategy_fingerprint
    ):
        return BookingPlanReadResult(
            snapshot,
            True,
            "Booking preferences changed after this plan was generated.",
        )
    return BookingPlanReadResult(snapshot, False, "Booking plan is current.")


def clear_booking_plan(path: Path = PLAN_FILE) -> None:
    """Remove the ephemeral plan artifact under its interprocess lock."""

    path = Path(path)
    try:
        with InterProcessFileLock(path.with_suffix(path.suffix + ".lock")):
            path.unlink(missing_ok=True)
    except (OSError, SettingsError) as exc:
        raise BookingPlanError(f"Could not clear booking plan: {exc}") from exc


__all__ = [
    "PLAN_FILE",
    "SCHEMA_VERSION",
    "BookingPlanError",
    "BookingPlanReadResult",
    "BookingPlanSnapshot",
    "CandidateState",
    "DayPlan",
    "DayPlanStatus",
    "PlanCandidate",
    "PlanStatus",
    "clear_booking_plan",
    "booking_plan_fingerprint",
    "read_booking_plan",
    "snapshot_from_dict",
    "snapshot_to_dict",
    "write_booking_plan",
]
