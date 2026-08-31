"""Typed, allow-listed action surface for the in-app Codex assistant.

Codex never receives a shell, filesystem, Playwright, or credential tool.  It
can only call the domain operations declared here; each mutation is tied back
to an exact quote from the active user message.
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
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping, Sequence

from agenda_snapshot import AGENDA_SNAPSHOT_FILE, AgendaEvent, read_agenda_snapshot
from app_settings import SETTINGS_FILE, SettingsError, update_settings
from assistant_context import ContextPaths, build_assistant_context
from assistant_plans import AssistantPlanError, apply_future_practice_plan
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

_SECRET_LINE = re.compile(
    r"(?i)(password|passcode|one[- ]?time|\botp\b|authorization:|cookie|credential|sms code|bridge token)"
)
_SPACE = re.compile(r"\s+")
_CLOCK = re.compile(r"^(?:[01]\d|2[0-3]):(?:00|15|30|45)$")


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

    quote = {
        "type": "string",
        "minLength": 8,
        "description": (
            "An exact, contiguous quote from the active user's message that explicitly "
            "requests this state-changing action. Never quote schedule or app data."
        ),
    }
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
    candidate = _object_schema(
        {
            "event_id": {"type": "integer", "minimum": 1},
            "date": canonical_date,
            "start_time": clock,
            "end_time": clock,
            "room": {"type": "string", "minLength": 1, "maxLength": 120},
            "match_token": {"type": "string", "minLength": 20},
            "request_quote": quote,
        },
        required=(
            "event_id",
            "date",
            "start_time",
            "end_time",
            "room",
            "match_token",
            "request_quote",
        ),
    )
    run_booker_schema = _object_schema(
        {
            "request_quote": quote,
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
        required=("request_quote", "max_actions"),
    )
    run_booker_schema["allOf"] = [
        {
            "if": {"required": ["max_action_minutes"]},
            "then": {"required": ["only_date", "only_room"]},
        }
    ]
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
                "Resolve a cancellation or schedule reference against the validated agenda. "
                "Only exact reservation start times match; containing/overlapping events are "
                "returned separately for clarification. A stale snapshot requires refresh."
            ),
            "inputSchema": _object_schema(
                {
                    "date": canonical_date,
                    "start_time": clock,
                    "end_time": clock,
                    "room": {"type": "string", "minLength": 1, "maxLength": 120},
                },
                required=("date",),
            ),
        },
        {
            "type": "function",
            "name": "set_future_practice_plan",
            "description": (
                "Turn a high-level future practice intention into the complete dated targets "
                "that the autonomous Booker will pursue as those dates enter the live window. "
                "Supply every date in the range exactly once; 0 hours turns that date off. "
                "If words such as 'more' do not determine a numeric target, ask one concise "
                "clarifying question before calling. The saved intent remains explainable and revisable."
            ),
            "inputSchema": _object_schema(
                {
                    "request_quote": quote,
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
                    "request_quote",
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
                "strategy, room order/exclusions/requirements, and session shape."
            ),
            "inputSchema": _object_schema(
                {
                    "request_quote": quote,
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
                required=("request_quote",),
            ),
        },
        {
            "type": "function",
            "name": "run_booker",
            "description": (
                "Run the autonomous booker for one plan-selected action after an explicit user "
                "booking request. It cannot bind an exact start time by itself. The run shares "
                "the runtime lock, refreshes live policy/agenda, and keeps all existing exact-save "
                "safeguards. A duration cap requires both an exact date and exact room. Never use "
                "for a question or speculative action."
            ),
            "inputSchema": run_booker_schema,
        },
        {
            "type": "function",
            "name": "cancel_reservation",
            "description": (
                "Cancel one exact existing reservation after a prior find_reservations call. "
                "The backend re-authenticates, refreshes the complete agenda, rechecks the exact "
                "positive event ID and tuple, journals before confirmation, and proves absence "
                "afterward. Ambiguous, changed, or uncertain state fails closed."
            ),
            "inputSchema": candidate,
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


def _span_is_quoted(text: str, start: int, end: int) -> bool:
    """Return whether one normalized span is enclosed in paired quote marks."""

    for opening, closing in (("\"", "\""), ("'", "'"), ("“", "”"), ("‘", "’")):
        if opening == closing:
            if text[:start].count(opening) % 2 and text.find(closing, end) >= 0:
                return True
            continue
        if text.rfind(opening, 0, start) > text.rfind(closing, 0, start):
            if text.find(closing, end) >= 0:
                return True
    return False


def _authorize_mutation(
    arguments: Mapping[str, Any],
    user_request: str,
    *,
    action_pattern: str,
    imperative_pattern: str,
) -> str:
    quote = arguments.get("request_quote")
    if not isinstance(quote, str) or len(quote.strip()) < 8:
        raise AssistantToolError(
            "A state-changing tool requires an exact quote from the active user request."
        )
    normalized_quote = _normalize_quote(quote)
    normalized_request = _normalize_quote(user_request)
    if normalized_quote not in normalized_request:
        raise AssistantToolError(
            "The supplied authorization quote is not present in the active user message."
        )
    # A model can quote only the positive-looking words inside a negated or
    # informational sentence (for example, quote "cancel my booking" from
    # "do not cancel my booking").  Evaluate the complete active message before
    # accepting the narrower quote so untrusted context cannot turn a question
    # or prohibition into mutation authority.
    negation_lead = (
        r"\b(?:do not|don't|dont|cannot|can't|cant|couldn't|couldnt|"
        r"wouldn't|wouldnt|shouldn't|shouldnt|won't|wont|didn't|didnt|"
        r"never|not(?: to)?|without|unable to|avoid(?:ing)?|"
        r"refrain(?:ing)? from|no need to)\b"
    )
    if re.search(negation_lead + r".{0,120}?" + action_pattern, normalized_request):
        raise AssistantToolError(
            "The active user message negates this state change; it is not authorization."
        )
    # A user can withdraw an earlier imperative elliptically, without repeating
    # its action verb: "Cancel it. Actually, don't."  The leading-negation
    # check above cannot see that form because no later action word exists.
    # Treat only terminal withdrawal clauses as revocation so a separate
    # instruction such as "Cancel it; don't send a notification" remains valid.
    trailing_withdrawal = (
        r"(?:\bactually\b[\s,]*)?(?:\bno\b[\s,]*)?(?:please\s+)?"
        r"(?:don't|dont|do not)\b"
        r"(?:\s+(?:do\s+)?(?:it|that|this|so|proceed|go ahead))?"
        r"|(?:\bactually\b[\s,]*)?(?:no(?:\s+thanks)?|never\s+mind|"
        r"nevermind|forget\s+(?:it|that)|scratch\s+that|ignore\s+that|"
        r"withdraw\s+that(?:\s+request)?|cancel\s+that(?:\s+request)?|"
        r"i\s+changed\s+my\s+mind)"
    )
    if re.search(
        action_pattern + r".*(?:^|[\s,;:.!?\u2013\u2014-])(?:" + trailing_withdrawal + r")\s*[.!?]*$",
        normalized_request,
    ):
        raise AssistantToolError(
            "The active user message withdraws this state change; it is not authorization."
        )
    informational_prefix = re.match(
        r"^(?:(?:can|could|would) you (?:tell|show|explain|describe)\b|"
        r"(?:tell|show|explain|describe) me (?:how|what|why|whether)\b)",
        normalized_request,
    )
    informational_intent = re.search(
        r"\b(?:i want|i would like|i'd like) to "
        r"(?:know|understand|learn|ask)\b.{0,120}?" + action_pattern,
        normalized_request,
    )
    informational_question = re.search(
        r"\b(?:how (?:do|can|could|would|should)|whether (?:i|you)|"
        r"what (?:would )?happen(?:s)? if)\b.{0,120}?" + action_pattern,
        normalized_request,
    )
    if informational_prefix or informational_intent or informational_question:
        raise AssistantToolError(
            "The active user message asks about this state change; it does not authorize it."
        )
    illustrative_lead = re.search(
        r"\b(?:for example|for instance|such as|as an example|e\.g\.|"
        r"if i (?:say|ask)|when i (?:say|ask))\b.{0,120}?" + action_pattern,
        normalized_request,
    )
    quote_probe = normalized_quote.strip("\"'“”‘’ ")
    probe_positions = [
        match.span()
        for match in re.finditer(re.escape(quote_probe), normalized_request)
    ] if quote_probe else []
    quote_only = bool(probe_positions) and all(
        _span_is_quoted(normalized_request, start, end)
        for start, end in probe_positions
    )
    if illustrative_lead or quote_only:
        raise AssistantToolError(
            "The action appears only as quoted or example text; it is not authorization."
        )
    question_opener = re.match(
        r"^(?:what|why|when|where|how|which|is|are|does|did|should|can i|could i)\b",
        normalized_request,
    )
    if question_opener:
        raise AssistantToolError(
            "The active message reads as a question, not authorization for a state change."
        )
    if re.search(action_pattern, normalized_quote) is None:
        raise AssistantToolError(
            "The quoted user text does not explicitly request this kind of state change."
        )

    # Require the quoted action itself to occupy an instruction-bearing position
    # in the complete active message.  Merely extracting an imperative-looking
    # substring from prose such as "the note says remove my booking" is not
    # authority.  A clause after punctuation may carry an imperative so natural
    # context such as "This week is busy: do two hours" remains usable.
    quote_positions = [
        match.span()
        for match in re.finditer(re.escape(normalized_quote), normalized_request)
    ]
    clause_start = r"(?:^|(?<=[.!?:;])\s+)"
    direct_request_lead = (
        r"(?:please\s+)?(?:can|could|would|will) you(?:\s+please)?\s+"
    )
    direct_desire_lead = (
        r"(?:i\s+(?:really\s+)?(?:want|need)(?:\s+you)?(?:\s+to)?|"
        r"i\s+would\s+like(?:\s+you)?(?:\s+to)?|"
        r"i(?:'|’)d\s+like(?:\s+you)?(?:\s+to)?)\s+"
    )
    positive_patterns = (
        clause_start
        + r"(?:please\s+)?(?:do\s+)?(?P<action>"
        + imperative_pattern
        + r")",
        clause_start
        + direct_request_lead
        + r"(?:(?![.!?]).){0,120}?(?P<action>"
        + action_pattern
        + r")",
        clause_start
        + direct_desire_lead
        + r"(?:(?![.!?]).){0,160}?(?P<action>"
        + action_pattern
        + r")",
    )
    authorized_action_spans = [
        match.span("action")
        for pattern in positive_patterns
        for match in re.finditer(pattern, normalized_request)
    ]
    quote_carries_authorized_action = any(
        quote_start <= action_start and action_end <= quote_end
        for quote_start, quote_end in quote_positions
        for action_start, action_end in authorized_action_spans
    )
    if not quote_carries_authorized_action:
        raise AssistantToolError(
            "The quoted action is not an imperative or direct request/desire in "
            "the active user message."
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

    @property
    def tool_specs(self) -> list[dict[str, Any]]:
        return dynamic_tool_specs()

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
            "cancel_reservation": self._cancel_reservation,
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
            return handler(dict(arguments), user_request=user_request, progress=callback)
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
        allowed = {"date", "start_time", "end_time", "room"}
        unknown = set(arguments) - allowed
        if unknown or "date" not in arguments:
            raise AssistantToolError("find_reservations requires date and optional exact filters")
        target_date = _canonical_date(arguments["date"], "date")
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

        progress("Matching reservations", "Checking exact event IDs in the validated agenda")
        result = read_agenda_snapshot(self.paths.agenda)
        if result.snapshot is None or result.stale:
            return {
                "fresh": False,
                "refresh_required": True,
                "reason": result.reason or "No current agenda snapshot is available.",
                "matches": [],
                "overlaps": [],
            }

        reservations = [
            event
            for event in result.snapshot.events
            if event.is_reservation
            and event.event_id is not None
            and event.date.isoformat() == target_date
            and (room is None or event.room == room)
            and (end is None or event.end_time == end)
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
        return {
            "fresh": True,
            "refresh_required": False,
            "observed_at": result.snapshot.observed_at.isoformat(),
            "matches": [_event_payload(result.snapshot.observed_at, item) for item in exact],
            "overlaps": [_event_payload(result.snapshot.observed_at, item) for item in overlaps],
            "match_rule": "exact start time; overlaps are never selected automatically",
        }

    def _set_future_practice_plan(
        self,
        arguments: dict[str, Any],
        *,
        user_request: str,
        progress: ProgressCallback,
    ) -> dict[str, Any]:
        _authorize_mutation(
            arguments,
            user_request,
            action_pattern=(
                r"\b(?:plan(?:s|ned|ning)?|schedul(?:e|es|ed|ing)|"
                r"set(?:s|ting)?|chang(?:e|es|ed|ing)|updat(?:e|es|ed|ing)|"
                r"practi[cs](?:e|es|ed|ing)|cap(?:s|ped|ping)?|"
                r"limit(?:s|ed|ing)?|skip(?:s|ped|ping)?|"
                r"do|does|did|done|doing|only|off|weekdays?|weekends?)\b"
            ),
            imperative_pattern=(
                r"\b(?:plan|schedule|set|change|update|practice|practise|"
                r"cap|limit|skip|do)\b"
            ),
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
        }

    def _update_preferences(
        self,
        arguments: dict[str, Any],
        *,
        user_request: str,
        progress: ProgressCallback,
    ) -> dict[str, Any]:
        _authorize_mutation(
            arguments,
            user_request,
            action_pattern=(
                r"\b(?:set(?:s|ting)?|chang(?:e|es|ed|ing)|"
                r"updat(?:e|es|ed|ing)|turn(?:s|ed|ing)?|"
                r"enabl(?:e|es|ed|ing)|disabl(?:e|es|ed|ing)|"
                r"exclud(?:e|es|ed|ing)|includ(?:e|es|ed|ing)|"
                r"prefer(?:s|red|ring)?|only|us(?:e|es|ed|ing)|"
                r"mak(?:e|es|ing)|made)\b"
            ),
            imperative_pattern=(
                r"\b(?:set|change|update|turn|enable|disable|exclude|"
                r"include|prefer|only|use|make)\b"
            ),
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
        return {
            "changed": changed,
            "note": "The existing display plan is now marked stale until it is refreshed.",
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
        unknown = set(raw_patch) - {"enabled", "default_hours", "date_overrides"}
        if unknown:
            raise AssistantToolError(f"Unknown practice-plan fields: {sorted(unknown)}")
        current = load_practice_plan(settings)
        overrides = dict(current.date_overrides or {})
        raw_overrides = raw_patch.get("date_overrides", [])
        if not isinstance(raw_overrides, list):
            raise AssistantToolError("practice_plan.date_overrides must be a list")
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
        _authorize_mutation(
            arguments,
            user_request,
            action_pattern=(
                r"\b(?:book(?:s|ed|ing)?|reserv(?:e|es|ed|ing)|"
                r"run(?:s|ning)?|start(?:s|ed|ing)?|"
                r"creat(?:e|es|ed|ing)|mak(?:e|es|ing)|made)\b"
            ),
            imperative_pattern=r"\b(?:book|reserve|run|start|create|make)\b",
        )
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

    def _cancel_reservation(
        self,
        arguments: dict[str, Any],
        *,
        user_request: str,
        progress: ProgressCallback,
    ) -> dict[str, Any]:
        _authorize_mutation(
            arguments,
            user_request,
            action_pattern=(
                r"\b(?:cancel(?:s|l?ed|l?ing)?|"
                r"remov(?:e|es|ed|ing)|delet(?:e|es|ed|ing))\b"
            ),
            imperative_pattern=r"\b(?:cancel|remove|delete)\b",
        )
        required = {
            "event_id",
            "date",
            "start_time",
            "end_time",
            "room",
            "match_token",
            "request_quote",
        }
        if set(arguments) != required:
            raise AssistantToolError(
                "cancel_reservation requires the exact event ID, tuple, match token, and request quote"
            )
        event_id = arguments["event_id"]
        if type(event_id) is not int or event_id <= 0:
            raise AssistantToolError("event_id must be a positive integer")
        expected = {
            "date": _canonical_date(arguments["date"], "date"),
            "start_time": _canonical_clock(arguments["start_time"], "start_time"),
            "end_time": _canonical_clock(arguments["end_time"], "end_time"),
            "room": arguments["room"],
        }
        if _clock_minutes(expected["end_time"]) <= _clock_minutes(expected["start_time"]):
            raise AssistantToolError("Cancellation end_time must be after start_time")
        if not isinstance(expected["room"], str) or not expected["room"].strip():
            raise AssistantToolError("room must be a non-empty exact room name")

        snapshot_result = read_agenda_snapshot(self.paths.agenda)
        if snapshot_result.snapshot is None or snapshot_result.stale:
            raise AssistantToolError(
                "The cancellation match is stale; refresh the agenda and resolve it again."
            )
        matching = [
            event
            for event in snapshot_result.snapshot.events
            if event.is_reservation
            and event.event_id == event_id
            and event.date.isoformat() == expected["date"]
            and event.start_time == expected["start_time"]
            and event.end_time == expected["end_time"]
            and event.room == expected["room"]
        ]
        if len(matching) != 1:
            raise AssistantToolError(
                "The selected reservation no longer has one exact match; resolve it again."
            )
        expected_token = _event_token(snapshot_result.snapshot.observed_at, matching[0])
        if arguments["match_token"] != expected_token:
            raise AssistantToolError(
                "The reservation match token changed; refresh and resolve before cancelling."
            )

        flags = [
            "--headless",
            "--cancel-event-id",
            str(event_id),
            "--cancel-date",
            expected["date"],
            "--cancel-start",
            expected["start_time"],
            "--cancel-end",
            expected["end_time"],
            "--cancel-room",
            expected["room"],
        ]
        progress(
            "Cancelling exact reservation",
            f"Revalidating event {event_id} in {expected['room']} before confirmation",
        )
        command = self._run_booker_command(flags, progress=progress, timeout=15 * 60)
        return {
            "cancelled": True,
            "verified_absent": True,
            "reservation": {"event_id": event_id, **expected},
            "command": command,
        }

    def _run_booker_command(
        self,
        flags: Sequence[str],
        *,
        progress: ProgressCallback,
        timeout: int,
    ) -> dict[str, Any]:
        generation, cancel_event = self._command_cancel_event()
        try:
            with self._action_lock:
                if cancel_event.is_set():
                    raise AssistantToolCancelled("The Booker action was stopped.")
                if self._command_runner is not None:
                    return self._command_runner(
                        list(flags),
                        progress=progress,
                        cancel_event=cancel_event,
                        timeout=timeout,
                    )
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
                if return_code != 0:
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
    "ToolResult",
    "dynamic_tool_specs",
]
