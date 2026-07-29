from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from asimut_booker.config import (
    AppConfig,
    ConfigError,
    UnknownRoomError,
    default_config_path,
)

BASE_CONFIG = """
institution:
  base_url: https://rwcmd.asimut.net/
  location_group_id: 10
  location_category: Music Practice Rooms - AHC
rooms:
  policies:
    - room_id: B0.29
      horizon_days: 5
      room_type: piano
      priority: 0
    - room_id: B1.09
      horizon_days: 3
      room_type: practice
      priority: 1
booking:
  max_horizon_days: 7
  default_duration_minutes: 90
  max_bookings_per_run: 4
  strict_preferences: true
  preferred_windows:
    monday:
      - start: "09:00"
        end: "11:00"
      - start: "14:00"
        end: "16:00"
    saturday:
      - start: "10:00"
        end: "12:00"
  disabled_dates:
    - 2026-08-01
rules:
  rolling_quota_hours: 28
  duration:
    minimum_minutes: 30
    maximum_minutes: 120
    increment_minutes: 15
  same_room_gap_minutes: 60
  peak_hours:
    enabled: true
    start: "09:00"
    end: "16:00"
    weekdays: [monday, tuesday, wednesday, thursday, friday]
    daily_quota_minutes: 120
schedule:
  cadence_minutes: 15
  lead_seconds: 120
  max_lateness_seconds: 180
browser:
  state_path: data/browser_state/state.json
  database_path: data/booker.sqlite3
  lock_path: data/booker.lock
  artifacts_dir: data/artifacts
  timezone: Europe/London
"""


def write_config(tmp_path: Path, text: str = BASE_CONFIG) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_complete_typed_config_and_resolves_paths(tmp_path: Path) -> None:
    config = AppConfig.load(write_config(tmp_path))

    assert config.base_url == "https://rwcmd.asimut.net"
    assert config.location_group_id == 10
    assert config.institution.overview_url.endswith("/overview?locationGroupId=10")
    assert [room.room_id for room in config.enabled_rooms] == ["B0.29", "B1.09"]
    assert config.room_policy("b0.29").horizon_days == 5
    assert config.rules.default_duration_minutes == 90
    assert config.rules.rolling_quota_minutes == 28 * 60
    assert config.rules.peak_weekdays == (0, 1, 2, 3, 4)
    assert config.preferred_windows_for(0)[1].start_text == "14:00"
    assert config.preferred_windows_for(5)[0].end_text == "12:00"
    assert not config.is_date_enabled(date(2026, 8, 1))
    assert config.schedule.cadence_minutes == 15
    assert config.schedule.enabled is True
    assert config.schedule.active_start_minute == 7 * 60
    assert config.schedule.active_end_minute == 22 * 60 + 15
    assert config.schedule.timeout_minutes == 12
    assert config.browser.database_path == (tmp_path / "data/booker.sqlite3").resolve()
    assert config.notifications.enabled is False

    domain_rules = config.to_domain_rules()
    assert domain_rules.minimum_duration == 30
    assert domain_rules.maximum_duration == 120
    assert domain_rules.booking_increment == 15
    assert domain_rules.future_quota_minutes == 28 * 60
    assert domain_rules.peak_window.start == 9 * 60
    assert domain_rules.peak_weekdays == frozenset({0, 1, 2, 3, 4})
    assert domain_rules.same_room_gap_minutes == 60


def test_default_path_points_to_repository_config() -> None:
    path = default_config_path()

    assert path.name == "config.yaml"
    assert path.parent.name == "config"
    assert path.parent.parent.name == "AsimutBooker"


def test_existing_legacy_shape_is_validated_without_permissive_room_defaults(
    tmp_path: Path,
) -> None:
    text = """
institution:
  url: https://rwcmd.asimut.net/
  location: Music Practice Rooms - AHC
rooms:
  priority:
    - room: B0.29
      horizon: 5
  exclude: []
  fallback: false
booking:
  max_horizon_days: 7
  preferred_times:
    - start: "09:00"
      end: "11:00"
  duration_hours: 2
  preferred_days: []
  max_bookings_per_run: 5
rules:
  rolling_quota: 28
  horizon_days: 7
  peak_hours:
    enabled: true
    max_hours: 2
    start: "09:00"
    end: "16:00"
    days: [0, 1, 2, 3, 4]
  duration:
    min_minutes: 30
    max_minutes: 120
  same_room_gap_minutes: 60
schedule:
  check_interval_hours: 0.25
logging:
  level: INFO
  retention_days: 30
"""
    config = AppConfig.load(write_config(tmp_path, text))

    assert config.room_policy("B0.29").horizon_days == 5
    assert config.rules.rolling_quota_hours == 28
    assert config.schedule.cadence_minutes == 15
    assert len(config.preferred_windows_for(6)) == 1


def test_unknown_room_fails_closed(tmp_path: Path) -> None:
    config = AppConfig.load(write_config(tmp_path))

    with pytest.raises(UnknownRoomError, match="explicitly verified"):
        config.room_policy("B9.99")
    assert config.try_room_policy("B9.99") is None


@pytest.mark.parametrize("horizon", [0, 4, 6, 8, "5"])
def test_invalid_or_untyped_horizon_is_rejected(tmp_path: Path, horizon: object) -> None:
    replacement = f"horizon_days: {horizon!r}".replace("'", '"')
    text = BASE_CONFIG.replace("horizon_days: 5", replacement, 1)

    with pytest.raises(ConfigError, match="horizon_days"):
        AppConfig.load(write_config(tmp_path, text))


def test_missing_horizon_is_rejected_instead_of_defaulting(tmp_path: Path) -> None:
    text = BASE_CONFIG.replace("      horizon_days: 5\n", "", 1)

    with pytest.raises(ConfigError, match="horizon_days is required"):
        AppConfig.load(write_config(tmp_path, text))


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    text = BASE_CONFIG + "\nmisspelled_notifications: true\n"

    with pytest.raises(ConfigError, match="unknown keys"):
        AppConfig.load(write_config(tmp_path, text))


def test_overlapping_preferred_windows_are_rejected(tmp_path: Path) -> None:
    text = BASE_CONFIG.replace(
        '      - start: "14:00"\n        end: "16:00"',
        '      - start: "10:30"\n        end: "16:00"',
    )

    with pytest.raises(ConfigError, match="overlapping"):
        AppConfig.load(write_config(tmp_path, text))


def test_duration_must_follow_configured_increment(tmp_path: Path) -> None:
    text = BASE_CONFIG.replace("default_duration_minutes: 90", "default_duration_minutes: 91")

    with pytest.raises(ConfigError, match="multiple"):
        AppConfig.load(write_config(tmp_path, text))


def test_notifications_are_disabled_by_default_and_require_secret_topic(
    tmp_path: Path,
) -> None:
    short_topic = (
        BASE_CONFIG
        + """
notifications:
  enabled: true
  topic: short-topic
"""
    )
    with pytest.raises(ConfigError, match="non-guessable"):
        AppConfig.load(write_config(tmp_path, short_topic))

    secure_topic = (
        BASE_CONFIG
        + """
notifications:
  enabled: true
  topic: 35c442eeb7174bd09aa09da9
"""
    )
    config = AppConfig.load(write_config(tmp_path, secure_topic))
    assert config.notifications.enabled is True


def test_insecure_or_credential_bearing_base_url_is_rejected(
    tmp_path: Path,
) -> None:
    for url in (
        "http://rwcmd.asimut.net/",
        "https://user:password@rwcmd.asimut.net/",
        "https://rwcmd.asimut.net/?token=secret",
    ):
        text = BASE_CONFIG.replace("https://rwcmd.asimut.net/", url)
        with pytest.raises(ConfigError, match="institution.base_url"):
            AppConfig.load(write_config(tmp_path, text))


def test_duplicate_room_and_priority_are_rejected(tmp_path: Path) -> None:
    duplicate_room = BASE_CONFIG.replace("room_id: B1.09", "room_id: B0.29")
    with pytest.raises(ConfigError, match="duplicate room"):
        AppConfig.load(write_config(tmp_path, duplicate_room))

    duplicate_priority = BASE_CONFIG.replace("priority: 1", "priority: 0")
    with pytest.raises(ConfigError, match="duplicates priority"):
        AppConfig.load(write_config(tmp_path, duplicate_priority))


def test_schedule_active_window_and_timeout_are_strictly_validated(
    tmp_path: Path,
) -> None:
    configured = BASE_CONFIG.replace(
        "  cadence_minutes: 15\n",
        "  enabled: false\n"
        "  cadence_minutes: 15\n"
        '  active_start: "08:30"\n'
        '  active_end: "21:45"\n'
        "  timeout_minutes: 7\n",
    )
    config = AppConfig.load(write_config(tmp_path, configured))

    assert config.schedule.enabled is False
    assert config.schedule.active_start_minute == 8 * 60 + 30
    assert config.schedule.active_end_minute == 21 * 60 + 45
    assert config.schedule.timeout_minutes == 7

    backwards = configured.replace('active_end: "21:45"', 'active_end: "08:00"')
    with pytest.raises(ConfigError, match="later than active_start"):
        AppConfig.load(write_config(tmp_path, backwards))

    off_grid = configured.replace('active_end: "21:45"', 'active_end: "21:44"')
    with pytest.raises(ConfigError, match="align to cadence"):
        AppConfig.load(write_config(tmp_path, off_grid))

    overlapping_windows = configured.replace(
        "  max_lateness_seconds: 180",
        "  max_lateness_seconds: 800",
    )
    with pytest.raises(ConfigError, match="less than one cadence"):
        AppConfig.load(write_config(tmp_path, overlapping_windows))


def test_institution_timezone_cannot_diverge_from_horizon_math(tmp_path: Path) -> None:
    text = BASE_CONFIG.replace("timezone: Europe/London", "timezone: Europe/Paris")

    with pytest.raises(ConfigError, match="must be Europe/London"):
        AppConfig.load(write_config(tmp_path, text))


def test_disabled_peak_policy_maps_to_no_domain_peak_weekdays(
    tmp_path: Path,
) -> None:
    text = BASE_CONFIG.replace(
        "    enabled: true\n    start:",
        "    enabled: false\n    start:",
    )
    config = AppConfig.load(write_config(tmp_path, text))

    domain_rules = config.to_domain_rules()
    assert domain_rules.peak_weekdays == frozenset()
    assert domain_rules.peak_limit_minutes == 0


def test_requires_an_enabled_room_and_rejects_unlisted_fallback(
    tmp_path: Path,
) -> None:
    all_disabled = BASE_CONFIG.replace(
        "      room_type: piano\n",
        "      room_type: piano\n      enabled: false\n",
    ).replace(
        "      room_type: practice\n",
        "      room_type: practice\n      enabled: false\n",
    )
    with pytest.raises(ConfigError, match="enable at least one room"):
        AppConfig.load(write_config(tmp_path, all_disabled))

    booking_fallback = BASE_CONFIG.replace(
        "  max_horizon_days: 7\n",
        "  max_horizon_days: 7\n  fallback: true\n",
    )
    with pytest.raises(ConfigError, match="unlisted room fallback is unsafe"):
        AppConfig.load(write_config(tmp_path, booking_fallback))

    duplicate_source = booking_fallback.replace(
        "rooms:\n",
        "rooms:\n  fallback: true\n",
    )
    with pytest.raises(ConfigError, match="defined in both"):
        AppConfig.load(write_config(tmp_path, duplicate_source))


def test_disabled_dates_reject_timestamps_that_would_not_match_dates(
    tmp_path: Path,
) -> None:
    text = BASE_CONFIG.replace(
        "    - 2026-08-01",
        "    - 2026-08-01T10:00:00",
    )

    with pytest.raises(ConfigError, match="ISO date"):
        AppConfig.load(write_config(tmp_path, text))
