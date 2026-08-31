"""Typed, allow-listed action surface for the in-app Codex assistant.

Codex never receives a shell, filesystem, Playwright, or credential tool.  It
can only call the domain operations declared here; each mutation is tied to
the complete active user message while Terra owns semantic interpretation.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping, Sequence

from agenda_snapshot import AGENDA_SNAPSHOT_FILE, AgendaEvent, read_agenda_snapshot
from app_settings import (
    SETTINGS_FILE,
    InterProcessFileLock,
    SettingsError,
    update_settings,
)
from assistant_context import LOCAL_TIMEZONE, ContextPaths, build_assistant_context
from assistant_plans import AssistantPlanError, apply_future_practice_plan
from booking_blackouts import (
    NATURAL_TIME_WINDOWS,
    add_rebooking_blackouts,
    interval_overlaps_blackout,
    load_rebooking_blackouts,
    subtract_rebooking_blackout,
)
from booking_plan import BookingPlanError, clear_booking_plan
from booking_strategy import (
    BookingStrategyError,
    apply_booking_strategy_update,
    booking_strategy_to_dict,
)
from practice_plan import PracticePlanError, load_practice_plan
from room_preferences import (
    RoomPreferencesError,
    apply_room_preferences_update,
    room_preferences_to_dict,
)


APP_DIR = Path(__file__).resolve().parent
DEFAULT_PYTHON = APP_DIR / ".venv" / "Scripts" / "python.exe"
BOOKER_SCRIPT = APP_DIR / "book_week.py"
ProgressCallback = Callable[[str, str], None]
MAX_BULK_CANCELLATIONS = 64
BULK_CANCELLATION_TIMEOUT_SECONDS = 20 * 60
ASSISTANT_MUTATION_LOCK_FILE = APP_DIR / "data" / "assistant-mutation.lock"
MUTATING_TOOLS = frozenset(
    {
        "set_future_practice_plan",
        "update_booker_preferences",
        "run_booker",
        "cancel_reservations",
        "reopen_booking_window",
    }
)

_SECRET_LINE = re.compile(
    r"(?i)(password|passcode|one[- ]?time|\botp\b|authorization:|cookie|credential|sms code|bridge token)"
)
_SPACE = re.compile(r"\s+")
_CLOCK = re.compile(r"^(?:[01]\d|2[0-3]):(?:00|15|30|45)$")
_RESERVATION_TIME_PERIODS = NATURAL_TIME_WINDOWS


class AssistantToolError(RuntimeError):
    """A safe, user-presentable domain-tool failure."""


class AssistantToolCancelled(AssistantToolError):
    """Raised after the user stops the current assistant action."""


@dataclass(frozen=True)
class ToolResult:
    success: bool
    data: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"success": self.success, **dict(self.data)}


def _object_schema(
    properties: Mapping[str, Any],
    *,
    required: Sequence[str] = (),
    description: str | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": dict(properties),
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    if description:
        schema["description"] = description
    return schema


def dynamic_tool_specs() -> list[dict[str, Any]]:
    """Return the exact experimental App Server dynamic-tool declarations."""

    canonical_date = {
        "type": "string",
        "pattern": r"^\d{4}-\d{2}-\d{2}$",
        "description": "Canonical Europe/London calendar date (YYYY-MM-DD).",
    }
    clock = {
        "type": "string",
        "pattern": r"^(?:[01]\d|2[0-3]):(?:00|15|30|45)$",
        "description": "Canonical 24-hour clock time on a 15-minute boundary.",
    }
    cancellation_selection_id = {
        "type": "string",
        "pattern": r"^selection:[0-9a-f]{64}$",
        "description": (
            "Opaque one-use current-turn selection_id returned by find_reservations."
        ),
    }
    run_booker_schema = _object_schema(
        {
            "only_date": canonical_date,
            "only_room": {"type": "string", "minLength": 1, "maxLength": 120},
            "max_actions": {"type": "integer", "const": 1},
            "max_action_minutes": {
                "type": "integer",
                "minimum": 30,
                "maximum": 120,
                "multipleOf": 15,
            },
        },
        required=("max_actions",),
    )
    run_booker_schema["allOf"] = [
        {
            "if": {"required": ["max_action_minutes"]},
            "then": {"required": ["only_date", "only_room"]},
        }
    ]
    find_reservations_schema = _object_schema(
        {
            "date": canonical_date,
            "start_time": clock,
            "end_time": clock,
            "room": {"type": "string", "minLength": 1, "maxLength": 120},
            "time_period": {
                "type": "string",
                "enum": ["morning", "afternoon", "evening"],
                "description": (
                    "Return every reservation whose interval overlaps the fixed app "
                    "window: morning [07:00,12:00), afternoon [12:00,18:00), "
                    "or evening [18:00,22:00). Cannot be combined with exact times."
                ),
            },
            "scope": {
                "type": "string",
                "enum": ["upcoming"],
                "description": (
                    "Return the complete set of future reservations in the fresh agenda. "
                    "Use for 'all my reservations' or 'all upcoming bookings'."
                ),
            },
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 31,
                "description": (
                    "Optional rolling-day horizon for scope=upcoming. Use 7 for "
                    "'over the next week'; omit for every upcoming reservation in the "
                    "complete agenda window."
                ),
            },
            "event_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_BULK_CANCELLATIONS,
                "uniqueItems": True,
                "items": {"type": "integer", "minimum": 1},
                "description": (
                    "Exact positive reservation IDs Terra selected from fresh agenda "
                    "context. The host re-resolves all of them and returns one reusable "
                    "verified selection."
                ),
            },
        },
    )
    find_reservations_schema["allOf"] = [
        {
            "oneOf": [
                {
                    "required": ["date"],
                    "not": {"required": ["scope"]},
                },
                {
                    "required": ["scope"],
                    "not": {"required": ["date"]},
                },
                {
                    "required": ["event_ids"],
                    "not": {
                        "anyOf": [
                            {"required": ["date"]},
                            {"required": ["scope"]},
                        ]
                    },
                },
            ]
        },
        {
            "if": {"required": ["time_period"]},
            "then": {
                "not": {
                    "anyOf": [
                        {"required": ["start_time"]},
                        {"required": ["end_time"]},
                    ]
                }
            },
        },
        {
            "if": {"required": ["scope"]},
            "then": {
                "not": {
                    "anyOf": [
                        {"required": ["date"]},
                        {"required": ["start_time"]},
                        {"required": ["end_time"]},
                        {"required": ["room"]},
                        {"required": ["time_period"]},
                    ]
                }
            },
        },
        {
            "if": {"required": ["days"]},
            "then": {"required": ["scope"]},
        },
        {
            "if": {"required": ["event_ids"]},
            "then": {
                "not": {
                    "anyOf": [
                        {"required": ["date"]},
                        {"required": ["scope"]},
                        {"required": ["days"]},
                        {"required": ["start_time"]},
                        {"required": ["end_time"]},
                        {"required": ["room"]},
                        {"required": ["time_period"]},
                    ]
                }
            },
        },
    ]
    cancel_reservations_schema = _object_schema(
        {
            "selection_id": cancellation_selection_id,
        },
        required=("selection_id",),
    )
    return [
        {
            "type": "function",
            "name": "get_booker_context",
            "description": (
                "Read sanitized Asimut Booker state. Use this before answering schedule, "
                "plan, preference, room, health, or capability questions. Stale data is "
                "labelled and is not mutation authority."
            ),
            "inputSchema": _object_schema(
                {
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "app",
                                "preferences",
                                "agenda",
                                "plan",
                                "rooms",
                                "health",
                                "mutations",
                                "history",
                            ],
                        },
                        "uniqueItems": True,
                        "maxItems": 8,
                    }
                }
            ),
        },
        {
            "type": "function",
            "name": "refresh_booker_data",
            "description": (
                "Run a read-only authenticated refresh: agenda scans the full current "
                "window, plan computes a fresh display plan, and login only verifies or "
                "recovers the saved session. It never creates, edits, or cancels bookings."
            ),
            "inputSchema": _object_schema(
                {"scope": {"type": "string", "enum": ["agenda", "plan", "login"]}},
                required=("scope",),
            ),
        },
        {
            "type": "function",
            "name": "find_reservations",
            "description": (
                "Let Terra resolve whichever reservation set it judges the user means against "
                "the validated agenda. It may submit selected event_ids, an exact date/time, a "
                "named daypart, or scope=upcoming (optionally days=7). The host returns an opaque "
                "one-use selection_id tied to the complete fresh exact set, so cancellation is "
                "one easy follow-up call. A stale snapshot requires refresh."
            ),
            "inputSchema": find_reservations_schema,
        },
        {
            "type": "function",
            "name": "set_future_practice_plan",
            "description": (
                "Turn a high-level future practice intention into the complete dated targets "
                "that the autonomous Booker will pursue as those dates enter the live window. "
                "A dated duration is the total practice goal for that date, not one booking; "
                "targets above the site session maximum are split across multiple ranked, "
                "non-overlapping sessions. Direct outcome wording such as 'book/get me three "
                "hours tomorrow' authorizes this necessary target write without a restatement. "
                "Supply every date in the range exactly once; 0 hours turns that date off. "
                "Resolve relative amounts from an explicit or saved numeric baseline; if no "
                "numeric result is determined, ask one concise clarification. The saved intent "
                "remains explainable and revisable."
            ),
            "inputSchema": _object_schema(
                {
                    "title": {"type": "string", "minLength": 1, "maxLength": 120},
                    "intent_summary": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 500,
                    },
                    "start_date": canonical_date,
                    "end_date": canonical_date,
                    "daily_targets": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 92,
                        "items": _object_schema(
                            {
                                "date": canonical_date,
                                "hours": {
                                    "type": "number",
                                    "minimum": 0,
                                    "maximum": 12,
                                    "multipleOf": 0.5,
                                },
                            },
                            required=("date", "hours"),
                        ),
                    },
                    "replace_overlapping": {
                        "type": "boolean",
                        "description": (
                            "True only when the user's instruction replaces or revises an "
                            "existing plan over the same dates."
                        ),
                    },
                },
                required=(
                    "title",
                    "intent_summary",
                    "start_date",
                    "end_date",
                    "daily_targets",
                    "replace_overlapping",
                ),
            ),
        },
        {
            "type": "function",
            "name": "update_booker_preferences",
            "description": (
                "Atomically update only the explicitly supplied Booker preferences. "
                "Use for practice targets, date on/off state, preferred times, planning "
                "strategy, room order/exclusions/requirements, and session shape. "
                "For a total such as 'three hours tomorrow', write one date_overrides "
                "entry. For relative wording such as 'another hour tomorrow' or "
                "'reduce tomorrow by one hour', write one date_adjustments entry with "
                "the signed literal delta; the host computes the new total from current "
                "state. Never convert a delta to an absolute target yourself."
            ),
            "inputSchema": _object_schema(
                {
                    "booking_days": {
                        "type": "array",
                        "maxItems": 31,
                        "items": _object_schema(
                            {"date": canonical_date, "enabled": {"type": "boolean"}},
                            required=("date", "enabled"),
                        ),
                    },
                    "practice_plan": _object_schema(
                        {
                            "enabled": {"type": "boolean"},
                            "default_hours": {
                                "type": "number",
                                "minimum": 0.5,
                                "maximum": 12,
                                "multipleOf": 0.5,
                            },
                            "date_overrides": {
                                "type": "array",
                                "maxItems": 31,
                                "items": _object_schema(
                                    {
                                        "date": canonical_date,
                                        "hours": {
                                            "anyOf": [
                                                {
                                                    "type": "number",
                                                    "minimum": 0.5,
                                                    "maximum": 12,
                                                    "multipleOf": 0.5,
                                                },
                                                {"type": "null"},
                                            ]
                                        },
                                    },
                                    required=("date", "hours"),
                                ),
                            },
                            "date_adjustments": {
                                "type": "array",
                                "maxItems": 31,
                                "description": (
                                    "Use only for relative wording such as 'another hour' "
                                    "or 'one hour less'; the host applies each signed delta "
                                    "to the current dated/default target."
                                ),
                                "items": _object_schema(
                                    {
                                        "date": canonical_date,
                                        "delta_hours": {
                                            "type": "number",
                                            "minimum": -11.5,
                                            "maximum": 11.5,
                                            "multipleOf": 0.5,
                                        },
                                    },
                                    required=("date", "delta_hours"),
                                ),
                            },
                        }
                    ),
                    "time_preferences": _object_schema(
                        {
                            "enabled": {"type": "boolean"},
                            "start_time": clock,
                            "end_time": clock,
                            "strict_mode": {"type": "boolean"},
                            "preset": {
                                "type": "string",
                                "enum": [
                                    "morning",
                                    "peak_afternoon",
                                    "afternoon",
                                    "evening",
                                    "afternoon_evening",
                                    "custom",
                                ],
                            },
                        }
                    ),
                    "booking_strategy": _object_schema(
                        {
                            "reverse_date_order": {"type": "boolean"},
                            "daily_planning": _object_schema(
                                {
                                    "enabled": {"type": "boolean"},
                                    "preferred_peak_start": clock,
                                    "preferred_peak_end": clock,
                                    "desired_peak_block_minutes": {
                                        "type": "integer",
                                        "minimum": 30,
                                        "maximum": 120,
                                        "multipleOf": 15,
                                    },
                                    "hold_early_peak_edges": {"type": "boolean"},
                                    "foresight_minutes": {
                                        "type": "integer",
                                        "minimum": 0,
                                        "maximum": 1440,
                                        "multipleOf": 15,
                                    },
                                    "minimum_later_options": {
                                        "type": "integer",
                                        "minimum": 1,
                                        "maximum": 5,
                                    },
                                    "fallback_lead_minutes": {
                                        "type": "integer",
                                        "minimum": 0,
                                        "maximum": 240,
                                        "multipleOf": 15,
                                    },
                                    "after_peak_mode": {
                                        "type": "string",
                                        "enum": ["longest_first", "earliest_first", "room_first"],
                                    },
                                    "priority_mode": {
                                        "type": "string",
                                        "enum": ["time_first", "room_first"],
                                    },
                                }
                            ),
                        }
                    ),
                    "room_preferences": _object_schema(
                        {
                            "ordered_rooms": {
                                "type": "array",
                                "items": {"type": "string"},
                                "uniqueItems": True,
                            },
                            "excluded_rooms": {
                                "type": "array",
                                "items": {"type": "string"},
                                "uniqueItems": True,
                            },
                            "acceptable_instrument_tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "uniqueItems": True,
                            },
                            "acceptable_room_type_tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "uniqueItems": True,
                            },
                            "required_feature_terms": {
                                "type": "array",
                                "items": {"type": "string"},
                                "uniqueItems": True,
                            },
                            "minimum_block_minutes": {
                                "type": "integer",
                                "minimum": 30,
                                "maximum": 120,
                                "multipleOf": 15,
                            },
                            "allow_fragmented_sessions": {"type": "boolean"},
                        }
                    ),
                },
            ),
        },
        {
            "type": "function",
            "name": "reopen_booking_window",
            "description": (
                "Explicitly allow automatic booking again in one dated protected window. "
                "Use only when the active user asks to remove or narrow a saved "
                "rebooking blackout. Natural morning, afternoon, and evening map to the "
                "same fixed app windows returned by find_reservations. The operation may "
                "split a larger blackout and invalidates the old display plan; it never "
                "starts a booking by itself."
            ),
            "inputSchema": _object_schema(
                {
                    "date": canonical_date,
                    "start_time": clock,
                    "end_time": clock,
                },
                required=("date", "start_time", "end_time"),
            ),
        },
        {
            "type": "function",
            "name": "run_booker",
            "description": (
                "Run the autonomous booker for one plan-selected action after an explicit user "
                "booking outcome. This immediate safety cap is not the daily practice limit: "
                "the saved dated target remains active and recurring runs pursue the remaining "
                "ranked sessions. It cannot bind an exact start time by itself. The run shares "
                "the runtime lock, refreshes live policy/agenda, and keeps all existing exact-save "
                "safeguards. A duration cap requires both an exact date and exact room. Never use "
                "for a question or speculative action."
            ),
            "inputSchema": run_booker_schema,
        },
        {
            "type": "function",
            "name": "cancel_reservations",
            "description": (
                "Cancel a bounded explicit set of exact existing reservations. Resolve every "
                "target from one fresh agenda snapshot first and preserve each positive event "
                "ID, date, start, end, room, and match token unchanged. The backend executes "
                "them sequentially and stops immediately after any non-success outcome, "
                "returning per-item outcomes plus targets that were not attempted. Never include "
                "overlapping or inferred events for an exact-time request. A verified period "
                "cancellation protects the complete named window from later automatic rebooking. "
                "Pass the selection_id from find_reservations; the host then supplies every "
                "fresh exact identity without exposing low-level cancellation fields to Terra."
            ),
            "inputSchema": cancel_reservations_schema,
        },
    ]


def _canonical_date(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise AssistantToolError(f"{label} must use YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise AssistantToolError(f"{label} must use YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise AssistantToolError(f"{label} must use canonical YYYY-MM-DD")
    return value


def _canonical_clock(value: Any, label: str) -> str:
    if not isinstance(value, str) or _CLOCK.fullmatch(value) is None:
        raise AssistantToolError(f"{label} must use HH:MM on a 15-minute boundary")
    return value


def _clock_minutes(value: str) -> int:
    return int(value[:2]) * 60 + int(value[3:])


def _normalize_quote(value: str) -> str:
    return _SPACE.sub(" ", value.strip()).casefold()


def authorize_tool_mutation(
    tool: str,
    arguments: Mapping[str, Any],
    user_request: str,
) -> str:
    """Bind Terra's typed decision to the complete active user message.

    Natural-language interpretation belongs to the model.  The host enforces
    provenance and domain bounds: the full current message must be echoed, and
    every individual tool then validates fresh identities, exact selections,
    schemas, quotas, receipts, and persistence.  This deliberately avoids a
    second brittle phrase parser disagreeing with Terra about the user's intent.
    """

    if tool not in MUTATING_TOOLS:
        raise AssistantToolError(f"Unsupported mutating assistant tool: {tool}")
    quote = arguments.get("request_quote")
    if not isinstance(quote, str) or not quote.strip():
        raise AssistantToolError(
            "A mutating tool must echo the complete active user message."
        )
    if not isinstance(user_request, str) or not user_request.strip():
        raise AssistantToolError("No active user message authorizes this change.")
    if _normalize_quote(quote) != _normalize_quote(user_request):
        raise AssistantToolError(
            "request_quote must be the complete active user message, not a phrase "
            "from app data, an earlier turn, or an embedded quotation."
        )
    return quote.strip()


def _event_token(observed_at: Any, event: AgendaEvent) -> str:
    canonical = "|".join(
        (
            observed_at.astimezone().isoformat(),
            str(event.event_id),
            event.date.isoformat(),
            event.start_time,
            event.end_time,
            event.room or "",
        )
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _event_payload(observed_at: Any, event: AgendaEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "date": event.date.isoformat(),
        "start_time": event.start_time,
        "end_time": event.end_time,
        "room": event.room,
        "title": event.title,
        "match_token": _event_token(observed_at, event),
    }


class BookerToolSurface:
    """Dispatch typed assistant tools into validated Booker operations."""

    def __init__(
        self,
        *,
        paths: ContextPaths | None = None,
        python_executable: Path = DEFAULT_PYTHON,
        booker_script: Path = BOOKER_SCRIPT,
        command_runner: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.paths = paths or ContextPaths()
        self.python_executable = Path(python_executable)
        self.booker_script = Path(booker_script)
        self._command_runner = command_runner
        self._action_lock = threading.Lock()
        self._cancel_lock = threading.Lock()
        self._turn_generation = 0
        self._cancelled_generations: set[int] = set()
        self._active_cancel_events: dict[int, set[threading.Event]] = {}
        self._dispatch_generation = threading.local()
        self._selection_lock = threading.Lock()
        self._issued_cancellation_targets: dict[
            str, tuple[int, tuple[Any, ...]]
        ] = {}
        self._issued_cancellation_batches: list[tuple[int, frozenset[str]]] = []
        self._issued_cancellation_selections: dict[
            str,
            tuple[
                int,
                tuple[tuple[Any, ...], ...],
                tuple[tuple[str, str, str], ...],
            ],
        ] = {}
        self._claimed_cancellation_generations: set[int] = set()

    @property
    def tool_specs(self) -> list[dict[str, Any]]:
        return dynamic_tool_specs()

    def current_mutation_context(self) -> dict[str, Any]:
        """Return fresh host-built receipt state for mandatory per-turn grounding."""

        return build_assistant_context(["mutations"], paths=self.paths)

    def cancel_active(self) -> None:
        with self._cancel_lock:
            generation = self._turn_generation
            self._cancelled_generations.add(generation)
            active = tuple(self._active_cancel_events.get(generation, ()))
        for cancel_event in active:
            cancel_event.set()

    def begin_turn(self) -> None:
        """Start a new cancellation generation without reviving older work."""

        with self._cancel_lock:
            self._turn_generation += 1
            generation = self._turn_generation
            self._claimed_cancellation_generations = {
                claimed
                for claimed in self._claimed_cancellation_generations
                if claimed >= generation - 1
            }
            self._cancelled_generations = {
                cancelled
                for cancelled in self._cancelled_generations
                if cancelled >= generation - 1
            }
        with self._selection_lock:
            self._issued_cancellation_targets = {
                token: issued
                for token, issued in self._issued_cancellation_targets.items()
                if issued[0] >= generation
            }
            self._issued_cancellation_batches = [
                issued
                for issued in self._issued_cancellation_batches
                if issued[0] >= generation
            ]
            self._issued_cancellation_selections = {
                selection_id: issued
                for selection_id, issued in self._issued_cancellation_selections.items()
                if issued[0] >= generation
            }

    def clear_cancellation_context(self) -> None:
        """Forget conversation-bound cancellation selections and operation claims."""

        with self._selection_lock:
            self._issued_cancellation_targets.clear()
            self._issued_cancellation_batches.clear()
            self._issued_cancellation_selections.clear()
        with self._cancel_lock:
            self._claimed_cancellation_generations.clear()

    def dispatch(
        self,
        tool: str,
        arguments: Mapping[str, Any] | None,
        *,
        user_request: str = "",
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            raise AssistantToolError("Tool arguments must be a JSON object")
        handlers = {
            "get_booker_context": self._get_context,
            "refresh_booker_data": self._refresh,
            "find_reservations": self._find_reservations,
            "set_future_practice_plan": self._set_future_practice_plan,
            "update_booker_preferences": self._update_preferences,
            "run_booker": self._run_booker,
            "cancel_reservations": self._cancel_reservations,
            "reopen_booking_window": self._reopen_booking_window,
        }
        handler = handlers.get(tool)
        if handler is None:
            raise AssistantToolError(f"Unsupported assistant tool: {tool}")
        callback = progress or (lambda _title, _detail: None)
        with self._cancel_lock:
            generation = self._turn_generation
        stack = getattr(self._dispatch_generation, "stack", None)
        if stack is None:
            stack = []
            self._dispatch_generation.stack = stack
        stack.append(generation)
        try:
            if tool not in MUTATING_TOOLS:
                return handler(
                    dict(arguments), user_request=user_request, progress=callback
                )
            mutation_lock = InterProcessFileLock(
                ASSISTANT_MUTATION_LOCK_FILE,
                timeout=1.0,
            )
            try:
                mutation_lock.acquire()
            except SettingsError as exc:
                raise AssistantToolError(
                    "Another assistant or Booker change has already started and is still in progress. "
                    "Wait for it to finish before trying a second change."
                ) from exc
            try:
                return handler(
                    dict(arguments), user_request=user_request, progress=callback
                )
            finally:
                mutation_lock.release()
        except AssistantToolError:
            raise
        except (
            SettingsError,
            AssistantPlanError,
            PracticePlanError,
            BookingStrategyError,
            RoomPreferencesError,
        ) as exc:
            raise AssistantToolError(str(exc)) from exc
        finally:
            stack.pop()

    def _command_cancel_event(self) -> tuple[int, threading.Event]:
        stack = getattr(self._dispatch_generation, "stack", ())
        if not stack:
            raise AssistantToolError("Booker command was not bound to a user turn")
        generation = stack[-1]
        cancel_event = threading.Event()
        with self._cancel_lock:
            if generation in self._cancelled_generations:
                cancel_event.set()
            self._active_cancel_events.setdefault(generation, set()).add(cancel_event)
        return generation, cancel_event

    def _release_command_cancel_event(
        self,
        generation: int,
        cancel_event: threading.Event,
    ) -> None:
        with self._cancel_lock:
            active = self._active_cancel_events.get(generation)
            if active is None:
                return
            active.discard(cancel_event)
            if not active:
                self._active_cancel_events.pop(generation, None)

    def _get_context(
        self,
        arguments: dict[str, Any],
        *,
        user_request: str,
        progress: ProgressCallback,
    ) -> dict[str, Any]:
        unknown = set(arguments) - {"sections"}
        if unknown:
            raise AssistantToolError(f"Unknown context argument(s): {sorted(unknown)}")
        sections = arguments.get("sections")
        if sections is not None and (
            not isinstance(sections, list)
            or not all(isinstance(item, str) for item in sections)
        ):
            raise AssistantToolError("sections must be a list of names")
        progress("Reading Booker context", "Validating local display evidence and preferences")
        return build_assistant_context(sections, paths=self.paths)

    def _refresh(
        self,
        arguments: dict[str, Any],
        *,
        user_request: str,
        progress: ProgressCallback,
    ) -> dict[str, Any]:
        if set(arguments) != {"scope"}:
            raise AssistantToolError("refresh_booker_data requires only scope")
        scope = arguments["scope"]
        flags = {
            "agenda": ["--headless", "--check-only"],
            "plan": ["--headless", "--plan-only"],
            "login": ["--headless", "--login-only"],
        }.get(scope)
        if flags is None:
            raise AssistantToolError("scope must be agenda, plan, or login")
        progress("Refreshing live data", f"Starting the read-only {scope} workflow")
        command = self._run_booker_command(flags, progress=progress, timeout=15 * 60)
        sections = {
            "agenda": ["agenda", "health", "mutations"],
            "plan": ["agenda", "plan", "health", "mutations"],
            "login": ["health"],
        }[scope]
        return {
            "scope": scope,
            "command": command,
            "context": build_assistant_context(sections, paths=self.paths),
        }

    def _find_reservations(
        self,
        arguments: dict[str, Any],
        *,
        user_request: str,
        progress: ProgressCallback,
    ) -> dict[str, Any]:
        allowed = {
            "date",
            "start_time",
            "end_time",
            "room",
            "time_period",
            "scope",
            "days",
            "event_ids",
        }
        unknown = set(arguments) - allowed
        if unknown:
            raise AssistantToolError(
                f"Unknown find_reservations argument(s): {sorted(unknown)}"
            )
        modes = [field for field in ("date", "scope", "event_ids") if field in arguments]
        if len(modes) != 1:
            raise AssistantToolError(
                "find_reservations requires exactly one of date, scope, or event_ids"
            )
        if modes[0] == "event_ids":
            return self._find_reservations_by_event_ids(arguments, progress=progress)
        if modes[0] == "scope":
            return self._find_upcoming_reservations(arguments, progress=progress)
        if "days" in arguments:
            raise AssistantToolError("days is valid only with scope=upcoming")
        target_date = _canonical_date(arguments["date"], "date")
        time_period = arguments.get("time_period")
        if time_period is not None and time_period not in _RESERVATION_TIME_PERIODS:
            raise AssistantToolError(
                "time_period must be morning, afternoon, or evening"
            )
        if time_period is not None and any(
            key in arguments for key in ("start_time", "end_time")
        ):
            raise AssistantToolError(
                "time_period cannot be combined with exact start_time or end_time"
            )
        start = (
            None
            if arguments.get("start_time") is None
            else _canonical_clock(arguments["start_time"], "start_time")
        )
        end = (
            None
            if arguments.get("end_time") is None
            else _canonical_clock(arguments["end_time"], "end_time")
        )
        room = arguments.get("room")
        if room is not None and (not isinstance(room, str) or not room.strip()):
            raise AssistantToolError("room must be a non-empty exact room name")
        interpreted_window = None
        period_minutes = None
        if time_period is not None:
            period_start, period_end = _RESERVATION_TIME_PERIODS[time_period]
            period_minutes = (
                _clock_minutes(period_start),
                _clock_minutes(period_end),
            )
            interpreted_window = {
                "name": time_period,
                "date": target_date,
                "start_time": period_start,
                "end_time": period_end,
                "selection": "reservation_interval_overlap",
            }

        progress("Matching reservations", "Checking exact event IDs in the validated agenda")
        result = read_agenda_snapshot(self.paths.agenda)
        if result.snapshot is None or result.stale:
            return {
                "fresh": False,
                "refresh_required": True,
                "reason": result.reason or "No current agenda snapshot is available.",
                "matches": [],
                "overlaps": [],
                "selection_id": None,
                "interpreted_window": interpreted_window,
            }

        if date.fromisoformat(target_date) not in set(result.snapshot.dates):
            return {
                "fresh": False,
                "refresh_required": True,
                "coverage_required": True,
                "reason": (
                    f"The complete agenda snapshot does not cover {target_date}; "
                    "refresh before deciding that no reservation exists."
                ),
                "matches": [],
                "overlaps": [],
                "selection_id": None,
                "interpreted_window": interpreted_window,
            }

        reservations = [
            event
            for event in result.snapshot.events
            if event.is_reservation
            and event.event_id is not None
            and event.date.isoformat() == target_date
            and (room is None or event.room == room)
            and (end is None or event.end_time == end)
            and (
                period_minutes is None
                or (
                    _clock_minutes(event.start_time) < period_minutes[1]
                    and period_minutes[0] < _clock_minutes(event.end_time)
                )
            )
        ]
        exact = reservations
        overlaps: list[AgendaEvent] = []
        if start is not None:
            exact = [event for event in reservations if event.start_time == start]
            target_minute = _clock_minutes(start)
            overlaps = [
                event
                for event in reservations
                if event.start_time != start
                and _clock_minutes(event.start_time) < target_minute < _clock_minutes(event.end_time)
            ]
        match_payloads = [_event_payload(result.snapshot.observed_at, item) for item in exact]
        blackout_windows = (
            ((target_date, period_start, period_end),)
            if time_period is not None
            else ()
        )
        selection_id = self._remember_cancellation_selections(
            match_payloads,
            blackout_windows=blackout_windows,
        )
        response = {
            "fresh": True,
            "refresh_required": False,
            "observed_at": result.snapshot.observed_at.isoformat(),
            "matches": match_payloads,
            "overlaps": [_event_payload(result.snapshot.observed_at, item) for item in overlaps],
            "selection_id": selection_id,
            "interpreted_window": interpreted_window,
            "interpreted_scope": None,
            "match_rule": (
                "all reservations whose intervals overlap the fixed half-open "
                f"{time_period} window [{interpreted_window['start_time']}, "
                f"{interpreted_window['end_time']})"
                if interpreted_window is not None
                else "exact start time; overlaps are never selected automatically"
            ),
        }
        return response

    def _find_reservations_by_event_ids(
        self,
        arguments: Mapping[str, Any],
        *,
        progress: ProgressCallback,
    ) -> dict[str, Any]:
        if set(arguments) != {"event_ids"}:
            raise AssistantToolError(
                "event_ids cannot be combined with date, scope, or filtering fields"
            )
        event_ids = arguments["event_ids"]
        if (
            not isinstance(event_ids, list)
            or not 1 <= len(event_ids) <= MAX_BULK_CANCELLATIONS
            or any(type(value) is not int or value <= 0 for value in event_ids)
            or len(set(event_ids)) != len(event_ids)
        ):
            raise AssistantToolError(
                f"event_ids must be 1-{MAX_BULK_CANCELLATIONS} unique positive integers"
            )
        progress(
            "Resolving Terra's selection",
            "Rechecking every selected event ID in the validated agenda",
        )
        result = read_agenda_snapshot(self.paths.agenda)
        if result.snapshot is None or result.stale:
            return {
                "fresh": False,
                "refresh_required": True,
                "reason": result.reason or "No current agenda snapshot is available.",
                "matches": [],
                "overlaps": [],
                "selection_id": None,
            }
        selected_events: list[AgendaEvent] = []
        unresolved: list[int] = []
        for event_id in event_ids:
            matches = [
                event
                for event in result.snapshot.events
                if event.is_reservation and event.event_id == event_id
            ]
            if len(matches) != 1:
                unresolved.append(event_id)
            else:
                selected_events.append(matches[0])
        if unresolved:
            raise AssistantToolError(
                "Terra selected reservation IDs that do not each have one exact match "
                f"in the fresh agenda: {unresolved}"
            )
        match_payloads = [
            _event_payload(result.snapshot.observed_at, event)
            for event in selected_events
        ]
        selection_id = self._remember_cancellation_selections(match_payloads)
        return {
            "fresh": True,
            "refresh_required": False,
            "observed_at": result.snapshot.observed_at.isoformat(),
            "matches": match_payloads,
            "overlaps": [],
            "selection_id": selection_id,
            "interpreted_window": None,
            "interpreted_scope": {
                "name": "terra_selected_event_ids",
                "event_ids": list(event_ids),
                "selection": "exact_positive_event_identity",
            },
            "match_rule": "the exact fresh reservation IDs selected by Terra",
        }

    def _find_upcoming_reservations(
        self,
        arguments: Mapping[str, Any],
        *,
        progress: ProgressCallback,
    ) -> dict[str, Any]:
        if set(arguments) - {"scope", "days"}:
            raise AssistantToolError(
                "scope=upcoming cannot be combined with date or reservation filters"
            )
        if arguments.get("scope") != "upcoming":
            raise AssistantToolError("scope must be upcoming")
        days = arguments.get("days")
        if days is not None and (type(days) is not int or not 1 <= days <= 31):
            raise AssistantToolError("days must be an integer from 1 through 31")
        progress(
            "Finding upcoming reservations",
            "Reading the complete fresh agenda and resolving every future event ID",
        )
        result = read_agenda_snapshot(self.paths.agenda)
        if result.snapshot is None or result.stale:
            return {
                "fresh": False,
                "refresh_required": True,
                "reason": result.reason or "No current agenda snapshot is available.",
                "matches": [],
                "overlaps": [],
                "selection_id": None,
            }

        now_local = datetime.now(LOCAL_TIMEZONE)
        window_end = None if days is None else now_local + timedelta(days=days)
        if window_end is not None:
            required_dates: set[date] = set()
            cursor = now_local.date()
            last_included_date = (window_end - timedelta(microseconds=1)).date()
            while cursor <= last_included_date:
                required_dates.add(cursor)
                cursor += timedelta(days=1)
            missing_dates = sorted(required_dates - set(result.snapshot.dates))
            if missing_dates:
                return {
                    "fresh": False,
                    "refresh_required": True,
                    "coverage_required": True,
                    "reason": (
                        "The complete agenda snapshot does not cover the full rolling "
                        "window; missing dates: "
                        + ", ".join(item.isoformat() for item in missing_dates)
                    ),
                    "matches": [],
                    "overlaps": [],
                    "selection_id": None,
                    "interpreted_scope": {
                        "name": "upcoming",
                        "days": days,
                        "start": now_local.isoformat(),
                        "end": window_end.isoformat(),
                    },
                }

        def event_times(event: AgendaEvent) -> tuple[datetime, datetime]:
            midnight = datetime.combine(
                event.date,
                datetime.min.time(),
                tzinfo=now_local.tzinfo,
            )
            return (
                midnight + timedelta(minutes=_clock_minutes(event.start_time)),
                midnight + timedelta(minutes=_clock_minutes(event.end_time)),
            )

        matches: list[AgendaEvent] = []
        for event in result.snapshot.events:
            if not event.is_reservation or event.event_id is None:
                continue
            event_start, event_end = event_times(event)
            if event_end <= now_local:
                continue
            if window_end is not None and event_start >= window_end:
                continue
            matches.append(event)
        matches.sort(
            key=lambda event: (
                event.date,
                event.start_time,
                event.end_time,
                int(event.event_id or 0),
            )
        )
        if len(matches) > MAX_BULK_CANCELLATIONS:
            raise AssistantToolError(
                "The fresh upcoming set exceeds the bounded cancellation capacity; "
                "Terra must choose a smaller exact event-ID set."
            )
        match_payloads = [
            _event_payload(result.snapshot.observed_at, event) for event in matches
        ]
        selection_id = self._remember_cancellation_selections(match_payloads)
        return {
            "fresh": True,
            "refresh_required": False,
            "observed_at": result.snapshot.observed_at.isoformat(),
            "matches": match_payloads,
            "overlaps": [],
            "selection_id": selection_id,
            "interpreted_window": None,
            "interpreted_scope": {
                "name": "upcoming",
                "days": days,
                "start": now_local.isoformat(),
                "end": None if window_end is None else window_end.isoformat(),
                "selection": "all_future_reservations_in_complete_fresh_agenda",
            },
            "match_rule": (
                "all upcoming reservations in the complete fresh agenda"
                if days is None
                else f"all upcoming reservations in the rolling next {days} days"
            ),
        }

    def _remember_cancellation_selections(
        self,
        candidates: Sequence[Mapping[str, Any]],
        *,
        blackout_windows: Sequence[tuple[str, str, str]] = (),
    ) -> str | None:
        if len(candidates) > MAX_BULK_CANCELLATIONS:
            raise AssistantToolError(
                f"A cancellation selection may contain at most {MAX_BULK_CANCELLATIONS} reservations"
            )
        required = {
            "event_id",
            "date",
            "start_time",
            "end_time",
            "room",
            "match_token",
        }
        if any(not required.issubset(candidate) for candidate in candidates):
            raise AssistantToolError(
                "Every cancellation selection must contain complete fresh event identity"
            )
        event_ids = [candidate.get("event_id") for candidate in candidates]
        candidate_tokens = [candidate.get("match_token") for candidate in candidates]
        if (
            any(type(event_id) is not int or event_id <= 0 for event_id in event_ids)
            or len(set(event_ids)) != len(event_ids)
            or any(
                not isinstance(token, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", token) is None
                for token in candidate_tokens
            )
            or len(set(candidate_tokens)) != len(candidate_tokens)
        ):
            raise AssistantToolError(
                "Cancellation selections require unique positive event IDs and fresh tokens"
            )
        stack = getattr(self._dispatch_generation, "stack", ())
        if not stack:
            return None
        generation = stack[-1]
        tokens: set[str] = set()
        identities: list[tuple[Any, ...]] = []
        with self._cancel_lock:
            if generation != self._turn_generation:
                return None
            with self._selection_lock:
                for candidate in candidates:
                    token = candidate.get("match_token")
                    assert isinstance(token, str)
                    tokens.add(token)
                    identity = (
                        candidate.get("event_id"),
                        candidate.get("date"),
                        candidate.get("start_time"),
                        candidate.get("end_time"),
                        candidate.get("room"),
                        token,
                    )
                    identities.append(identity)
                    self._issued_cancellation_targets[token] = (
                        generation,
                        identity[:-1],
                    )
                if tokens:
                    issued_batch = (generation, frozenset(tokens))
                    if issued_batch not in self._issued_cancellation_batches:
                        self._issued_cancellation_batches.append(issued_batch)
                    ordered_identities = tuple(identities)
                    canonical_windows = tuple(
                        sorted(tuple(window) for window in blackout_windows)
                    )
                    digest_input = json.dumps(
                        {
                            "generation": generation,
                            "targets": ordered_identities,
                            "blackout_windows": canonical_windows,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    selection_id = "selection:" + hashlib.sha256(
                        digest_input.encode("utf-8")
                    ).hexdigest()
                    self._issued_cancellation_selections[selection_id] = (
                        generation,
                        ordered_identities,
                        canonical_windows,
                    )
                    return selection_id
        return None

    def _set_future_practice_plan(
        self,
        arguments: dict[str, Any],
        *,
        user_request: str,
        progress: ProgressCallback,
    ) -> dict[str, Any]:
        authorize_tool_mutation(
            "set_future_practice_plan", arguments, user_request
        )
        expected = {
            "request_quote",
            "title",
            "intent_summary",
            "start_date",
            "end_date",
            "daily_targets",
            "replace_overlapping",
        }
        if set(arguments) != expected:
            raise AssistantToolError(
                "A future practice plan requires its complete range, intent, targets, and replacement choice."
            )
        progress(
            "Resolving your future plan",
            "Validating every date and translating the intent into Booker targets",
        )

        def mutate(settings: dict[str, Any]) -> dict[str, Any]:
            return apply_future_practice_plan(
                settings,
                title=arguments["title"],
                intent_summary=arguments["intent_summary"],
                start_date=arguments["start_date"],
                end_date=arguments["end_date"],
                daily_targets=arguments["daily_targets"],
                replace_overlapping=arguments["replace_overlapping"],
            )

        record = update_settings(mutate, self.paths.settings)
        progress(
            "Future plan saved",
            "The autonomous Booker will use these targets as each date becomes bookable",
        )
        return {
            "plan": record,
            "booking_effect": (
                "These exact date targets are now active in practice_plan. Live room, quota, "
                "conflict, and booking-window rules still apply."
            ),
            "target_semantics": "Each value is total desired practice on that date.",
            "session_planning": {
                "maximum_single_session_minutes": 120,
                "split_larger_targets": True,
                "ranking": "best feasible non-overlapping sessions from current preferences",
                "weekday_peak_minutes_maximum": 120,
                "recurring_runs_pursue_remaining_target": True,
                "multi_session_dates": [
                    item["date"]
                    for item in arguments["daily_targets"]
                    if float(item["hours"]) * 60 > 120
                ],
            },
        }

    def _update_preferences(
        self,
        arguments: dict[str, Any],
        *,
        user_request: str,
        progress: ProgressCallback,
    ) -> dict[str, Any]:
        authorize_tool_mutation(
            "update_booker_preferences", arguments, user_request
        )
        patch = dict(arguments)
        patch.pop("request_quote", None)
        allowed = {
            "booking_days",
            "practice_plan",
            "time_preferences",
            "booking_strategy",
            "room_preferences",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise AssistantToolError(f"Unknown preference section(s): {sorted(unknown)}")
        if not patch:
            raise AssistantToolError("No preference changes were supplied")
        progress("Updating preferences", "Validating a locked, scoped settings change")

        def mutate(settings: dict[str, Any]) -> dict[str, Any]:
            changed: dict[str, Any] = {}
            if "booking_days" in patch:
                changed["booking_days"] = self._apply_booking_days(
                    settings, patch["booking_days"]
                )
            if "practice_plan" in patch:
                changed["practice_plan"] = self._apply_practice_plan(
                    settings, patch["practice_plan"]
                )
            if "time_preferences" in patch:
                changed["time_preferences"] = self._apply_time_preferences(
                    settings, patch["time_preferences"]
                )
            if "booking_strategy" in patch:
                value = apply_booking_strategy_update(settings, patch["booking_strategy"])
                changed["booking_strategy"] = booking_strategy_to_dict(value)
            if "room_preferences" in patch:
                value = apply_room_preferences_update(settings, patch["room_preferences"])
                changed["room_preferences"] = room_preferences_to_dict(value)
            return changed

        changed = update_settings(mutate, self.paths.settings)
        progress("Preferences saved", "The next plan refresh will use the updated settings")
        multi_session_dates = []
        practice_patch = patch.get("practice_plan")
        if isinstance(practice_patch, Mapping):
            touched_dates = {
                item.get("date")
                for field in ("date_overrides", "date_adjustments")
                for item in practice_patch.get(field, [])
                if isinstance(item, Mapping) and isinstance(item.get("date"), str)
            }
            final_practice = changed.get("practice_plan", {})
            final_overrides = (
                final_practice.get("date_overrides", {})
                if isinstance(final_practice, Mapping)
                else {}
            )
            for target_date in touched_dates:
                hours = final_overrides.get(target_date)
                if (
                    isinstance(hours, (int, float))
                    and not isinstance(hours, bool)
                    and float(hours) * 60 > 120
                ):
                    multi_session_dates.append(target_date)
        return {
            "changed": changed,
            "note": "The existing display plan is now marked stale until it is refreshed.",
            "target_semantics": "Dated practice hours are total daily goals, not one booking.",
            "session_planning": {
                "maximum_single_session_minutes": 120,
                "split_larger_targets": True,
                "weekday_peak_minutes_maximum": 120,
                "recurring_runs_pursue_remaining_target": True,
                "multi_session_dates": sorted(set(multi_session_dates)),
            },
        }

    @staticmethod
    def _apply_booking_days(
        settings: MutableMapping[str, Any], raw_updates: Any
    ) -> dict[str, bool]:
        if not isinstance(raw_updates, list) or not raw_updates:
            raise AssistantToolError("booking_days must be a non-empty list")
        disabled = settings.get("disabled_dates", [])
        if not isinstance(disabled, list) or not all(isinstance(item, str) for item in disabled):
            raise AssistantToolError("Existing disabled_dates settings are invalid")
        disabled_set = {_canonical_date(item, "disabled date") for item in disabled}
        changed: dict[str, bool] = {}
        for item in raw_updates:
            if not isinstance(item, Mapping) or set(item) != {"date", "enabled"}:
                raise AssistantToolError("Each booking-day update needs date and enabled")
            day = _canonical_date(item["date"], "booking day")
            enabled = item["enabled"]
            if type(enabled) is not bool:
                raise AssistantToolError("booking day enabled must be Boolean")
            if day in changed:
                raise AssistantToolError(f"Duplicate booking-day update: {day}")
            changed[day] = enabled
            if enabled:
                disabled_set.discard(day)
            else:
                disabled_set.add(day)
        settings["disabled_dates"] = sorted(disabled_set)
        return changed

    @staticmethod
    def _apply_practice_plan(
        settings: MutableMapping[str, Any], raw_patch: Any
    ) -> dict[str, Any]:
        if not isinstance(raw_patch, Mapping):
            raise AssistantToolError("practice_plan update must be an object")
        unknown = set(raw_patch) - {
            "enabled",
            "default_hours",
            "date_overrides",
            "date_adjustments",
        }
        if unknown:
            raise AssistantToolError(f"Unknown practice-plan fields: {sorted(unknown)}")
        current = load_practice_plan(settings)
        overrides = dict(current.date_overrides or {})
        raw_overrides = raw_patch.get("date_overrides", [])
        raw_adjustments = raw_patch.get("date_adjustments", [])
        if not isinstance(raw_overrides, list):
            raise AssistantToolError("practice_plan.date_overrides must be a list")
        if not isinstance(raw_adjustments, list):
            raise AssistantToolError("practice_plan.date_adjustments must be a list")
        if raw_overrides and raw_adjustments:
            raise AssistantToolError(
                "Use either absolute date overrides or additive date adjustments, not both"
            )
        seen: set[str] = set()
        for item in raw_overrides:
            if not isinstance(item, Mapping) or set(item) != {"date", "hours"}:
                raise AssistantToolError("Each date override needs date and hours")
            key = _canonical_date(item["date"], "practice-plan date")
            if key in seen:
                raise AssistantToolError(f"Duplicate practice-plan override: {key}")
            seen.add(key)
            if item["hours"] is None:
                overrides.pop(key, None)
            else:
                overrides[key] = item["hours"]
        for item in raw_adjustments:
            if not isinstance(item, Mapping) or set(item) != {"date", "delta_hours"}:
                raise AssistantToolError(
                    "Each date adjustment needs date and delta_hours"
                )
            key = _canonical_date(item["date"], "practice-plan adjustment date")
            if key in seen:
                raise AssistantToolError(f"Duplicate practice-plan adjustment: {key}")
            delta = item["delta_hours"]
            if (
                isinstance(delta, bool)
                or not isinstance(delta, (int, float))
                or float(delta) == 0
                or abs(float(delta) * 2 - round(float(delta) * 2)) > 1e-9
            ):
                raise AssistantToolError(
                    "practice-plan delta_hours must be a non-zero half-hour step"
                )
            seen.add(key)
            baseline = overrides.get(key, current.default_hours)
            overrides[key] = float(baseline) + float(delta)
        candidate = {
            "enabled": raw_patch.get("enabled", current.enabled),
            "default_hours": raw_patch.get("default_hours", current.default_hours),
            "date_overrides": overrides,
        }
        validated = load_practice_plan({"practice_plan": candidate})
        stored = {
            "enabled": validated.enabled,
            "default_hours": validated.default_hours,
            "date_overrides": dict(validated.date_overrides or {}),
        }
        settings["practice_plan"] = stored
        return stored

    @staticmethod
    def _apply_time_preferences(
        settings: MutableMapping[str, Any], raw_patch: Any
    ) -> dict[str, Any]:
        if not isinstance(raw_patch, Mapping):
            raise AssistantToolError("time_preferences update must be an object")
        allowed = {"enabled", "start_time", "end_time", "strict_mode", "preset"}
        unknown = set(raw_patch) - allowed
        if unknown:
            raise AssistantToolError(f"Unknown time-preference fields: {sorted(unknown)}")
        current = settings.get("time_preferences", {})
        if not isinstance(current, Mapping):
            raise AssistantToolError("Existing time_preferences settings are invalid")
        stored = {
            "enabled": current.get("enabled", False),
            "preset": current.get("preset", "afternoon_evening"),
            "custom_start_hour": current.get("custom_start_hour", 14),
            "custom_start_min": current.get("custom_start_min", 0),
            "custom_end_hour": current.get("custom_end_hour", 22),
            "custom_end_min": current.get("custom_end_min", 0),
            "strict_mode": current.get("strict_mode", False),
        }
        if "enabled" in raw_patch:
            stored["enabled"] = raw_patch["enabled"]
        if "strict_mode" in raw_patch:
            stored["strict_mode"] = raw_patch["strict_mode"]
        if "preset" in raw_patch:
            stored["preset"] = raw_patch["preset"]
        for key, prefix in (("start_time", "custom_start"), ("end_time", "custom_end")):
            if key in raw_patch:
                value = _canonical_clock(raw_patch[key], f"time_preferences.{key}")
                stored[f"{prefix}_hour"] = int(value[:2])
                stored[f"{prefix}_min"] = int(value[3:])
                stored["preset"] = "custom"
        for flag in ("enabled", "strict_mode"):
            if type(stored[flag]) is not bool:
                raise AssistantToolError(f"time_preferences.{flag} must be Boolean")
        if stored["preset"] not in {
            "morning",
            "peak_afternoon",
            "afternoon",
            "evening",
            "afternoon_evening",
            "custom",
        }:
            raise AssistantToolError("time_preferences.preset is unsupported")
        start = stored["custom_start_hour"] * 60 + stored["custom_start_min"]
        end = stored["custom_end_hour"] * 60 + stored["custom_end_min"]
        if stored["preset"] == "custom" and end <= start:
            raise AssistantToolError("Preferred end time must be after start time")
        settings["time_preferences"] = stored
        return {
            "enabled": stored["enabled"],
            "preset": stored["preset"],
            "start_time": f"{stored['custom_start_hour']:02d}:{stored['custom_start_min']:02d}",
            "end_time": f"{stored['custom_end_hour']:02d}:{stored['custom_end_min']:02d}",
            "strict_mode": stored["strict_mode"],
        }

    def _run_booker(
        self,
        arguments: dict[str, Any],
        *,
        user_request: str,
        progress: ProgressCallback,
    ) -> dict[str, Any]:
        authorize_tool_mutation("run_booker", arguments, user_request)
        allowed = {
            "request_quote",
            "only_date",
            "only_room",
            "max_actions",
            "max_action_minutes",
        }
        unknown = set(arguments) - allowed
        if unknown:
            raise AssistantToolError(f"Unknown run_booker argument(s): {sorted(unknown)}")
        max_actions = arguments.get("max_actions")
        if type(max_actions) is not int or max_actions != 1:
            raise AssistantToolError("The assistant can run exactly one plan-selected action")
        if "max_action_minutes" in arguments and not {
            "only_date",
            "only_room",
        }.issubset(arguments):
            raise AssistantToolError(
                "max_action_minutes requires both only_date and only_room for exact scope"
            )
        flags = ["--headless", "--max-actions", str(max_actions)]
        if "only_date" in arguments:
            flags.extend(["--only-date", _canonical_date(arguments["only_date"], "only_date")])
        if "only_room" in arguments:
            room = arguments["only_room"]
            if not isinstance(room, str) or not room.strip():
                raise AssistantToolError("only_room must be a non-empty exact room name")
            flags.extend(["--only-room", room])
        if "max_action_minutes" in arguments:
            minutes = arguments["max_action_minutes"]
            if type(minutes) is not int or not 30 <= minutes <= 120 or minutes % 15:
                raise AssistantToolError("max_action_minutes must be 30-120 in 15-minute steps")
            flags.extend(["--max-action-minutes", str(minutes)])
        progress("Running the booker", "Refreshing live policy and checking exact booking opportunities")
        command = self._run_booker_command(flags, progress=progress, timeout=20 * 60)
        verified_actions: int | None = None
        for line in reversed(command.get("output", [])):
            match = re.fullmatch(r"History saved: (\d+) bookings, \d+ events", line)
            if match is not None:
                verified_actions = int(match.group(1))
                break
        return {
            "command": command,
            "verified_actions": verified_actions,
            "result_note": (
                "verified_actions is the run's persisted count of verified creates and "
                "extensions. Null means the workflow exited without publishing normal run history."
            ),
            "post_run_context": build_assistant_context(
                ["agenda", "plan", "mutations", "history"], paths=self.paths
            ),
        }

    def _resolve_cancellation_selection(
        self,
        selection_id: Any,
    ) -> tuple[list[dict[str, Any]], tuple[tuple[str, str, str], ...]]:
        if not isinstance(selection_id, str) or re.fullmatch(
            r"selection:[0-9a-f]{64}", selection_id
        ) is None:
            raise AssistantToolError(
                "selection_id must be one opaque current-turn value from find_reservations"
            )
        stack = getattr(self._dispatch_generation, "stack", ())
        generation = stack[-1] if stack else self._turn_generation
        with self._cancel_lock:
            with self._selection_lock:
                issued = self._issued_cancellation_selections.get(selection_id)
                if issued is None or issued[0] != generation:
                    raise AssistantToolError(
                        "The cancellation selection is missing, stale, or belongs to an "
                        "earlier user turn. Resolve the reservations again."
                    )
                identities = issued[1]
                protected_windows = issued[2]
        targets = [
            {
                "event_id": identity[0],
                "date": identity[1],
                "start_time": identity[2],
                "end_time": identity[3],
                "room": identity[4],
                "match_token": identity[5],
            }
            for identity in identities
        ]
        return targets, protected_windows

    def _cancel_reservations(
        self,
        arguments: dict[str, Any],
        *,
        user_request: str,
        progress: ProgressCallback,
    ) -> dict[str, Any]:
        authorize_tool_mutation("cancel_reservations", arguments, user_request)
        if set(arguments) != {"request_quote", "selection_id"}:
            raise AssistantToolError(
                "cancel_reservations requires the complete active message and exactly "
                "one current selection_id from find_reservations"
            )
        raw_targets, protected_windows = self._resolve_cancellation_selection(
            arguments["selection_id"]
        )
        if (
            not isinstance(raw_targets, list)
            or not 1 <= len(raw_targets) <= MAX_BULK_CANCELLATIONS
        ):
            raise AssistantToolError(
                f"reservations must contain 1-{MAX_BULK_CANCELLATIONS} exact targets"
            )
        if not all(isinstance(item, Mapping) for item in raw_targets):
            raise AssistantToolError("Every bulk cancellation target must be an object")

        # Validate every target and token against one complete fresh snapshot
        # before the first remote mutation. No partially validated batch starts.
        targets, trusted_observed_at = self._validate_cancellation_targets(
            raw_targets,
            require_exact_batch=True,
        )
        outcomes: list[dict[str, Any]] = []
        deadline = time.monotonic() + BULK_CANCELLATION_TIMEOUT_SECONDS
        stopped_early = False
        reconciliation_required = False
        remaining_targets: list[dict[str, Any]] = []
        stop_reason: str | None = None
        requested_windows = protected_windows or tuple(
            (
                str(target["date"]),
                str(target["start_time"]),
                str(target["end_time"]),
            )
            for target in targets
        )
        persisted_windows: list[tuple[str, str, str]] = []
        blackout_result: list[dict[str, str]] = []
        blackout_error: str | None = None
        plan_warning: str | None = None
        plan_clear_attempted = False
        broad_protection_persisted = False

        for index, target in enumerate(targets):
            remaining_seconds = int(deadline - time.monotonic())
            if remaining_seconds <= 0:
                stopped_early = True
                remaining_targets = targets[index:]
                stop_reason = (
                    "The bounded bulk-cancellation deadline elapsed before this target; "
                    "it and every later target were not attempted."
                )
                break
            progress(
                f"Cancelling reservation {index + 1} of {len(targets)}",
                (
                    f"Revalidating event {target['event_id']} in {target['room']} "
                    "before cancellation"
                ),
            )
            try:
                command = self._run_booker_command(
                    self._cancellation_flags(target),
                    progress=progress,
                    timeout=min(15 * 60, remaining_seconds),
                    allow_nonzero=True,
                )
            except AssistantToolCancelled:
                raise
            except AssistantToolError as exc:
                outcomes.append(
                    {
                        "reservation": target,
                        "status": "uncertain_pending",
                        "verified_absent": False,
                        "reconciliation_required": True,
                        "message": str(exc),
                    }
                )
                stopped_early = True
                reconciliation_required = True
                remaining_targets = targets[index + 1 :]
                stop_reason = str(exc)
                break

            exit_code = command["exit_code"]
            if exit_code == 0:
                outcomes.append(
                    {
                        "reservation": target,
                        "status": "verified_cancelled",
                        "verified_absent": True,
                        "reconciliation_required": False,
                        "command": command,
                    }
                )
                windows_for_success: tuple[tuple[str, str, str], ...]
                if protected_windows:
                    windows_for_success = (
                        () if broad_protection_persisted else protected_windows
                    )
                else:
                    windows_for_success = (
                        (
                            str(target["date"]),
                            str(target["start_time"]),
                            str(target["end_time"]),
                        ),
                    )
                if windows_for_success:
                    try:
                        blackouts = add_rebooking_blackouts(
                            windows_for_success,
                            path=self.paths.settings,
                        )
                    except SettingsError as exc:
                        blackout_error = str(exc)
                        stopped_early = True
                        remaining_targets = targets[index + 1 :]
                        stop_reason = (
                            "Cancellation was verified, but its no-rebook plan could not "
                            f"be persisted: {exc}. No later target was attempted."
                        )
                        break
                    persisted_windows.extend(windows_for_success)
                    blackout_result = [item.to_dict() for item in blackouts]
                    broad_protection_persisted = bool(protected_windows)
                    if not plan_clear_attempted:
                        plan_clear_attempted = True
                        try:
                            clear_booking_plan(self.paths.plan)
                        except BookingPlanError as exc:
                            plan_warning = str(exc)
                if index + 1 < len(targets):
                    try:
                        refreshed, trusted_observed_at = (
                            self._refresh_cancellation_targets_after_success(
                                targets[index + 1 :],
                                after=trusted_observed_at,
                            )
                        )
                    except AssistantToolError as exc:
                        stopped_early = True
                        remaining_targets = targets[index + 1 :]
                        stop_reason = str(exc)
                        break
                    targets[index + 1 :] = refreshed
                continue
            # Exit 7 is the backend's explicit pre-receipt, safely-not-applied
            # result. It still stops the batch because later targets must not
            # run after any failed item. Exit 5 and every other nonzero result
            # are uncertain/pending and require reconciliation.
            not_applied = exit_code == 7
            outcomes.append(
                {
                    "reservation": target,
                    "status": (
                        "not_applied_pre_receipt"
                        if not_applied
                        else "uncertain_pending"
                    ),
                    "verified_absent": False,
                    "reconciliation_required": not not_applied,
                    "command": command,
                }
            )
            stopped_early = True
            reconciliation_required = not not_applied
            remaining_targets = targets[index + 1 :]
            if not_applied:
                stop_reason = (
                    "Cancellation command exited 7 before any receipt or destructive click; "
                    "the target was not applied and no later target was attempted."
                )
            else:
                stop_reason = (
                    f"Cancellation command exited {exit_code}; its remote outcome is "
                    "uncertain/pending, so no later target was attempted."
                )
            break

        per_target_outcomes = [
            *outcomes,
            *(
                {
                    "reservation": target,
                    "status": "not_attempted",
                    "verified_absent": False,
                    "reconciliation_required": reconciliation_required,
                }
                for target in remaining_targets
            ),
        ]
        verified_targets = [
            item["reservation"]
            for item in outcomes
            if item["status"] == "verified_cancelled"
        ]
        return {
            "requested_count": len(targets),
            "cancelled_count": sum(
                item["status"] == "verified_cancelled" for item in outcomes
            ),
            "outcomes": per_target_outcomes,
            "remaining_targets": remaining_targets,
            "stopped_early": stopped_early,
            "reconciliation_required": reconciliation_required,
            "stop_reason": stop_reason,
            "requested_protection_windows": [
                {
                    "date": target_date,
                    "start_time": start_time,
                    "end_time": end_time,
                }
                for target_date, start_time, end_time in requested_windows
            ],
            "protected_windows": [
                {
                    "date": target_date,
                    "start_time": start_time,
                    "end_time": end_time,
                }
                for target_date, start_time, end_time in persisted_windows
            ],
            "protection_persisted": bool(persisted_windows),
            "rebooking_blackouts": blackout_result,
            "blackout_update_error": blackout_error,
            "plan_invalidation_warning": plan_warning,
            "plan_effect": (
                "Verified cancelled time is kept free on later Booker runs until the "
                "user explicitly reopens it. Booking-plan fingerprints changed immediately."
                if persisted_windows
                else None
            ),
            "result_note": (
                "Only status=verified_cancelled with verified_absent=true is a completed "
                "cancellation. Exit 7 is status=not_applied_pre_receipt; exit 5, other "
                "nonzero exits, and command failures are status=uncertain_pending. Every "
                "nonzero result stops the sequence, and remaining targets are not_attempted. "
                "After each verified success, the host re-resolves all remaining exact tuples "
                "in a newer complete snapshot."
            ),
        }

    def _reopen_booking_window(
        self,
        arguments: dict[str, Any],
        *,
        user_request: str,
        progress: ProgressCallback,
    ) -> dict[str, Any]:
        authorize_tool_mutation("reopen_booking_window", arguments, user_request)
        if set(arguments) != {"request_quote", "date", "start_time", "end_time"}:
            raise AssistantToolError(
                "reopen_booking_window requires exactly date, start_time, end_time, "
                "and the complete active message"
            )
        target_date = _canonical_date(arguments["date"], "date")
        start_time = _canonical_clock(arguments["start_time"], "start_time")
        end_time = _canonical_clock(arguments["end_time"], "end_time")
        start_minutes = _clock_minutes(start_time)
        end_minutes = _clock_minutes(end_time)
        if end_minutes <= start_minutes:
            raise AssistantToolError("The reopened window end must be after its start")
        try:
            existing = load_rebooking_blackouts(path=self.paths.settings)
        except SettingsError as exc:
            raise AssistantToolError(str(exc)) from exc
        if not interval_overlaps_blackout(
            existing,
            target_date,
            start_minutes,
            end_minutes,
        ):
            raise AssistantToolError(
                "That exact dated interval does not overlap any protected cancelled time."
            )
        progress(
            "Reopening booking time",
            "Removing the selected interval from the persistent no-rebook plan",
        )
        try:
            remaining = subtract_rebooking_blackout(
                target_date,
                start_time,
                end_time,
                path=self.paths.settings,
            )
        except SettingsError as exc:
            raise AssistantToolError(str(exc)) from exc
        plan_warning: str | None = None
        try:
            clear_booking_plan(self.paths.plan)
        except BookingPlanError as exc:
            plan_warning = str(exc)
        return {
            "reopened": {
                "date": target_date,
                "start_time": start_time,
                "end_time": end_time,
            },
            "remaining_rebooking_blackouts": [
                item.to_dict() for item in remaining
            ],
            "reopened_persisted": True,
            "plan_invalidation_warning": plan_warning,
            "plan_effect": (
                "Automatic booking may use the reopened interval on later runs; "
                "booking-plan fingerprints changed immediately."
            ),
        }

    @staticmethod
    def _cancellation_target_fields() -> set[str]:
        return {"event_id", "date", "start_time", "end_time", "room", "match_token"}

    @staticmethod
    def _cancellation_flags(target: Mapping[str, Any]) -> list[str]:
        return [
            "--headless",
            "--cancel-event-id",
            str(target["event_id"]),
            "--cancel-date",
            str(target["date"]),
            "--cancel-start",
            str(target["start_time"]),
            "--cancel-end",
            str(target["end_time"]),
            "--cancel-room",
            str(target["room"]),
        ]

    def _validate_cancellation_targets(
        self,
        raw_targets: Sequence[Mapping[str, Any]],
        *,
        require_exact_batch: bool,
    ) -> tuple[list[dict[str, Any]], datetime]:
        required = self._cancellation_target_fields()
        normalized: list[dict[str, Any]] = []
        seen_event_ids: set[int] = set()
        for index, raw in enumerate(raw_targets):
            if set(raw) != required:
                raise AssistantToolError(
                    f"reservations[{index}] requires the exact event ID, tuple, and match token"
                )
            event_id = raw["event_id"]
            if type(event_id) is not int or event_id <= 0:
                raise AssistantToolError(
                    f"reservations[{index}].event_id must be a positive integer"
                )
            if event_id in seen_event_ids:
                raise AssistantToolError("Bulk cancellation event IDs must be unique")
            seen_event_ids.add(event_id)
            target = {
                "event_id": event_id,
                "date": _canonical_date(raw["date"], f"reservations[{index}].date"),
                "start_time": _canonical_clock(
                    raw["start_time"], f"reservations[{index}].start_time"
                ),
                "end_time": _canonical_clock(
                    raw["end_time"], f"reservations[{index}].end_time"
                ),
                "room": raw["room"],
                "match_token": raw["match_token"],
            }
            if _clock_minutes(target["end_time"]) <= _clock_minutes(target["start_time"]):
                raise AssistantToolError("Cancellation end_time must be after start_time")
            if not isinstance(target["room"], str) or not target["room"].strip():
                raise AssistantToolError("room must be a non-empty exact room name")
            if not isinstance(target["match_token"], str) or re.fullmatch(
                r"sha256:[0-9a-f]{64}", target["match_token"]
            ) is None:
                raise AssistantToolError("match_token must be one exact fresh agenda token")
            normalized.append(target)

        snapshot_result = read_agenda_snapshot(self.paths.agenda)
        if snapshot_result.snapshot is None or snapshot_result.stale:
            raise AssistantToolError(
                "The cancellation match is stale; refresh the agenda and resolve it again."
            )
        for target in normalized:
            matching = [
                event
                for event in snapshot_result.snapshot.events
                if event.is_reservation
                and event.event_id == target["event_id"]
                and event.date.isoformat() == target["date"]
                and event.start_time == target["start_time"]
                and event.end_time == target["end_time"]
                and event.room == target["room"]
            ]
            if len(matching) != 1:
                raise AssistantToolError(
                    "A selected reservation no longer has one exact match; resolve the batch again."
                )
            expected_token = _event_token(snapshot_result.snapshot.observed_at, matching[0])
            if target["match_token"] != expected_token:
                raise AssistantToolError(
                    "The reservation match token changed; refresh and resolve before cancelling."
                )
        self._claim_and_consume_cancellation_operation(
            normalized,
            require_exact_batch=require_exact_batch,
        )
        return normalized, snapshot_result.snapshot.observed_at

    def _claim_and_consume_cancellation_operation(
        self,
        targets: Sequence[Mapping[str, Any]],
        *,
        require_exact_batch: bool,
    ) -> None:
        """Atomically bind one validated selection set to one user-turn operation."""

        stack = getattr(self._dispatch_generation, "stack", ())
        generation = stack[-1] if stack else self._turn_generation
        tokens = frozenset(str(target["match_token"]) for target in targets)
        identities = {
            str(target["match_token"]): (
                target["event_id"],
                target["date"],
                target["start_time"],
                target["end_time"],
                target["room"],
            )
            for target in targets
        }
        with self._cancel_lock:
            with self._selection_lock:
                if generation != self._turn_generation:
                    raise AssistantToolCancelled(
                        "This cancellation request no longer belongs to the active user turn."
                    )
                if generation in self._cancelled_generations:
                    raise AssistantToolCancelled("The Booker action was stopped.")
                if generation in self._claimed_cancellation_generations:
                    raise AssistantToolError(
                        "A cancellation operation already started for this user turn; "
                        "no second cancellation may queue or retry."
                    )
                for token, identity in identities.items():
                    selection = self._issued_cancellation_targets.get(token)
                    if (
                        selection is None
                        or selection[0] != generation
                        or selection[1] != identity
                    ):
                        raise AssistantToolError(
                            "Every cancellation target must come unchanged from a "
                            "find_reservations result in the active user turn."
                        )
                if require_exact_batch:
                    complete_batches = [
                        issued_tokens
                        for issued_generation, issued_tokens
                        in self._issued_cancellation_batches
                        if issued_generation == generation
                        and issued_tokens.issubset(tokens)
                    ]
                    covered_tokens = frozenset().union(*complete_batches)
                    if covered_tokens != tokens:
                        raise AssistantToolError(
                            "The cancellation targets must be the exact union of one or more "
                            "complete unchanged find_reservations match sets; a partial broad "
                            "result or an unissued addition is not authorized."
                        )

                self._claimed_cancellation_generations.add(generation)
                for token in tokens:
                    self._issued_cancellation_targets.pop(token, None)
                self._issued_cancellation_batches = [
                    issued
                    for issued in self._issued_cancellation_batches
                    if issued[1].isdisjoint(tokens)
                ]
                self._issued_cancellation_selections = {
                    selection_id: issued
                    for selection_id, issued in self._issued_cancellation_selections.items()
                    if frozenset(
                        str(identity[5]) for identity in issued[1]
                    ).isdisjoint(tokens)
                }

    def _refresh_cancellation_targets_after_success(
        self,
        targets: Sequence[Mapping[str, Any]],
        *,
        after: datetime,
    ) -> tuple[list[dict[str, Any]], datetime]:
        """Rebind untouched exact tuples to the newer post-cancel agenda proof."""

        snapshot_result = read_agenda_snapshot(self.paths.agenda)
        snapshot = snapshot_result.snapshot
        if (
            snapshot is None
            or snapshot_result.stale
            or snapshot.observed_at <= after
        ):
            raise AssistantToolError(
                "The verified cancellation did not publish a newer fresh agenda snapshot; "
                "remaining targets were not attempted."
            )
        refreshed: list[dict[str, Any]] = []
        for target in targets:
            matching = [
                event
                for event in snapshot.events
                if event.is_reservation
                and event.event_id == target["event_id"]
                and event.date.isoformat() == target["date"]
                and event.start_time == target["start_time"]
                and event.end_time == target["end_time"]
                and event.room == target["room"]
            ]
            if len(matching) != 1:
                raise AssistantToolError(
                    "A remaining reservation was not one unchanged exact match in the newer "
                    "agenda; no further targets were attempted."
                )
            rebound = dict(target)
            rebound["match_token"] = _event_token(snapshot.observed_at, matching[0])
            refreshed.append(rebound)
        return refreshed, snapshot.observed_at

    def _run_booker_command(
        self,
        flags: Sequence[str],
        *,
        progress: ProgressCallback,
        timeout: int,
        allow_nonzero: bool = False,
    ) -> dict[str, Any]:
        generation, cancel_event = self._command_cancel_event()
        try:
            with self._action_lock:
                if cancel_event.is_set():
                    raise AssistantToolCancelled("The Booker action was stopped.")
                if self._command_runner is not None:
                    result = self._command_runner(
                        list(flags),
                        progress=progress,
                        cancel_event=cancel_event,
                        timeout=timeout,
                    )
                    if not isinstance(result, Mapping) or type(result.get("exit_code")) is not int:
                        raise AssistantToolError("The Booker command returned an invalid result")
                    payload = dict(result)
                    if payload["exit_code"] != 0 and not allow_nonzero:
                        output = payload.get("output")
                        detail = output[-1] if isinstance(output, list) and output else None
                        raise AssistantToolError(
                            "The Booker stopped without a verified result. "
                            + (str(detail) if detail else f"Exit code {payload['exit_code']}.")
                        )
                    return payload
                if not self.python_executable.is_file():
                    raise AssistantToolError(
                        f"The isolated Booker Python runtime is missing: {self.python_executable}"
                    )
                if not self.booker_script.is_file():
                    raise AssistantToolError(
                        f"The Booker entry point is missing: {self.booker_script}"
                    )
                command = [str(self.python_executable), str(self.booker_script), *flags]
                creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                try:
                    process = subprocess.Popen(
                        command,
                        cwd=str(APP_DIR),
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                        creationflags=creationflags,
                    )
                except OSError as exc:
                    raise AssistantToolError(f"Could not start the Booker: {exc}") from exc

                safe_lines: list[str] = []
                assert process.stdout is not None
                output_queue: queue.Queue[str | None] = queue.Queue()

                def read_output() -> None:
                    try:
                        for stream_line in process.stdout:
                            output_queue.put(stream_line)
                    finally:
                        output_queue.put(None)

                reader = threading.Thread(
                    target=read_output,
                    name="AsimutAssistantBookerOutput",
                    daemon=True,
                )
                reader.start()
                deadline = time.monotonic() + timeout
                stream_closed = False
                try:
                    while not stream_closed or process.poll() is None:
                        if cancel_event.is_set():
                            process.terminate()
                            try:
                                process.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                process.kill()
                            raise AssistantToolCancelled("The Booker action was stopped.")
                        if time.monotonic() >= deadline:
                            process.terminate()
                            try:
                                process.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                process.kill()
                            raise AssistantToolError(
                                f"The Booker did not finish within {timeout // 60} minutes."
                            )
                        try:
                            line = output_queue.get(timeout=0.1)
                        except queue.Empty:
                            continue
                        if line is None:
                            stream_closed = True
                        else:
                            clean = " ".join(line.strip().split())
                            if clean and not _SECRET_LINE.search(clean):
                                safe_lines.append(clean[:500])
                                safe_lines = safe_lines[-40:]
                                progress("Booker activity", clean[:220])
                    return_code = process.wait(timeout=5)
                finally:
                    reader.join(timeout=1)
                    try:
                        process.stdout.close()
                    except OSError:
                        pass
                payload = {
                    "exit_code": return_code,
                    "output": safe_lines,
                    "completed": return_code == 0,
                }
                if return_code != 0 and not allow_nonzero:
                    raise AssistantToolError(
                        "The Booker stopped without a verified result. "
                        + (safe_lines[-1] if safe_lines else f"Exit code {return_code}.")
                    )
                return payload
        finally:
            self._release_command_cancel_event(generation, cancel_event)


__all__ = [
    "AssistantToolCancelled",
    "AssistantToolError",
    "BookerToolSurface",
    "MUTATING_TOOLS",
    "ToolResult",
    "authorize_tool_mutation",
    "dynamic_tool_specs",
]
