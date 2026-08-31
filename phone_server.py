"""Private loopback HTTP/SSE host for the Asimut phone companion.

The server is intentionally narrow:

* it binds only to ``127.0.0.1`` and is published through tailnet-only HTTPS;
* Tailscale identity, exact Host, exact Origin, a server-side session cookie,
  and a synchronizer CSRF token protect the assistant entry points;
* the browser receives sanitized display state and filtered progress events;
* natural-language changes still pass through the existing AssistantRuntime and
  its exact Terra-medium typed Booker tools.

There is no shell, generic file API, direct booking/cancellation endpoint, or
credential/authentication form on this surface.
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
import mimetypes
import os
import re
import secrets
import signal
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import unquote, urlsplit
from uuid import UUID

from app_settings import InterProcessFileLock, SettingsError, atomic_write_json
from assistant_runtime import AssistantRuntime, AssistantRuntimeError, load_assistant_state
from phone_api import build_phone_snapshot
from runtime_guard import SingleInstanceLock


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "phone" / "dist-phone"
CONFIG_FILE = APP_DIR / "data" / "phone_server_config.json"
REQUEST_FILE = APP_DIR / "data" / "phone_request_ids.json"
ASSISTANT_STATE_FILE = APP_DIR / "data" / "phone_assistant_state.json"
SERVER_LOCK_FILE = APP_DIR / "data" / "phone-server.lock"
SERVER_LOG_FILE = APP_DIR / "logs" / "phone_server.log"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8794
SESSION_COOKIE = "asimut_phone_session"
MAX_BODY_BYTES = 24_000
MAX_PROMPT_CHARS = 10_000
MAX_PUBLIC_TEXT = 4_000
MAX_EVENT_HISTORY = 512
MAX_REQUEST_IDS = 256
SESSION_IDLE_SECONDS = 30 * 60
SESSION_ABSOLUTE_SECONDS = 8 * 60 * 60
MESSAGE_RATE_WINDOW_SECONDS = 60
MESSAGE_RATE_LIMIT = 12
SSE_CONNECTION_SECONDS = 20 * 60

LOGGER = logging.getLogger("asimut.phone")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SENSITIVE_TEXT = re.compile(
    r"(?ix)(?:"
    r"\b(?:password|passcode|credential)\b\s*(?:[:=]|\b(?:is|was|equals?)\b)\s*\S+"
    r"|\b(?:otp|one[- ]?time(?:\s+(?:password|passcode|code))?|sms\s+code|"
    r"verification\s+code)\b\s*(?:(?:[:=]|\b(?:is|was|equals?)\b)\s*)?\d{4,10}\b"
    r"|\b(?:authorization|access\s+token|refresh\s+token|bridge\s+token|cookie)\b"
    r"\s*(?:[:=]|\b(?:is|was|equals?)\b)\s*\S+"
    r")"
)


class PhoneServerError(RuntimeError):
    """Raised when the phone service cannot start safely."""


class PhoneRateLimitError(PhoneServerError):
    """Raised only when the bounded phone message rate is exceeded."""


class PhoneActiveTurnError(PhoneServerError):
    """Raised when uncertain outcomes cannot be reviewed during an active turn."""


@dataclass(frozen=True)
class PhoneServerConfig:
    allowed_login: str
    public_origin: str
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    codex_executable: str | None = None

    @property
    def public_host(self) -> str:
        return urlsplit(self.public_origin).netloc


def load_phone_server_config(path: Path = CONFIG_FILE) -> PhoneServerConfig:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PhoneServerError(
            "Phone server configuration is missing or unreadable. Run the phone setup first."
        ) from exc
    if not isinstance(raw, Mapping) or set(raw) != {
        "version",
        "allowed_login",
        "public_origin",
        "host",
        "port",
        "codex_executable",
    }:
        raise PhoneServerError("Phone server configuration has unexpected fields")
    if raw.get("version") != 2:
        raise PhoneServerError("Phone server configuration version is unsupported")
    login = raw.get("allowed_login")
    origin = raw.get("public_origin")
    host = raw.get("host")
    port = raw.get("port")
    codex_executable = raw.get("codex_executable")
    if not isinstance(login, str) or not login.strip() or len(login) > 320:
        raise PhoneServerError("Phone server identity allow-list is invalid")
    parsed = urlsplit(origin) if isinstance(origin, str) else None
    if (
        parsed is None
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise PhoneServerError("Phone server public origin must be one exact HTTPS origin")
    if host != DEFAULT_HOST:
        raise PhoneServerError("Phone server must bind to exact loopback")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise PhoneServerError("Phone server port is invalid")
    if not isinstance(codex_executable, str) or not codex_executable.strip():
        raise PhoneServerError("Phone server Codex executable is invalid")
    codex_path = Path(codex_executable)
    if (
        not codex_path.is_absolute()
        or codex_path.name.casefold() != "codex.exe"
        or not codex_path.is_file()
    ):
        raise PhoneServerError("Phone server Codex executable is unavailable")
    return PhoneServerConfig(
        allowed_login=login.strip().casefold(),
        public_origin=origin.rstrip("/"),
        host=host,
        port=port,
        codex_executable=str(codex_path.resolve()),
    )


def _utc_now() -> float:
    return time.time()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _configure_logging(path: Path = SERVER_LOG_FILE) -> None:
    if LOGGER.handlers:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=512_000,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False


def _safe_public_text(value: Any, *, maximum: int = MAX_PUBLIC_TEXT) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = _CONTROL_CHARACTERS.sub("", value).strip()
    if _SENSITIVE_TEXT.search(cleaned):
        return "Sensitive information was withheld from the phone display."
    return cleaned[:maximum]


def _safe_public_delta(value: Any, *, maximum: int = MAX_PUBLIC_TEXT) -> str:
    """Sanitize one streamed fragment without destroying token boundaries."""

    if not isinstance(value, str):
        return ""
    cleaned = _CONTROL_CHARACTERS.sub("", value)
    if _SENSITIVE_TEXT.search(cleaned):
        return "Sensitive information was withheld from the phone display."
    return cleaned[:maximum]


def public_assistant_event(event: Mapping[str, Any]) -> dict[str, Any] | None:
    """Map one internal controller event onto the phone's strict public schema."""

    kind = event.get("kind")
    if kind == "assistant_delta":
        text = _safe_public_delta(event.get("text"))
        return {"kind": "assistant.delta", "text": text} if text else None
    if kind == "commentary_delta":
        text = _safe_public_delta(event.get("text"))
        if not text:
            return None
        return {
            "kind": "progress.delta",
            "text": text,
            "replace": bool(event.get("first_delta")),
        }
    if kind == "reasoning_summary_delta":
        text = _safe_public_delta(event.get("text"))
        if not text:
            return None
        summary_index = event.get("summary_index")
        part = summary_index if type(summary_index) is int and 0 <= summary_index <= 64 else 0
        return {"kind": "reasoning.delta", "text": text, "part": part}
    if kind in {"reasoning_start", "reasoning_summary"}:
        return {
            "kind": "reasoning.status",
            "status": _safe_public_text(event.get("status"), maximum=40) or "in_progress",
            "title": _safe_public_text(event.get("title"), maximum=120)
            or "Working through the request",
            "text": _safe_public_text(event.get("text"), maximum=800),
        }
    if kind in {"tool_call", "tool_start", "tool_update", "tool_end", "tool"}:
        return {
            "kind": "tool.status",
            "status": _safe_public_text(event.get("status"), maximum=40) or "in_progress",
            "title": _safe_public_text(event.get("title"), maximum=140)
            or "Checking Booker data",
            "text": _safe_public_text(event.get("text"), maximum=800),
        }
    if kind in {"activity", "status"}:
        title = _safe_public_text(event.get("title"), maximum=140)
        if title == "Chat status changed":
            return None
        if title == "Thinking" and type(event.get("summary_index")) is int:
            return {
                "kind": "reasoning.status",
                "status": _safe_public_text(event.get("status"), maximum=40)
                or "in_progress",
                "part": max(0, min(64, event["summary_index"])),
            }
        return {
            "kind": "activity",
            "status": _safe_public_text(event.get("status"), maximum=40) or "in_progress",
            "title": title or "Asimut Assistant",
            "text": _safe_public_text(event.get("text"), maximum=800),
        }
    if kind == "turn_started":
        return {"kind": "turn.started", "status": "in_progress"}
    if kind == "turn_completed":
        status = _safe_public_text(event.get("status"), maximum=40) or "completed"
        return {"kind": "turn.completed", "status": status, "terminal": True}
    if kind == "connection":
        status = _safe_public_text(event.get("status"), maximum=40) or "unknown"
        return {
            "kind": "session.status",
            "status": status,
            "text": _safe_public_text(event.get("text"), maximum=160),
            "terminal": status in {"failed", "offline", "disconnected"},
        }
    if kind == "busy":
        return {
            "kind": "session.busy",
            "status": _safe_public_text(event.get("status"), maximum=40) or "unknown",
            "text": _safe_public_text(event.get("text"), maximum=160),
        }
    if kind == "error":
        title = _safe_public_text(event.get("title"), maximum=140)
        if title == "Sensitive information was not sent":
            detail = (
                "Passwords, passcodes, credentials, and verification codes cannot be sent in chat."
            )
        elif title == "Previous chat could not be restored":
            detail = "The previous phone conversation could not be restored."
        else:
            detail = "The assistant could not continue. Try again or check the desktop control panel."
        return {
            "kind": "error",
            "title": title or "Asimut Assistant could not continue",
            "text": detail,
            "terminal": event.get("status") in {"send", "stop", "new_chat"},
        }
    # History, raw item lifecycles, approval events, request IDs, and any future
    # provider event are deliberately absent until explicitly reviewed.
    return None


class EventHub:
    def __init__(self, capacity: int = MAX_EVENT_HISTORY) -> None:
        if capacity < 16:
            raise ValueError("Event history capacity must be at least 16")
        self._events: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._sequence = 0
        self._condition = threading.Condition()

    @property
    def cursor(self) -> int:
        with self._condition:
            return self._sequence

    def publish(self, event: Mapping[str, Any]) -> dict[str, Any]:
        with self._condition:
            self._sequence += 1
            item = {"seq": self._sequence, "at": _timestamp(), **dict(event)}
            self._events.append(item)
            self._condition.notify_all()
            return dict(item)

    def wait_after(
        self, after: int, *, timeout: float = 20.0
    ) -> tuple[list[dict[str, Any]], bool]:
        if type(after) is not int or after < 0:
            raise ValueError("Event cursor must be a non-negative integer")
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while self._sequence <= after and time.monotonic() < deadline:
                self._condition.wait(timeout=max(0.0, deadline - time.monotonic()))
            oldest = self._events[0]["seq"] if self._events else self._sequence + 1
            overflow = after < oldest - 1
            return (
                [dict(item) for item in self._events if item["seq"] > after],
                overflow,
            )


class RequestLedger:
    """Durable request lifecycle deduplication without storing prompt text."""

    def __init__(self, path: Path = REQUEST_FILE) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PhoneServerError("Phone request ledger is unreadable") from exc
        if not isinstance(raw, Mapping) or set(raw) != {"version", "requests"}:
            raise PhoneServerError("Phone request ledger has unexpected fields")
        version = raw.get("version")
        if version not in {1, 2, 3} or not isinstance(raw.get("requests"), list):
            raise PhoneServerError("Phone request ledger version is unsupported")
        entries: list[dict[str, Any]] = []
        for item in raw["requests"][-MAX_REQUEST_IDS:]:
            if version == 1 and (
                isinstance(item, Mapping)
                and set(item) == {"id", "accepted_at"}
                and isinstance(item.get("id"), str)
                and isinstance(item.get("accepted_at"), str)
            ):
                entries.append(
                    {
                        "id": item["id"],
                        "state": "accepted",
                        "recorded_at": item["accepted_at"],
                    }
                )
            elif version in {2, 3} and (
                isinstance(item, Mapping)
                and set(item) == {"id", "state", "recorded_at"}
                and isinstance(item.get("id"), str)
                and item.get("state")
                in {"reserved", "accepted", "rejected", "acknowledged"}
                and isinstance(item.get("recorded_at"), str)
            ):
                if version == 2 and item.get("state") == "acknowledged":
                    raise PhoneServerError("Phone request ledger contains a malformed entry")
                entries.append(dict(item))
            else:
                raise PhoneServerError("Phone request ledger contains a malformed entry")
        return entries

    def _write_unlocked(self, entries: list[dict[str, Any]]) -> None:
        try:
            atomic_write_json(
                self.path,
                {"version": 3, "requests": entries[-MAX_REQUEST_IDS:]},
                backup=True,
            )
        except SettingsError as exc:
            raise PhoneServerError("Phone request ID could not be persisted") from exc

    def lookup(self, request_id: str) -> str | None:
        with self._lock:
            try:
                with InterProcessFileLock(self.path.with_suffix(self.path.suffix + ".lock")):
                    for item in self._load_unlocked():
                        if item["id"] == request_id:
                            return str(item["state"])
            except SettingsError as exc:
                raise PhoneServerError("Phone request ledger lock is unavailable") from exc
        return None

    def reserve(self, request_id: str) -> str:
        with self._lock:
            try:
                with InterProcessFileLock(self.path.with_suffix(self.path.suffix + ".lock")):
                    entries = self._load_unlocked()
                    for item in entries:
                        if item["id"] == request_id:
                            return str(item["state"])
                    entries.append(
                        {"id": request_id, "state": "reserved", "recorded_at": _timestamp()}
                    )
                    self._write_unlocked(entries)
            except SettingsError as exc:
                raise PhoneServerError("Phone request ledger lock is unavailable") from exc
        return "reserved"

    def mark(self, request_id: str, state: str) -> None:
        if state not in {"accepted", "rejected"}:
            raise ValueError("Phone request state is invalid")
        with self._lock:
            try:
                with InterProcessFileLock(self.path.with_suffix(self.path.suffix + ".lock")):
                    entries = self._load_unlocked()
                    matches = [item for item in entries if item["id"] == request_id]
                    if len(matches) != 1:
                        raise PhoneServerError("Phone request reservation is missing")
                    matches[0]["state"] = state
                    matches[0]["recorded_at"] = _timestamp()
                    self._write_unlocked(entries)
            except SettingsError as exc:
                raise PhoneServerError("Phone request ledger lock is unavailable") from exc

    def unresolved_reserved_count(self) -> int:
        with self._lock:
            try:
                with InterProcessFileLock(self.path.with_suffix(self.path.suffix + ".lock")):
                    return sum(
                        1 for item in self._load_unlocked() if item["state"] == "reserved"
                    )
            except SettingsError as exc:
                raise PhoneServerError("Phone request ledger lock is unavailable") from exc

    def acknowledge_reserved(self) -> int:
        """Persist explicit review of every unresolved crash-window request."""

        with self._lock:
            try:
                with InterProcessFileLock(self.path.with_suffix(self.path.suffix + ".lock")):
                    entries = self._load_unlocked()
                    acknowledged = 0
                    for item in entries:
                        if item["state"] == "reserved":
                            item["state"] = "acknowledged"
                            item["recorded_at"] = _timestamp()
                            acknowledged += 1
                    if acknowledged:
                        self._write_unlocked(entries)
                    return acknowledged
            except SettingsError as exc:
                raise PhoneServerError("Phone request ledger lock is unavailable") from exc


class PhoneAssistantService:
    def __init__(
        self,
        *,
        runtime_factory: Callable[..., Any] = AssistantRuntime,
        ledger: RequestLedger | None = None,
        state_path: Path = ASSISTANT_STATE_FILE,
        workspace: Path | None = None,
        stream_generation: str | None = None,
        codex_executable: str | None = None,
    ) -> None:
        self.events = EventHub()
        self.ledger = ledger or RequestLedger()
        self.stream_generation = stream_generation or secrets.token_urlsafe(18)
        # Runtime fakes and future controller implementations may emit the
        # first lifecycle event synchronously from send(). Production emits on
        # its loop thread; an RLock makes both orderings safe.
        self._request_lock = threading.RLock()
        self._runtime_lock = threading.Lock()
        self._active_lock = threading.Lock()
        self._active_client_message_id: str | None = None
        self._active_turn_started = False
        self._message_times: deque[float] = deque()
        if workspace is None:
            local_app_data = os.environ.get("LOCALAPPDATA")
            base = Path(local_app_data) if local_app_data else APP_DIR / "data"
            workspace = base / "AsimutBooker" / "PhoneAssistant"
        self._runtime_factory = runtime_factory
        self._state_path = Path(state_path)
        self._workspace = Path(workspace)
        self._codex_executable = codex_executable
        self._runtime: Any | None = None

    @property
    def runtime(self) -> Any:
        with self._runtime_lock:
            if self._runtime is None:
                runtime_options: dict[str, Any] = {}
                if self._codex_executable:
                    runtime_options["controller_kwargs"] = {
                        "codex_executable": self._codex_executable
                    }
                self._runtime = self._runtime_factory(
                    self._receive_runtime_event,
                    state_path=self._state_path,
                    assistant_workspace=self._workspace,
                    **runtime_options,
                )
                self._runtime.start()
            return self._runtime

    @property
    def is_busy(self) -> bool:
        with self._runtime_lock:
            return False if self._runtime is None else bool(self._runtime.is_busy)

    def _receive_runtime_event(self, event: Mapping[str, Any]) -> None:
        public = public_assistant_event(event)
        if public is None:
            return
        # Keep the durable request unresolved for the entire real controller
        # turn. A crash before its terminal event can leave a mutation partly
        # applied, so only a correlated terminal transition may settle it.
        with self._request_lock:
            with self._active_lock:
                active_id = self._active_client_message_id
                if event.get("kind") == "turn_started" and active_id is not None:
                    self._active_turn_started = True
                active_started = self._active_turn_started
            if active_id is not None:
                public = {**public, "client_message_id": active_id}
            if public.get("terminal") and active_id is not None and active_started:
                try:
                    self.ledger.mark(active_id, "accepted")
                except PhoneServerError:
                    # Leave the reservation unresolved and replace the result
                    # with a safe review requirement when settlement fails.
                    public = {
                        "kind": "error",
                        "title": "Assistant outcome needs review",
                        "text": (
                            "The assistant finished, but its outcome could not be "
                            "recorded safely. Review Booker status before continuing."
                        ),
                        "terminal": True,
                        "client_message_id": active_id,
                    }
            # Settlement and terminal publication are serialized against
            # snapshots, so either the state or a replayable event is visible.
            self.events.publish(public)
            if public.get("terminal") and active_id is not None:
                with self._active_lock:
                    if self._active_client_message_id == active_id:
                        self._active_client_message_id = None
                        self._active_turn_started = False

    def snapshot(self) -> dict[str, Any]:
        # Capture the replay boundary first. Runtime state is updated before
        # its event is emitted, so any later transition is either reflected in
        # the snapshot or remains replayable strictly after this cursor.
        event_cursor = self.events.cursor
        with self._request_lock:
            unresolved_reserved_count = self.ledger.unresolved_reserved_count()
            with self._runtime_lock:
                runtime = self._runtime
            if runtime is None:
                try:
                    messages = load_assistant_state(self._state_path)["messages"]
                except AssistantRuntimeError:
                    messages = []
                model = "GPT-5.6 Terra · medium"
                busy = False
            else:
                messages = runtime.restored_messages()
                model = runtime.model_label
                busy = bool(runtime.is_busy)
            with self._active_lock:
                active_client_message_id = self._active_client_message_id
                if active_client_message_id is not None and not busy:
                    # Some controller failure paths finish the runtime without
                    # emitting a mapped terminal event. Drop only the stale
                    # in-memory correlation; the durable reservation remains
                    # unresolved until the user explicitly reviews it.
                    self._active_client_message_id = None
                    self._active_turn_started = False
                    active_client_message_id = None
            if (
                active_client_message_id is not None
                and busy
                and self.ledger.lookup(active_client_message_id) == "reserved"
            ):
                # The one in-process request is already represented by the
                # busy state. It becomes unresolved and visible after restart.
                unresolved_reserved_count = max(0, unresolved_reserved_count - 1)
        return {
            "model": model,
            "busy": busy,
            "messages": messages,
            "event_cursor": event_cursor,
            "stream_generation": self.stream_generation,
            "active_client_message_id": active_client_message_id,
            "unresolved_reserved_count": unresolved_reserved_count,
            "booker": build_phone_snapshot(),
        }

    def submit(self, client_message_id: str, text: str) -> tuple[bool, str]:
        try:
            canonical_id = str(UUID(client_message_id))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("client_message_id must be a UUID") from exc
        if not isinstance(text, str) or not text.strip():
            raise ValueError("message must be non-empty")
        prompt = text.strip()
        if len(prompt) > MAX_PROMPT_CHARS:
            raise ValueError(f"message must be at most {MAX_PROMPT_CHARS} characters")

        with self._request_lock:
            existing = self.ledger.lookup(canonical_id)
            if existing == "accepted":
                return True, "duplicate"
            if existing == "rejected":
                return False, "not_accepted"
            if existing == "reserved":
                with self._active_lock:
                    same_active_turn = self._active_client_message_id == canonical_id
                if same_active_turn and self.is_busy:
                    return True, "duplicate"
                # Never replay a crash-window reservation. Return a handled,
                # explicit uncertain outcome so the UI can require deliberate
                # review without trapping the user in an endless retry loop.
                return True, "outcome_uncertain"
            if existing == "acknowledged":
                # Explicit review permits future commands, never replay of the
                # exact request whose crash-window outcome remains unknowable.
                return False, "uncertain_acknowledged"
            if self.ledger.unresolved_reserved_count() > 0:
                # Enforce the gate server-side as well as in the PWA. A stale
                # or alternate client cannot bypass review with a new UUID.
                return False, "uncertain_review_required"
            now = time.monotonic()
            while self._message_times and self._message_times[0] < (
                now - MESSAGE_RATE_WINDOW_SECONDS
            ):
                self._message_times.popleft()
            if len(self._message_times) >= MESSAGE_RATE_LIMIT:
                raise PhoneRateLimitError(
                    "Too many assistant messages; wait a moment and try again"
                )
            if self.is_busy:
                return False, "turn_in_progress"
            # Reserve before the runtime can invoke any mutation, then record
            # whether it really started. A crash between those states remains
            # uncertain and can never be replayed as a second turn.
            self.ledger.reserve(canonical_id)
            with self._active_lock:
                self._active_client_message_id = canonical_id
                self._active_turn_started = False
            try:
                accepted = bool(self.runtime.send(prompt))
            except Exception:
                if not self.is_busy:
                    with self._active_lock:
                        if self._active_client_message_id == canonical_id:
                            self._active_client_message_id = None
                            self._active_turn_started = False
                raise
            if not accepted:
                self.ledger.mark(canonical_id, "rejected")
                with self._active_lock:
                    if self._active_client_message_id == canonical_id:
                        self._active_client_message_id = None
                        self._active_turn_started = False
                return False, "not_accepted"
            self._message_times.append(now)
            with self._active_lock:
                still_active = self._active_client_message_id == canonical_id
                turn_started = self._active_turn_started
            if still_active and self.is_busy:
                return True, "started" if turn_started else "queued"
            state = self.ledger.lookup(canonical_id)
            if state == "accepted":
                return True, "terminal"
            # A synchronous failure before the first real controller event is
            # an unresolved outcome, even though send() queued successfully.
            return True, "outcome_uncertain"

    def acknowledge_uncertain(self) -> int:
        with self._request_lock:
            busy = self.is_busy
            with self._active_lock:
                if self._active_client_message_id is not None and not busy:
                    self._active_client_message_id = None
                    self._active_turn_started = False
                active = self._active_client_message_id is not None
            if active or busy:
                raise PhoneActiveTurnError(
                    "An active assistant turn must finish before review"
                )
            return self.ledger.acknowledge_reserved()

    def stop(self) -> bool:
        with self._runtime_lock:
            runtime = self._runtime
        return False if runtime is None else bool(runtime.stop())

    def new_chat(self) -> bool:
        accepted = bool(self.runtime.new_chat())
        if accepted:
            self.events.publish({"kind": "chat.reset", "status": "in_progress"})
        return accepted

    def close(self) -> None:
        with self._runtime_lock:
            runtime = self._runtime
            self._runtime = None
        if runtime is not None:
            runtime.close(timeout=8.0)


@dataclass
class _Session:
    token: str
    csrf: str
    login: str
    created_at: float
    last_seen: float


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        expired = [
            token
            for token, session in self._sessions.items()
            if now - session.last_seen > SESSION_IDLE_SECONDS
            or now - session.created_at > SESSION_ABSOLUTE_SECONDS
        ]
        for token in expired:
            self._sessions.pop(token, None)

    def issue(self, login: str) -> _Session:
        now = _utc_now()
        with self._lock:
            self._prune(now)
            token = secrets.token_urlsafe(32)
            session = _Session(
                token=token,
                csrf=secrets.token_urlsafe(32),
                login=login.casefold(),
                created_at=now,
                last_seen=now,
            )
            self._sessions[token] = session
            return session

    def get(self, token: str, login: str) -> _Session | None:
        now = _utc_now()
        with self._lock:
            self._prune(now)
            session = self._sessions.get(token)
            if session is None or not hmac.compare_digest(session.login, login.casefold()):
                return None
            session.last_seen = now
            return session


class PhoneApplication:
    def __init__(
        self,
        config: PhoneServerConfig,
        *,
        assistant: PhoneAssistantService | Any | None = None,
        static_dir: Path = STATIC_DIR,
        secure_cookie: bool = True,
    ) -> None:
        self.config = config
        self.assistant = assistant or PhoneAssistantService(
            codex_executable=config.codex_executable
        )
        self.static_dir = Path(static_dir).resolve()
        self.secure_cookie = secure_cookie
        self.sessions = SessionStore()

    def close(self) -> None:
        self.assistant.close()


class PhoneRequestHandler(BaseHTTPRequestHandler):
    server_version = "AsimutPhone/1"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> PhoneApplication:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Never log URLs, query strings, cookies, request bodies, or prompts.
        LOGGER.info("phone request %s", self.command)

    def _header_values(self, name: str) -> list[str]:
        return [value.strip() for value in self.headers.get_all(name, []) if value.strip()]

    def _one_header(self, name: str) -> str | None:
        values = self._header_values(name)
        return values[0] if len(values) == 1 else None

    def _identity(self) -> str | None:
        login = self._one_header("Tailscale-User-Login")
        if login is None:
            return None
        if not hmac.compare_digest(login.casefold(), self.app.config.allowed_login):
            return None
        return login.casefold()

    def _host_ok(self) -> bool:
        host = self._one_header("Host")
        return host is not None and hmac.compare_digest(
            host.casefold(), self.app.config.public_host.casefold()
        )

    def _origin_ok(self) -> bool:
        origin = self._one_header("Origin")
        return origin is not None and hmac.compare_digest(
            origin.rstrip("/").casefold(), self.app.config.public_origin.casefold()
        )

    def _fetch_metadata_ok(self) -> bool:
        site = self._one_header("Sec-Fetch-Site")
        return site is None or site in {"same-origin", "none"}

    def _session(self, login: str) -> _Session | None:
        raw_cookie = self._one_header("Cookie")
        if raw_cookie is None or len(raw_cookie) > 4096:
            return None
        try:
            parsed = SimpleCookie(raw_cookie)
        except Exception:
            return None
        morsel = parsed.get(SESSION_COOKIE)
        return None if morsel is None else self.app.sessions.get(morsel.value, login)

    def _csrf_ok(self, session: _Session) -> bool:
        token = self._one_header("X-Asimut-CSRF")
        return token is not None and hmac.compare_digest(token, session.csrf)

    def _security_headers(self, *, api: bool = False) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
        )
        if api:
            self.send_header("Cache-Control", "no-store")
            self.send_header("Pragma", "no-cache")

    def _json(self, status: HTTPStatus | int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers(api=True)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: HTTPStatus, code: str, message: str) -> None:
        self._json(status, {"error": code, "message": message})

    def _read_json(self) -> Mapping[str, Any] | None:
        content_type = self._one_header("Content-Type")
        if content_type is None or content_type.split(";", 1)[0].strip().casefold() != (
            "application/json"
        ):
            self._error(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "json_required",
                "This endpoint requires application/json.",
            )
            return None
        raw_length = self._one_header("Content-Length")
        try:
            length = int(raw_length or "")
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_length", "Invalid request length.")
            return None
        if not 0 <= length <= MAX_BODY_BYTES:
            self._error(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "request_too_large",
                "The request is too large.",
            )
            return None
        try:
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_json", "Invalid JSON request.")
            return None
        if not isinstance(payload, Mapping):
            self._error(HTTPStatus.BAD_REQUEST, "object_required", "A JSON object is required.")
            return None
        return payload

    def _authorized(self, *, csrf: bool = False) -> tuple[str, _Session] | None:
        if not self._host_ok() or not self._fetch_metadata_ok():
            self._error(HTTPStatus.FORBIDDEN, "request_rejected", "Request origin was rejected.")
            return None
        login = self._identity()
        if login is None:
            self._error(HTTPStatus.FORBIDDEN, "identity_rejected", "Tailnet identity was rejected.")
            return None
        session = self._session(login)
        if session is None:
            self._error(HTTPStatus.UNAUTHORIZED, "session_required", "Open a new phone session.")
            return None
        if csrf and (not self._origin_ok() or not self._csrf_ok(session)):
            self._error(HTTPStatus.FORBIDDEN, "csrf_rejected", "Request token was rejected.")
            return None
        return login, session

    def do_OPTIONS(self) -> None:  # noqa: N802
        # The production PWA and API are one exact origin. Never grant CORS.
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "same_origin_only", "Cross-origin access is disabled.")

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/healthz":
            self._json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "assistant": "busy" if self.app.assistant.is_busy else "ready",
                    "version": self._build_version(),
                },
            )
            return
        if path == "/api/v1/bootstrap":
            if self._authorized() is None:
                return
            self._json(HTTPStatus.OK, self.app.assistant.snapshot())
            return
        if path == "/api/v1/assistant/events":
            authorized = self._authorized()
            if authorized is None:
                return
            self._stream_events(parsed.query, authorized[1])
            return
        if path.startswith("/api/"):
            self._error(HTTPStatus.NOT_FOUND, "not_found", "Endpoint not found.")
            return
        self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment:
            self._error(HTTPStatus.BAD_REQUEST, "query_rejected", "Queries are not accepted here.")
            return
        path = parsed.path
        if path == "/api/v1/session":
            if not self._host_ok() or not self._origin_ok() or not self._fetch_metadata_ok():
                self._error(HTTPStatus.FORBIDDEN, "origin_rejected", "Request origin was rejected.")
                return
            login = self._identity()
            if login is None:
                self._error(HTTPStatus.FORBIDDEN, "identity_rejected", "Tailnet identity was rejected.")
                return
            payload = self._read_json()
            if payload is None:
                return
            if set(payload) != {"client"} or payload.get("client") != "asimut-phone-v1":
                self._error(HTTPStatus.BAD_REQUEST, "client_rejected", "Unknown phone client.")
                return
            session = self.app.sessions.issue(login)
            body = {
                "csrf_token": session.csrf,
                "session_expires_in": SESSION_IDLE_SECONDS,
                "bootstrap": self.app.assistant.snapshot(),
            }
            encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            cookie = (
                f"{SESSION_COOKIE}={session.token}; Path=/; HttpOnly; SameSite=Strict; "
                f"Max-Age={SESSION_ABSOLUTE_SECONDS}"
            )
            if self.app.secure_cookie:
                cookie += "; Secure"
            self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self._security_headers(api=True)
            self.end_headers()
            self.wfile.write(encoded)
            return

        authorized = self._authorized(csrf=True)
        if authorized is None:
            return
        payload = self._read_json()
        if payload is None:
            return
        if path == "/api/v1/assistant/messages":
            if set(payload) != {"client_message_id", "text"}:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_message", "Message fields are invalid.")
                return
            try:
                accepted, status = self.app.assistant.submit(
                    payload.get("client_message_id"), payload.get("text")
                )
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_message", str(exc))
                return
            except PhoneRateLimitError as exc:
                self._error(HTTPStatus.TOO_MANY_REQUESTS, "rate_limited", str(exc))
                return
            except PhoneServerError:
                self._error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "assistant_unavailable",
                    "The assistant request could not be recorded safely. Try again later.",
                )
                return
            if not accepted:
                if status == "delivery_uncertain":
                    self._error(
                        HTTPStatus.CONFLICT,
                        status,
                        "This exact message has an uncertain prior outcome and was not replayed.",
                    )
                    return
                if status in {"uncertain_acknowledged", "uncertain_review_required"}:
                    self._error(
                        HTTPStatus.CONFLICT,
                        status,
                        "Review the uncertain earlier command before sending another request.",
                    )
                    return
                self._error(
                    HTTPStatus.CONFLICT,
                    status,
                    "The assistant is already working. Stop it or wait for completion.",
                )
                return
            self._json(
                HTTPStatus.ACCEPTED,
                {
                    "accepted": status != "outcome_uncertain",
                    "duplicate": status in {"duplicate", "outcome_uncertain"},
                    "outcome_uncertain": status == "outcome_uncertain",
                },
            )
            return
        if path == "/api/v1/assistant/stop":
            if payload:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_stop", "Stop takes no fields.")
                return
            self._json(HTTPStatus.OK, {"stopping": self.app.assistant.stop()})
            return
        if path == "/api/v1/assistant/new-chat":
            if payload:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_reset", "New chat takes no fields.")
                return
            if not self.app.assistant.new_chat():
                self._error(
                    HTTPStatus.CONFLICT,
                    "turn_in_progress",
                    "Stop the current assistant turn before starting a new chat.",
                )
                return
            self._json(HTTPStatus.ACCEPTED, {"resetting": True})
            return
        if path == "/api/v1/assistant/uncertain/acknowledge":
            if set(payload) != {"reviewed"} or payload.get("reviewed") is not True:
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "review_required",
                    "Confirm that the uncertain outcome was reviewed.",
                )
                return
            try:
                acknowledged = self.app.assistant.acknowledge_uncertain()
                bootstrap = self.app.assistant.snapshot()
            except PhoneActiveTurnError:
                self._error(
                    HTTPStatus.CONFLICT,
                    "turn_in_progress",
                    "Wait for or stop the active turn before reviewing uncertain outcomes.",
                )
                return
            except PhoneServerError:
                self._error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "assistant_unavailable",
                    "The review could not be recorded safely. Try again later.",
                )
                return
            self._json(
                HTTPStatus.OK,
                {"acknowledged": acknowledged, "bootstrap": bootstrap},
            )
            return
        if path == "/api/v1/refresh":
            if payload:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_refresh", "Refresh takes no fields.")
                return
            self._json(HTTPStatus.OK, self.app.assistant.snapshot())
            return
        self._error(HTTPStatus.NOT_FOUND, "not_found", "Endpoint not found.")

    def _stream_events(self, query: str, session: _Session) -> None:
        fields: dict[str, str] = {}
        for part in query.split("&") if query else []:
            key, separator, value = part.partition("=")
            if not separator or key in fields:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_cursor", "Invalid event cursor.")
                return
            fields[key] = value
        if set(fields) != {"after", "generation"}:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_cursor", "Invalid event cursor.")
            return
        try:
            cursor = int(fields["after"])
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_cursor", "Invalid event cursor.")
            return
        if cursor < 0:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_cursor", "Invalid event cursor.")
            return
        requested_generation = fields["generation"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", requested_generation):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_cursor", "Invalid event cursor.")
            return
        same_generation = hmac.compare_digest(
            requested_generation,
            self.app.assistant.stream_generation,
        )
        if not same_generation:
            # A process restart creates a fresh in-memory sequence. The old
            # high-water mark is meaningless and must not suppress new events.
            cursor = 0
        last_event_id = self._one_header("Last-Event-ID")
        if last_event_id is not None and same_generation:
            try:
                resumed_cursor = int(last_event_id)
            except ValueError:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_cursor", "Invalid event cursor.")
                return
            if resumed_cursor < 0:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_cursor", "Invalid event cursor.")
                return
            # EventSource reconnects with Last-Event-ID while retaining the
            # original URL. Taking the newer cursor avoids replaying already
            # rendered assistant deltas after a transient phone connection.
            cursor = max(cursor, resumed_cursor)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self._security_headers(api=True)
        self.end_headers()
        absolute_remaining = max(
            0.0,
            session.created_at + SESSION_ABSOLUTE_SECONDS - _utc_now(),
        )
        deadline = time.monotonic() + min(SSE_CONNECTION_SECONDS, absolute_remaining)
        try:
            while time.monotonic() < deadline:
                events, overflow = self.app.assistant.events.wait_after(
                    cursor,
                    timeout=min(12.0, max(0.0, deadline - time.monotonic())),
                )
                if overflow:
                    event = {
                        "seq": self.app.assistant.events.cursor,
                        "at": _timestamp(),
                        "kind": "snapshot.required",
                    }
                    events = [event]
                if not events:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                for event in events:
                    cursor = int(event["seq"])
                    payload = json.dumps(
                        {
                            **event,
                            "stream_generation": self.app.assistant.stream_generation,
                        },
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                    frame = f"id: {cursor}\nevent: update\ndata: {payload}\n\n".encode("utf-8")
                    self.wfile.write(frame)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        finally:
            # Bound every stream to the server-side session lifetime. The PWA
            # creates a fresh exact-origin session before opening its next SSE
            # connection, so an old stream can never outlive its authorization.
            self.close_connection = True

    def _build_version(self) -> str:
        path = self.app.static_dir / "build-info.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            value = raw.get("version") if isinstance(raw, Mapping) else None
            return value if isinstance(value, str) and value else "unknown"
        except (OSError, UnicodeError, json.JSONDecodeError):
            return "unknown"

    def _serve_static(self, raw_path: str) -> None:
        try:
            decoded = unquote(raw_path, errors="strict")
        except UnicodeError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "Page not found.")
            return
        if (
            "\0" in decoded
            or "\\" in decoded
            or any(part in {"..", "."} or part.startswith(".") for part in decoded.split("/") if part)
        ):
            self._error(HTTPStatus.NOT_FOUND, "not_found", "Page not found.")
            return
        relative = decoded.lstrip("/") or "index.html"
        candidate = (self.app.static_dir / relative).resolve()
        try:
            candidate.relative_to(self.app.static_dir)
        except ValueError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "Page not found.")
            return
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.is_file():
            # Client-side routes receive only the built shell. Requests that
            # look like files never fall through to index.html.
            if "." not in Path(relative).name:
                candidate = self.app.static_dir / "index.html"
            if not candidate.is_file():
                self._error(HTTPStatus.NOT_FOUND, "not_found", "Page not found.")
                return
        if candidate.suffix.casefold() == ".map":
            self._error(HTTPStatus.NOT_FOUND, "not_found", "Page not found.")
            return
        mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        try:
            body = candidate.read_bytes()
        except OSError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "Page not found.")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime}; charset=utf-8" if mime.startswith("text/") else mime)
        self.send_header("Content-Length", str(len(body)))
        if candidate.name in {"index.html", "sw.js", "manifest.webmanifest"}:
            self.send_header("Cache-Control", "no-cache")
        elif "/assets/" in candidate.as_posix():
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        else:
            self.send_header("Cache-Control", "public, max-age=3600")
        self._security_headers(api=False)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


class PhoneHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], app: PhoneApplication) -> None:
        self.app = app
        super().__init__(address, PhoneRequestHandler)


def serve(config: PhoneServerConfig) -> None:
    if not STATIC_DIR.joinpath("index.html").is_file():
        raise PhoneServerError("Phone app build is missing; run the phone build first")
    app = PhoneApplication(config)
    server = PhoneHTTPServer((config.host, config.port), app)
    LOGGER.info("phone service listening on exact loopback")
    stopped = threading.Event()

    def stop(_signum: int | None = None, _frame: Any = None) -> None:
        if stopped.is_set():
            return
        stopped.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    for signal_name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, signal_name, None)
        if signum is not None:
            try:
                signal.signal(signum, stop)
            except (OSError, ValueError):
                pass
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        app.close()
        LOGGER.info("phone service stopped")


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(description="Run the private Asimut phone companion")
    parser.add_argument("--config", type=Path, default=CONFIG_FILE)
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = load_phone_server_config(args.config)
        if args.check_config:
            return 0
        instance = SingleInstanceLock(SERVER_LOCK_FILE)
        if not instance.acquire():
            LOGGER.info("phone service already owns the runtime lock")
            return 0
        try:
            serve(config)
        finally:
            instance.release()
        return 0
    except Exception:
        LOGGER.exception("phone service failed closed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EventHub",
    "PhoneActiveTurnError",
    "PhoneApplication",
    "PhoneAssistantService",
    "PhoneHTTPServer",
    "PhoneRequestHandler",
    "PhoneRateLimitError",
    "PhoneServerConfig",
    "PhoneServerError",
    "RequestLedger",
    "SessionStore",
    "load_phone_server_config",
    "public_assistant_event",
]
