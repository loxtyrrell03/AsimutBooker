"""Configuration loader for AsimutBooker."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass
class RoomConfig:
    """Configuration for a single room."""
    room_id: str
    horizon: int  # Days in advance this room can be booked


class Config:
    """Configuration manager for AsimutBooker."""

    def __init__(self, config_path: str | None = None):
        """Load configuration from YAML file and environment variables."""
        load_dotenv()

        # Determine config file path
        if config_path:
            self.config_path = Path(config_path)
        else:
            # Default: look for config.yaml in config/ directory
            project_root = Path(__file__).parent.parent
            self.config_path = project_root / "config" / "config.yaml"

        # Load YAML config
        if not self.config_path.exists():
            example_path = self.config_path.parent / "config.example.yaml"
            raise FileNotFoundError(
                f"Config file not found: {self.config_path}\n"
                f"Please copy {example_path} to {self.config_path} and customize it."
            )

        with open(self.config_path, "r", encoding="utf-8") as f:
            self._config = yaml.safe_load(f)

        # Set up paths
        self.project_root = Path(__file__).parent.parent
        self.data_dir = self.project_root / "data"
        self.browser_state_dir = self.data_dir / "browser_state"
        self.logs_dir = self.project_root / "logs"

        # Ensure directories exist
        self.browser_state_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        # Parse room configurations
        self._parse_rooms()

    def _parse_rooms(self) -> None:
        """Parse room configurations from the config file."""
        self._rooms: list[RoomConfig] = []
        default_horizon = self._config.get("booking", {}).get("max_horizon_days", 7)

        priority_rooms = self._config.get("rooms", {}).get("priority", [])

        for room_entry in priority_rooms:
            if isinstance(room_entry, dict):
                # New format: {"room": "B0.35", "horizon": 7}
                room_id = room_entry.get("room")
                horizon = room_entry.get("horizon", default_horizon)
            elif isinstance(room_entry, str):
                # Old format: just "B0.35"
                room_id = room_entry
                horizon = default_horizon
            else:
                continue

            if room_id:
                self._rooms.append(RoomConfig(room_id=room_id, horizon=horizon))

    @property
    def asimut_url(self) -> str:
        """Get the Asimut institution URL."""
        return self._config["institution"]["url"]

    @property
    def location(self) -> str:
        """Get the location/category to book from."""
        return self._config["institution"]["location"]

    @property
    def rooms(self) -> list[RoomConfig]:
        """Get all configured rooms with their horizons."""
        return self._rooms

    @property
    def priority_rooms(self) -> list[str]:
        """Get the list of priority room IDs in order of preference."""
        return [r.room_id for r in self._rooms]

    def get_room_horizon(self, room_id: str) -> int:
        """Get the booking horizon for a specific room."""
        for room in self._rooms:
            if room.room_id == room_id:
                return room.horizon
        return self.max_horizon_days

    @property
    def excluded_rooms(self) -> list[str]:
        """Get the list of rooms to exclude from booking."""
        return self._config.get("rooms", {}).get("exclude", [])

    @property
    def fallback_enabled(self) -> bool:
        """Whether to book any available room if priorities are full."""
        return self._config.get("rooms", {}).get("fallback", False)

    @property
    def max_horizon_days(self) -> int:
        """Maximum days in advance to look for bookings."""
        return self._config.get("booking", {}).get("max_horizon_days", 7)

    @property
    def preferred_times(self) -> list[dict[str, str]]:
        """Get preferred booking time slots."""
        return self._config.get("booking", {}).get("preferred_times", [])

    @property
    def duration_hours(self) -> int:
        """Default booking duration in hours."""
        return self._config.get("booking", {}).get("duration_hours", 2)

    @property
    def preferred_days(self) -> list[int]:
        """Days of week to book (0=Monday, 6=Sunday). Empty = all days."""
        return self._config.get("booking", {}).get("preferred_days", [])

    @property
    def max_bookings_per_run(self) -> int:
        """Maximum bookings to make per run."""
        return self._config.get("booking", {}).get("max_bookings_per_run", 5)

    @property
    def check_interval_hours(self) -> int:
        """How often to check for availability."""
        return self._config.get("schedule", {}).get("check_interval_hours", 1)

    @property
    def run_at_times(self) -> list[str]:
        """Specific times to run checks (overrides interval if set)."""
        return self._config.get("schedule", {}).get("run_at_times", [])

    @property
    def log_level(self) -> str:
        """Logging level."""
        return self._config.get("logging", {}).get("level", "INFO")

    @property
    def log_retention_days(self) -> int:
        """How many days to keep logs."""
        return self._config.get("logging", {}).get("retention_days", 30)

    @property
    def email(self) -> str | None:
        """Get email from environment (optional)."""
        return os.getenv("ASIMUT_EMAIL")

    def get(self, key: str, default: Any = None) -> Any:
        """Get a raw config value by dot-notation key."""
        keys = key.split(".")
        value = self._config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    # Keep old property for backward compatibility
    @property
    def horizon_days(self) -> int:
        """Alias for max_horizon_days (backward compatibility)."""
        return self.max_horizon_days

    # Booking Rules Properties
    @property
    def rolling_quota(self) -> int:
        """Rolling booking quota (total active bookings allowed)."""
        return self._config.get("rules", {}).get("rolling_quota", 28)

    @property
    def peak_hours_enabled(self) -> bool:
        """Whether peak hours restrictions are enabled."""
        return self._config.get("rules", {}).get("peak_hours", {}).get("enabled", True)

    @property
    def peak_hours_max(self) -> float:
        """Maximum hours of peak time allowed per rolling period."""
        return self._config.get("rules", {}).get("peak_hours", {}).get("max_hours", 2)

    @property
    def peak_hours_start(self) -> str:
        """Peak hours start time."""
        return self._config.get("rules", {}).get("peak_hours", {}).get("start", "09:00")

    @property
    def peak_hours_end(self) -> str:
        """Peak hours end time."""
        return self._config.get("rules", {}).get("peak_hours", {}).get("end", "16:00")

    @property
    def peak_hours_days(self) -> list[int]:
        """Days when peak hours apply (0=Monday, 4=Friday)."""
        return self._config.get("rules", {}).get("peak_hours", {}).get("days", [0, 1, 2, 3, 4])

    @property
    def min_duration_minutes(self) -> int:
        """Minimum booking duration in minutes."""
        return self._config.get("rules", {}).get("duration", {}).get("min_minutes", 30)

    @property
    def max_duration_minutes(self) -> int:
        """Maximum booking duration in minutes."""
        return self._config.get("rules", {}).get("duration", {}).get("max_minutes", 120)

    @property
    def same_room_gap_minutes(self) -> int:
        """Minimum gap between same-room bookings in minutes."""
        return self._config.get("rules", {}).get("same_room_gap_minutes", 60)
