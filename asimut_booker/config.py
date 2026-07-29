"""Strict, typed configuration for AsimutBooker.

The legacy application silently fell back to permissive defaults whenever a
configuration value was malformed.  This module deliberately does the
opposite: configuration errors are actionable and unknown room horizons never
fall back to a more permissive value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from .models import RoomPolicy

if TYPE_CHECKING:
    from .policies import BookingRules as DomainBookingRules


ALLOWED_ROOM_HORIZONS = frozenset({3, 5, 7})
WEEKDAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
_ROOM_ID_RE = re.compile(r"^[A-Z][A-Z0-9-]*\.[A-Z0-9-]+$")
_NOTIFICATION_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ConfigError(ValueError):
    """The configuration is present but invalid."""


class ConfigFileNotFoundError(ConfigError, FileNotFoundError):
    """The requested configuration file does not exist."""


class UnknownRoomError(ConfigError, KeyError):
    """No explicitly verified policy exists for a room."""


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """Half-open wall-clock interval expressed as minutes after midnight."""

    start_minute: int
    end_minute: int

    def __post_init__(self) -> None:
        if not 0 <= self.start_minute < 24 * 60:
            raise ConfigError("time window start must be between 00:00 and 23:59")
        if not 0 < self.end_minute <= 24 * 60:
            raise ConfigError("time window end must be between 00:01 and 24:00")
        if self.end_minute <= self.start_minute:
            raise ConfigError("time window end must be later than its start")

    @property
    def start_text(self) -> str:
        return _format_minutes(self.start_minute)

    @property
    def end_text(self) -> str:
        return _format_minutes(self.end_minute)


@dataclass(frozen=True, slots=True)
class InstitutionConfig:
    base_url: str
    location_group_id: int
    location_category: str

    @property
    def overview_url(self) -> str:
        return f"{self.base_url}/overview?locationGroupId={self.location_group_id}"


@dataclass(frozen=True, slots=True)
class BookingRules:
    minimum_duration_minutes: int
    maximum_duration_minutes: int
    default_duration_minutes: int
    duration_increment_minutes: int
    same_room_gap_minutes: int
    rolling_quota_minutes: int
    peak_enabled: bool
    peak_window: TimeWindow
    peak_weekdays: tuple[int, ...]
    peak_daily_quota_minutes: int

    @property
    def rolling_quota_hours(self) -> float:
        return self.rolling_quota_minutes / 60

    @property
    def peak_daily_quota_hours(self) -> float:
        return self.peak_daily_quota_minutes / 60


@dataclass(frozen=True, slots=True)
class BookingPreferences:
    """User booking preferences indexed Monday (0) through Sunday (6)."""

    windows_by_weekday: tuple[tuple[TimeWindow, ...], ...]
    strict: bool
    fallback: bool
    disabled_dates: frozenset[date]
    max_bookings_per_run: int
    max_horizon_days: int

    def windows_for(self, weekday: int) -> tuple[TimeWindow, ...]:
        if not 0 <= weekday <= 6:
            raise ValueError("weekday must be in the range 0..6")
        return self.windows_by_weekday[weekday]


@dataclass(frozen=True, slots=True)
class ScheduleConfig:
    enabled: bool
    cadence_minutes: int
    lead_seconds: int
    max_lateness_seconds: int
    active_start_minute: int
    active_end_minute: int
    timeout_minutes: int


@dataclass(frozen=True, slots=True)
class BrowserConfig:
    state_path: Path
    executable_path: Path | None
    artifacts_dir: Path
    database_path: Path
    lock_path: Path
    navigation_timeout_ms: int
    action_timeout_ms: int
    timezone: str


@dataclass(frozen=True, slots=True)
class NotificationConfig:
    enabled: bool
    provider: str
    base_url: str
    topic: str | None
    priority: str


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    level: str
    retention_days: int
    directory: Path


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Complete validated application configuration."""

    institution: InstitutionConfig
    rooms: tuple[RoomPolicy, ...]
    excluded_rooms: frozenset[str]
    rules: BookingRules
    preferences: BookingPreferences
    schedule: ScheduleConfig
    browser: BrowserConfig
    notifications: NotificationConfig
    logging: LoggingConfig
    config_path: Path
    project_root: Path

    @classmethod
    def load(cls, path: str | Path | None = None) -> "AppConfig":
        config_path = Path(path) if path is not None else default_config_path()
        config_path = config_path.expanduser().resolve()
        if not config_path.is_file():
            raise ConfigFileNotFoundError(f"configuration file not found: {config_path}")

        try:
            with config_path.open("r", encoding="utf-8") as handle:
                raw = yaml.safe_load(handle)
        except yaml.YAMLError as exc:
            raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"could not read configuration {config_path}: {exc}") from exc

        root = _mapping(raw, "configuration")
        _reject_unknown(
            root,
            {
                "institution",
                "rooms",
                "booking",
                "rules",
                "schedule",
                "browser",
                "notifications",
                "logging",
                "disabled_dates",
            },
            "configuration",
        )

        project_root = _project_root_for(config_path)
        institution = _parse_institution(root.get("institution"))
        rooms, excluded, rooms_fallback = _parse_rooms(root.get("rooms"))
        rules = _parse_rules(root.get("rules"), root.get("booking"))
        preferences = _parse_preferences(
            root.get("booking"),
            root.get("disabled_dates"),
            rooms_fallback,
            rooms,
        )
        schedule = _parse_schedule(root.get("schedule"))
        browser = _parse_browser(root.get("browser"), project_root)
        notifications = _parse_notifications(root.get("notifications"))
        logging_config = _parse_logging(root.get("logging"), project_root)

        return cls(
            institution=institution,
            rooms=rooms,
            excluded_rooms=excluded,
            rules=rules,
            preferences=preferences,
            schedule=schedule,
            browser=browser,
            notifications=notifications,
            logging=logging_config,
            config_path=config_path,
            project_root=project_root,
        )

    @property
    def enabled_rooms(self) -> tuple[RoomPolicy, ...]:
        return tuple(room for room in self.rooms if room.enabled)

    @property
    def base_url(self) -> str:
        return self.institution.base_url

    @property
    def location_group_id(self) -> int:
        return self.institution.location_group_id

    @property
    def location_category(self) -> str:
        return self.institution.location_category

    def room_policy(self, room_id: str) -> RoomPolicy:
        normalized = _normalize_room_id(room_id, "room_id")
        for policy in self.rooms:
            if policy.room_id == normalized and policy.enabled:
                return policy
        raise UnknownRoomError(
            f"room {normalized!r} has no enabled, explicitly verified horizon policy"
        )

    def try_room_policy(self, room_id: str) -> RoomPolicy | None:
        try:
            return self.room_policy(room_id)
        except UnknownRoomError:
            return None

    def preferred_windows_for(self, value: date | int) -> tuple[TimeWindow, ...]:
        weekday = value.weekday() if isinstance(value, date) else value
        return self.preferences.windows_for(weekday)

    def is_date_enabled(self, value: date) -> bool:
        return value not in self.preferences.disabled_dates

    def to_domain_rules(self) -> "DomainBookingRules":
        """Translate validated configuration into the pure policy model.

        The configuration model deliberately retains user-facing names and
        options (including the peak-hours enable switch), while the policy
        engine accepts only executable rules.  Keeping this conversion here
        gives every coordinator the same, explicit interpretation.
        """

        from .intervals import Interval
        from .policies import BookingRules as DomainBookingRules

        peak_enabled = self.rules.peak_enabled
        return DomainBookingRules(
            minimum_duration=self.rules.minimum_duration_minutes,
            maximum_duration=self.rules.maximum_duration_minutes,
            booking_increment=self.rules.duration_increment_minutes,
            future_quota_minutes=self.rules.rolling_quota_minutes,
            peak_window=Interval(
                self.rules.peak_window.start_minute,
                self.rules.peak_window.end_minute,
            ),
            peak_limit_minutes=(self.rules.peak_daily_quota_minutes if peak_enabled else 0),
            peak_weekdays=(frozenset(self.rules.peak_weekdays) if peak_enabled else frozenset()),
            same_room_gap_minutes=self.rules.same_room_gap_minutes,
        )


def default_config_path() -> Path:
    """Return the repository-local default configuration path."""

    return Path(__file__).resolve().parent.parent / "config" / "config.yaml"


def load_config(path: str | Path | None = None) -> AppConfig:
    """Convenience wrapper used by CLI and GUI callers."""

    return AppConfig.load(path)


def _parse_institution(value: Any) -> InstitutionConfig:
    data = _mapping(value, "institution")
    _reject_unknown(
        data,
        {"url", "base_url", "location", "location_category", "location_group_id"},
        "institution",
    )

    if "url" in data and "base_url" in data:
        raise ConfigError("institution: specify only one of 'url' or 'base_url'")
    raw_url = data.get("base_url", data.get("url"))
    base_url = _https_url(raw_url, "institution.base_url").rstrip("/")

    if "location" in data and "location_category" in data:
        raise ConfigError("institution: specify only one of 'location' or 'location_category'")
    location = _nonempty_string(
        data.get("location_category", data.get("location")),
        "institution.location_category",
    )
    group_id = _integer(
        data.get("location_group_id", 10), "institution.location_group_id", minimum=1
    )
    return InstitutionConfig(base_url, group_id, location)


def _parse_rooms(
    value: Any,
) -> tuple[tuple[RoomPolicy, ...], frozenset[str], bool | None]:
    data = _mapping(value, "rooms")
    _reject_unknown(data, {"priority", "policies", "exclude", "fallback"}, "rooms")
    if "priority" in data and "policies" in data:
        raise ConfigError("rooms: specify only one of 'priority' or 'policies'")

    raw_policies = data.get("policies", data.get("priority"))
    entries = _sequence(raw_policies, "rooms.policies")
    if not entries:
        raise ConfigError("rooms.policies must contain at least one room")

    policies: list[RoomPolicy] = []
    seen: set[str] = set()
    priorities: set[int] = set()
    for index, raw_entry in enumerate(entries):
        path = f"rooms.policies[{index}]"
        entry = _mapping(raw_entry, path)
        _reject_unknown(
            entry,
            {
                "room",
                "room_id",
                "horizon",
                "horizon_days",
                "type",
                "room_type",
                "priority",
                "enabled",
            },
            path,
        )
        if "room" in entry and "room_id" in entry:
            raise ConfigError(f"{path}: specify only one of 'room' or 'room_id'")
        room_id = _normalize_room_id(entry.get("room_id", entry.get("room")), f"{path}.room_id")
        if room_id in seen:
            raise ConfigError(f"{path}: duplicate room policy for {room_id}")
        seen.add(room_id)

        if "horizon" in entry and "horizon_days" in entry:
            raise ConfigError(f"{path}: specify only one of 'horizon' or 'horizon_days'")
        if "horizon" not in entry and "horizon_days" not in entry:
            raise ConfigError(
                f"{path}.horizon_days is required; unknown horizons may not use a default"
            )
        horizon = _integer(
            entry.get("horizon_days", entry.get("horizon")),
            f"{path}.horizon_days",
        )
        if horizon not in ALLOWED_ROOM_HORIZONS:
            allowed = ", ".join(str(item) for item in sorted(ALLOWED_ROOM_HORIZONS))
            raise ConfigError(f"{path}.horizon_days must be one of: {allowed}")

        if "type" in entry and "room_type" in entry:
            raise ConfigError(f"{path}: specify only one of 'type' or 'room_type'")
        room_type = _nonempty_string(
            entry.get("room_type", entry.get("type", "practice")),
            f"{path}.room_type",
        ).lower()
        priority = _integer(entry.get("priority", index), f"{path}.priority", minimum=0)
        if priority in priorities:
            raise ConfigError(f"{path}.priority duplicates priority {priority}")
        priorities.add(priority)
        enabled = _boolean(entry.get("enabled", True), f"{path}.enabled")
        policies.append(
            RoomPolicy(
                room_id=room_id,
                horizon_days=horizon,
                priority=priority,
                room_type=room_type,
                enabled=enabled,
            )
        )

    policies.sort(key=lambda item: item.priority)
    if not any(policy.enabled for policy in policies):
        raise ConfigError("rooms.policies must enable at least one room")
    excluded_values = _sequence(data.get("exclude", []), "rooms.exclude")
    excluded = frozenset(
        _normalize_room_id(item, f"rooms.exclude[{index}]")
        for index, item in enumerate(excluded_values)
    )
    enabled_ids = {room.room_id for room in policies if room.enabled}
    overlap = enabled_ids.intersection(excluded)
    if overlap:
        raise ConfigError(
            f"rooms cannot be both enabled and excluded: {', '.join(sorted(overlap))}"
        )
    fallback = _boolean(data["fallback"], "rooms.fallback") if "fallback" in data else None
    return tuple(policies), excluded, fallback


def _parse_rules(rules_value: Any, booking_value: Any) -> BookingRules:
    rules = _mapping(rules_value, "rules", default={})
    _reject_unknown(
        rules,
        {
            "rolling_quota",
            "rolling_quota_hours",
            "rolling_quota_minutes",
            "peak_hours",
            "duration",
            "same_room_gap_minutes",
            "horizon_days",
        },
        "rules",
    )
    booking = _mapping(booking_value, "booking", default={})

    duration = _mapping(rules.get("duration"), "rules.duration", default={})
    _reject_unknown(
        duration,
        {
            "min_minutes",
            "minimum_minutes",
            "max_minutes",
            "maximum_minutes",
            "default_minutes",
            "increment_minutes",
        },
        "rules.duration",
    )
    minimum = _integer(
        duration.get("minimum_minutes", duration.get("min_minutes", 30)),
        "rules.duration.minimum_minutes",
        minimum=1,
        maximum=24 * 60,
    )
    maximum = _integer(
        duration.get("maximum_minutes", duration.get("max_minutes", 120)),
        "rules.duration.maximum_minutes",
        minimum=minimum,
        maximum=24 * 60,
    )
    if "default_duration_minutes" in booking and "duration_hours" in booking:
        raise ConfigError("booking: specify only one of default_duration_minutes or duration_hours")
    if "default_minutes" in duration and (
        "default_duration_minutes" in booking or "duration_hours" in booking
    ):
        raise ConfigError("default duration is defined in both rules.duration and booking")
    if "default_minutes" in duration:
        default_duration = _integer(
            duration["default_minutes"],
            "rules.duration.default_minutes",
            minimum=minimum,
            maximum=maximum,
        )
    elif "default_duration_minutes" in booking:
        default_duration = _integer(
            booking["default_duration_minutes"],
            "booking.default_duration_minutes",
            minimum=minimum,
            maximum=maximum,
        )
    elif "duration_hours" in booking:
        default_duration = _hours_to_minutes(
            booking["duration_hours"],
            "booking.duration_hours",
        )
    else:
        default_duration = maximum
    if not minimum <= default_duration <= maximum:
        raise ConfigError("default booking duration must be between minimum and maximum duration")
    increment = _integer(
        duration.get("increment_minutes", 15),
        "rules.duration.increment_minutes",
        minimum=1,
        maximum=60,
    )
    for label, duration_value in (
        ("minimum", minimum),
        ("maximum", maximum),
        ("default", default_duration),
    ):
        if duration_value % increment:
            raise ConfigError(
                f"{label} duration ({duration_value}) must be a multiple of "
                f"the {increment}-minute increment"
            )

    gap = _integer(
        rules.get("same_room_gap_minutes", 60),
        "rules.same_room_gap_minutes",
        minimum=0,
        maximum=24 * 60,
    )

    quota_keys = [
        key
        for key in ("rolling_quota", "rolling_quota_hours", "rolling_quota_minutes")
        if key in rules
    ]
    if len(quota_keys) > 1:
        raise ConfigError("rules: specify only one rolling quota value/unit")
    quota_key = quota_keys[0] if quota_keys else "rolling_quota_hours"
    quota_value = rules.get(quota_key, 28)
    rolling_quota = (
        _integer(quota_value, "rules.rolling_quota_minutes", minimum=minimum)
        if quota_key == "rolling_quota_minutes"
        else _hours_to_minutes(quota_value, f"rules.{quota_key}")
    )
    if rolling_quota < minimum:
        raise ConfigError("rolling quota must permit at least one minimum booking")

    peak = _mapping(rules.get("peak_hours"), "rules.peak_hours", default={})
    _reject_unknown(
        peak,
        {
            "enabled",
            "start",
            "end",
            "days",
            "weekdays",
            "max_hours",
            "daily_quota_hours",
            "daily_quota_minutes",
        },
        "rules.peak_hours",
    )
    peak_enabled = _boolean(peak.get("enabled", True), "rules.peak_hours.enabled")
    peak_window = TimeWindow(
        _parse_clock(peak.get("start", "09:00"), "rules.peak_hours.start", allow_24=False),
        _parse_clock(peak.get("end", "16:00"), "rules.peak_hours.end", allow_24=True),
    )
    if "days" in peak and "weekdays" in peak:
        raise ConfigError("rules.peak_hours: specify only one of days or weekdays")
    raw_weekdays = _sequence(
        peak.get("weekdays", peak.get("days", [0, 1, 2, 3, 4])),
        "rules.peak_hours.weekdays",
    )
    peak_weekdays = tuple(
        _weekday(item, f"rules.peak_hours.weekdays[{index}]")
        for index, item in enumerate(raw_weekdays)
    )
    if len(set(peak_weekdays)) != len(peak_weekdays):
        raise ConfigError("rules.peak_hours.weekdays contains duplicates")
    if peak_enabled and not peak_weekdays:
        raise ConfigError("enabled peak hours require at least one weekday")

    peak_quota_keys = [
        key for key in ("max_hours", "daily_quota_hours", "daily_quota_minutes") if key in peak
    ]
    if len(peak_quota_keys) > 1:
        raise ConfigError("rules.peak_hours: specify only one daily quota value/unit")
    peak_quota_key = peak_quota_keys[0] if peak_quota_keys else "daily_quota_hours"
    peak_quota_value = peak.get(peak_quota_key, 2)
    peak_quota = (
        _integer(
            peak_quota_value,
            "rules.peak_hours.daily_quota_minutes",
            minimum=0,
        )
        if peak_quota_key == "daily_quota_minutes"
        else _hours_to_minutes(
            peak_quota_value,
            f"rules.peak_hours.{peak_quota_key}",
            allow_zero=True,
        )
    )
    if peak_enabled and peak_quota < minimum:
        raise ConfigError("enabled peak daily quota must permit at least one minimum booking")

    if "horizon_days" in rules:
        legacy_horizon = _integer(rules["horizon_days"], "rules.horizon_days")
        if legacy_horizon not in ALLOWED_ROOM_HORIZONS:
            raise ConfigError("rules.horizon_days is not a recognized room horizon")

    return BookingRules(
        minimum_duration_minutes=minimum,
        maximum_duration_minutes=maximum,
        default_duration_minutes=default_duration,
        duration_increment_minutes=increment,
        same_room_gap_minutes=gap,
        rolling_quota_minutes=rolling_quota,
        peak_enabled=peak_enabled,
        peak_window=peak_window,
        peak_weekdays=tuple(sorted(peak_weekdays)),
        peak_daily_quota_minutes=peak_quota,
    )


def _parse_preferences(
    booking_value: Any,
    top_level_disabled_dates: Any,
    rooms_fallback: bool | None,
    rooms: tuple[RoomPolicy, ...],
) -> BookingPreferences:
    booking = _mapping(booking_value, "booking", default={})
    _reject_unknown(
        booking,
        {
            "max_horizon_days",
            "preferred_times",
            "preferred_windows",
            "preferred_days",
            "duration_hours",
            "default_duration_minutes",
            "max_bookings_per_run",
            "strict",
            "strict_mode",
            "strict_preferences",
            "fallback",
            "disabled_dates",
        },
        "booking",
    )
    strict_keys = [key for key in ("strict", "strict_mode", "strict_preferences") if key in booking]
    if len(strict_keys) > 1:
        raise ConfigError("booking: specify only one strict preference setting")
    strict = _boolean(
        booking.get(strict_keys[0], False) if strict_keys else False,
        "booking.strict",
    )

    if "fallback" in booking:
        if rooms_fallback is not None:
            raise ConfigError("fallback is defined in both rooms and booking; define it once")
        fallback = _boolean(booking["fallback"], "booking.fallback")
    else:
        fallback = rooms_fallback if rooms_fallback is not None else False
    if fallback:
        raise ConfigError(
            "unlisted room fallback is unsafe because every bookable room "
            "requires an explicitly verified horizon policy"
        )

    if "preferred_times" in booking and "preferred_windows" in booking:
        raise ConfigError("booking: specify only one of preferred_times or preferred_windows")

    windows: list[tuple[TimeWindow, ...]] = [tuple() for _ in range(7)]
    if "preferred_windows" in booking:
        raw_by_day = _mapping(booking["preferred_windows"], "booking.preferred_windows")
        seen_days: set[int] = set()
        for raw_day, raw_windows in raw_by_day.items():
            weekday = _weekday(raw_day, f"booking.preferred_windows.{raw_day}")
            if weekday in seen_days:
                raise ConfigError(
                    f"booking.preferred_windows defines {WEEKDAY_NAMES[weekday]} twice"
                )
            seen_days.add(weekday)
            windows[weekday] = _parse_windows(
                raw_windows,
                f"booking.preferred_windows.{WEEKDAY_NAMES[weekday]}",
            )
    elif "preferred_times" in booking:
        shared_windows = _parse_windows(
            booking["preferred_times"],
            "booking.preferred_times",
        )
        raw_days = _sequence(
            booking.get("preferred_days", list(range(7))),
            "booking.preferred_days",
        )
        selected_days = (
            tuple(range(7))
            if not raw_days
            else tuple(
                _weekday(item, f"booking.preferred_days[{index}]")
                for index, item in enumerate(raw_days)
            )
        )
        if len(set(selected_days)) != len(selected_days):
            raise ConfigError("booking.preferred_days contains duplicates")
        for weekday in selected_days:
            windows[weekday] = shared_windows
    elif "preferred_days" in booking:
        raise ConfigError("booking.preferred_days has no effect without preferred_times")

    if strict and not any(windows):
        raise ConfigError("strict preferences require at least one preferred window")

    if top_level_disabled_dates is not None and "disabled_dates" in booking:
        raise ConfigError("disabled_dates is defined both at top level and in booking")
    raw_disabled = (
        top_level_disabled_dates
        if top_level_disabled_dates is not None
        else booking.get("disabled_dates", [])
    )
    disabled_items = _sequence(raw_disabled, "disabled_dates")
    disabled: set[date] = set()
    for index, item in enumerate(disabled_items):
        if not isinstance(item, str) and type(item) is not date:
            raise ConfigError(f"disabled_dates[{index}] must be an ISO date")
        try:
            parsed = item if isinstance(item, date) else date.fromisoformat(item)
        except ValueError as exc:
            raise ConfigError(f"disabled_dates[{index}] must use YYYY-MM-DD") from exc
        disabled.add(parsed)

    maximum_bookings = _integer(
        booking.get("max_bookings_per_run", 5),
        "booking.max_bookings_per_run",
        minimum=1,
        maximum=100,
    )
    enabled_horizons = [room.horizon_days for room in rooms if room.enabled]
    configured_maximum = _integer(
        booking.get("max_horizon_days", max(enabled_horizons)),
        "booking.max_horizon_days",
        minimum=1,
        maximum=max(ALLOWED_ROOM_HORIZONS),
    )
    if configured_maximum < max(enabled_horizons):
        raise ConfigError("booking.max_horizon_days is less than an enabled room horizon")

    return BookingPreferences(
        windows_by_weekday=tuple(windows),
        strict=strict,
        fallback=fallback,
        disabled_dates=frozenset(disabled),
        max_bookings_per_run=maximum_bookings,
        max_horizon_days=configured_maximum,
    )


def _parse_schedule(value: Any) -> ScheduleConfig:
    data = _mapping(value, "schedule", default={})
    _reject_unknown(
        data,
        {
            "enabled",
            "cadence_minutes",
            "check_interval_hours",
            "lead_seconds",
            "max_lateness_seconds",
            "active_start",
            "active_end",
            "timeout_minutes",
        },
        "schedule",
    )
    enabled = _boolean(data.get("enabled", True), "schedule.enabled")
    if "cadence_minutes" in data and "check_interval_hours" in data:
        raise ConfigError("schedule: specify only cadence_minutes or check_interval_hours")
    if "check_interval_hours" in data:
        cadence = _hours_to_minutes(
            data["check_interval_hours"],
            "schedule.check_interval_hours",
        )
    else:
        cadence = _integer(
            data.get("cadence_minutes", 15),
            "schedule.cadence_minutes",
            minimum=1,
            maximum=24 * 60,
        )
    if cadence != 15:
        raise ConfigError(
            "schedule cadence must be 15 minutes so runs align with Asimut's "
            "booking increment and the canonical Windows task"
        )
    lead = _integer(
        data.get("lead_seconds", 120),
        "schedule.lead_seconds",
        minimum=0,
        maximum=24 * 60 * 60,
    )
    lateness = _integer(
        data.get("max_lateness_seconds", 180),
        "schedule.max_lateness_seconds",
        minimum=0,
        maximum=24 * 60 * 60,
    )
    active_start = _parse_clock(
        data.get("active_start", "07:00"),
        "schedule.active_start",
        allow_24=False,
    )
    active_end = _parse_clock(
        data.get("active_end", "22:15"),
        "schedule.active_end",
        allow_24=True,
    )
    if active_end <= active_start:
        raise ConfigError("schedule.active_end must be later than active_start")
    if (active_end - active_start) % cadence:
        raise ConfigError("schedule.active_end must align to cadence_minutes from active_start")
    if lead + lateness >= cadence * 60:
        raise ConfigError(
            "schedule lead_seconds plus max_lateness_seconds must be less than one cadence interval"
        )
    timeout_minutes = _integer(
        data.get("timeout_minutes", 12),
        "schedule.timeout_minutes",
        minimum=1,
        maximum=24 * 60,
    )
    return ScheduleConfig(
        enabled=enabled,
        cadence_minutes=cadence,
        lead_seconds=lead,
        max_lateness_seconds=lateness,
        active_start_minute=active_start,
        active_end_minute=active_end,
        timeout_minutes=timeout_minutes,
    )


def _parse_browser(value: Any, project_root: Path) -> BrowserConfig:
    data = _mapping(value, "browser", default={})
    _reject_unknown(
        data,
        {
            "state_path",
            "executable_path",
            "artifacts_dir",
            "database_path",
            "lock_path",
            "navigation_timeout_ms",
            "action_timeout_ms",
            "timezone",
        },
        "browser",
    )
    state_path = _resolved_path(
        data.get("state_path", "data/browser_state/state.json"),
        project_root,
        "browser.state_path",
    )
    executable_path = _optional_resolved_path(
        data.get("executable_path"),
        project_root,
        "browser.executable_path",
    )
    artifacts_dir = _resolved_path(
        data.get("artifacts_dir", "data/artifacts"),
        project_root,
        "browser.artifacts_dir",
    )
    database_path = _resolved_path(
        data.get("database_path", "data/asimut_booker.sqlite3"),
        project_root,
        "browser.database_path",
    )
    lock_path = _resolved_path(
        data.get("lock_path", "data/asimut_booker.lock"),
        project_root,
        "browser.lock_path",
    )
    navigation_timeout = _integer(
        data.get("navigation_timeout_ms", 30_000),
        "browser.navigation_timeout_ms",
        minimum=1_000,
        maximum=10 * 60_000,
    )
    action_timeout = _integer(
        data.get("action_timeout_ms", 10_000),
        "browser.action_timeout_ms",
        minimum=500,
        maximum=10 * 60_000,
    )
    timezone_name = _nonempty_string(
        data.get("timezone", "Europe/London"),
        "browser.timezone",
    )
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"browser.timezone is unknown: {timezone_name}") from exc
    if timezone_name != "Europe/London":
        raise ConfigError(
            "browser.timezone must be Europe/London because RWCMD booking "
            "horizons and calendar dates are defined in the institution's "
            "local time"
        )

    return BrowserConfig(
        state_path=state_path,
        executable_path=executable_path,
        artifacts_dir=artifacts_dir,
        database_path=database_path,
        lock_path=lock_path,
        navigation_timeout_ms=navigation_timeout,
        action_timeout_ms=action_timeout,
        timezone=timezone_name,
    )


def _parse_notifications(value: Any) -> NotificationConfig:
    data = _mapping(value, "notifications", default={})
    _reject_unknown(
        data,
        {"enabled", "provider", "base_url", "topic", "priority"},
        "notifications",
    )
    enabled = _boolean(data.get("enabled", False), "notifications.enabled")
    provider = _nonempty_string(
        data.get("provider", "ntfy"),
        "notifications.provider",
    ).lower()
    if provider != "ntfy":
        raise ConfigError("notifications.provider currently supports only 'ntfy'")
    base_url = _https_url(
        data.get("base_url", "https://ntfy.sh"),
        "notifications.base_url",
    ).rstrip("/")
    raw_topic = data.get("topic")
    topic = None if raw_topic is None else _nonempty_string(raw_topic, "notifications.topic")
    if topic is not None and not _NOTIFICATION_TOPIC_RE.fullmatch(topic):
        raise ConfigError("notifications.topic may contain only letters, numbers, '-' and '_'")
    if enabled and (topic is None or len(topic) < 16):
        raise ConfigError(
            "enabled notifications require a non-guessable topic of at least 16 characters"
        )
    priority = _nonempty_string(
        data.get("priority", "default"),
        "notifications.priority",
    ).lower()
    if priority not in {"min", "low", "default", "high", "max", "urgent"}:
        raise ConfigError("notifications.priority is not recognized")
    return NotificationConfig(enabled, provider, base_url, topic, priority)


def _parse_logging(value: Any, project_root: Path) -> LoggingConfig:
    data = _mapping(value, "logging", default={})
    _reject_unknown(data, {"level", "retention_days", "directory"}, "logging")
    level = _nonempty_string(data.get("level", "INFO"), "logging.level").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError("logging.level is not recognized")
    retention = _integer(
        data.get("retention_days", 30),
        "logging.retention_days",
        minimum=1,
        maximum=3650,
    )
    directory = _resolved_path(
        data.get("directory", "logs"),
        project_root,
        "logging.directory",
    )
    return LoggingConfig(level, retention, directory)


def _parse_windows(value: Any, path: str) -> tuple[TimeWindow, ...]:
    entries = _sequence(value, path)
    windows: list[TimeWindow] = []
    for index, raw_entry in enumerate(entries):
        item_path = f"{path}[{index}]"
        entry = _mapping(raw_entry, item_path)
        _reject_unknown(entry, {"start", "end"}, item_path)
        window = TimeWindow(
            _parse_clock(entry.get("start"), f"{item_path}.start", allow_24=False),
            _parse_clock(entry.get("end"), f"{item_path}.end", allow_24=True),
        )
        windows.append(window)
    windows.sort(key=lambda item: item.start_minute)
    for previous, current in zip(windows, windows[1:], strict=False):
        if current.start_minute < previous.end_minute:
            raise ConfigError(f"{path} contains overlapping preferred windows")
    return tuple(windows)


def _project_root_for(config_path: Path) -> Path:
    if config_path.parent.name.lower() == "config":
        return config_path.parent.parent.resolve()
    return config_path.parent.resolve()


def _mapping(
    value: Any, path: str, *, default: Mapping[str, Any] | None = None
) -> Mapping[str, Any]:
    if value is None and default is not None:
        return default
    if not isinstance(value, Mapping):
        raise ConfigError(f"{path} must be a mapping")
    for key in value:
        if not isinstance(key, str):
            raise ConfigError(f"{path} keys must be strings")
    return value


def _sequence(value: Any, path: str) -> Sequence[Any]:
    if value is None:
        raise ConfigError(f"{path} is required")
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ConfigError(f"{path} must be a list")
    return value


def _reject_unknown(data: Mapping[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(data).difference(allowed))
    if unknown:
        raise ConfigError(f"{path} contains unknown keys: {', '.join(unknown)}")


def _nonempty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path} must be a non-empty string")
    return value.strip()


def _boolean(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise ConfigError(f"{path} must be true or false")
    return value


def _integer(
    value: Any,
    path: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if type(value) is not int:
        raise ConfigError(f"{path} must be an integer")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{path} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{path} must be no more than {maximum}")
    return value


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path} must be a number")
    return float(value)


def _hours_to_minutes(value: Any, path: str, *, allow_zero: bool = False) -> int:
    hours = _number(value, path)
    minimum = 0 if allow_zero else 1 / 60
    if not minimum <= hours <= 24 * 7:
        raise ConfigError(f"{path} is outside the supported range")
    raw_minutes = hours * 60
    rounded = round(raw_minutes)
    if abs(raw_minutes - rounded) > 1e-9:
        raise ConfigError(f"{path} must resolve to a whole number of minutes")
    return int(rounded)


def _parse_clock(value: Any, path: str, *, allow_24: bool) -> int:
    text = _nonempty_string(value, path)
    match = re.fullmatch(r"(\d{2}):(\d{2})", text)
    if not match:
        raise ConfigError(f"{path} must use zero-padded HH:MM format")
    hour, minute = int(match.group(1)), int(match.group(2))
    if allow_24 and hour == 24 and minute == 0:
        return 24 * 60
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ConfigError(f"{path} is not a valid wall-clock time")
    return hour * 60 + minute


def _format_minutes(value: int) -> str:
    if value == 24 * 60:
        return "24:00"
    return f"{value // 60:02d}:{value % 60:02d}"


def _weekday(value: Any, path: str) -> int:
    if type(value) is int:
        if 0 <= value <= 6:
            return value
        raise ConfigError(f"{path} weekday number must be in the range 0..6")
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized.isdigit():
            return _weekday(int(normalized), path)
        if normalized in WEEKDAY_NAMES:
            return WEEKDAY_NAMES.index(normalized)
    raise ConfigError(f"{path} must be a weekday name or number from 0 (Monday) to 6 (Sunday)")


def _normalize_room_id(value: Any, path: str) -> str:
    room_id = _nonempty_string(value, path).upper()
    if not _ROOM_ID_RE.fullmatch(room_id):
        raise ConfigError(f"{path} is not a recognized room identifier: {room_id}")
    return room_id


def _https_url(value: Any, path: str) -> str:
    text = _nonempty_string(value, path)
    parsed = urlsplit(text)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ConfigError(f"{path} must be an absolute https URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigError(f"{path} must not contain credentials, a query string, or a fragment")
    return text


def _resolved_path(value: Any, root: Path, path: str) -> Path:
    text = _nonempty_string(value, path)
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve()


def _optional_resolved_path(value: Any, root: Path, path: str) -> Path | None:
    if value is None:
        return None
    return _resolved_path(value, root, path)
