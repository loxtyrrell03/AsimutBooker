"""Strict room-preference validation and live-catalog filtering helpers.

This module deliberately has no GUI, Playwright, or persistence dependencies so
the desktop editor and autonomous booker can share exactly the same contract.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, MutableMapping, Sequence


ROOM_PREFERENCES_KEY = "room_preferences"
MAX_ROOM_NAME_LENGTH = 120

# This is the built-in priority order used only when the user has not supplied
# an explicit order. Named rooms can disappear from a live catalog temporarily;
# keeping them here lets them resume their original rank when they reappear.
DEFAULT_ORDERED_ROOMS = (
    "Weston Gallery",
    "Corus Recital Room",
    "B0.29",
    "B1.09",
    "B0.11",
    "B1.16",
    "B0.15",
    "B0.13",
    "B0.14",
    "B0.27",
    "B1.14",
    "B1.17",
    "A3.39",
    "B0.16",
    "B1.20",
    "B1.18",
    "B1.21",
    "B1.19",
    "B1.06",
    "B1.07",
    "B1.08",
    "B1.10",
    "B1.11",
    "B1.15",
    "B0.23",
)

MIN_BLOCK_MINUTES = 30
MAX_BLOCK_MINUTES = 120
BLOCK_STEP_MINUTES = 15

_PREFERENCE_FIELDS = {
    "ordered_rooms",
    "excluded_rooms",
    "acceptable_instrument_tags",
    "acceptable_room_type_tags",
    "required_feature_terms",
    "minimum_block_minutes",
    "allow_fragmented_sessions",
}


class RoomPreferencesError(ValueError):
    """Raised when saved room preferences are malformed or ambiguous."""


@dataclass(frozen=True)
class RoomPreferences:
    """Validated room preferences in a JSON-serializable representation."""

    ordered_rooms: tuple[str, ...] = DEFAULT_ORDERED_ROOMS
    excluded_rooms: tuple[str, ...] = ()
    acceptable_instrument_tags: tuple[str, ...] = ()
    acceptable_room_type_tags: tuple[str, ...] = ()
    required_feature_terms: tuple[str, ...] = ()
    minimum_block_minutes: int = MIN_BLOCK_MINUTES
    allow_fragmented_sessions: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Return the exact JSON object persisted under ``room_preferences``."""

        return room_preferences_to_dict(self)


def validate_room_name(value: Any, label: str = "room") -> str:
    """Validate one exact site room name without assuming a code-shaped ID."""

    if not isinstance(value, str):
        raise RoomPreferencesError(f"{label} must be a room name string")
    if not value or value != value.strip():
        raise RoomPreferencesError(f"{label} must be a non-empty trimmed room name")
    if len(value) > MAX_ROOM_NAME_LENGTH:
        raise RoomPreferencesError(
            f"{label} must be at most {MAX_ROOM_NAME_LENGTH} characters"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise RoomPreferencesError(f"{label} contains control characters")
    if not any(character.isalnum() for character in value):
        raise RoomPreferencesError(f"{label} must contain a letter or number")
    return value


def _validate_room_sequence(
    value: Any,
    label: str,
    *,
    require_json_list: bool,
    allow_empty: bool,
) -> tuple[str, ...]:
    if require_json_list:
        valid_container = isinstance(value, list)
    else:
        valid_container = isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        )
    if not valid_container:
        raise RoomPreferencesError(f"{label} must be a JSON list of room names")
    if not value and not allow_empty:
        raise RoomPreferencesError(f"{label} must contain at least one room")

    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        room = validate_room_name(item, f"{label}[{index}]")
        if room in seen:
            raise RoomPreferencesError(f"{label} contains duplicate room {room}")
        seen.add(room)
        result.append(room)
    return tuple(result)


def _validate_text_list(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RoomPreferencesError(f"{label} must be a JSON list of strings")

    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        item_label = f"{label}[{index}]"
        if not isinstance(item, str):
            raise RoomPreferencesError(f"{item_label} must be a string")
        if not item or item != item.strip() or not item.isprintable():
            raise RoomPreferencesError(
                f"{item_label} must be a non-empty, trimmed printable string"
            )
        normalized = item.casefold()
        if normalized in seen:
            raise RoomPreferencesError(
                f"{label} contains duplicate value {item!r}"
            )
        seen.add(normalized)
        result.append(item)
    return tuple(result)


def _validate_minimum_block(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RoomPreferencesError(
            "room_preferences.minimum_block_minutes must be an integer"
        )
    if not MIN_BLOCK_MINUTES <= value <= MAX_BLOCK_MINUTES:
        raise RoomPreferencesError(
            "room_preferences.minimum_block_minutes must be between "
            f"{MIN_BLOCK_MINUTES} and {MAX_BLOCK_MINUTES}"
        )
    if value % BLOCK_STEP_MINUTES:
        raise RoomPreferencesError(
            "room_preferences.minimum_block_minutes must use 15-minute steps"
        )
    return value


def _parse_room_preferences(
    raw: Mapping[str, Any],
    *,
    default_ordered_rooms: Sequence[str],
) -> RoomPreferences:
    unknown = set(raw) - _PREFERENCE_FIELDS
    if unknown:
        raise RoomPreferencesError(
            "Unsupported room preference setting(s): " + ", ".join(sorted(unknown))
        )

    defaults = _validate_room_sequence(
        default_ordered_rooms,
        "default_ordered_rooms",
        require_json_list=False,
        allow_empty=False,
    )
    ordered_rooms = _validate_room_sequence(
        raw.get("ordered_rooms", list(defaults)),
        "room_preferences.ordered_rooms",
        require_json_list=True,
        allow_empty=False,
    )
    excluded_rooms = _validate_room_sequence(
        raw.get("excluded_rooms", []),
        "room_preferences.excluded_rooms",
        require_json_list=True,
        allow_empty=True,
    )
    unknown_exclusions = set(excluded_rooms) - set(ordered_rooms)
    if unknown_exclusions:
        raise RoomPreferencesError(
            "room_preferences.excluded_rooms must be a subset of ordered_rooms; "
            "unknown exclusion(s): " + ", ".join(sorted(unknown_exclusions))
        )

    minimum_block_minutes = _validate_minimum_block(
        raw.get("minimum_block_minutes", MIN_BLOCK_MINUTES)
    )
    allow_fragmented_sessions = raw.get("allow_fragmented_sessions", True)
    if not isinstance(allow_fragmented_sessions, bool):
        raise RoomPreferencesError(
            "room_preferences.allow_fragmented_sessions must be true or false"
        )

    return RoomPreferences(
        ordered_rooms=ordered_rooms,
        excluded_rooms=excluded_rooms,
        acceptable_instrument_tags=_validate_text_list(
            raw.get("acceptable_instrument_tags", []),
            "room_preferences.acceptable_instrument_tags",
        ),
        acceptable_room_type_tags=_validate_text_list(
            raw.get("acceptable_room_type_tags", []),
            "room_preferences.acceptable_room_type_tags",
        ),
        required_feature_terms=_validate_text_list(
            raw.get("required_feature_terms", []),
            "room_preferences.required_feature_terms",
        ),
        minimum_block_minutes=minimum_block_minutes,
        allow_fragmented_sessions=allow_fragmented_sessions,
    )


def load_room_preferences(
    settings: Mapping[str, Any],
    *,
    default_ordered_rooms: Sequence[str] = DEFAULT_ORDERED_ROOMS,
) -> RoomPreferences:
    """Load the optional room-preference block, rejecting malformed values.

    A missing block uses the booker's built-in priority order and its existing
    30-minute, fragmentation-allowed behavior.
    """

    if not isinstance(settings, Mapping):
        raise RoomPreferencesError("Settings must be a JSON object")
    raw = settings.get(ROOM_PREFERENCES_KEY, {})
    if not isinstance(raw, Mapping):
        raise RoomPreferencesError("room_preferences must be a JSON object")
    if any(not isinstance(key, str) for key in raw):
        raise RoomPreferencesError("room_preferences keys must be strings")
    return _parse_room_preferences(
        raw,
        default_ordered_rooms=default_ordered_rooms,
    )


def room_preferences_to_dict(preferences: RoomPreferences) -> dict[str, Any]:
    """Convert validated preferences to their stable JSON-compatible schema."""

    if not isinstance(preferences, RoomPreferences):
        raise TypeError("preferences must be a RoomPreferences instance")
    return {
        "ordered_rooms": list(preferences.ordered_rooms),
        "excluded_rooms": list(preferences.excluded_rooms),
        "acceptable_instrument_tags": list(preferences.acceptable_instrument_tags),
        "acceptable_room_type_tags": list(preferences.acceptable_room_type_tags),
        "required_feature_terms": list(preferences.required_feature_terms),
        "minimum_block_minutes": preferences.minimum_block_minutes,
        "allow_fragmented_sessions": preferences.allow_fragmented_sessions,
    }


def apply_room_preferences_update(
    settings: MutableMapping[str, Any],
    updates: Mapping[str, Any],
    *,
    default_ordered_rooms: Sequence[str] = DEFAULT_ORDERED_ROOMS,
) -> RoomPreferences:
    """Merge only edited room fields into the latest settings document.

    Unrelated top-level settings and unmentioned room fields survive concurrent
    UI edits.  The document is mutated only after the fully merged candidate
    passes the same strict loader used by the autonomous process.
    """

    if not isinstance(settings, MutableMapping):
        raise RoomPreferencesError("Settings must be a mutable JSON object")
    if not isinstance(updates, Mapping):
        raise RoomPreferencesError("Room preference updates must be a JSON object")
    if any(not isinstance(key, str) for key in updates):
        raise RoomPreferencesError("Room preference update keys must be strings")
    unknown = set(updates) - _PREFERENCE_FIELDS
    if unknown:
        raise RoomPreferencesError(
            "Unsupported room preference update(s): " + ", ".join(sorted(unknown))
        )

    current = load_room_preferences(
        settings,
        default_ordered_rooms=default_ordered_rooms,
    )
    candidate = room_preferences_to_dict(current)
    candidate.update(updates)
    merged = _parse_room_preferences(
        candidate,
        default_ordered_rooms=default_ordered_rooms,
    )
    settings[ROOM_PREFERENCES_KEY] = room_preferences_to_dict(merged)
    return merged


def _catalog_room_ids(live_catalog: Iterable[str] | Mapping[str, Any]) -> tuple[str, ...]:
    if isinstance(live_catalog, Mapping):
        values: Iterable[str] = live_catalog.keys()
    elif isinstance(live_catalog, (str, bytes, bytearray, set, frozenset)):
        raise RoomPreferencesError("live_catalog must be a collection of room IDs")
    else:
        try:
            values = iter(live_catalog)
        except TypeError as exc:
            raise RoomPreferencesError(
                "live_catalog must be a collection of room IDs"
            ) from exc

    result: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        room = validate_room_name(value, f"live_catalog[{index}]")
        if room in seen:
            raise RoomPreferencesError(f"live_catalog contains duplicate room {room}")
        seen.add(room)
        result.append(room)
    return tuple(result)


def reconcile_room_order(
    saved_order: Sequence[str],
    live_catalog: Iterable[str] | Mapping[str, Any],
) -> tuple[str, ...]:
    """Keep every saved rank and append newly discovered rooms in site order."""

    validated_saved = _validate_room_sequence(
        saved_order,
        "saved_order",
        require_json_list=False,
        allow_empty=True,
    )
    live_rooms = _catalog_room_ids(live_catalog)
    saved = set(validated_saved)
    return validated_saved + tuple(room for room in live_rooms if room not in saved)


def reconcile_room_preferences(
    preferences: RoomPreferences,
    live_catalog: Iterable[str] | Mapping[str, Any],
) -> RoomPreferences:
    """Return preferences with new catalog rooms appended, preserving old ranks."""

    if not isinstance(preferences, RoomPreferences):
        raise TypeError("preferences must be a RoomPreferences instance")
    return replace(
        preferences,
        ordered_rooms=reconcile_room_order(preferences.ordered_rooms, live_catalog),
    )


def effective_room_order(
    preferences: RoomPreferences,
    live_catalog: Iterable[str] | Mapping[str, Any],
) -> tuple[str, ...]:
    """Return ranked, non-excluded rooms that are present in today's catalog."""

    if not isinstance(preferences, RoomPreferences):
        raise TypeError("preferences must be a RoomPreferences instance")
    live_rooms = _catalog_room_ids(live_catalog)
    live_set = set(live_rooms)
    excluded = set(preferences.excluded_rooms)
    reconciled = reconcile_room_order(preferences.ordered_rooms, live_rooms)
    return tuple(
        room for room in reconciled if room in live_set and room not in excluded
    )


def _metadata_values(metadata: Mapping[str, Any], key: str, room: str) -> tuple[str, ...]:
    raw = metadata.get(key, [])
    if not isinstance(raw, list):
        raise RoomPreferencesError(f"Live metadata {room}.{key} must be a JSON list")

    result: list[str] = []
    for index, value in enumerate(raw):
        if not isinstance(value, str) or not value.strip() or not value.isprintable():
            raise RoomPreferencesError(
                f"Live metadata {room}.{key}[{index}] must be a non-empty string"
            )
        result.append(value.strip().casefold())
    return tuple(result)


def room_matches_preferences(
    preferences: RoomPreferences,
    metadata: Mapping[str, Any],
    *,
    room: str = "room",
) -> bool:
    """Apply OR tag filters and all-required feature-term matching."""

    if not isinstance(preferences, RoomPreferences):
        raise TypeError("preferences must be a RoomPreferences instance")
    if not isinstance(metadata, Mapping):
        raise RoomPreferencesError(f"Live metadata for {room} must be a JSON object")

    instruments = _metadata_values(metadata, "instrument_tags", room)
    room_types = _metadata_values(metadata, "room_type_tags", room)
    features = _metadata_values(metadata, "features", room)

    accepted_instruments = {
        value.casefold() for value in preferences.acceptable_instrument_tags
    }
    if accepted_instruments and accepted_instruments.isdisjoint(instruments):
        return False

    accepted_room_types = {
        value.casefold() for value in preferences.acceptable_room_type_tags
    }
    if accepted_room_types and accepted_room_types.isdisjoint(room_types):
        return False

    for required in preferences.required_feature_terms:
        # Treat a multi-word requirement as a small search expression rather
        # than requiring exact adjacency.  "adjustable stool" should match a
        # site feature such as "Adjustable piano stool", while every word and
        # every independently configured requirement must still be present.
        required_words = required.casefold().split()
        if not any(
            all(word in feature for word in required_words) for feature in features
        ):
            return False
    return True


def filter_effective_rooms(
    preferences: RoomPreferences,
    live_room_metadata: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...]:
    """Return the effective room order after catalog and metadata filtering."""

    if not isinstance(live_room_metadata, Mapping):
        raise RoomPreferencesError("Live room metadata must be a JSON object")
    eligible: list[str] = []
    for room in effective_room_order(preferences, live_room_metadata):
        metadata = live_room_metadata[room]
        if room_matches_preferences(preferences, metadata, room=room):
            eligible.append(room)
    return tuple(eligible)


def _compact_terms(values: Sequence[str]) -> str:
    if len(values) <= 2:
        return " / ".join(values)
    return " / ".join(values[:2]) + f" +{len(values) - 2}"


def summarize_room_preferences(
    preferences: RoomPreferences,
    live_catalog: Iterable[str] | Mapping[str, Any] | None = None,
) -> str:
    """Build one concise, human-readable summary for the desktop UI."""

    if not isinstance(preferences, RoomPreferences):
        raise TypeError("preferences must be a RoomPreferences instance")

    if live_catalog is None:
        available_count = len(
            set(preferences.ordered_rooms) - set(preferences.excluded_rooms)
        )
        room_text = f"{available_count} eligible room"
    elif isinstance(live_catalog, Mapping) and all(
        isinstance(value, Mapping) for value in live_catalog.values()
    ):
        available_count = len(filter_effective_rooms(preferences, live_catalog))
        room_text = f"{available_count} live eligible room"
    else:
        available_count = len(effective_room_order(preferences, live_catalog))
        room_text = f"{available_count} live eligible room"
    if available_count != 1:
        room_text += "s"

    parts = [room_text]
    if preferences.excluded_rooms:
        parts.append(f"{len(preferences.excluded_rooms)} excluded")

    if preferences.acceptable_instrument_tags:
        parts.append(
            "Instrument: " + _compact_terms(preferences.acceptable_instrument_tags)
        )
    if preferences.acceptable_room_type_tags:
        parts.append(
            "Type: " + _compact_terms(preferences.acceptable_room_type_tags)
        )
    if preferences.required_feature_terms:
        parts.append("Needs: " + _compact_terms(preferences.required_feature_terms))
    if not (
        preferences.acceptable_instrument_tags
        or preferences.acceptable_room_type_tags
        or preferences.required_feature_terms
    ):
        parts.append("any instrument/type")

    parts.append(f"{preferences.minimum_block_minutes}+ min")
    parts.append(
        "fragments allowed"
        if preferences.allow_fragmented_sessions
        else "single blocks only"
    )
    return " • ".join(parts)
