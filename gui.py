"""AsimutBooker desktop control panel.

The control panel is intentionally a thin client.  It edits the canonical YAML
configuration, reads operational state from SQLite (with legacy JSON fallbacks),
manages the single Windows scheduled task, and launches the canonical CLI.
It does not contain browser selectors or scrape Asimut itself.
"""

from __future__ import annotations

import copy
import csv
import importlib.util
import json
import os
import queue
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

try:
    import yaml
except ImportError:  # pragma: no cover - displayed as a configuration health error
    yaml = None


APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "config" / "config.yaml"
DATA_DIR = APP_DIR / "data"
STATE_FILE = DATA_DIR / "browser_state" / "state.json"
LEGACY_SETTINGS_FILE = DATA_DIR / "settings.json"
LEGACY_HISTORY_FILE = DATA_DIR / "booking_history.json"
DEFAULT_DATABASE_FILE = DATA_DIR / "asimut_booker.sqlite3"
LOGS_DIR = APP_DIR / "logs"
SETUP_SCRIPT = APP_DIR / "setup_scheduled_tasks.ps1"
TASK_NAME = "AsimutBooker"
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
END_TIME_RE = re.compile(r"^(?:(?:[01]\d|2[0-3]):[0-5]\d|24:00)$")
ROOM_RE = re.compile(r"^[A-Za-z]\d+\.\d{1,3}$")


def _creation_flags(new_process_group: bool = False) -> int:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if new_process_group:
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return flags


def _record_to_dict(record: Any) -> dict[str, Any]:
    if record is None:
        return {}
    if isinstance(record, dict):
        return dict(record)
    if isinstance(record, sqlite3.Row):
        return dict(record)
    if is_dataclass(record):
        return asdict(record)
    if hasattr(record, "_asdict"):
        return dict(record._asdict())
    if hasattr(record, "__dict__"):
        return {
            key: value
            for key, value in vars(record).items()
            if not key.startswith("_")
        }
    return {"value": record}


def _first(mapping: dict[str, Any], *names: str, default: Any = "") -> Any:
    for name in names:
        value = mapping.get(name)
        if value is not None:
            return value
    return default


def _format_timestamp(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return text[:19].replace("T", " ")


def _human_age(path: Path) -> str:
    if not path.exists():
        return "missing"
    seconds = max(0, time.time() - path.stat().st_mtime)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} hr ago"
    return f"{int(seconds // 86400)} days ago"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalise_status(value: Any, success_count: int = 0) -> str:
    if value:
        return str(value).replace("_", " ").title()
    return "Succeeded" if success_count else "Completed"


def _minute_of_day_to_text(value: Any) -> str:
    try:
        minute = int(value)
    except (TypeError, ValueError):
        return str(value or "")
    if not 0 <= minute <= 24 * 60:
        return str(value)
    hours, minutes = divmod(minute, 60)
    return f"{hours:02d}:{minutes:02d}"


class ConfigStore:
    """Load and atomically save YAML while preserving unknown keys."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, Any]:
        if yaml is None:
            raise RuntimeError("PyYAML is not installed. Run the project setup first.")
        if not self.path.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.path}")
        with self.path.open("r", encoding="utf-8") as handle:
            value = yaml.safe_load(handle) or {}
        if not isinstance(value, dict):
            raise ValueError("The root of config.yaml must be a mapping.")
        return value

    def save(self, value: dict[str, Any]) -> None:
        if yaml is None:
            raise RuntimeError("PyYAML is not installed.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        serialised = yaml.safe_dump(
            value,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
        backup = self.path.with_suffix(self.path.suffix + ".bak")
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="\n",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(serialised)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            try:
                from asimut_booker.config import AppConfig

                AppConfig.load(temp_path)
            except ImportError:
                # The GUI can still be used to bootstrap an older checkout, but
                # canonical installs always validate through AppConfig.
                pass
            if self.path.exists():
                shutil.copy2(self.path, backup)
            os.replace(temp_path, self.path)
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)


class DataRepository:
    """Read operational state through the package API or SQLite/JSON fallback."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._configured_path = False
        self.db_path = self._discover_database()

    def _discover_database(self) -> Path:
        if DEFAULT_DATABASE_FILE.exists():
            return DEFAULT_DATABASE_FILE
        candidates: list[Path] = []
        for pattern in ("*.sqlite3", "*.sqlite", "*.db"):
            candidates.extend(self.data_dir.glob(pattern))
        return max(candidates, key=lambda item: item.stat().st_mtime) if candidates else DEFAULT_DATABASE_FILE

    def refresh_path(self) -> None:
        if not self._configured_path:
            self.db_path = self._discover_database()

    def configure_path(self, raw_path: Any, project_root: Path) -> None:
        if not raw_path:
            return
        path = Path(str(raw_path)).expanduser()
        self.db_path = path if path.is_absolute() else (project_root / path).resolve()
        self._configured_path = True

    def _package_database(self) -> Any | None:
        if not self.db_path.exists():
            return None
        candidates = (
            ("asimut_booker.database", "Database"),
            ("asimut_booker.persistence", "Database"),
            ("asimut_booker.storage", "Database"),
        )
        for module_name, class_name in candidates:
            try:
                module = __import__(module_name, fromlist=[class_name])
                database_class = getattr(module, class_name)
                return database_class(self.db_path, read_only=True)
            except (ImportError, AttributeError, TypeError, RuntimeError):
                continue
        return None

    def _connect_readonly(self) -> sqlite3.Connection:
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _table_names(connection: sqlite3.Connection) -> set[str]:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {str(row[0]) for row in rows}

    @staticmethod
    def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
        safe_table = table.replace('"', '""')
        return [
            str(row[1])
            for row in connection.execute(f'PRAGMA table_info("{safe_table}")').fetchall()
        ]

    @staticmethod
    def _ordered_rows(
        connection: sqlite3.Connection,
        table: str,
        columns: Iterable[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        column_set = set(columns)
        order_candidates = (
            "started_at",
            "created_at",
            "timestamp",
            "updated_at",
            "scheduled_for",
            "id",
        )
        order_column = next((name for name in order_candidates if name in column_set), None)
        safe_table = table.replace('"', '""')
        order_clause = f' ORDER BY "{order_column}" DESC' if order_column else ""
        rows = connection.execute(
            f'SELECT * FROM "{safe_table}"{order_clause} LIMIT ?',
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        database = self._package_database()
        if database is not None and hasattr(database, "list_runs"):
            try:
                return [_record_to_dict(item) for item in database.list_runs(limit=limit)]
            except Exception:
                pass
            finally:
                if hasattr(database, "close"):
                    database.close()

        if self.db_path.exists():
            try:
                with self._connect_readonly() as connection:
                    tables = self._table_names(connection)
                    table = next(
                        (name for name in ("runs", "booking_runs", "run_history") if name in tables),
                        None,
                    )
                    if table:
                        columns = self._columns(connection, table)
                        return self._ordered_rows(connection, table, columns, limit)
            except sqlite3.Error:
                pass

        if LEGACY_HISTORY_FILE.exists():
            try:
                with LEGACY_HISTORY_FILE.open("r", encoding="utf-8") as handle:
                    return list((json.load(handle) or {}).get("runs", []))[:limit]
            except (OSError, ValueError, TypeError):
                pass
        return []

    def list_extensions(self, limit: int = 200) -> list[dict[str, Any]]:
        database = self._package_database()
        if database is not None and hasattr(database, "list_extensions"):
            try:
                records = database.list_extensions(status=None, limit=limit)
                enriched: list[dict[str, Any]] = []
                for item in records:
                    row = _record_to_dict(item)
                    intent_id = row.get("intent_id")
                    if intent_id and hasattr(database, "get_intent"):
                        try:
                            intent = _record_to_dict(database.get_intent(intent_id))
                            row.setdefault("booking_date", intent.get("requested_date"))
                            row.setdefault("room_id", intent.get("room_id"))
                            row.setdefault(
                                "start_time",
                                _minute_of_day_to_text(intent.get("start_minute")),
                            )
                        except Exception:
                            pass
                    enriched.append(row)
                return enriched
            except Exception:
                pass
            finally:
                if hasattr(database, "close"):
                    database.close()

        if self.db_path.exists():
            try:
                with self._connect_readonly() as connection:
                    tables = self._table_names(connection)
                    table = next(
                        (
                            name
                            for name in ("extensions", "extension_jobs", "extendable_bookings")
                            if name in tables
                        ),
                        None,
                    )
                    if table:
                        columns = self._columns(connection, table)
                        return self._ordered_rows(connection, table, columns, limit)
            except sqlite3.Error:
                pass

        if LEGACY_SETTINGS_FILE.exists():
            try:
                with LEGACY_SETTINGS_FILE.open("r", encoding="utf-8") as handle:
                    settings = json.load(handle) or {}
                rows = list(settings.get("extendable_bookings", []))[:limit]
                for index, row in enumerate(rows):
                    row.setdefault("id", f"legacy-{index}")
                    row.setdefault("status", "legacy")
                return rows
            except (OSError, ValueError, TypeError):
                pass
        return []

    def health(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        database = self._package_database()
        if database is not None and hasattr(database, "get_health"):
            try:
                package_health = database.get_health()
                if isinstance(package_health, dict):
                    for component, record in package_health.items():
                        result[str(component)] = _record_to_dict(record)
            except Exception as exc:
                result["database_api"] = {
                    "status": "warning",
                    "message": f"Health API unavailable: {exc}",
                }
            finally:
                if hasattr(database, "close"):
                    database.close()

        if not self.db_path.exists():
            result.setdefault(
                "database",
                {
                    "status": "pending",
                    "message": f"Not created yet ({self.db_path.name})",
                },
            )
            return result

        try:
            with self._connect_readonly() as connection:
                quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
                journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
                tables = sorted(self._table_names(connection))
            result.setdefault(
                "database",
                {
                    "status": "healthy" if quick_check == "ok" else "error",
                    "message": (
                        f"{quick_check}; {journal_mode} journal; "
                        f"{len(tables)} tables; {self.db_path.stat().st_size / 1024:.0f} KiB"
                    ),
                    "updated_at": datetime.fromtimestamp(self.db_path.stat().st_mtime).isoformat(),
                },
            )
        except sqlite3.Error as exc:
            result["database"] = {"status": "error", "message": str(exc)}
        return result


class AsimutBookerGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AsimutBooker")
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        width = min(1280, max(940, screen_width - 140))
        height = min(860, max(680, screen_height - 140))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(900, 640)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.config_store = ConfigStore(CONFIG_FILE)
        self.repository = DataRepository(DATA_DIR)
        self.config: dict[str, Any] = {}
        self._loading = False
        self._dirty = False
        self._closing = False
        self._active_process: subprocess.Popen[str] | None = None
        self._process_stop_requested = False
        self._workers: set[threading.Thread] = set()
        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._last_diagnostics: dict[str, Any] = {}
        self._room_rows: list[dict[str, Any]] = []
        self._room_config_key = "priority"
        self._window_rows: list[dict[str, Any]] = []
        self._blackout_rows: list[dict[str, Any]] = []
        self._extension_records: dict[str, dict[str, Any]] = {}

        self._configure_styles()
        self._create_layout()
        self.root.after(75, self._drain_ui_queue)
        self.reload_everything(initial=True)

    # ------------------------------------------------------------------ UI setup

    def _configure_styles(self) -> None:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("TButton", padding=(10, 6))
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 10))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 20))
        style.configure("Subtitle.TLabel", foreground="#5f6368")
        style.configure("Metric.TLabel", font=("Segoe UI Semibold", 16))
        style.configure("Good.TLabel", foreground="#137333")
        style.configure("Warn.TLabel", foreground="#b06000")
        style.configure("Bad.TLabel", foreground="#b3261e")
        style.configure("Treeview", rowheight=27)
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))

    def _create_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=(16, 12, 16, 8))
        outer.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(outer)
        header.pack(fill=tk.X, pady=(0, 10))
        title_group = ttk.Frame(header)
        title_group.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(title_group, text="AsimutBooker", style="Title.TLabel").pack(anchor=tk.W)
        self.header_status_var = tk.StringVar(value="Loading configuration and health…")
        ttk.Label(
            title_group,
            textvariable=self.header_status_var,
            style="Subtitle.TLabel",
        ).pack(anchor=tk.W, pady=(1, 0))

        header_actions = ttk.Frame(header)
        header_actions.pack(side=tk.RIGHT)
        self.save_button = ttk.Button(
            header_actions,
            text="Save changes",
            command=self.save_all,
            style="Accent.TButton",
            state=tk.DISABLED,
        )
        self.save_button.pack(side=tk.LEFT, padx=4)
        ttk.Button(header_actions, text="Refresh", command=self.reload_everything).pack(
            side=tk.LEFT, padx=4
        )

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self.dashboard_tab = ttk.Frame(self.notebook, padding=12)
        self.rooms_tab = ttk.Frame(self.notebook, padding=12)
        self.preferences_tab = ttk.Frame(self.notebook, padding=12)
        self.schedule_tab = ttk.Frame(self.notebook, padding=12)
        self.extensions_tab = ttk.Frame(self.notebook, padding=12)
        self.history_tab = ttk.Frame(self.notebook, padding=12)
        self.diagnostics_tab = ttk.Frame(self.notebook, padding=12)
        for frame, title in (
            (self.dashboard_tab, "Dashboard"),
            (self.rooms_tab, "Rooms"),
            (self.preferences_tab, "Preferences"),
            (self.schedule_tab, "Schedule"),
            (self.extensions_tab, "Extensions"),
            (self.history_tab, "History"),
            (self.diagnostics_tab, "Diagnostics"),
        ):
            self.notebook.add(frame, text=title)

        self._build_dashboard_tab()
        self._build_rooms_tab()
        self._build_preferences_tab()
        self._build_schedule_tab()
        self._build_extensions_tab()
        self._build_history_tab()
        self._build_diagnostics_tab()

        status_bar = ttk.Frame(outer)
        status_bar.pack(fill=tk.X, pady=(8, 0))
        self.footer_var = tk.StringVar(value="Ready")
        ttk.Label(status_bar, textvariable=self.footer_var, style="Subtitle.TLabel").pack(
            side=tk.LEFT
        )
        self.unsaved_var = tk.StringVar(value="")
        ttk.Label(status_bar, textvariable=self.unsaved_var, style="Warn.TLabel").pack(
            side=tk.RIGHT
        )

    @staticmethod
    def _section(parent: tk.Widget, title: str) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text=title, padding=10)
        return frame

    @staticmethod
    def _tree(
        parent: tk.Widget,
        columns: list[tuple[str, str, int]],
        *,
        height: int = 10,
        selectmode: str = "browse",
    ) -> tuple[ttk.Frame, ttk.Treeview]:
        frame = ttk.Frame(parent)
        tree = ttk.Treeview(
            frame,
            columns=[item[0] for item in columns],
            show="headings",
            height=height,
            selectmode=selectmode,
        )
        scroll_y = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        scroll_x = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        for key, title, width in columns:
            tree.heading(key, text=title)
            anchor = tk.CENTER if key in {"priority", "horizon", "bookings", "attempts"} else tk.W
            tree.column(key, width=width, minwidth=min(width, 80), anchor=anchor)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return frame, tree

    def _build_dashboard_tab(self) -> None:
        controls = self._section(self.dashboard_tab, "Run controls")
        controls.pack(fill=tk.X, pady=(0, 10))
        self.run_visible_button = ttk.Button(
            controls,
            text="Run with browser",
            command=lambda: self.launch_cli(["run"]),
            style="Accent.TButton",
        )
        self.run_visible_button.pack(side=tk.LEFT, padx=(0, 6))
        self.run_headless_button = ttk.Button(
            controls,
            text="Run headless",
            command=lambda: self.launch_cli(["run", "--headless"]),
        )
        self.run_headless_button.pack(side=tk.LEFT, padx=6)
        self.stop_button = ttk.Button(
            controls,
            text="Stop run",
            command=self.stop_active_process,
            state=tk.DISABLED,
        )
        self.stop_button.pack(side=tk.LEFT, padx=6)
        ttk.Separator(controls, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=10
        )
        self.login_button = ttk.Button(
            controls,
            text="Refresh login",
            command=lambda: self.launch_cli(["login"]),
        )
        self.login_button.pack(side=tk.LEFT, padx=6)
        self.process_status_var = tk.StringVar(value="No CLI process running")
        ttk.Label(controls, textvariable=self.process_status_var).pack(
            side=tk.LEFT, padx=(16, 0)
        )

        metrics = ttk.Frame(self.dashboard_tab)
        metrics.pack(fill=tk.X, pady=(0, 10))
        self.metric_vars: dict[str, tuple[tk.StringVar, tk.StringVar]] = {}
        for index, (key, title) in enumerate(
            (
                ("scheduler", "Scheduler"),
                ("last_run", "Last run"),
                ("extensions", "Pending extensions"),
                ("session", "Login state"),
            )
        ):
            card = self._section(metrics, title)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 5, 5))
            value_var = tk.StringVar(value="—")
            detail_var = tk.StringVar(value="Loading…")
            ttk.Label(card, textvariable=value_var, style="Metric.TLabel").pack(anchor=tk.W)
            ttk.Label(
                card,
                textvariable=detail_var,
                style="Subtitle.TLabel",
                wraplength=220,
            ).pack(anchor=tk.W, pady=(3, 0))
            self.metric_vars[key] = (value_var, detail_var)
            metrics.columnconfigure(index, weight=1)

        output = self._section(self.dashboard_tab, "Live output")
        output.pack(fill=tk.BOTH, expand=True)
        self.output_text = scrolledtext.ScrolledText(
            output,
            height=13,
            wrap=tk.WORD,
            font=("Cascadia Mono", 9),
            state=tk.DISABLED,
        )
        self.output_text.pack(fill=tk.BOTH, expand=True)
        self.output_text.tag_configure("info", foreground="#174ea6")
        self.output_text.tag_configure("success", foreground="#137333")
        self.output_text.tag_configure("warning", foreground="#b06000")
        self.output_text.tag_configure("error", foreground="#b3261e")
        output_actions = ttk.Frame(output)
        output_actions.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(output_actions, text="Clear", command=self.clear_output).pack(side=tk.LEFT)
        ttk.Button(
            output_actions,
            text="Open logs",
            command=lambda: self._open_path(LOGS_DIR),
        ).pack(side=tk.LEFT, padx=6)

    def _build_rooms_tab(self) -> None:
        intro = ttk.Frame(self.rooms_tab)
        intro.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(
            intro,
            text="Rooms are tried from top to bottom. Horizon is the verified advance-booking window.",
            style="Subtitle.TLabel",
        ).pack(side=tk.LEFT)

        tree_frame, self.rooms_tree = self._tree(
            self.rooms_tab,
            [
                ("priority", "#", 55),
                ("room", "Room", 180),
                ("horizon", "Horizon (days)", 130),
                ("enabled", "Enabled", 90),
            ],
            height=16,
            selectmode="extended",
        )
        tree_frame.pack(fill=tk.BOTH, expand=True)
        self.rooms_tree.bind("<Double-1>", lambda _event: self.edit_room())

        actions = ttk.Frame(self.rooms_tab)
        actions.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(actions, text="Add room", command=self.add_room).pack(side=tk.LEFT)
        ttk.Button(actions, text="Edit", command=self.edit_room).pack(side=tk.LEFT, padx=5)
        ttk.Button(actions, text="Remove", command=self.remove_rooms).pack(side=tk.LEFT, padx=5)
        ttk.Button(actions, text="Move up", command=lambda: self.move_room(-1)).pack(
            side=tk.LEFT, padx=(18, 5)
        )
        ttk.Button(actions, text="Move down", command=lambda: self.move_room(1)).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Label(actions, text="Excluded rooms:").pack(side=tk.LEFT, padx=(28, 6))
        self.excluded_rooms_var = tk.StringVar()
        excluded_entry = ttk.Entry(actions, textvariable=self.excluded_rooms_var, width=30)
        excluded_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.fallback_var = tk.BooleanVar()
        ttk.Checkbutton(
            actions,
            text="Unlisted fallback disabled (explicit horizons required)",
            variable=self.fallback_var,
            command=self.mark_dirty,
            state=tk.DISABLED,
        ).pack(side=tk.RIGHT, padx=(12, 0))
        self.excluded_rooms_var.trace_add("write", self._trace_dirty)

    def _build_preferences_tab(self) -> None:
        pane = ttk.Panedwindow(self.preferences_tab, orient=tk.VERTICAL)
        pane.pack(fill=tk.BOTH, expand=True)

        windows_section = self._section(pane, "Preferred booking windows")
        pane.add(windows_section, weight=3)
        tree_frame, self.windows_tree = self._tree(
            windows_section,
            [
                ("day", "Day", 160),
                ("start", "Start", 100),
                ("end", "End", 100),
            ],
            height=8,
            selectmode="extended",
        )
        tree_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        window_actions = ttk.Frame(windows_section)
        window_actions.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        ttk.Button(window_actions, text="Add", command=self.add_window).pack(fill=tk.X)
        ttk.Button(window_actions, text="Edit", command=self.edit_window).pack(
            fill=tk.X, pady=5
        )
        ttk.Button(window_actions, text="Remove", command=self.remove_windows).pack(fill=tk.X)
        self.windows_tree.bind("<Double-1>", lambda _event: self.edit_window())

        lower = ttk.Frame(pane)
        pane.add(lower, weight=2)
        rules = self._section(lower, "Booking rules")
        rules.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5), pady=(8, 0))
        self.target_duration_var = tk.IntVar(value=120)
        self.min_duration_var = tk.IntVar(value=30)
        self.max_duration_var = tk.IntVar(value=120)
        self.max_per_run_var = tk.IntVar(value=5)
        self.strict_windows_var = tk.BooleanVar(value=False)
        fields = (
            ("Target duration (minutes)", self.target_duration_var, 15, 240, 15),
            ("Minimum duration", self.min_duration_var, 15, 240, 15),
            ("Maximum duration", self.max_duration_var, 15, 360, 15),
            ("Maximum new bookings per run", self.max_per_run_var, 1, 30, 1),
        )
        for row, (label, variable, lower_bound, upper_bound, increment) in enumerate(fields):
            ttk.Label(rules, text=label).grid(row=row, column=0, sticky=tk.W, pady=4)
            spin = ttk.Spinbox(
                rules,
                from_=lower_bound,
                to=upper_bound,
                increment=increment,
                textvariable=variable,
                width=8,
                command=self.mark_dirty,
            )
            spin.grid(row=row, column=1, sticky=tk.W, padx=(12, 0), pady=4)
            variable.trace_add("write", self._trace_dirty)
        ttk.Checkbutton(
            rules,
            text="Only book inside preferred windows",
            variable=self.strict_windows_var,
            command=self.mark_dirty,
        ).grid(row=len(fields), column=0, columnspan=2, sticky=tk.W, pady=(8, 2))

        blackouts = self._section(lower, "Blackout dates")
        blackouts.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0), pady=(8, 0))
        blackout_frame, self.blackouts_tree = self._tree(
            blackouts,
            [("date", "Date", 160)],
            height=7,
            selectmode="extended",
        )
        blackout_frame.pack(fill=tk.BOTH, expand=True)
        blackout_actions = ttk.Frame(blackouts)
        blackout_actions.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(blackout_actions, text="Add", command=self.add_blackout).pack(side=tk.LEFT)
        ttk.Button(blackout_actions, text="Edit", command=self.edit_blackout).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(
            blackout_actions,
            text="Remove",
            command=self.remove_blackouts,
        ).pack(side=tk.LEFT)
        self.blackouts_tree.bind("<Double-1>", lambda _event: self.edit_blackout())

    def _build_schedule_tab(self) -> None:
        layout = ttk.Frame(self.schedule_tab)
        layout.pack(fill=tk.BOTH, expand=True)
        settings = self._section(layout, "Automatic booking schedule")
        settings.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))

        self.schedule_enabled_var = tk.BooleanVar(value=True)
        self.schedule_start_var = tk.StringVar(value="07:30")
        self.schedule_end_var = tk.StringVar(value="22:15")
        self.schedule_interval_var = tk.IntVar(value=15)
        self.schedule_lead_var = tk.IntVar(value=120)
        self.schedule_lateness_var = tk.IntVar(value=180)
        self.schedule_timeout_var = tk.IntVar(value=12)
        ttk.Checkbutton(
            settings,
            text="Enable automatic scheduled runs",
            variable=self.schedule_enabled_var,
            command=self.mark_dirty,
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 12))
        schedule_fields: list[tuple[str, tk.Variable, str]] = [
            ("First target time", self.schedule_start_var, "entry"),
            ("Last target time", self.schedule_end_var, "entry"),
            ("Interval (minutes)", self.schedule_interval_var, "interval"),
            ("Start this many seconds early", self.schedule_lead_var, "lead"),
            ("Skip jobs later than (seconds)", self.schedule_lateness_var, "lateness"),
            ("Maximum run time (minutes)", self.schedule_timeout_var, "timeout"),
        ]
        for row, (label, variable, field_type) in enumerate(schedule_fields, start=1):
            ttk.Label(settings, text=label).grid(row=row, column=0, sticky=tk.W, pady=6)
            if field_type == "interval":
                widget = ttk.Combobox(
                    settings,
                    textvariable=variable,
                    values=(15,),
                    state="readonly",
                    width=12,
                )
            elif field_type == "entry":
                widget = ttk.Entry(settings, textvariable=variable, width=15)
            else:
                ranges = {
                    "lead": (0, 600, 15),
                    "lateness": (0, 3600, 15),
                    "timeout": (5, 120, 5),
                }
                start, end, increment = ranges[field_type]
                widget = ttk.Spinbox(
                    settings,
                    from_=start,
                    to=end,
                    increment=increment,
                    textvariable=variable,
                    width=12,
                    command=self.mark_dirty,
                )
            widget.grid(row=row, column=1, sticky=tk.W, padx=(16, 0), pady=6)
            variable.trace_add("write", self._trace_dirty)
        ttk.Label(
            settings,
            text=(
                "One Windows task repeats every 15 minutes. Overlapping launches are ignored; "
                "missed snipes outside the lateness limit are not replayed."
            ),
            style="Subtitle.TLabel",
            wraplength=500,
        ).grid(row=8, column=0, columnspan=2, sticky=tk.W, pady=(14, 0))

        task = self._section(layout, "Windows task")
        task.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0))
        self.task_detail_vars: dict[str, tk.StringVar] = {}
        for row, title in enumerate(("State", "Next run", "Last run", "Last result", "Action")):
            ttk.Label(task, text=f"{title}:").grid(row=row, column=0, sticky="nw", pady=5)
            variable = tk.StringVar(value="Loading…")
            ttk.Label(task, textvariable=variable, wraplength=420).grid(
                row=row, column=1, sticky="nw", padx=(10, 0), pady=5
            )
            self.task_detail_vars[title.lower().replace(" ", "_")] = variable
        task_actions = ttk.Frame(task)
        task_actions.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        ttk.Button(
            task_actions,
            text="Install / update task",
            command=self.install_scheduled_task,
            style="Accent.TButton",
        ).pack(side=tk.LEFT)
        ttk.Button(
            task_actions,
            text="Run task now",
            command=lambda: self.task_action("start"),
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            task_actions,
            text="Enable / disable",
            command=lambda: self.task_action("toggle"),
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            task_actions,
            text="Remove task",
            command=lambda: self.task_action("remove"),
        ).pack(side=tk.LEFT, padx=5)

    def _build_extensions_tab(self) -> None:
        header = ttk.Frame(self.extensions_tab)
        header.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(
            header,
            text="Pending extensions are reconciled against Asimut before every edit.",
            style="Subtitle.TLabel",
        ).pack(side=tk.LEFT)
        ttk.Button(header, text="Refresh", command=self.refresh_extensions).pack(side=tk.RIGHT)
        tree_frame, self.extensions_tree = self._tree(
            self.extensions_tab,
            [
                ("status", "Status", 110),
                ("date", "Date", 105),
                ("room", "Room", 90),
                ("start", "Start", 75),
                ("current", "Current end", 95),
                ("target", "Target end", 90),
                ("next", "Next attempt", 155),
                ("attempts", "Attempts", 75),
                ("error", "Last result", 280),
            ],
            height=16,
            selectmode="extended",
        )
        tree_frame.pack(fill=tk.BOTH, expand=True)
        actions = ttk.Frame(self.extensions_tab)
        actions.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(
            actions,
            text="Reconcile selected",
            command=lambda: self.extension_action("reconcile"),
        ).pack(side=tk.LEFT)
        ttk.Button(
            actions,
            text="Retry selected",
            command=lambda: self.extension_action("retry"),
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            actions,
            text="Cancel selected",
            command=lambda: self.extension_action("cancel"),
        ).pack(side=tk.LEFT, padx=5)
        self.extension_summary_var = tk.StringVar()
        ttk.Label(actions, textvariable=self.extension_summary_var).pack(side=tk.RIGHT)

    def _build_history_tab(self) -> None:
        header = ttk.Frame(self.history_tab)
        header.pack(fill=tk.X, pady=(0, 8))
        self.history_summary_var = tk.StringVar(value="")
        ttk.Label(header, textvariable=self.history_summary_var).pack(side=tk.LEFT)
        ttk.Button(header, text="Refresh", command=self.refresh_history).pack(side=tk.RIGHT)
        tree_frame, self.history_tree = self._tree(
            self.history_tab,
            [
                ("time", "Started", 155),
                ("status", "Outcome", 115),
                ("bookings", "Bookings", 85),
                ("attempts", "Attempts", 80),
                ("duration", "Duration", 90),
                ("mode", "Mode", 100),
                ("details", "Details", 430),
            ],
            height=17,
        )
        tree_frame.pack(fill=tk.BOTH, expand=True)
        actions = ttk.Frame(self.history_tab)
        actions.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(actions, text="Open logs", command=lambda: self._open_path(LOGS_DIR)).pack(
            side=tk.LEFT
        )
        ttk.Button(actions, text="Export CSV", command=self.export_history).pack(
            side=tk.LEFT, padx=5
        )

    def _build_diagnostics_tab(self) -> None:
        actions = ttk.Frame(self.diagnostics_tab)
        actions.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(
            actions,
            text="Run canonical doctor",
            command=lambda: self.launch_cli(["doctor", "--json"]),
        ).pack(side=tk.LEFT)
        ttk.Button(actions, text="Refresh health", command=self.refresh_health).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(
            actions,
            text="Export diagnostics",
            command=self.export_diagnostics,
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            actions,
            text="Open config",
            command=lambda: self._open_path(CONFIG_FILE),
        ).pack(side=tk.RIGHT)
        health_frame, self.health_tree = self._tree(
            self.diagnostics_tab,
            [
                ("component", "Component", 180),
                ("status", "Status", 110),
                ("updated", "Last update", 160),
                ("details", "Details", 560),
            ],
            height=12,
        )
        health_frame.pack(fill=tk.BOTH, expand=True)
        info = self._section(self.diagnostics_tab, "Environment")
        info.pack(fill=tk.X, pady=(10, 0))
        self.environment_var = tk.StringVar(value="")
        ttk.Label(info, textvariable=self.environment_var, wraplength=1050).pack(anchor=tk.W)

    # --------------------------------------------------------------- threading/log

    def _post(self, callback: Callable[[], None]) -> None:
        if not self._closing:
            self._ui_queue.put(callback)

    def _drain_ui_queue(self) -> None:
        if self._closing:
            return
        try:
            while True:
                callback = self._ui_queue.get_nowait()
                try:
                    callback()
                except tk.TclError:
                    if not self._closing:
                        raise
        except queue.Empty:
            pass
        self.root.after(75, self._drain_ui_queue)

    def run_worker(
        self,
        function: Callable[[], Any],
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def worker() -> None:
            try:
                result = function()
            except Exception as exc:
                if on_error:
                    self._post(lambda error=exc: on_error(error))
                else:
                    self._post(lambda error=exc: self.show_error(str(error)))
            else:
                if on_success:
                    self._post(lambda value=result: on_success(value))
            finally:
                self._workers.discard(threading.current_thread())

        thread = threading.Thread(target=worker, daemon=True)
        self._workers.add(thread)
        thread.start()

    def append_output(self, message: str, level: str = "info") -> None:
        if not message:
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.output_text.configure(state=tk.NORMAL)
        self.output_text.insert(tk.END, f"[{timestamp}] {message.rstrip()}\n", level)
        self.output_text.configure(state=tk.DISABLED)
        self.output_text.see(tk.END)

    def clear_output(self) -> None:
        self.output_text.configure(state=tk.NORMAL)
        self.output_text.delete("1.0", tk.END)
        self.output_text.configure(state=tk.DISABLED)

    def show_error(self, message: str, title: str = "AsimutBooker") -> None:
        self.footer_var.set(message)
        self.append_output(message, "error")
        messagebox.showerror(title, message, parent=self.root)

    # ------------------------------------------------------------- configuration

    def reload_everything(self, initial: bool = False) -> None:
        if self._dirty and not initial:
            if not messagebox.askyesno(
                "Discard unsaved changes?",
                "Refreshing will discard unsaved changes. Continue?",
                parent=self.root,
            ):
                return
        try:
            self._loading = True
            self.config = self.config_store.load()
            self._load_config_into_widgets()
            self._set_dirty(False)
            self.footer_var.set(f"Loaded {CONFIG_FILE}")
        except Exception as exc:
            self.config = {}
            self.show_error(f"Could not load configuration: {exc}", "Configuration error")
        finally:
            self._loading = False
        self.refresh_all_status()

    def _load_config_into_widgets(self) -> None:
        self.repository.configure_path(
            (self.config.get("browser", {}) or {}).get(
                "database_path", "data/asimut_booker.sqlite3"
            ),
            APP_DIR,
        )
        rooms = self.config.get("rooms", {}) or {}
        self._room_config_key = "policies" if "policies" in rooms else "priority"
        priority = rooms.get(self._room_config_key, []) or []
        self._room_rows = []
        for entry in priority:
            if isinstance(entry, str):
                self._room_rows.append(
                    {
                        "room": entry.strip(),
                        "horizon": _safe_int(
                            self.config.get("booking", {}).get("max_horizon_days", 7),
                            7,
                        ),
                        "enabled": True,
                        "_raw": {},
                    }
                )
            elif isinstance(entry, dict) and (entry.get("room") or entry.get("room_id")):
                self._room_rows.append(
                    {
                        "room": str(entry.get("room", entry.get("room_id"))).strip(),
                        "horizon": _safe_int(
                            entry.get("horizon", entry.get("horizon_days")),
                            7,
                        ),
                        "enabled": bool(entry.get("enabled", True)),
                        "_raw": copy.deepcopy(entry),
                    }
                )
        self.excluded_rooms_var.set(", ".join(str(item) for item in rooms.get("exclude", [])))
        self.fallback_var.set(False)
        self._render_rooms()

        booking = self.config.get("booking", {}) or {}
        rules = self.config.get("rules", {}) or {}
        duration_rules = rules.get("duration", {}) or {}
        target_minutes = booking.get("default_duration_minutes")
        if target_minutes is None:
            target_minutes = _safe_int(booking.get("duration_hours"), 2) * 60
        self.target_duration_var.set(_safe_int(target_minutes, 120))
        self.min_duration_var.set(
            _safe_int(
                duration_rules.get(
                    "minimum_minutes", duration_rules.get("min_minutes")
                ),
                30,
            )
        )
        self.max_duration_var.set(
            _safe_int(
                duration_rules.get(
                    "maximum_minutes", duration_rules.get("max_minutes")
                ),
                120,
            )
        )
        self.max_per_run_var.set(_safe_int(booking.get("max_bookings_per_run"), 5))
        self.strict_windows_var.set(
            bool(
                booking.get(
                    "strict_preferences",
                    booking.get("strict_mode", booking.get("strict", False)),
                )
            )
        )
        self._window_rows = self._read_day_windows(booking)
        self._blackout_rows = self._read_blackouts(
            booking,
            self.config.get("disabled_dates"),
        )
        self._render_windows()
        self._render_blackouts()

        schedule = self.config.get("schedule", {}) or {}
        self.schedule_enabled_var.set(bool(schedule.get("enabled", True)))
        self.schedule_start_var.set(str(schedule.get("active_start", "07:15")))
        self.schedule_end_var.set(str(schedule.get("active_end", "22:00")))
        self.schedule_interval_var.set(15)
        self.schedule_lead_var.set(_safe_int(schedule.get("lead_seconds"), 120))
        self.schedule_lateness_var.set(_safe_int(schedule.get("max_lateness_seconds"), 180))
        self.schedule_timeout_var.set(_safe_int(schedule.get("timeout_minutes"), 25))

    def _read_day_windows(self, booking: dict[str, Any]) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        day_windows = booking.get("preferred_windows")
        if isinstance(day_windows, dict):
            for day in DAY_NAMES:
                entries = day_windows.get(day.lower(), day_windows.get(day, [])) or []
                for entry in entries:
                    if isinstance(entry, dict):
                        result.append(
                            {
                                "day": day,
                                "start": str(entry.get("start", "")),
                                "end": str(entry.get("end", "")),
                            }
                        )
            return result

        preferred_times = booking.get("preferred_times", []) or []
        preferred_days = booking.get("preferred_days", []) or list(range(7))
        for day_index in preferred_days:
            if not isinstance(day_index, int) or not 0 <= day_index <= 6:
                continue
            for entry in preferred_times:
                if isinstance(entry, dict):
                    result.append(
                        {
                            "day": DAY_NAMES[day_index],
                            "start": str(entry.get("start", "")),
                            "end": str(entry.get("end", "")),
                        }
                    )
        return result

    @staticmethod
    def _read_blackouts(
        booking: dict[str, Any],
        top_level_disabled: Any = None,
    ) -> list[dict[str, str]]:
        raw = (
            top_level_disabled
            if top_level_disabled is not None
            else booking.get("disabled_dates", [])
        ) or []
        result: list[dict[str, str]] = []
        for item in raw:
            if isinstance(item, str):
                result.append({"date": item})
        return sorted(result, key=lambda item: item["date"])

    def _collect_config_from_widgets(self) -> dict[str, Any]:
        errors = self._validate_config_widgets()
        if errors:
            raise ValueError("\n".join(f"• {error}" for error in errors))
        result = copy.deepcopy(self.config)
        rooms = result.setdefault("rooms", {})
        room_policies: list[dict[str, Any]] = []
        for index, row in enumerate(self._room_rows):
            policy = copy.deepcopy(row.get("_raw", {}))
            if self._room_config_key == "policies":
                policy.pop("room", None)
                policy.pop("horizon", None)
                policy["room_id"] = row["room"]
                policy["horizon_days"] = int(row["horizon"])
            else:
                policy.pop("room_id", None)
                policy.pop("horizon_days", None)
                policy["room"] = row["room"]
                policy["horizon"] = int(row["horizon"])
            policy["priority"] = index
            policy["enabled"] = bool(row.get("enabled", True))
            room_policies.append(policy)
        rooms.pop("policies" if self._room_config_key == "priority" else "priority", None)
        rooms[self._room_config_key] = room_policies
        rooms["exclude"] = [
            part.strip()
            for part in self.excluded_rooms_var.get().split(",")
            if part.strip()
        ]
        rooms["fallback"] = False

        booking = result.setdefault("booking", {})
        for obsolete_key in (
            "target_duration_minutes",
            "strict_time_windows",
            "day_windows",
            "blackouts",
            "blackout_dates",
        ):
            booking.pop(obsolete_key, None)
        booking.pop("duration_hours", None)
        booking["default_duration_minutes"] = self.target_duration_var.get()
        booking["max_bookings_per_run"] = self.max_per_run_var.get()
        for obsolete_key in ("strict", "strict_mode"):
            booking.pop(obsolete_key, None)
        booking["strict_preferences"] = self.strict_windows_var.get()
        preferred_windows: dict[str, list[dict[str, str]]] = {
            day.lower(): [] for day in DAY_NAMES
        }
        for row in self._window_rows:
            preferred_windows[row["day"].lower()].append(
                {"start": row["start"], "end": row["end"]}
            )
        booking.pop("preferred_times", None)
        booking.pop("preferred_days", None)
        booking["preferred_windows"] = preferred_windows
        booking["disabled_dates"] = [row["date"] for row in self._blackout_rows]
        result.pop("disabled_dates", None)

        rules = result.setdefault("rules", {})
        duration = rules.setdefault("duration", {})
        duration.pop("min_minutes", None)
        duration.pop("max_minutes", None)
        duration["minimum_minutes"] = self.min_duration_var.get()
        duration["maximum_minutes"] = self.max_duration_var.get()

        schedule = result.setdefault("schedule", {})
        schedule.pop("interval_minutes", None)
        schedule.pop("lead_minutes", None)
        schedule["enabled"] = self.schedule_enabled_var.get()
        schedule["active_start"] = self.schedule_start_var.get().strip()
        schedule["active_end"] = self.schedule_end_var.get().strip()
        schedule.pop("check_interval_hours", None)
        schedule["cadence_minutes"] = 15
        schedule["lead_seconds"] = self.schedule_lead_var.get()
        schedule["max_lateness_seconds"] = self.schedule_lateness_var.get()
        schedule["timeout_minutes"] = self.schedule_timeout_var.get()
        # The canonical coordinator computes due jobs. Remove legacy duplicated run list.
        schedule.pop("run_at_times", None)
        return result

    def _validate_config_widgets(self) -> list[str]:
        errors: list[str] = []
        seen_rooms: set[str] = set()
        for row in self._room_rows:
            room = str(row["room"]).strip()
            if not room:
                errors.append("A room name is blank.")
            elif room.casefold() in seen_rooms:
                errors.append(f"Room {room} appears more than once.")
            else:
                seen_rooms.add(room.casefold())
            if _safe_int(row["horizon"]) not in {3, 5, 7}:
                errors.append(f"Room {room or '?'} has an invalid horizon.")
        excluded = {
            item.strip().casefold()
            for item in self.excluded_rooms_var.get().split(",")
            if item.strip()
        }
        enabled_rooms = {
            str(row["room"]).casefold()
            for row in self._room_rows
            if row.get("enabled", True)
        }
        overlap = excluded.intersection(enabled_rooms)
        if overlap:
            errors.append(
                "Rooms cannot be both prioritised and excluded: "
                + ", ".join(sorted(overlap))
            )
        for row in self._window_rows:
            if not TIME_RE.match(row["start"]) or not TIME_RE.match(row["end"]):
                errors.append(f"{row['day']} has an invalid time window.")
                continue
            if row["end"] <= row["start"]:
                errors.append(f"{row['day']} window must end after it starts.")
        for row in self._blackout_rows:
            try:
                date.fromisoformat(row["date"])
            except ValueError:
                errors.append(f"Blackout date {row['date']} is invalid.")
        if self.min_duration_var.get() > self.max_duration_var.get():
            errors.append("Minimum duration cannot exceed maximum duration.")
        if not (
            self.min_duration_var.get()
            <= self.target_duration_var.get()
            <= self.max_duration_var.get()
        ):
            errors.append("Target duration must be within the configured duration limits.")
        for label, value in (
            ("First target time", self.schedule_start_var.get()),
            ("Last target time", self.schedule_end_var.get()),
        ):
            pattern = END_TIME_RE if label.startswith("Last") else TIME_RE
            if not pattern.match(value.strip()):
                errors.append(f"{label} must use HH:MM.")
        if (
            TIME_RE.match(self.schedule_start_var.get().strip())
            and END_TIME_RE.match(self.schedule_end_var.get().strip())
            and self.schedule_end_var.get().strip() <= self.schedule_start_var.get().strip()
        ):
            errors.append("Last target time must be after first target time.")
        if self.schedule_interval_var.get() != 15:
            errors.append("The reliable scheduler interval must be 15 minutes.")
        return errors

    def save_all(self) -> bool:
        try:
            value = self._collect_config_from_widgets()
            self.config_store.save(value)
            self.config = value
            self._set_dirty(False)
            self.footer_var.set(f"Saved {CONFIG_FILE.name}; previous version is config.yaml.bak")
            self.append_output("Configuration saved atomically.", "success")
            self.refresh_all_status()
            return True
        except Exception as exc:
            self.show_error(f"Could not save configuration:\n{exc}", "Validation error")
            return False

    def _trace_dirty(self, *_args: Any) -> None:
        self.mark_dirty()

    def mark_dirty(self) -> None:
        if not self._loading:
            self._set_dirty(True)

    def _set_dirty(self, value: bool) -> None:
        self._dirty = value
        self.unsaved_var.set("Unsaved changes" if value else "")
        self.save_button.configure(state=tk.NORMAL if value else tk.DISABLED)

    # -------------------------------------------------------------- room editor

    def _render_rooms(self, select_index: int | None = None) -> None:
        self.rooms_tree.delete(*self.rooms_tree.get_children())
        for index, row in enumerate(self._room_rows):
            self.rooms_tree.insert(
                "",
                tk.END,
                iid=f"room-{index}",
                values=(
                    index + 1,
                    row["room"],
                    row["horizon"],
                    "Yes" if row.get("enabled", True) else "No",
                ),
            )
        if select_index is not None and 0 <= select_index < len(self._room_rows):
            iid = f"room-{select_index}"
            self.rooms_tree.selection_set(iid)
            self.rooms_tree.see(iid)

    def _room_dialog(
        self,
        title: str,
        initial: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=18)
        body.pack(fill=tk.BOTH, expand=True)
        room_var = tk.StringVar(value=str((initial or {}).get("room", "")))
        horizon_var = tk.IntVar(value=_safe_int((initial or {}).get("horizon"), 7))
        enabled_var = tk.BooleanVar(value=bool((initial or {}).get("enabled", True)))
        ttk.Label(body, text="Room identifier").grid(row=0, column=0, sticky=tk.W, pady=6)
        room_entry = ttk.Entry(body, textvariable=room_var, width=24)
        room_entry.grid(row=0, column=1, padx=(12, 0), pady=6)
        ttk.Label(body, text="Booking horizon (days)").grid(row=1, column=0, sticky=tk.W, pady=6)
        ttk.Combobox(
            body,
            values=(3, 5, 7),
            textvariable=horizon_var,
            state="readonly",
            width=8,
        ).grid(
            row=1, column=1, sticky=tk.W, padx=(12, 0), pady=6
        )
        ttk.Checkbutton(
            body,
            text="Enabled for automatic booking",
            variable=enabled_var,
        ).grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(8, 2))
        result: dict[str, Any] = {}

        def accept() -> None:
            room = room_var.get().strip()
            if not room:
                messagebox.showwarning("Room required", "Enter a room identifier.", parent=dialog)
                return
            if not ROOM_RE.match(room):
                if not messagebox.askyesno(
                    "Unusual room identifier",
                    f"“{room}” does not match the usual A1.23 format. Keep it?",
                    parent=dialog,
                ):
                    return
            horizon = horizon_var.get()
            if horizon not in {3, 5, 7}:
                messagebox.showwarning(
                    "Invalid horizon",
                    "Verified room horizons are 3, 5, or 7 days.",
                    parent=dialog,
                )
                return
            result.update(room=room, horizon=horizon, enabled=enabled_var.get())
            dialog.destroy()

        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Save", command=accept, style="Accent.TButton").pack(
            side=tk.RIGHT, padx=6
        )
        room_entry.focus_set()
        dialog.bind("<Return>", lambda _event: accept())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        self.root.wait_window(dialog)
        return result or None

    def add_room(self) -> None:
        result = self._room_dialog("Add room")
        if result:
            self._room_rows.append(result)
            self._render_rooms(len(self._room_rows) - 1)
            self.mark_dirty()

    def _selected_room_index(self) -> int | None:
        selection = self.rooms_tree.selection()
        if not selection:
            return None
        return int(selection[0].split("-")[-1])

    def edit_room(self) -> None:
        index = self._selected_room_index()
        if index is None:
            messagebox.showinfo("Select a room", "Select one room to edit.", parent=self.root)
            return
        result = self._room_dialog("Edit room", self._room_rows[index])
        if result:
            result["_raw"] = copy.deepcopy(self._room_rows[index].get("_raw", {}))
            self._room_rows[index] = result
            self._render_rooms(index)
            self.mark_dirty()

    def remove_rooms(self) -> None:
        indices = sorted(
            (int(item.split("-")[-1]) for item in self.rooms_tree.selection()),
            reverse=True,
        )
        if not indices:
            return
        if not messagebox.askyesno(
            "Remove rooms?",
            f"Remove {len(indices)} selected room(s) from the priority list?",
            parent=self.root,
        ):
            return
        for index in indices:
            self._room_rows.pop(index)
        self._render_rooms()
        self.mark_dirty()

    def move_room(self, direction: int) -> None:
        index = self._selected_room_index()
        if index is None:
            return
        target = index + direction
        if not 0 <= target < len(self._room_rows):
            return
        self._room_rows[index], self._room_rows[target] = (
            self._room_rows[target],
            self._room_rows[index],
        )
        self._render_rooms(target)
        self.mark_dirty()

    # ------------------------------------------------------------ windows/blackouts

    def _render_windows(self) -> None:
        self.windows_tree.delete(*self.windows_tree.get_children())
        order = {day: index for index, day in enumerate(DAY_NAMES)}
        self._window_rows.sort(key=lambda item: (order[item["day"]], item["start"], item["end"]))
        for index, row in enumerate(self._window_rows):
            self.windows_tree.insert(
                "", tk.END, iid=f"window-{index}", values=(row["day"], row["start"], row["end"])
            )

    def _window_dialog(
        self,
        title: str,
        initial: dict[str, str] | None = None,
    ) -> dict[str, str] | None:
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=18)
        body.pack()
        day_var = tk.StringVar(value=(initial or {}).get("day", "Monday"))
        start_var = tk.StringVar(value=(initial or {}).get("start", "09:00"))
        end_var = tk.StringVar(value=(initial or {}).get("end", "11:00"))
        choices = [f"{hour:02d}:{minute:02d}" for hour in range(24) for minute in (0, 15, 30, 45)]
        ttk.Label(body, text="Day").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Combobox(
            body, values=DAY_NAMES, textvariable=day_var, state="readonly", width=18
        ).grid(row=0, column=1, padx=(12, 0), pady=5)
        ttk.Label(body, text="Start").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Combobox(body, values=choices, textvariable=start_var, width=10).grid(
            row=1, column=1, sticky=tk.W, padx=(12, 0), pady=5
        )
        ttk.Label(body, text="End").grid(row=2, column=0, sticky=tk.W, pady=5)
        ttk.Combobox(body, values=choices, textvariable=end_var, width=10).grid(
            row=2, column=1, sticky=tk.W, padx=(12, 0), pady=5
        )
        result: dict[str, str] = {}

        def accept() -> None:
            start = start_var.get().strip()
            end = end_var.get().strip()
            if not TIME_RE.match(start) or not TIME_RE.match(end) or end <= start:
                messagebox.showwarning(
                    "Invalid window",
                    "Use HH:MM and make sure the end is after the start.",
                    parent=dialog,
                )
                return
            result.update(day=day_var.get(), start=start, end=end)
            dialog.destroy()

        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Save", command=accept, style="Accent.TButton").pack(
            side=tk.RIGHT, padx=6
        )
        self.root.wait_window(dialog)
        return result or None

    def add_window(self) -> None:
        result = self._window_dialog("Add booking window")
        if result:
            self._window_rows.append(result)
            self._render_windows()
            self.mark_dirty()

    def edit_window(self) -> None:
        selection = self.windows_tree.selection()
        if len(selection) != 1:
            messagebox.showinfo("Select a window", "Select one window to edit.", parent=self.root)
            return
        index = int(selection[0].split("-")[-1])
        result = self._window_dialog("Edit booking window", self._window_rows[index])
        if result:
            self._window_rows[index] = result
            self._render_windows()
            self.mark_dirty()

    def remove_windows(self) -> None:
        indices = sorted(
            (int(item.split("-")[-1]) for item in self.windows_tree.selection()),
            reverse=True,
        )
        for index in indices:
            self._window_rows.pop(index)
        if indices:
            self._render_windows()
            self.mark_dirty()

    def _render_blackouts(self) -> None:
        self.blackouts_tree.delete(*self.blackouts_tree.get_children())
        self._blackout_rows.sort(key=lambda item: item["date"])
        for index, row in enumerate(self._blackout_rows):
            self.blackouts_tree.insert(
                "",
                tk.END,
                iid=f"blackout-{index}",
                values=(row["date"],),
            )

    def _blackout_dialog(
        self,
        title: str,
        initial: dict[str, str] | None = None,
    ) -> dict[str, str] | None:
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=18)
        body.pack()
        date_var = tk.StringVar(value=(initial or {}).get("date", date.today().isoformat()))
        ttk.Label(body, text="Date (YYYY-MM-DD)").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(body, textvariable=date_var, width=18).grid(
            row=0, column=1, padx=(12, 0), pady=5
        )
        result: dict[str, str] = {}

        def accept() -> None:
            try:
                parsed = date.fromisoformat(date_var.get().strip())
            except ValueError:
                messagebox.showwarning(
                    "Invalid date", "Enter the date as YYYY-MM-DD.", parent=dialog
                )
                return
            result.update(date=parsed.isoformat())
            dialog.destroy()

        buttons = ttk.Frame(body)
        buttons.grid(row=1, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Save", command=accept, style="Accent.TButton").pack(
            side=tk.RIGHT, padx=6
        )
        self.root.wait_window(dialog)
        return result or None

    def add_blackout(self) -> None:
        result = self._blackout_dialog("Add blackout date")
        if result:
            self._blackout_rows.append(result)
            self._render_blackouts()
            self.mark_dirty()

    def edit_blackout(self) -> None:
        selection = self.blackouts_tree.selection()
        if len(selection) != 1:
            return
        index = int(selection[0].split("-")[-1])
        result = self._blackout_dialog("Edit blackout date", self._blackout_rows[index])
        if result:
            self._blackout_rows[index] = result
            self._render_blackouts()
            self.mark_dirty()

    def remove_blackouts(self) -> None:
        indices = sorted(
            (int(item.split("-")[-1]) for item in self.blackouts_tree.selection()),
            reverse=True,
        )
        for index in indices:
            self._blackout_rows.pop(index)
        if indices:
            self._render_blackouts()
            self.mark_dirty()

    # --------------------------------------------------------------- CLI process

    def _python_executable(self) -> Path:
        candidates = (
            APP_DIR / ".venv" / "Scripts" / "python.exe",
            APP_DIR / "venv" / "Scripts" / "python.exe",
            Path(sys.executable),
        )
        return next((path for path in candidates if path.exists()), Path(sys.executable))

    def _cli_available(self) -> bool:
        return importlib.util.find_spec("asimut_booker.cli") is not None

    def launch_cli(self, arguments: list[str]) -> None:
        if self._active_process and self._active_process.poll() is None:
            messagebox.showwarning(
                "Run already active",
                "Wait for the active command to finish or stop it first.",
                parent=self.root,
            )
            return
        if self._dirty and not self.save_all():
            return
        command = [
            str(self._python_executable()),
            "-u",
            "-m",
            "asimut_booker.cli",
            *arguments,
        ]
        self._process_stop_requested = False
        self._set_process_controls(True)
        self.append_output(f"Starting: {' '.join(arguments)}", "info")

        def worker() -> None:
            try:
                environment = os.environ.copy()
                environment["PYTHONUTF8"] = "1"
                environment["PYTHONUNBUFFERED"] = "1"
                process = subprocess.Popen(
                    command,
                    cwd=str(APP_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=environment,
                    creationflags=_creation_flags(new_process_group=True),
                )
                self._active_process = process
                self._post(
                    lambda pid=process.pid: self.process_status_var.set(f"Running (PID {pid})")
                )
                assert process.stdout is not None
                for raw_line in process.stdout:
                    line = raw_line.rstrip("\r\n")
                    self._post(lambda value=line: self._handle_cli_line(value))
                return_code = process.wait()
                stopped = self._process_stop_requested
                self._post(
                    lambda code=return_code, was_stopped=stopped: self._on_cli_finished(
                        code, was_stopped
                    )
                )
            except Exception as exc:
                self._post(lambda error=exc: self._on_cli_launch_error(error))
            finally:
                self._workers.discard(threading.current_thread())

        thread = threading.Thread(target=worker, daemon=True)
        self._workers.add(thread)
        thread.start()

    def _handle_cli_line(self, line: str) -> None:
        if not line:
            return
        level = "info"
        message = line
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            lowered = line.lower()
            if "traceback" in lowered or "error" in lowered or "failed" in lowered:
                level = "error"
            elif "warning" in lowered or "skip" in lowered:
                level = "warning"
            elif "success" in lowered or "verified" in lowered:
                level = "success"
        else:
            if isinstance(payload, dict):
                level = str(payload.get("level", payload.get("status", "info"))).lower()
                if level in {"ok", "healthy", "succeeded", "success"}:
                    level = "success"
                elif level in {"warn", "warning", "degraded", "skipped"}:
                    level = "warning"
                elif level in {"failed", "failure", "critical"}:
                    level = "error"
                if level not in {"info", "success", "warning", "error"}:
                    level = "info"
                message = str(
                    payload.get("message")
                    or payload.get("event")
                    or payload.get("detail")
                    or json.dumps(payload, ensure_ascii=False)
                )
                progress = payload.get("progress")
                if progress is not None:
                    self.process_status_var.set(f"{message} ({progress})")
        self.append_output(message, level)

    def _on_cli_finished(self, return_code: int, stopped: bool) -> None:
        self._active_process = None
        self._set_process_controls(False)
        if stopped:
            self.process_status_var.set("Stopped")
            self.append_output("Command stopped by user.", "warning")
        elif return_code == 0:
            self.process_status_var.set("Completed successfully")
            self.append_output("Command completed successfully.", "success")
        else:
            self.process_status_var.set(f"Failed (exit {return_code})")
            self.append_output(f"Command failed with exit code {return_code}.", "error")
        self.refresh_all_status()

    def _on_cli_launch_error(self, error: Exception) -> None:
        self._active_process = None
        self._set_process_controls(False)
        self.process_status_var.set("Could not start")
        self.show_error(
            f"Could not launch the canonical CLI:\n{error}\n\n"
            "Expected module: asimut_booker.cli"
        )

    def _set_process_controls(self, running: bool) -> None:
        state = tk.DISABLED if running else tk.NORMAL
        for button in (
            self.run_visible_button,
            self.run_headless_button,
            self.login_button,
        ):
            button.configure(state=state)
        self.stop_button.configure(state=tk.NORMAL if running else tk.DISABLED)

    def stop_active_process(self) -> None:
        process = self._active_process
        if not process or process.poll() is not None:
            return
        if not messagebox.askyesno(
            "Stop active command?",
            "The coordinator will stop and close its browser. Continue?",
            parent=self.root,
        ):
            return
        self._process_stop_requested = True
        self.process_status_var.set("Stopping…")

        def stop() -> None:
            try:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:  # pragma: no cover - Windows is the supported desktop
                    process.terminate()
                process.wait(timeout=6)
            except (OSError, subprocess.TimeoutExpired):
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        creationflags=_creation_flags(),
                        check=False,
                    )
                else:  # pragma: no cover
                    process.kill()

        self.run_worker(stop)

    # ---------------------------------------------------------- status/history/db

    def refresh_all_status(self) -> None:
        self.refresh_task_status()
        self.refresh_history()
        self.refresh_extensions()
        self.refresh_health()

    def _query_task_status(self) -> dict[str, Any]:
        script = rf"""
$task = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue
if (-not $task) {{
  [pscustomobject]@{{ Exists=$false }} | ConvertTo-Json -Compress
  exit 0
}}
$info = Get-ScheduledTaskInfo -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue
$action = $task.Actions | Select-Object -First 1
[pscustomobject]@{{
  Exists = $true
  State = [string]$task.State
  Enabled = [bool]$task.Settings.Enabled
  NextRun = if ($info.NextRunTime) {{ $info.NextRunTime.ToString('o') }} else {{ '' }}
  LastRun = if ($info.LastRunTime) {{ $info.LastRunTime.ToString('o') }} else {{ '' }}
  LastResult = if ($null -ne $info.LastTaskResult) {{ [int]$info.LastTaskResult }} else {{ $null }}
  MissedRuns = if ($null -ne $info.NumberOfMissedRuns) {{ [int]$info.NumberOfMissedRuns }} else {{ 0 }}
  Action = "$($action.Execute) $($action.Arguments)"
  TriggerCount = @($task.Triggers).Count
  MultipleInstances = [string]$task.Settings.MultipleInstances
}} | ConvertTo-Json -Compress
"""
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=_creation_flags(),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Task Scheduler query failed")
        output = result.stdout.strip()
        return json.loads(output) if output else {"Exists": False}

    def refresh_task_status(self) -> None:
        self.run_worker(
            self._query_task_status,
            self._apply_task_status,
            lambda error: self._apply_task_status({"Exists": False, "Error": str(error)}),
        )

    def _apply_task_status(self, status: dict[str, Any]) -> None:
        exists = bool(status.get("Exists"))
        if not exists:
            state = "Not installed"
            detail = status.get("Error", "Install it from the Schedule tab")
            values = {
                "state": state,
                "next_run": "—",
                "last_run": "—",
                "last_result": "—",
                "action": "—",
            }
        else:
            state = str(status.get("State", "Unknown"))
            last_result = status.get("LastResult")
            result_text = (
                "Success (0)"
                if last_result == 0
                else f"Exit / HRESULT {last_result}"
                if last_result is not None
                else "—"
            )
            values = {
                "state": (
                    f"{state}; {status.get('MultipleInstances', '?')}; "
                    f"{status.get('TriggerCount', '?')} trigger"
                ),
                "next_run": _format_timestamp(status.get("NextRun")) or "—",
                "last_run": _format_timestamp(status.get("LastRun")) or "Never",
                "last_result": result_text,
                "action": str(status.get("Action", "—")),
            }
            detail = values["next_run"]
        for key, variable in self.task_detail_vars.items():
            variable.set(values.get(key, "—"))
        metric, metric_detail = self.metric_vars["scheduler"]
        metric.set(state)
        metric_detail.set(str(detail))
        self.header_status_var.set(
            f"Scheduler: {state}  •  Database: {self.repository.db_path.name}"
        )
        self._last_diagnostics["scheduled_task"] = status

    def refresh_history(self) -> None:
        self.run_worker(
            lambda: self.repository.list_runs(limit=250),
            self._apply_history,
            lambda error: self.append_output(f"History refresh failed: {error}", "warning"),
        )

    def _apply_history(self, records: list[dict[str, Any]]) -> None:
        self.history_tree.delete(*self.history_tree.get_children())
        successful_bookings = 0
        for index, raw in enumerate(records):
            row = _record_to_dict(raw)
            bookings = _safe_int(
                _first(
                    row,
                    "bookings_created",
                    "bookings_made",
                    "successful_bookings",
                    "booking_count",
                    default=0,
                )
            )
            successful_bookings += bookings
            attempts = _safe_int(_first(row, "attempts", "attempt_count", default=0))
            started = _first(row, "started_at", "timestamp", "created_at")
            finished = _first(row, "finished_at", "completed_at")
            duration_text = str(_first(row, "duration_seconds", "duration", default=""))
            if not duration_text and started and finished:
                try:
                    duration = datetime.fromisoformat(str(finished)) - datetime.fromisoformat(
                        str(started)
                    )
                    duration_text = f"{duration.total_seconds():.1f}s"
                except ValueError:
                    pass
            elif duration_text and duration_text.replace(".", "", 1).isdigit():
                duration_text = f"{float(duration_text):.1f}s"
            status = _normalise_status(
                _first(row, "status", "outcome", "result", default=""), bookings
            )
            details = str(
                _first(
                    row,
                    "summary",
                    "details",
                    "error_message",
                    "message",
                    default="",
                )
            )
            self.history_tree.insert(
                "",
                tk.END,
                iid=f"history-{index}",
                values=(
                    _format_timestamp(started),
                    status,
                    bookings,
                    attempts,
                    duration_text,
                    _first(row, "trigger", "mode", default=""),
                    details,
                ),
            )
        self.history_summary_var.set(
            f"{len(records)} recent runs  •  {successful_bookings} verified bookings"
        )
        metric, detail = self.metric_vars["last_run"]
        if records:
            first = _record_to_dict(records[0])
            timestamp = _first(first, "started_at", "timestamp", "created_at")
            status = _normalise_status(
                _first(first, "status", "outcome", default=""),
                _safe_int(_first(first, "bookings_created", "bookings_made", default=0)),
            )
            metric.set(status)
            detail.set(_format_timestamp(timestamp))
        else:
            metric.set("No runs")
            detail.set("The coordinator has not recorded a run")
        self._last_diagnostics["run_count_loaded"] = len(records)

    def refresh_extensions(self) -> None:
        self.run_worker(
            lambda: self.repository.list_extensions(limit=250),
            self._apply_extensions,
            lambda error: self.append_output(f"Extension refresh failed: {error}", "warning"),
        )

    def _apply_extensions(self, records: list[dict[str, Any]]) -> None:
        self.extensions_tree.delete(*self.extensions_tree.get_children())
        self._extension_records.clear()
        pending = 0
        for index, raw in enumerate(records):
            row = _record_to_dict(raw)
            record_id = str(_first(row, "id", "extension_id", default=f"row-{index}"))
            iid = f"extension-{index}"
            self._extension_records[iid] = row
            status = str(_first(row, "status", default="pending"))
            if status.lower() not in {"completed", "cancelled", "expired", "failed_permanent"}:
                pending += 1
            self.extensions_tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(
                    status.replace("_", " ").title(),
                    _first(row, "booking_date", "date"),
                    _first(row, "room_id", "room"),
                    _first(row, "start_time", "startTime"),
                    _minute_of_day_to_text(
                        _first(
                            row,
                            "current_end_minute",
                            "current_end_time",
                            "end_time",
                            "endTime",
                        )
                    ),
                    _minute_of_day_to_text(
                        _first(
                            row,
                            "target_end_minute",
                            "target_end_time",
                            "target_end",
                        )
                    ),
                    _format_timestamp(
                        _first(row, "next_attempt_at", "due_at", "scheduled_for")
                    ),
                    _safe_int(_first(row, "attempt_count", "attempts", default=0)),
                    _first(
                        row,
                        "last_error_message",
                        "last_error",
                        "message",
                        "event_key",
                        default="",
                    ),
                ),
            )
        self.extension_summary_var.set(f"{pending} pending  •  {len(records)} total")
        metric, detail = self.metric_vars["extensions"]
        metric.set(str(pending))
        detail.set("Awaiting extension or reconciliation" if pending else "Queue is clear")
        self._last_diagnostics["extension_count_loaded"] = len(records)

    def refresh_health(self) -> None:
        def gather() -> dict[str, Any]:
            self.repository.refresh_path()
            health = self.repository.health()
            health["configuration"] = self._configuration_health()
            health["session_state"] = self._session_health()
            health["runtime"] = self._runtime_health()
            return health

        self.run_worker(
            gather,
            self._apply_health,
            lambda error: self.append_output(f"Health refresh failed: {error}", "warning"),
        )

    def _configuration_health(self) -> dict[str, Any]:
        try:
            from asimut_booker.config import AppConfig

            value = AppConfig.load(CONFIG_FILE)
            return {
                "status": "healthy",
                "message": (
                    f"Typed configuration valid; {len(value.enabled_rooms)} "
                    "explicit room horizons"
                ),
                "updated_at": datetime.fromtimestamp(CONFIG_FILE.stat().st_mtime).isoformat(),
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @staticmethod
    def _session_health() -> dict[str, Any]:
        if not STATE_FILE.exists():
            return {
                "status": "error",
                "message": "No saved browser state. Use Refresh login.",
            }
        try:
            with STATE_FILE.open("r", encoding="utf-8") as handle:
                state = json.load(handle)
            cookies = len(state.get("cookies", [])) if isinstance(state, dict) else 0
            return {
                "status": "unverified",
                "message": (
                    f"State file is {_human_age(STATE_FILE)} with {cookies} cookies. "
                    "Authentication is only confirmed by the canonical health check."
                ),
                "updated_at": datetime.fromtimestamp(STATE_FILE.stat().st_mtime).isoformat(),
            }
        except (OSError, ValueError, TypeError) as exc:
            return {"status": "error", "message": f"Invalid state file: {exc}"}

    def _runtime_health(self) -> dict[str, Any]:
        python = self._python_executable()
        cli_present = self._cli_available()
        return {
            "status": "healthy" if cli_present and ".venv" in str(python) else "warning",
            "message": (
                f"Python {python}; canonical CLI "
                f"{'available' if cli_present else 'not installed'}"
            ),
            "updated_at": datetime.now().isoformat(),
        }

    def _apply_health(self, health: dict[str, Any]) -> None:
        self.health_tree.delete(*self.health_tree.get_children())
        unhealthy = 0
        for index, component in enumerate(sorted(health)):
            record = _record_to_dict(health[component])
            status = str(_first(record, "status", default="unknown"))
            if status.lower() not in {"healthy", "ok", "success", "unverified"}:
                unhealthy += 1
            details = str(
                _first(record, "message", "details", "last_error", default="")
            )
            self.health_tree.insert(
                "",
                tk.END,
                iid=f"health-{index}",
                values=(
                    component.replace("_", " ").title(),
                    status.replace("_", " ").title(),
                    _format_timestamp(_first(record, "updated_at", "checked_at")),
                    details,
                ),
            )
        session = _record_to_dict(health.get("session_state", {}))
        session_status = str(session.get("status", "unknown")).title()
        metric, detail = self.metric_vars["session"]
        metric.set(session_status)
        detail.set(str(session.get("message", ""))[:110])
        self.environment_var.set(
            f"Project: {APP_DIR}    •    Python: {self._python_executable()}    •    "
            f"Database: {self.repository.db_path}    •    Time zone: {datetime.now().astimezone().tzinfo}"
        )
        self._last_diagnostics["health"] = health
        if unhealthy:
            self.footer_var.set(f"Health check completed with {unhealthy} item(s) needing attention")

    # ---------------------------------------------------------- scheduler actions

    def install_scheduled_task(self) -> None:
        if not self.save_all():
            return
        if not SETUP_SCRIPT.exists():
            self.show_error(f"Setup script not found: {SETUP_SCRIPT}")
            return
        arguments = [
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SETUP_SCRIPT),
            "-StartTime",
            self.schedule_start_var.get(),
            "-EndTime",
            self.schedule_end_var.get(),
            "-IntervalMinutes",
            "15",
            "-LeadSeconds",
            str(self.schedule_lead_var.get()),
            "-ExecutionTimeLimitMinutes",
            str(self.schedule_timeout_var.get()),
            "-NoPause",
        ]
        if not self.schedule_enabled_var.get():
            arguments.append("-Disabled")

        def install() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["powershell.exe", *arguments],
                cwd=str(APP_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                creationflags=_creation_flags(),
            )

        self.footer_var.set("Installing the single Windows scheduled task…")

        def complete(result: subprocess.CompletedProcess[str]) -> None:
            output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
            if output:
                self.append_output(output, "success" if result.returncode == 0 else "error")
            if result.returncode == 0:
                self.footer_var.set("Scheduled task installed")
                messagebox.showinfo(
                    "Scheduler ready",
                    "The single AsimutBooker task was installed successfully.",
                    parent=self.root,
                )
            else:
                self.show_error(
                    f"Task installation failed with exit code {result.returncode}.\n\n{output}",
                    "Scheduler setup",
                )
            self.refresh_task_status()

        self.run_worker(install, complete)

    def task_action(self, action: str) -> None:
        if action == "remove" and not messagebox.askyesno(
            "Remove scheduler?",
            "Remove the AsimutBooker Windows scheduled task?",
            parent=self.root,
        ):
            return

        def run_action() -> subprocess.CompletedProcess[str]:
            if action == "start":
                command = f"Start-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction Stop"
            elif action == "remove":
                command = (
                    f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' "
                    "-Confirm:$false -ErrorAction Stop"
                )
            else:
                command = rf"""
$task = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction Stop
if ($task.State -eq 'Disabled') {{
  Enable-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction Stop | Out-Null
}} else {{
  Disable-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction Stop | Out-Null
}}
"""
            return subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    command,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                creationflags=_creation_flags(),
            )

        def complete(result: subprocess.CompletedProcess[str]) -> None:
            if result.returncode != 0:
                self.show_error(result.stderr.strip() or "Task action failed.")
            else:
                self.footer_var.set(f"Task action completed: {action}")
            self.refresh_task_status()

        self.run_worker(run_action, complete)

    # --------------------------------------------------------- extension CLI actions

    def extension_action(self, action: str) -> None:
        selected = self.extensions_tree.selection()
        if not selected:
            messagebox.showinfo(
                "Select an extension",
                "Select one or more extension jobs first.",
                parent=self.root,
            )
            return
        if action == "cancel" and not messagebox.askyesno(
            "Cancel extension jobs?",
            "The existing room reservations will remain; only further extension attempts stop.",
            parent=self.root,
        ):
            return
        identifiers: list[str] = []
        for iid in selected:
            record = self._extension_records.get(iid, {})
            identifier = _first(record, "id", "extension_id")
            if identifier and not str(identifier).startswith("legacy-"):
                identifiers.append(str(identifier))
        if not identifiers:
            messagebox.showinfo(
                "Legacy extension record",
                "This record predates the canonical database. Run a normal reconciliation "
                "after migration rather than editing legacy JSON.",
                parent=self.root,
            )
            return
        arguments = ["extensions", action]
        for identifier in identifiers:
            arguments.extend(["--id", identifier])
        self.launch_cli(arguments)

    # --------------------------------------------------------------- export/helpers

    def export_history(self) -> None:
        filename = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export run history",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile=f"asimut_history_{datetime.now():%Y%m%d_%H%M}.csv",
        )
        if not filename:
            return
        rows = [self.history_tree.item(item, "values") for item in self.history_tree.get_children()]
        try:
            with open(filename, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    ("Started", "Outcome", "Bookings", "Attempts", "Duration", "Mode", "Details")
                )
                writer.writerows(rows)
            self.footer_var.set(f"Exported {len(rows)} runs")
        except OSError as exc:
            self.show_error(f"Could not export history: {exc}")

    def export_diagnostics(self) -> None:
        filename = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export redacted diagnostics",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
            initialfile=f"asimut_diagnostics_{datetime.now():%Y%m%d_%H%M}.json",
        )
        if not filename:
            return
        payload = {
            "generated_at": datetime.now().astimezone().isoformat(),
            "application_directory": str(APP_DIR),
            "python": str(self._python_executable()),
            "python_version": sys.version,
            "database": str(self.repository.db_path),
            "configuration_path": str(CONFIG_FILE),
            "configuration_modified": (
                datetime.fromtimestamp(CONFIG_FILE.stat().st_mtime).isoformat()
                if CONFIG_FILE.exists()
                else None
            ),
            "state_file_present": STATE_FILE.exists(),
            "state_file_age": _human_age(STATE_FILE),
            **self._last_diagnostics,
        }
        try:
            with open(filename, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, indent=2, default=str)
            self.footer_var.set("Redacted diagnostics exported")
        except OSError as exc:
            self.show_error(f"Could not export diagnostics: {exc}")

    @staticmethod
    def _open_path(path: Path) -> None:
        target = path
        if not target.exists() and target.suffix == "":
            target.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            messagebox.showerror("Not found", f"Path does not exist:\n{target}")
            return
        os.startfile(str(target))  # type: ignore[attr-defined]

    # -------------------------------------------------------------------- shutdown

    def on_close(self) -> None:
        if self._dirty:
            answer = messagebox.askyesnocancel(
                "Save changes?",
                "Save configuration changes before closing?",
                parent=self.root,
            )
            if answer is None:
                return
            if answer and not self.save_all():
                return
        process = self._active_process
        if process and process.poll() is None:
            if not messagebox.askyesno(
                "A command is still running",
                "Stop the active command and close AsimutBooker?",
                parent=self.root,
            ):
                return
            self._process_stop_requested = True
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        creationflags=_creation_flags(),
                        timeout=8,
                        check=False,
                    )
                else:  # pragma: no cover
                    process.terminate()
            except (OSError, subprocess.TimeoutExpired):
                pass
        self._closing = True
        self.root.destroy()


def main() -> None:
    if os.name == "nt":
        try:
            from ctypes import windll

            windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    AsimutBookerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
