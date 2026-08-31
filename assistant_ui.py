"""Reusable Tk chat surface for the Asimut Booker assistant.

The panel deliberately knows nothing about Codex, Playwright, or booking
mutations.  A controller supplies three callbacks and posts small, neutral
events.  Worker threads may call :meth:`AssistantPanel.post_event`; every Tk
mutation is performed later by the panel's UI-thread event pump.

``reasoning_*`` events are for concise, user-facing summaries such as
"Checking tomorrow's agenda".  They must never contain hidden chain-of-thought.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
import math
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any, Literal


DEFAULT_PALETTE = {
    "page": "#F7F7F8",
    "surface": "#FFFFFF",
    "surface_muted": "#F4F4F5",
    "surface_hover": "#ECECEF",
    "text": "#1D1D1F",
    "secondary_text": "#6E6E73",
    "tertiary_text": "#86868B",
    "border": "#D9D9DE",
    "accent": "#0071E3",
    "accent_hover": "#0077ED",
    "accent_pressed": "#0068D1",
    "selection": "#E8F2FF",
    "user_bubble": "#EAF2FB",
    "assistant_bubble": "#FFFFFF",
    "progress": "#F5F7FA",
    "success": "#147D34",
    "warning": "#A85D00",
    "danger": "#B00020",
}

DEFAULT_STARTER_PROMPTS = (
    "What bookings and events do I have tomorrow?",
    "Explain the next planned booking.",
    "Is the automatic booker healthy?",
)

MAX_EVENT_TEXT = 250_000
SHIFT_MASK = 0x0001
CONTROL_MASK = 0x0004

EventKind = Literal[
    "clear",
    "user_message",
    "assistant_start",
    "assistant_delta",
    "assistant_message",
    "commentary_delta",
    "reasoning_start",
    "reasoning_delta",
    "reasoning_summary",
    "activity",
    "tool",
    "error",
    "done",
    "connection",
    "busy",
    "turn_started",
]

SUPPORTED_EVENT_KINDS = frozenset(
    {
        "clear",
        "user_message",
        "assistant_start",
        "assistant_delta",
        "assistant_message",
        "commentary_delta",
        "reasoning_start",
        "reasoning_delta",
        "reasoning_summary",
        "activity",
        "tool",
        "error",
        "done",
        "connection",
        "busy",
        "turn_started",
    }
)

EVENT_KIND_ALIASES = {
    "message_start": "assistant_start",
    "message_delta": "assistant_delta",
    "message": "assistant_message",
    "reasoning": "reasoning_summary",
    "reasoning_summary_delta": "reasoning_delta",
    "status": "activity",
    "tool_call": "tool",
    "tool_start": "tool",
    "tool_update": "tool",
    "tool_end": "tool",
    "completed": "done",
    "turn_completed": "done",
    "history": "activity",
    "user_input_request": "activity",
}


@dataclass(frozen=True, slots=True)
class AssistantEvent:
    """One presentation event emitted by a controller.

    ``event_id`` lets streamed deltas and status updates replace the correct
    card.  It may be omitted for simple controllers; the panel then binds the
    event to the currently active assistant/reasoning card.
    """

    kind: EventKind | str
    text: str = ""
    title: str = ""
    status: str = ""
    event_id: str = ""
    replace: bool = False
    expanded: bool | None = None


def _mapping_text(
    raw: Mapping[str, Any],
    *keys: str,
    default: str = "",
) -> str:
    for key in keys:
        if key not in raw or raw[key] is None:
            continue
        value = raw[key]
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        raise ValueError(f"Assistant event {key} must be text")
    return default


def normalize_assistant_event(
    event: AssistantEvent | Mapping[str, Any],
) -> AssistantEvent:
    """Return one validated event while accepting neutral controller aliases."""

    if isinstance(event, AssistantEvent):
        candidate = event
    elif isinstance(event, Mapping):
        source_kind = _mapping_text(event, "kind", "type").strip().casefold()
        kind = source_kind
        text = _mapping_text(event, "text", "delta", "message", "detail")
        title = _mapping_text(event, "title", "name", "tool_name", "tool")
        status = _mapping_text(event, "status", "state", "phase")
        event_id = _mapping_text(
            event,
            "event_id",
            "item_id",
            "call_id",
            "turn_id",
            "id",
        )
        replace_value = event.get("replace", False)
        expanded_value = event.get("expanded")
        if not isinstance(replace_value, bool):
            raise ValueError("Assistant event replace must be a boolean")
        if expanded_value is not None and not isinstance(expanded_value, bool):
            raise ValueError("Assistant event expanded must be a boolean or null")
        if source_kind == "history":
            title = title or "Previous chat loaded"
            status = status or "completed"
        elif source_kind == "user_input_request":
            title = title or "Clarification needed"
            status = status or "attention"
            if not text:
                questions = event.get("questions")
                if isinstance(questions, Sequence) and not isinstance(questions, str):
                    question_text = []
                    for question in questions[:3]:
                        if isinstance(question, Mapping):
                            value = question.get("question") or question.get("prompt")
                            if isinstance(value, str) and value.strip():
                                question_text.append(value.strip())
                    text = "\n".join(question_text)
        elif (
            source_kind == "activity"
            and "summary_index" in event
            and title.strip().casefold() == "thinking"
        ):
            # Codex announces a user-facing reasoning-summary part immediately
            # before streaming that part.  Route both events to one card.
            kind = "reasoning_start"
        candidate = AssistantEvent(
            kind=kind,
            text=text,
            title=title,
            status=status,
            event_id=event_id,
            replace=replace_value,
            expanded=expanded_value,
        )
    else:
        raise TypeError("Assistant events must be AssistantEvent objects or mappings")

    if not isinstance(candidate.kind, str):
        raise ValueError("Assistant event kind must be text")
    kind = candidate.kind.strip().casefold()
    kind = EVENT_KIND_ALIASES.get(kind, kind)
    if kind not in SUPPORTED_EVENT_KINDS:
        raise ValueError(f"Unsupported assistant event kind: {candidate.kind!r}")

    values = {
        "text": candidate.text,
        "title": candidate.title,
        "status": candidate.status,
        "event_id": candidate.event_id,
    }
    for field, value in values.items():
        if not isinstance(value, str):
            raise ValueError(f"Assistant event {field} must be text")
        if len(value) > MAX_EVENT_TEXT:
            raise ValueError(f"Assistant event {field} is too large")
    if not isinstance(candidate.replace, bool):
        raise ValueError("Assistant event replace must be a boolean")
    if candidate.expanded is not None and not isinstance(candidate.expanded, bool):
        raise ValueError("Assistant event expanded must be a boolean or null")

    return AssistantEvent(
        kind=kind,
        text=candidate.text,
        title=candidate.title,
        status=candidate.status,
        event_id=candidate.event_id,
        replace=candidate.replace,
        expanded=candidate.expanded,
    )


def responsive_content_width(
    available_width: int,
    *,
    maximum: int = 900,
    comfortable_minimum: int = 560,
) -> int:
    """Choose a centered transcript width without overflowing small windows."""

    if isinstance(available_width, bool) or not isinstance(available_width, int):
        raise TypeError("available_width must be an integer")
    if available_width <= 0:
        return 1
    if maximum < 240 or comfortable_minimum < 240 or comfortable_minimum > maximum:
        raise ValueError("responsive width bounds are invalid")

    gutter = 44 if available_width >= 900 else 26 if available_width >= 560 else 14
    usable = max(1, available_width - gutter * 2)
    if usable < comfortable_minimum:
        return usable
    return min(maximum, usable)


def composer_return_action(modifier_state: int) -> Literal["send", "newline"]:
    """Map Tk's modifier mask to the familiar Enter/Shift+Enter behavior."""

    if isinstance(modifier_state, bool) or not isinstance(modifier_state, int):
        raise TypeError("modifier_state must be an integer")
    return "newline" if modifier_state & SHIFT_MASK else "send"


def estimate_text_rows(text: str, columns: int, *, maximum: int = 80) -> int:
    """Estimate a compact Text-widget height without requiring a display server."""

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if isinstance(columns, bool) or not isinstance(columns, int) or columns <= 0:
        raise ValueError("columns must be a positive integer")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
        raise ValueError("maximum must be a positive integer")
    if not text:
        return 1
    rows = 0
    for line in text.splitlines() or [""]:
        # Keep an exact multiple on one fewer row than a naive +1 formula.
        rows += max(1, math.ceil(max(1, len(line)) / columns))
    if text.endswith("\n"):
        rows += 1
    return max(1, min(maximum, rows))


class AssistantEventBuffer:
    """Small locked buffer that coalesces adjacent streaming deltas."""

    _DELTA_KINDS = frozenset(
        {"assistant_delta", "reasoning_delta", "commentary_delta"}
    )
    _TERMINAL_KINDS = frozenset({"clear", "done", "error", "connection", "busy"})

    def __init__(self, capacity: int = 4096) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 8:
            raise ValueError("Assistant event buffer capacity must be at least 8")
        self.capacity = capacity
        self._events: deque[AssistantEvent] = deque()
        self._lock = threading.Lock()

    def put(self, event: AssistantEvent) -> bool:
        """Add an event, returning False only for non-terminal overflow."""

        if not isinstance(event, AssistantEvent):
            raise TypeError("AssistantEventBuffer accepts AssistantEvent objects")
        with self._lock:
            if (
                event.kind in self._DELTA_KINDS
                and self._events
                and self._events[-1].kind == event.kind
                and self._events[-1].event_id == event.event_id
                and self._events[-1].title == event.title
                and self._events[-1].status == event.status
                and not event.replace
                and not self._events[-1].replace
            ):
                previous = self._events[-1]
                self._events[-1] = replace(previous, text=previous.text + event.text)
                return True

            if len(self._events) >= self.capacity:
                if event.kind not in self._TERMINAL_KINDS:
                    return False
                self._events.popleft()
            self._events.append(event)
            return True

    def drain(self, limit: int = 256) -> list[AssistantEvent]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("Assistant event drain limit must be positive")
        with self._lock:
            count = min(limit, len(self._events))
            return [self._events.popleft() for _ in range(count)]

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


@dataclass(slots=True)
class _TranscriptCard:
    event_id: str
    kind: str
    row: tk.Frame
    shell: tk.Frame
    body: tk.Text
    text: str = ""
    status_label: tk.Label | None = None
    body_host: tk.Frame | None = None
    toggle_button: tk.Button | None = None
    expanded: bool = True
    active: bool = True


class AssistantPanel(ttk.Frame):
    """Polished, controller-agnostic assistant transcript and composer."""

    EVENT_POLL_MS = 33
    EVENT_BATCH_SIZE = 256

    def __init__(
        self,
        master: tk.Misc,
        *,
        on_send: Callable[[str], Any] | None = None,
        on_stop: Callable[[], Any] | None = None,
        on_new_chat: Callable[[], Any] | None = None,
        palette: Mapping[str, str] | None = None,
        font_family: str = "Segoe UI",
        display_font_family: str | None = None,
        starter_prompts: Sequence[str] = DEFAULT_STARTER_PROMPTS,
    ) -> None:
        self.palette = dict(DEFAULT_PALETTE)
        if palette:
            self.palette.update(
                {key: value for key, value in palette.items() if key in self.palette}
            )
        self.font_family = font_family
        self.display_font_family = display_font_family or font_family
        self.on_send = on_send
        self.on_stop = on_stop
        self.on_new_chat = on_new_chat
        self.starter_prompts = tuple(
            prompt.strip()
            for prompt in starter_prompts
            if isinstance(prompt, str) and prompt.strip()
        )

        self._ui_thread_id = threading.get_ident()
        self._closed = False
        self._event_after_id: str | None = None
        self._event_buffer = AssistantEventBuffer()
        self._id_counter = 0
        self._row_counter = 0
        self._cards: dict[str, _TranscriptCard] = {}
        self._active_assistant_id = ""
        self._active_reasoning_id = ""
        self._welcome_frame: tk.Frame | None = None
        self._busy = False
        self._content_width = 760

        self._configure_styles(master)
        super().__init__(master, style="Assistant.Page.TFrame", takefocus=False)
        self._build_interface()
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._schedule_event_pump()

    @property
    def busy(self) -> bool:
        return self._busy

    def _configure_styles(self, master: tk.Misc) -> None:
        style = ttk.Style(master)
        style.configure("Assistant.Page.TFrame", background=self.palette["page"])
        style.configure("Assistant.Surface.TFrame", background=self.palette["surface"])
        style.configure(
            "Assistant.Title.TLabel",
            background=self.palette["page"],
            foreground=self.palette["text"],
            font=(self.display_font_family, 20, "bold"),
        )
        style.configure(
            "Assistant.Subtitle.TLabel",
            background=self.palette["page"],
            foreground=self.palette["secondary_text"],
            font=(self.font_family, 10),
        )
        style.configure(
            "Assistant.Primary.TButton",
            background=self.palette["accent"],
            foreground="#FFFFFF",
            bordercolor=self.palette["accent"],
            lightcolor=self.palette["accent"],
            darkcolor=self.palette["accent"],
            padding=(16, 10),
            font=(self.font_family, 10, "bold"),
            relief="flat",
        )
        style.map(
            "Assistant.Primary.TButton",
            background=[
                ("pressed", self.palette["accent_pressed"]),
                ("active", self.palette["accent_hover"]),
                ("disabled", "#A8CDEE"),
            ],
            foreground=[("disabled", "#F4F8FC")],
        )
        style.configure(
            "Assistant.Toolbar.TButton",
            padding=(12, 8),
            font=(self.font_family, 10, "bold"),
        )
        style.configure(
            "Assistant.Stop.TButton",
            padding=(16, 10),
            foreground=self.palette["danger"],
            font=(self.font_family, 10, "bold"),
        )

    def _build_interface(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = tk.Frame(self, background=self.palette["page"])
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 12))
        header.columnconfigure(0, weight=1)

        heading = tk.Frame(header, background=self.palette["page"])
        heading.grid(row=0, column=0, sticky="w")
        ttk.Label(
            heading,
            text="Asimut Assistant",
            style="Assistant.Title.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            heading,
            text="Ask about your schedule and plans, or tell the booker what to do.",
            style="Assistant.Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        actions = tk.Frame(header, background=self.palette["page"])
        actions.grid(row=0, column=1, sticky="e")
        self.connection_dot = tk.Label(
            actions,
            text="●",
            background=self.palette["page"],
            foreground=self.palette["tertiary_text"],
            font=(self.font_family, 10, "bold"),
        )
        self.connection_dot.pack(side="left", padx=(0, 5))
        self.connection_var = tk.StringVar(value="Starting…")
        tk.Label(
            actions,
            textvariable=self.connection_var,
            background=self.palette["page"],
            foreground=self.palette["secondary_text"],
            font=(self.font_family, 9),
        ).pack(side="left", padx=(0, 14))
        self.new_chat_button = ttk.Button(
            actions,
            text="New chat",
            command=self._request_new_chat,
            style="Assistant.Toolbar.TButton",
            takefocus=True,
        )
        self.new_chat_button.pack(side="left")

        transcript_container = tk.Frame(self, background=self.palette["page"])
        transcript_container.grid(row=1, column=0, sticky="nsew")
        transcript_container.columnconfigure(0, weight=1)
        transcript_container.rowconfigure(0, weight=1)

        self.transcript_canvas = tk.Canvas(
            transcript_container,
            background=self.palette["page"],
            borderwidth=0,
            highlightthickness=0,
            takefocus=True,
        )
        self.transcript_scrollbar = ttk.Scrollbar(
            transcript_container,
            orient="vertical",
            command=self.transcript_canvas.yview,
        )
        self.transcript_canvas.configure(
            yscrollcommand=self.transcript_scrollbar.set
        )
        self.transcript_canvas.grid(row=0, column=0, sticky="nsew")
        self.transcript_scrollbar.grid(row=0, column=1, sticky="ns")

        self.transcript_inner = tk.Frame(
            self.transcript_canvas,
            background=self.palette["page"],
        )
        self.transcript_inner.columnconfigure(0, weight=1)
        self._transcript_window = self.transcript_canvas.create_window(
            (0, 0),
            window=self.transcript_inner,
            anchor="n",
            width=self._content_width,
        )
        self.transcript_inner.bind(
            "<Configure>", self._on_transcript_configure, add="+"
        )
        self.transcript_canvas.bind(
            "<Configure>", self._on_canvas_configure, add="+"
        )
        self.transcript_canvas.bind(
            "<Prior>",
            lambda _event: self._scroll_transcript_page(-1),
            add="+",
        )
        self.transcript_canvas.bind(
            "<Next>",
            lambda _event: self._scroll_transcript_page(1),
            add="+",
        )
        self.transcript_canvas.bind(
            "<Home>",
            lambda _event: self._move_transcript_to(0.0),
            add="+",
        )
        self.transcript_canvas.bind(
            "<End>",
            lambda _event: self._move_transcript_to(1.0),
            add="+",
        )
        self._bind_mousewheel(self.transcript_canvas)
        self._show_welcome()

        composer_area = tk.Frame(self, background=self.palette["page"])
        composer_area.grid(row=2, column=0, sticky="ew", padx=24, pady=(8, 20))
        composer_area.columnconfigure(0, weight=1)
        self.composer_column = tk.Frame(
            composer_area,
            background=self.palette["page"],
        )
        self.composer_column.grid(row=0, column=0)
        self.composer_column.columnconfigure(0, weight=1)
        self.composer_host = tk.Frame(
            self.composer_column,
            background=self.palette["surface"],
            highlightbackground=self.palette["border"],
            highlightcolor=self.palette["accent"],
            highlightthickness=1,
            padx=12,
            pady=10,
        )
        self.composer_host.grid(row=0, column=0, sticky="ew")
        self.composer_host.columnconfigure(0, weight=1)

        self.composer = tk.Text(
            self.composer_host,
            height=3,
            wrap="word",
            undo=True,
            maxundo=50,
            background=self.palette["surface"],
            foreground=self.palette["text"],
            insertbackground=self.palette["text"],
            selectbackground=self.palette["selection"],
            selectforeground=self.palette["text"],
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=2,
            pady=2,
            font=(self.font_family, 11),
            takefocus=True,
        )
        self.composer.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.composer.bind("<Return>", self._on_composer_return, add="+")
        self.composer.bind("<KP_Enter>", self._on_composer_return, add="+")
        self.composer.bind("<KeyRelease>", self._sync_composer_state, add="+")
        self.composer.bind("<FocusIn>", self._sync_composer_state, add="+")
        self.composer.bind("<FocusOut>", self._sync_composer_state, add="+")
        self.composer.bind("<Escape>", self._on_escape, add="+")

        self.placeholder = tk.Label(
            self.composer_host,
            text="Message Asimut Assistant",
            background=self.palette["surface"],
            foreground=self.palette["tertiary_text"],
            font=(self.font_family, 11),
            cursor="xterm",
        )
        self.placeholder.place(x=15, y=13)
        self.placeholder.bind("<Button-1>", lambda _event: self.composer.focus_set())

        self.send_button = ttk.Button(
            self.composer_host,
            text="Send",
            command=self._submit_composer,
            style="Assistant.Primary.TButton",
            takefocus=True,
        )
        self.send_button.grid(row=0, column=1, sticky="se")
        self.stop_button = ttk.Button(
            self.composer_host,
            text="Stop",
            command=self._request_stop,
            style="Assistant.Stop.TButton",
            takefocus=True,
        )
        self.stop_button.grid(row=0, column=1, sticky="se")
        self.stop_button.grid_remove()

        self.composer_status_var = tk.StringVar(
            value="Enter to send · Shift+Enter for a new line"
        )
        tk.Label(
            self.composer_column,
            textvariable=self.composer_status_var,
            background=self.palette["page"],
            foreground=self.palette["tertiary_text"],
            font=(self.font_family, 9),
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._sync_composer_width(self._content_width)
        self._sync_composer_state()

    def _next_event_id(self, prefix: str) -> str:
        self._id_counter += 1
        return f"{prefix}-{self._id_counter}"

    def _resolve_event_id(self, event: AssistantEvent) -> AssistantEvent:
        namespace = {
            "user_message": "user",
            "assistant_start": "assistant",
            "assistant_delta": "assistant",
            "assistant_message": "assistant",
            "commentary_delta": "reasoning",
            "reasoning_start": "reasoning",
            "reasoning_delta": "reasoning",
            "reasoning_summary": "reasoning",
            "activity": "activity",
            "tool": "tool",
            "error": "error",
            "turn_started": "assistant",
        }.get(event.kind, "")
        if event.event_id:
            prefix = f"{namespace}:" if namespace else ""
            event_id = (
                event.event_id
                if not prefix or event.event_id.startswith(prefix)
                else prefix + event.event_id
            )
            return replace(event, event_id=event_id)
        if event.kind == "assistant_start":
            return replace(event, event_id=self._next_event_id("assistant"))
        if event.kind in {"assistant_delta", "assistant_message"}:
            event_id = self._active_assistant_id or self._next_event_id("assistant")
            return replace(event, event_id=event_id)
        if event.kind == "commentary_delta":
            event_id = self._active_reasoning_id or self._next_event_id("reasoning")
            return replace(event, event_id=event_id)
        if event.kind == "reasoning_start":
            return replace(event, event_id=self._next_event_id("reasoning"))
        if event.kind in {"reasoning_delta", "reasoning_summary"}:
            event_id = self._active_reasoning_id or self._next_event_id("reasoning")
            return replace(event, event_id=event_id)
        if event.kind in {"user_message", "activity", "tool", "error"}:
            return replace(event, event_id=self._next_event_id(event.kind))
        return event

    def post_event(self, event: AssistantEvent | Mapping[str, Any]) -> bool:
        """Queue an event from any thread without touching Tk."""

        if self._closed:
            return False
        try:
            normalized = normalize_assistant_event(event)
        except (TypeError, ValueError) as exc:
            normalized = AssistantEvent(
                "error",
                text=f"The assistant sent an invalid update: {exc}",
                title="Assistant update error",
            )
        return self._event_buffer.put(normalized)

    def apply_event(self, event: AssistantEvent | Mapping[str, Any]) -> bool:
        """Apply on the Tk thread, or enqueue automatically from a worker."""

        if self._closed:
            return False
        if threading.get_ident() != self._ui_thread_id:
            return self.post_event(event)
        try:
            normalized = normalize_assistant_event(event)
        except (TypeError, ValueError) as exc:
            normalized = AssistantEvent(
                "error",
                text=f"The assistant sent an invalid update: {exc}",
                title="Assistant update error",
            )
        self._apply_event_on_ui(self._resolve_event_id(normalized))
        return True

    def _apply_event_on_ui(self, event: AssistantEvent) -> None:
        at_bottom = self._is_at_bottom()
        kind = event.kind
        if kind == "clear":
            self._clear_transcript(show_welcome=True)
            self.set_busy(False)
        elif kind == "user_message":
            self._hide_welcome()
            self._upsert_message(event, role="user")
        elif kind == "assistant_start":
            self._hide_welcome()
            self._active_assistant_id = event.event_id
            self._upsert_message(event, role="assistant")
        elif kind in {"assistant_delta", "assistant_message"}:
            self._hide_welcome()
            self._active_assistant_id = event.event_id
            self._upsert_message(event, role="assistant")
        elif kind in {
            "reasoning_start",
            "reasoning_delta",
            "reasoning_summary",
            "commentary_delta",
        }:
            self._hide_welcome()
            self._active_reasoning_id = event.event_id
            self._upsert_progress(event, reasoning=True)
        elif kind in {"activity", "tool"}:
            self._hide_welcome()
            self._upsert_progress(event, reasoning=False)
        elif kind == "error":
            self._hide_welcome()
            self._create_alert(event)
            self.set_busy(False, event.status or "Assistant needs attention")
        elif kind == "turn_started":
            self.set_busy(True, event.text or event.title or event.status or "Working…")
            if event.event_id:
                self._active_assistant_id = event.event_id
        elif kind == "done":
            self._finish_streaming_cards()
            self.set_busy(False, event.text or "Ready")
            self._active_assistant_id = ""
            self._active_reasoning_id = ""
        elif kind == "connection":
            self.set_connection(event.status or "unknown", event.text or event.title)
        elif kind == "busy":
            requested = (event.status or event.text).strip().casefold()
            is_busy = requested in {"1", "true", "yes", "busy", "working", "running"}
            self.set_busy(is_busy, event.text or event.title)

        if at_bottom or kind in {"user_message", "clear"}:
            self.after_idle(self._scroll_to_bottom)

    def _schedule_event_pump(self, delay: int | None = None) -> None:
        if self._closed or self._event_after_id is not None:
            return
        self._event_after_id = self.after(
            self.EVENT_POLL_MS if delay is None else delay,
            self._drain_posted_events,
        )

    def _drain_posted_events(self) -> None:
        self._event_after_id = None
        if self._closed:
            return
        for event in self._event_buffer.drain(self.EVENT_BATCH_SIZE):
            self.apply_event(event)
        self._schedule_event_pump(8 if len(self._event_buffer) else self.EVENT_POLL_MS)

    def _show_welcome(self) -> None:
        if self._welcome_frame is not None:
            return
        frame = tk.Frame(self.transcript_inner, background=self.palette["page"])
        frame.grid(
            row=self._row_counter,
            column=0,
            sticky="ew",
            padx=10,
            pady=(30, 16),
        )
        self._row_counter += 1
        frame.columnconfigure(0, weight=1)
        tk.Label(
            frame,
            text="How can I help with your practice-room bookings?",
            background=self.palette["page"],
            foreground=self.palette["text"],
            font=(self.display_font_family, 16, "bold"),
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            frame,
            text=(
                "I can explain your schedule, booking plan and preferences, "
                "and carry out supported booker actions."
            ),
            background=self.palette["page"],
            foreground=self.palette["secondary_text"],
            font=(self.font_family, 10),
            justify="left",
            wraplength=max(300, self._content_width - 40),
        ).grid(row=1, column=0, sticky="w", pady=(6, 18))
        for index, prompt in enumerate(self.starter_prompts, start=2):
            button = tk.Button(
                frame,
                text=prompt,
                command=lambda value=prompt: self._submit_text(value),
                anchor="w",
                justify="left",
                background=self.palette["surface"],
                foreground=self.palette["text"],
                activebackground=self.palette["surface_hover"],
                activeforeground=self.palette["text"],
                highlightbackground=self.palette["border"],
                highlightcolor=self.palette["accent"],
                highlightthickness=1,
                borderwidth=0,
                relief="flat",
                padx=14,
                pady=10,
                font=(self.font_family, 10),
                takefocus=True,
                cursor="hand2",
            )
            button.grid(row=index, column=0, sticky="ew", pady=(0, 8))
            self._bind_mousewheel(button)
        self._welcome_frame = frame
        self._bind_mousewheel(frame)

    def _hide_welcome(self) -> None:
        frame = self._welcome_frame
        self._welcome_frame = None
        if frame is not None:
            try:
                frame.destroy()
            except tk.TclError:
                pass

    def _clear_transcript(self, *, show_welcome: bool) -> None:
        self._event_buffer.clear()
        for child in self.transcript_inner.winfo_children():
            child.destroy()
        self._cards.clear()
        self._row_counter = 0
        self._active_assistant_id = ""
        self._active_reasoning_id = ""
        self._welcome_frame = None
        if show_welcome:
            self._show_welcome()

    def _new_text_widget(
        self,
        parent: tk.Misc,
        *,
        text: str,
        background: str,
        foreground: str,
        columns: int,
        weight: str = "normal",
    ) -> tk.Text:
        body = tk.Text(
            parent,
            wrap="word",
            width=max(20, columns),
            height=estimate_text_rows(text, max(20, columns)),
            background=background,
            foreground=foreground,
            insertbackground=foreground,
            selectbackground=self.palette["selection"],
            selectforeground=self.palette["text"],
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=0,
            pady=0,
            font=(self.font_family, 11, weight),
            cursor="arrow",
            takefocus=True,
        )
        body.insert("1.0", text)
        body.configure(state="disabled")
        body.bind("<Control-a>", self._select_all_text, add="+")
        body.bind("<Control-A>", self._select_all_text, add="+")
        self._bind_mousewheel(body)
        return body

    @staticmethod
    def _select_all_text(event: tk.Event) -> str:
        widget = event.widget
        try:
            widget.tag_add("sel", "1.0", "end-1c")
        except tk.TclError:
            pass
        return "break"

    def _upsert_message(self, event: AssistantEvent, *, role: str) -> None:
        card = self._cards.get(event.event_id)
        if card is None or card.kind != role:
            card = self._create_message_card(event, role=role)
        elif event.replace or event.kind in {"assistant_start", "assistant_message"}:
            self._set_card_text(card, event.text)
        else:
            self._set_card_text(card, card.text + event.text)

    def _create_message_card(
        self,
        event: AssistantEvent,
        *,
        role: str,
    ) -> _TranscriptCard:
        row = tk.Frame(self.transcript_inner, background=self.palette["page"])
        row.grid(
            row=self._row_counter,
            column=0,
            sticky="ew",
            padx=10,
            pady=(4, 12),
        )
        self._row_counter += 1
        row.columnconfigure(0, weight=1)
        row.columnconfigure(1, weight=0)

        is_user = role == "user"
        background = (
            self.palette["user_bubble"]
            if is_user
            else self.palette["assistant_bubble"]
        )
        shell = tk.Frame(
            row,
            background=background,
            padx=15 if is_user else 8,
            pady=11 if is_user else 8,
        )
        shell.grid(
            row=0,
            column=1 if is_user else 0,
            columnspan=1 if is_user else 2,
            sticky="e" if is_user else "ew",
        )
        shell.columnconfigure(1 if not is_user else 0, weight=1)

        text_column = (
            max(12, min(70, int(self._content_width * 0.72) // 8))
            if is_user
            else max(14, min(96, (self._content_width - 90) // 8))
        )
        if not is_user:
            avatar = tk.Label(
                shell,
                text="A",
                background=self.palette["accent"],
                foreground="#FFFFFF",
                font=(self.font_family, 9, "bold"),
                width=2,
                height=1,
            )
            avatar.grid(row=0, column=0, sticky="nw", padx=(0, 11), pady=(1, 0))
            tk.Label(
                shell,
                text="Asimut Assistant",
                background=background,
                foreground=self.palette["secondary_text"],
                font=(self.font_family, 9, "bold"),
            ).grid(row=0, column=1, sticky="w", pady=(0, 5))
            body_row, body_column = 1, 1
        else:
            body_row, body_column = 0, 0

        body = self._new_text_widget(
            shell,
            text=event.text,
            background=background,
            foreground=self.palette["text"],
            columns=text_column,
        )
        body.grid(row=body_row, column=body_column, sticky="ew")
        card = _TranscriptCard(
            event_id=event.event_id,
            kind=role,
            row=row,
            shell=shell,
            body=body,
            text=event.text,
        )
        self._cards[event.event_id] = card
        self._bind_mousewheel(row)
        self._bind_mousewheel(shell)
        return card

    def _upsert_progress(self, event: AssistantEvent, *, reasoning: bool) -> None:
        card = self._cards.get(event.event_id)
        if card is None or card.kind not in {"reasoning", "activity", "tool"}:
            card = self._create_progress_card(event, reasoning=reasoning)
        else:
            if event.kind in {"reasoning_delta", "commentary_delta"} and not event.replace:
                text = card.text + event.text
            elif event.text:
                text = event.text
            else:
                text = card.text
            self._set_card_text(card, text)
            self._set_progress_status(card, event.status, event.kind)
            if event.title:
                # The title widget is the first label after the toggle button.
                for child in card.shell.winfo_children():
                    if getattr(child, "_assistant_title", False):
                        child.configure(text=event.title)
                        break
        if event.expanded is not None and event.expanded != card.expanded:
            self._toggle_progress(card, desired=event.expanded)
        elif event.kind == "reasoning_summary" and event.expanded is None:
            self._toggle_progress(card, desired=False)

    def _create_progress_card(
        self,
        event: AssistantEvent,
        *,
        reasoning: bool,
    ) -> _TranscriptCard:
        row = tk.Frame(self.transcript_inner, background=self.palette["page"])
        row.grid(
            row=self._row_counter,
            column=0,
            sticky="ew",
            padx=58,
            pady=(1, 10),
        )
        self._row_counter += 1
        row.columnconfigure(0, weight=1)
        shell = tk.Frame(
            row,
            background=self.palette["progress"],
            highlightbackground=self.palette["border"],
            highlightthickness=1,
            padx=11,
            pady=9,
        )
        shell.grid(row=0, column=0, sticky="ew")
        shell.columnconfigure(1, weight=1)

        kind = "reasoning" if reasoning else "tool" if event.kind == "tool" else "activity"
        expanded = event.expanded if event.expanded is not None else True
        card = _TranscriptCard(
            event_id=event.event_id,
            kind=kind,
            row=row,
            shell=shell,
            body=None,  # type: ignore[arg-type]
            text=event.text,
            expanded=expanded,
        )
        toggle = tk.Button(
            shell,
            text="▾" if expanded else "▸",
            command=lambda: self._toggle_progress(card),
            background=self.palette["progress"],
            foreground=self.palette["secondary_text"],
            activebackground=self.palette["surface_hover"],
            activeforeground=self.palette["text"],
            borderwidth=0,
            relief="flat",
            padx=2,
            pady=0,
            takefocus=True,
            cursor="hand2",
            font=(self.font_family, 10, "bold"),
        )
        toggle.grid(row=0, column=0, sticky="nw", padx=(0, 6))
        card.toggle_button = toggle
        title = tk.Label(
            shell,
            text=event.title
            or (
                "Work update"
                if event.kind == "commentary_delta"
                else "Reasoning summary"
                if reasoning
                else "Using booker tools"
                if event.kind == "tool"
                else "Activity"
            ),
            background=self.palette["progress"],
            foreground=self.palette["text"],
            font=(self.font_family, 10, "bold"),
            anchor="w",
        )
        title._assistant_title = True  # type: ignore[attr-defined]
        title.grid(row=0, column=1, sticky="ew")
        status_label = tk.Label(
            shell,
            text="",
            background=self.palette["progress"],
            foreground=self.palette["secondary_text"],
            font=(self.font_family, 9),
            anchor="e",
        )
        status_label.grid(row=0, column=2, sticky="e", padx=(12, 0))
        card.status_label = status_label

        body_host = tk.Frame(shell, background=self.palette["progress"])
        body_host.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(7, 1))
        body_host.columnconfigure(0, weight=1)
        body = self._new_text_widget(
            body_host,
            text=event.text,
            background=self.palette["progress"],
            foreground=self.palette["secondary_text"],
            columns=max(14, min(92, (self._content_width - 120) // 8)),
        )
        body.grid(row=0, column=0, sticky="ew")
        card.body = body
        card.body_host = body_host
        if not expanded or not event.text:
            body_host.grid_remove()
        self._cards[event.event_id] = card
        self._set_progress_status(card, event.status, event.kind)
        self._bind_mousewheel(row)
        self._bind_mousewheel(shell)
        self._bind_mousewheel(toggle)
        self._bind_mousewheel(title)
        self._bind_mousewheel(status_label)
        return card

    def _toggle_progress(
        self,
        card: _TranscriptCard,
        *,
        desired: bool | None = None,
    ) -> None:
        expanded = not card.expanded if desired is None else bool(desired)
        card.expanded = expanded
        if card.toggle_button is not None:
            card.toggle_button.configure(text="▾" if expanded else "▸")
        if card.body_host is not None:
            if expanded:
                card.body_host.grid()
            else:
                card.body_host.grid_remove()
        self.after_idle(self._refresh_scrollregion)

    def _set_progress_status(
        self,
        card: _TranscriptCard,
        status: str,
        event_kind: str,
    ) -> None:
        if card.status_label is None:
            return
        normalized = status.strip().casefold()
        if not status:
            status = "Done" if event_kind == "reasoning_summary" else "Working…"
            normalized = status.casefold()
        if normalized in {
            "done",
            "complete",
            "completed",
            "success",
            "succeeded",
            "ready",
            "answered",
        }:
            color = self.palette["success"]
            card.active = False
        elif normalized in {"error", "failed", "blocked"}:
            color = self.palette["danger"]
            card.active = False
        elif normalized in {"warning", "attention"}:
            color = self.palette["warning"]
            card.active = False
        else:
            color = self.palette["secondary_text"]
            card.active = True
        card.status_label.configure(text=status, foreground=color)

    def _create_alert(self, event: AssistantEvent) -> None:
        row = tk.Frame(self.transcript_inner, background=self.palette["page"])
        row.grid(
            row=self._row_counter,
            column=0,
            sticky="ew",
            padx=58,
            pady=(2, 12),
        )
        self._row_counter += 1
        shell = tk.Frame(
            row,
            background="#FFF5F5",
            highlightbackground="#E6B8BC",
            highlightthickness=1,
            padx=13,
            pady=11,
        )
        shell.pack(fill="x")
        tk.Label(
            shell,
            text=event.title or "Something went wrong",
            background="#FFF5F5",
            foreground=self.palette["danger"],
            font=(self.font_family, 10, "bold"),
            anchor="w",
        ).pack(fill="x")
        body = self._new_text_widget(
            shell,
            text=event.text or "The assistant could not complete this request.",
            background="#FFF5F5",
            foreground=self.palette["text"],
            columns=78,
        )
        body.pack(fill="x", pady=(5, 0))
        self._cards[event.event_id] = _TranscriptCard(
            event.event_id,
            "error",
            row,
            shell,
            body,
            event.text,
        )
        self._bind_mousewheel(row)
        self._bind_mousewheel(shell)

    def _set_card_text(self, card: _TranscriptCard, text: str) -> None:
        card.text = text
        body = card.body
        body.configure(state="normal")
        body.delete("1.0", "end")
        body.insert("1.0", text)
        body.configure(state="disabled")
        columns = max(20, int(body.cget("width")))
        body.configure(height=estimate_text_rows(text, columns))
        if card.body_host is not None:
            if text and card.expanded:
                card.body_host.grid()
            elif not text:
                card.body_host.grid_remove()
        self.after_idle(self._refresh_scrollregion)

    def _finish_streaming_cards(self) -> None:
        for card in self._cards.values():
            if card.kind in {"reasoning", "activity", "tool"} and card.active:
                self._set_progress_status(card, "Done", "done")
                if card.kind == "reasoning" and card.expanded:
                    self._toggle_progress(card, desired=False)

    def set_busy(self, busy: bool, status_text: str = "") -> None:
        """Update controls on the UI thread; worker calls are safely queued."""

        if threading.get_ident() != self._ui_thread_id:
            self.post_event(
                AssistantEvent(
                    "busy",
                    text=status_text,
                    status="busy" if busy else "ready",
                )
            )
            return
        self._busy = bool(busy)
        if self._busy:
            self.send_button.grid_remove()
            self.stop_button.grid()
            self.stop_button.configure(state="normal")
            self.composer_status_var.set(status_text or "Working…")
        else:
            self.stop_button.grid_remove()
            self.send_button.grid()
            self.send_button.configure(state="normal")
            self.composer_status_var.set(
                status_text or "Enter to send · Shift+Enter for a new line"
            )
        self._sync_composer_state()

    def set_connection(self, state: str, label: str = "") -> None:
        """Show connection state without treating it as booking authority."""

        if threading.get_ident() != self._ui_thread_id:
            self.post_event(
                AssistantEvent("connection", text=label, status=state)
            )
            return
        normalized = (state or "unknown").strip().casefold()
        if normalized in {"connected", "ready", "online"}:
            color = self.palette["success"]
            default_label = "Connected"
        elif normalized in {"connecting", "starting", "reconnecting"}:
            color = self.palette["warning"]
            default_label = "Connecting…"
        elif normalized in {"error", "failed", "offline", "disconnected", "stopped"}:
            color = self.palette["danger"]
            default_label = "Offline"
        else:
            color = self.palette["tertiary_text"]
            default_label = "Unavailable"
        self.connection_dot.configure(foreground=color)
        self.connection_var.set(label.strip() or default_label)

    def _submit_composer(self) -> None:
        text = self.composer.get("1.0", "end-1c")
        self._submit_text(text)

    def _submit_text(self, text: str) -> bool:
        prompt = text.strip()
        if not prompt or self._busy:
            self.bell()
            return False
        self.set_busy(True, "Starting…")
        try:
            accepted = True if self.on_send is None else self.on_send(prompt)
        except Exception as exc:
            self.apply_event(
                AssistantEvent(
                    "error",
                    text=f"The request could not be started: {exc}",
                    title="Could not start assistant",
                )
            )
            return False
        if accepted is False:
            # A rejected prompt may contain a password or verification code.
            # Remove it from the composer and never mirror it into a chat card.
            self.composer.delete("1.0", "end")
            self._sync_composer_state()
            self.apply_event(
                AssistantEvent(
                    "error",
                    text="The assistant did not accept this request.",
                    title="Request not started",
                )
            )
            return False
        self.apply_event(AssistantEvent("user_message", prompt))
        self.composer.delete("1.0", "end")
        self._sync_composer_state()
        return True

    def _request_stop(self) -> None:
        if not self._busy:
            return
        self.stop_button.configure(state="disabled")
        self.composer_status_var.set("Stopping…")
        try:
            accepted = True if self.on_stop is None else self.on_stop()
        except Exception as exc:
            self.apply_event(
                AssistantEvent(
                    "error",
                    text=f"The assistant could not be stopped cleanly: {exc}",
                    title="Stop failed",
                )
            )
            return
        if accepted is False:
            self.stop_button.configure(state="normal")
            self.composer_status_var.set("Still working…")

    def _request_new_chat(self) -> None:
        try:
            accepted = True if self.on_new_chat is None else self.on_new_chat()
        except Exception as exc:
            self.apply_event(
                AssistantEvent(
                    "error",
                    text=f"A new chat could not be started: {exc}",
                    title="New chat failed",
                )
            )
            return
        if accepted is False:
            return
        self._clear_transcript(show_welcome=True)
        self.set_busy(True, "Starting new chat…")
        self.composer.delete("1.0", "end")
        self._sync_composer_state()
        self.composer.focus_set()

    def _on_composer_return(self, event: tk.Event) -> str | None:
        if composer_return_action(int(getattr(event, "state", 0))) == "newline":
            return None
        self._submit_composer()
        return "break"

    def _on_escape(self, _event: tk.Event) -> str | None:
        if self._busy:
            self._request_stop()
            return "break"
        return None

    def _sync_composer_state(self, _event: tk.Event | None = None) -> None:
        try:
            has_text = bool(self.composer.get("1.0", "end-1c").strip())
        except tk.TclError:
            return
        if has_text or self.composer.focus_get() is self.composer:
            self.placeholder.place_forget()
        elif not self.placeholder.winfo_manager():
            self.placeholder.place(x=15, y=13)
        if not self._busy:
            self.send_button.configure(state="normal" if has_text else "disabled")

    def _on_canvas_configure(self, event: tk.Event) -> None:
        width = responsive_content_width(max(1, int(event.width)))
        self._content_width = width
        self.transcript_canvas.coords(
            self._transcript_window,
            max(0, int(event.width) // 2),
            0,
        )
        self.transcript_canvas.itemconfigure(self._transcript_window, width=width)
        self._sync_composer_width(width)
        self._reflow_cards(width)

    def _sync_composer_width(self, width: int) -> None:
        try:
            self.composer_column.configure(width=width, height=112)
            self.composer_column.grid_propagate(False)
            self.composer_host.configure(width=width)
            self.composer_host.grid_propagate(False)
            self.composer_host.configure(height=86)
        except tk.TclError:
            return

    def _reflow_cards(self, width: int) -> None:
        assistant_columns = max(14, min(96, (width - 90) // 8))
        user_columns = max(12, min(70, int(width * 0.72) // 8))
        for card in self._cards.values():
            columns = user_columns if card.kind == "user" else assistant_columns
            try:
                card.body.configure(
                    width=columns,
                    height=estimate_text_rows(card.text, columns),
                )
            except tk.TclError:
                continue
        if self._welcome_frame is not None:
            for child in self._welcome_frame.winfo_children():
                try:
                    if isinstance(child, tk.Label):
                        child.configure(wraplength=max(260, width - 40))
                except tk.TclError:
                    pass

    def _on_transcript_configure(self, _event: tk.Event) -> None:
        self._refresh_scrollregion()

    def _refresh_scrollregion(self) -> None:
        try:
            self.transcript_canvas.configure(
                scrollregion=self.transcript_canvas.bbox("all")
            )
        except tk.TclError:
            pass

    def _is_at_bottom(self) -> bool:
        try:
            _first, last = self.transcript_canvas.yview()
            return last >= 0.985
        except tk.TclError:
            return True

    def _scroll_to_bottom(self) -> None:
        try:
            self._refresh_scrollregion()
            self.transcript_canvas.yview_moveto(1.0)
        except tk.TclError:
            pass

    def _bind_mousewheel(self, widget: tk.Misc) -> None:
        widget.bind("<MouseWheel>", self._on_mousewheel, add="+")

    def _on_mousewheel(self, event: tk.Event) -> str:
        delta = int(getattr(event, "delta", 0))
        if delta:
            units = -max(1, abs(delta) // 120) if delta > 0 else max(1, abs(delta) // 120)
            self.transcript_canvas.yview_scroll(units, "units")
        return "break"

    def _scroll_transcript_page(self, direction: int) -> str:
        self.transcript_canvas.yview_scroll(direction, "pages")
        return "break"

    def _move_transcript_to(self, fraction: float) -> str:
        self.transcript_canvas.yview_moveto(fraction)
        return "break"

    def focus_composer(self) -> None:
        if not self._closed:
            self.composer.focus_set()

    def close(self) -> None:
        """Stop the event pump before the containing application is destroyed."""

        if self._closed:
            return
        self._closed = True
        self._event_buffer.clear()
        if self._event_after_id is not None:
            try:
                self.after_cancel(self._event_after_id)
            except (RuntimeError, tk.TclError):
                pass
            self._event_after_id = None

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            self.close()


__all__ = [
    "AssistantEvent",
    "AssistantEventBuffer",
    "AssistantPanel",
    "DEFAULT_PALETTE",
    "DEFAULT_STARTER_PROMPTS",
    "composer_return_action",
    "estimate_text_rows",
    "normalize_assistant_event",
    "responsive_content_width",
]
