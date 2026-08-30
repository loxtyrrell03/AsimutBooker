"""AsimutBooker GUI - Desktop control panel for managing practice room bookings."""

import os
import ntpath
import sys
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from pathlib import Path
from datetime import date, datetime, timedelta
import json
import csv
import calendar as cal
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from app_settings import (
    InterProcessFileLock,
    SettingsError,
    atomic_write_json,
    load_settings as strict_load_settings,
    update_settings as atomic_update_settings,
)
from event_identity import (
    IgnoredEventResolution,
    deduplicate_events,
    event_identity_v2,
    is_v2_event_identity_key,
    resolve_ignored_event_keys,
)
from practice_plan import (
    DEFAULT_TARGET_HOURS,
    MAX_TARGET_HOURS,
    MIN_TARGET_HOURS,
    TARGET_STEP_HOURS,
    PracticePlan,
    PracticePlanError,
    load_practice_plan,
)
from health_status import HealthItem, collect_local_health
from room_preferences import (
    BLOCK_STEP_MINUTES,
    MAX_BLOCK_MINUTES,
    MIN_BLOCK_MINUTES,
    RoomPreferences,
    RoomPreferencesError,
    apply_room_preferences_update,
    filter_effective_rooms,
    load_room_preferences as strict_load_room_preferences,
    reconcile_room_preferences,
    room_preferences_to_dict,
    summarize_room_preferences,
)

# Constants
APP_DIR = Path(__file__).resolve().parent
STATE_FILE = APP_DIR / "data" / "browser_state" / "state.json"
LOGS_DIR = APP_DIR / "logs"
CONFIG_FILE = APP_DIR / "config" / "config.yaml"
HISTORY_FILE = APP_DIR / "data" / "booking_history.json"
SETTINGS_FILE = APP_DIR / "data" / "settings.json"
RECURRING_TASK_NAME = "AsimutBooker_Recurring"
RECURRING_TASK_PATH = "\\"
RECURRING_SCHEDULE_TEXT = "Every 15 minutes, 07:13-21:58"
RECURRING_FIRST_RUN_LOCAL = "07:13"
RECURRING_INTERVAL_ISO = "PT15M"
RECURRING_DURATION_ISO = "PT14H46M"
RECURRING_EXECUTION_LIMIT_ISO = "PT14M"
RECURRING_RESTART_INTERVAL_ISO = "PT1M"
ASIMUT_AGENDA_URL = "https://rwcmd.asimut.net/agenda"
ASIMUT_OVERVIEW_URL = "https://rwcmd.asimut.net/overview?locationGroupId=10"

TIME_PREFERENCE_PRESET_KEYS = frozenset(
    {"morning", "afternoon", "evening", "afternoon_evening", "custom"}
)
ROOM_MINIMUM_BLOCK_CHOICES = tuple(
    range(MIN_BLOCK_MINUTES, MAX_BLOCK_MINUTES + 1, BLOCK_STEP_MINUTES)
)
HEALTH_CARD_ORDER = (
    "last_success",
    "next_run",
    "saved_session",
    "auth_cooldown",
    "pending_mutations",
    "physical_wake",
)
HEALTH_STATE_SYMBOLS = {
    "ok": "●",
    "warn": "●",
    "error": "●",
    "unknown": "●",
}
HEALTH_STATE_COLORS = {
    "ok": "#147d34",
    "warn": "#a85d00",
    "error": "#b00020",
    "unknown": "#666666",
}


def is_exact_asimut_scan_url(
    url: str,
    expected_path: str,
    *,
    expected_query: str = "",
) -> bool:
    """Require the exact secure RWCMD Asimut page used by a GUI scan."""

    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and parsed.netloc == "rwcmd.asimut.net"
        and parsed.path == expected_path
        and parsed.query == expected_query
        and not parsed.fragment
    )


def require_authenticated_scan_page(
    page,
    authenticated_checker,
    expected_path: str,
    *,
    expected_query: str = "",
) -> None:
    """Fail closed unless a background scan is on its exact authenticated page."""

    if not authenticated_checker(page) or not is_exact_asimut_scan_url(
        page.url,
        expected_path,
        expected_query=expected_query,
    ):
        expected = expected_path + (f"?{expected_query}" if expected_query else "")
        raise RuntimeError(
            f"Authenticated Asimut scan page verification failed; expected {expected}"
        )


def build_login_check_command(app_dir: Path = APP_DIR) -> list[str]:
    """Build the isolated, non-interactive login health/recovery command."""

    root = Path(app_dir).resolve()
    return [
        str(root / ".venv" / "Scripts" / "python.exe"),
        str(root / "book_week.py"),
        "--headless",
        "--login-only",
    ]


def build_secure_login_update_command(app_dir: Path = APP_DIR) -> list[str]:
    """Build the interactive secure-credential prompt command without secrets."""

    root = Path(app_dir).resolve()
    return [
        str(root / ".venv" / "Scripts" / "python.exe"),
        str(root / "book_week.py"),
        "--configure-autonomous-login",
    ]


def validate_login_command(command: list[str]) -> None:
    """Fail before launch when the isolated runtime or entry point is unavailable."""

    if len(command) < 2:
        raise RuntimeError("Login command is incomplete")
    python_path = Path(command[0])
    script_path = Path(command[1])
    if not python_path.is_file():
        raise RuntimeError(f"AsimutBooker virtual-environment Python is missing: {python_path}")
    if not script_path.is_file():
        raise RuntimeError(f"AsimutBooker entry point is missing: {script_path}")


def build_event_preference_rows(
    cached_events: list[Mapping[str, object]],
    ignored_keys: list[str] | set[str],
) -> tuple[
    list[tuple[Mapping[str, object], str, bool]],
    IgnoredEventResolution,
]:
    """Build collision-safe GUI rows and resolve legacy ignore selections."""

    unique_events = deduplicate_events(cached_events)
    resolution = resolve_ignored_event_keys(ignored_keys, unique_events)
    rows = []
    for event in unique_events:
        key = event_identity_v2(event)
        rows.append((event, key, key not in resolution.ignored_v2_keys))
    return rows, resolution


def ignored_v2_keys_from_selection(selection: Mapping[str, bool]) -> list[str]:
    """Return only deterministic v2 keys for unchecked GUI event rows."""

    ignored = []
    for event_key, enabled in selection.items():
        if not isinstance(enabled, bool):
            raise SettingsError("Event selection values must be true or false")
        if not is_v2_event_identity_key(event_key):
            raise SettingsError("Event selection contains a non-v2 identity key")
        if not enabled:
            ignored.append(event_key)
    return sorted(ignored)


def build_scheduler_setup_command(
    script_path: Path,
    *,
    local_app_data: str | Path | None = None,
) -> list[str]:
    """Build the required headless Agent UAC command for scheduler setup."""
    local_data = local_app_data or os.environ.get("LOCALAPPDATA")
    if not local_data:
        raise RuntimeError("LOCALAPPDATA is not available, so Agent UAC cannot be located")

    agent_uac = Path(local_data) / "AgentUAC" / "agent-uac.exe"
    return [
        str(agent_uac),
        "run",
        "--reason",
        "Install or repair the AsimutBooker recurring scheduled task",
        "--",
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(Path(script_path).resolve()),
    ]


def build_scheduler_remove_command(
    *,
    local_app_data: str | Path | None = None,
) -> list[str]:
    """Build an elevated, idempotent removal command for the recurring task."""
    local_data = local_app_data or os.environ.get("LOCALAPPDATA")
    if not local_data:
        raise RuntimeError("LOCALAPPDATA is not available, so Agent UAC cannot be located")

    agent_uac = Path(local_data) / "AgentUAC" / "agent-uac.exe"
    removal_script = (
        "$ErrorActionPreference = 'Stop'; "
        "$taskName = 'AsimutBooker_Recurring'; $taskPath = '\\'; "
        "$task = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath "
        "-ErrorAction SilentlyContinue; "
        "if ($null -ne $task) { Unregister-ScheduledTask -TaskName $taskName "
        "-TaskPath $taskPath -Confirm:$false }; "
        "$remaining = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath "
        "-ErrorAction SilentlyContinue; "
        "if ($null -ne $remaining) { throw 'The recurring task still exists after removal.' }"
    )
    return [
        str(agent_uac),
        "run",
        "--reason",
        "Remove and verify the AsimutBooker recurring scheduled task",
        "--",
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        removal_script,
    ]


def _same_windows_path(actual: str, expected: str) -> bool:
    """Compare absolute Windows paths without weakening component equality."""

    return ntpath.normcase(ntpath.normpath(actual)) == ntpath.normcase(
        ntpath.normpath(expected)
    )


def _expected_recurring_action(app_dir: Path = APP_DIR) -> tuple[str, str, str]:
    """Return the exact action registered by setup_scheduled_tasks.ps1."""

    root = Path(app_dir).resolve()
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    execute = str(system_root / "System32" / "cmd.exe")
    arguments = f'/d /c ""{root / "run_booker.bat"}" --scheduled"'
    return execute, arguments, str(root)


def parse_recurring_task_status(payload: str) -> dict[str, Any]:
    """Validate the recurring task's complete safety-relevant shape."""
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Task Scheduler returned unreadable status data") from exc

    if not isinstance(value, dict) or value.get("TaskName") != RECURRING_TASK_NAME:
        raise RuntimeError("Task Scheduler returned status for an unexpected task")

    text_fields = (
        "TaskPath",
        "State",
        "NextRunTime",
        "Execute",
        "Arguments",
        "WorkingDirectory",
        "TriggerClass",
        "StartLocalTime",
        "Interval",
        "Duration",
        "MultipleInstances",
        "ExecutionTimeLimit",
        "RestartInterval",
        "PrincipalUserSid",
        "CurrentUserSid",
        "LogonType",
        "RunLevel",
    )
    bool_fields = (
        "Enabled",
        "TriggerEnabled",
        "StopAtDurationEnd",
        "WakeToRun",
        "StartWhenAvailable",
        "RunOnlyIfNetworkAvailable",
        "DisallowStartIfOnBatteries",
        "StopIfGoingOnBatteries",
    )
    if any(not isinstance(value.get(field), str) for field in text_fields) or any(
        not isinstance(value.get(field), bool) for field in bool_fields
    ):
        raise RuntimeError("Task Scheduler returned incomplete status data")
    int_fields = ("ActionCount", "TriggerCount", "DaysInterval", "RestartCount")
    if any(
        isinstance(value.get(field), bool) or not isinstance(value.get(field), int)
        for field in int_fields
    ):
        raise RuntimeError("Task Scheduler returned incomplete task cardinality")

    reasons = []
    state = value["State"]
    next_run = value["NextRunTime"]
    execute = value["Execute"]
    arguments = value["Arguments"]
    expected_execute, expected_arguments, expected_working_directory = (
        _expected_recurring_action()
    )
    if value["TaskPath"] != RECURRING_TASK_PATH:
        reasons.append("wrong task path")
    if value["ActionCount"] != 1:
        reasons.append("wrong action count")
    if value["TriggerCount"] != 1:
        reasons.append("wrong trigger count")
    if state.casefold() not in {"ready", "running"}:
        reasons.append(f"state is {state}")
    if not value["Enabled"]:
        reasons.append("task is disabled")
    if next_run == "Not scheduled":
        reasons.append("no next run")
    if not _same_windows_path(execute, expected_execute):
        reasons.append("wrong executable")
    if arguments != expected_arguments:
        reasons.append("wrong launcher arguments")
    if not _same_windows_path(value["WorkingDirectory"], expected_working_directory):
        reasons.append("wrong working directory")
    if value["TriggerClass"] != "MSFT_TaskDailyTrigger":
        reasons.append("wrong trigger type")
    if not value["TriggerEnabled"]:
        reasons.append("trigger is disabled")
    if value["DaysInterval"] != 1:
        reasons.append("wrong daily interval")
    if value["StartLocalTime"] != RECURRING_FIRST_RUN_LOCAL:
        reasons.append("wrong daily start")
    if (
        value["Interval"] != RECURRING_INTERVAL_ISO
        or value["Duration"] != RECURRING_DURATION_ISO
    ):
        reasons.append("wrong repetition window")
    if value["StopAtDurationEnd"]:
        reasons.append("repetition stops at duration end")
    if not value["WakeToRun"] or not value["StartWhenAvailable"]:
        reasons.append("wake/recovery disabled")
    if not value["RunOnlyIfNetworkAvailable"]:
        reasons.append("network requirement disabled")
    if value["DisallowStartIfOnBatteries"] or value["StopIfGoingOnBatteries"]:
        reasons.append("battery policy drifted")
    if value["MultipleInstances"] != "IgnoreNew":
        reasons.append("overlap protection disabled")
    if value["ExecutionTimeLimit"] != RECURRING_EXECUTION_LIMIT_ISO:
        reasons.append("wrong execution time limit")
    if (
        value["RestartCount"] != 1
        or value["RestartInterval"] != RECURRING_RESTART_INTERVAL_ISO
    ):
        reasons.append("wrong restart policy")
    if value["PrincipalUserSid"] != value["CurrentUserSid"]:
        reasons.append("wrong task principal")
    if value["LogonType"] != "Interactive" or value["RunLevel"] != "Limited":
        reasons.append("wrong principal mode")

    return {
        "task_name": RECURRING_TASK_NAME,
        "state": state,
        "next_run": next_run,
        "healthy": not reasons,
        "problem": "; ".join(reasons),
    }


def query_recurring_task_status(timeout: int = 30) -> dict[str, Any] | None:
    """Return the one supported recurring task, or None when it is absent."""
    command = (
        "$ErrorActionPreference = 'Stop'; "
        "try { $tasks = @(Get-ScheduledTask -TaskName 'AsimutBooker_Recurring' "
        "-TaskPath '\\' -ErrorAction Stop) } catch { "
        "if ($_.CategoryInfo.Category -eq 'ObjectNotFound') { exit 3 }; throw }; "
        "if ($tasks.Count -ne 1) { throw 'Expected exactly one recurring task' }; "
        "$task = $tasks[0]; "
        "$info = Get-ScheduledTaskInfo -TaskName $task.TaskName "
        "-TaskPath $task.TaskPath -ErrorAction Stop; "
        "$nextRun = if ($info.NextRunTime -and $info.NextRunTime.Year -gt 1900) "
        "{ $info.NextRunTime.ToString('yyyy-MM-dd HH:mm') } else { 'Not scheduled' }; "
        "$triggers = @($task.Triggers); $actions = @($task.Actions); "
        "$trigger = $triggers[0]; $action = $actions[0]; "
        "try { $start = [DateTimeOffset]::Parse([string]$trigger.StartBoundary, "
        "[Globalization.CultureInfo]::InvariantCulture); "
        "$startLocal = $start.ToLocalTime().ToString('HH:mm', "
        "[Globalization.CultureInfo]::InvariantCulture) } catch { "
        "throw 'The recurring task has an unreadable start boundary' }; "
        "$principalName = New-Object System.Security.Principal.NTAccount "
        "-ArgumentList ([string]$task.Principal.UserId); "
        "$principalSid = $principalName.Translate("
        "[System.Security.Principal.SecurityIdentifier]).Value; "
        "$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
        "[pscustomobject]@{ TaskName = $task.TaskName; "
        "TaskPath = [string]$task.TaskPath; "
        "ActionCount = $actions.Count; TriggerCount = $triggers.Count; "
        "State = [string]$task.State; "
        "Enabled = [bool]$task.Settings.Enabled; "
        "NextRunTime = $nextRun; Execute = [string]$action.Execute; "
        "Arguments = [string]$action.Arguments; "
        "WorkingDirectory = [string]$action.WorkingDirectory; "
        "TriggerClass = [string]$trigger.CimClass.CimClassName; "
        "TriggerEnabled = [bool]$trigger.Enabled; "
        "DaysInterval = [int]$trigger.DaysInterval; "
        "StartLocalTime = $startLocal; "
        "Interval = [string]$trigger.Repetition.Interval; "
        "Duration = [string]$trigger.Repetition.Duration; "
        "StopAtDurationEnd = [bool]$trigger.Repetition.StopAtDurationEnd; "
        "WakeToRun = [bool]$task.Settings.WakeToRun; "
        "StartWhenAvailable = [bool]$task.Settings.StartWhenAvailable; "
        "RunOnlyIfNetworkAvailable = [bool]$task.Settings.RunOnlyIfNetworkAvailable; "
        "DisallowStartIfOnBatteries = [bool]$task.Settings.DisallowStartIfOnBatteries; "
        "StopIfGoingOnBatteries = [bool]$task.Settings.StopIfGoingOnBatteries; "
        "MultipleInstances = [string]$task.Settings.MultipleInstances; "
        "ExecutionTimeLimit = [string]$task.Settings.ExecutionTimeLimit; "
        "RestartCount = [int]$task.Settings.RestartCount; "
        "RestartInterval = [string]$task.Settings.RestartInterval; "
        "PrincipalUserSid = $principalSid; CurrentUserSid = $currentSid; "
        "LogonType = [string]$task.Principal.LogonType; "
        "RunLevel = [string]$task.Principal.RunLevel } | "
        "ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        cwd=str(APP_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode == 3:
        return None
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"Task Scheduler query failed ({result.returncode})")
    return parse_recurring_task_status(result.stdout.strip())


def scheduler_health_item(
    task: Mapping[str, Any] | None,
    error: Exception | None = None,
) -> HealthItem:
    """Build the dashboard's honest Task Scheduler card."""

    if error is not None:
        return HealthItem(
            "next_run",
            "Next automatic run",
            "error",
            "Automatic schedule status could not be read",
            "Task Scheduler returned an error; this is not treated as an absent task.",
        )
    if task is None:
        return HealthItem(
            "next_run",
            "Next automatic run",
            "unknown",
            "Automatic schedule is not installed",
            "Install or repair the schedule to enable background runs.",
        )
    if not task.get("healthy"):
        problem = task.get("problem") or "the saved task does not match the required contract"
        return HealthItem(
            "next_run",
            "Next automatic run",
            "error",
            "Automatic schedule needs repair",
            f"Next reported run: {task.get('next_run', 'unknown')}. Problem: {problem}.",
        )
    state = str(task.get("state", "")).casefold()
    headline = (
        "Automatic booking is running now"
        if state == "running"
        else f"Next automatic run: {task['next_run']}"
    )
    return HealthItem(
        "next_run",
        "Next automatic run",
        "ok",
        headline,
        "Every 15 minutes in the daily window; Windows wake is requested, while physical wake proof is shown separately.",
    )


def _require_bool(value: Any, label: str) -> bool:
    """Return a real JSON boolean without accepting truthy strings/numbers."""
    if not isinstance(value, bool):
        raise SettingsError(f"{label} must be true or false")
    return value


def _require_clock_part(value: Any, label: str, maximum: int) -> int:
    """Validate one integer component of a 24-hour time."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError(f"{label} must be a whole number")
    if not 0 <= value <= maximum:
        raise SettingsError(f"{label} must be between 0 and {maximum}")
    return value


def validate_time_preferences_section(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and canonicalize the GUI's time-preference settings section."""
    prefs = settings.get("time_preferences", {})
    if not isinstance(prefs, dict):
        raise SettingsError("time_preferences must be a JSON object")

    enabled = _require_bool(prefs.get("enabled", False), "time_preferences.enabled")
    strict_mode = _require_bool(
        prefs.get("strict_mode", False), "time_preferences.strict_mode"
    )
    preset = prefs.get("preset", "afternoon_evening")
    if not isinstance(preset, str) or preset not in TIME_PREFERENCE_PRESET_KEYS:
        raise SettingsError("time_preferences.preset is not a supported preset")

    start_hour = _require_clock_part(
        prefs.get("custom_start_hour", 14),
        "time_preferences.custom_start_hour",
        23,
    )
    start_min = _require_clock_part(
        prefs.get("custom_start_min", 0),
        "time_preferences.custom_start_min",
        59,
    )
    end_hour = _require_clock_part(
        prefs.get("custom_end_hour", 22),
        "time_preferences.custom_end_hour",
        23,
    )
    end_min = _require_clock_part(
        prefs.get("custom_end_min", 0),
        "time_preferences.custom_end_min",
        59,
    )
    if preset == "custom" and (end_hour, end_min) <= (start_hour, start_min):
        raise SettingsError("Custom preferred end time must be later than start time")

    return {
        "enabled": enabled,
        "preset": preset,
        "custom_start_hour": start_hour,
        "custom_start_min": start_min,
        "custom_end_hour": end_hour,
        "custom_end_min": end_min,
        "strict_mode": strict_mode,
    }


def validate_booking_strategy_section(settings: Mapping[str, Any]) -> dict[str, bool]:
    """Validate and canonicalize the GUI's booking-strategy settings section."""
    strategy = settings.get("booking_strategy", {})
    if not isinstance(strategy, dict):
        raise SettingsError("booking_strategy must be a JSON object")
    return {
        "reverse_date_order": _require_bool(
            strategy.get("reverse_date_order", False),
            "booking_strategy.reverse_date_order",
        )
    }


def validate_history_document(value: Any) -> dict[str, Any]:
    """Reject malformed history instead of silently replacing it with an empty file."""
    if not isinstance(value, dict) or not isinstance(value.get("runs"), list):
        raise SettingsError("Booking history must contain a runs list")
    return value


def load_history_document(path: Path = HISTORY_FILE) -> dict[str, Any]:
    """Load and validate the booking history document."""
    path = Path(path)
    if not path.exists():
        return {"runs": []}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return validate_history_document(json.load(handle))
    except SettingsError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SettingsError(f"Booking history is unreadable: {exc}") from exc


def replace_history_document(value: Any, path: Path = HISTORY_FILE) -> None:
    """Atomically replace history while sharing the autonomous booker's lock."""
    path = Path(path)
    validated = validate_history_document(value)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with InterProcessFileLock(lock_path):
        atomic_write_json(path, validated, backup=True)


def append_history_entry(entry: Mapping[str, Any], path: Path = HISTORY_FILE) -> None:
    """Append one GUI history record without losing a concurrent booker record."""
    path = Path(path)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with InterProcessFileLock(lock_path):
        history = load_history_document(path)
        history["runs"].insert(0, dict(entry))
        history["runs"] = history["runs"][:100]
        atomic_write_json(path, history, backup=True)


def format_practice_hours(hours: float) -> str:
    """Format a practice target compactly for controls and summaries."""
    value = float(hours)
    return str(int(value)) if value.is_integer() else f"{value:g}"


PRACTICE_TARGET_CHOICES = tuple(
    f"{format_practice_hours(step * TARGET_STEP_HOURS)}h"
    for step in range(
        int(MIN_TARGET_HOURS / TARGET_STEP_HOURS),
        int(MAX_TARGET_HOURS / TARGET_STEP_HOURS) + 1,
    )
)


def parse_practice_target_choice(value: str) -> float:
    """Parse an explicit per-date target such as ``1.5h``."""
    text = value.strip()
    if not text.endswith("h"):
        raise PracticePlanError("Daily target must be an hour value")
    try:
        hours = float(text[:-1])
    except ValueError as exc:
        raise PracticePlanError("Daily target must be a number") from exc

    # Reuse the shared schema validator so UI and autonomous runs agree.
    validated = load_practice_plan(
        {"practice_plan": {"enabled": True, "default_hours": hours}}
    )
    return validated.default_hours


def validate_iso_date_key(value: str, label: str) -> str:
    """Validate a canonical date key before it can enter user preferences."""
    if not isinstance(value, str):
        raise PracticePlanError(f"{label} must be a YYYY-MM-DD string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise PracticePlanError(f"{label} must use YYYY-MM-DD: {value}") from exc
    if parsed.isoformat() != value:
        raise PracticePlanError(f"{label} must use YYYY-MM-DD: {value}")
    return value


def apply_booking_day_updates(
    settings: dict[str, Any],
    day_states: Mapping[str, bool],
) -> set[str]:
    """Merge only explicitly edited booking dates into the latest settings."""

    disabled_raw = settings.get("disabled_dates", [])
    if not isinstance(disabled_raw, list) or not all(
        isinstance(item, str) for item in disabled_raw
    ):
        raise SettingsError("disabled_dates must be a list of YYYY-MM-DD strings")
    try:
        disabled_dates = {
            validate_iso_date_key(item, "Disabled date") for item in disabled_raw
        }
        validated_states = {
            validate_iso_date_key(date_key, "Booking date"): enabled
            for date_key, enabled in day_states.items()
        }
    except PracticePlanError as exc:
        raise SettingsError(str(exc)) from exc

    for date_key, enabled in validated_states.items():
        if not isinstance(enabled, bool):
            raise SettingsError(f"Enabled state for {date_key} must be true or false")
        if enabled:
            disabled_dates.discard(date_key)
        else:
            disabled_dates.add(date_key)
    settings["disabled_dates"] = sorted(disabled_dates)
    return disabled_dates


def apply_practice_plan_update(
    settings: dict[str, Any],
    *,
    plan_enabled: bool,
    default_hours: float | None,
    day_states: Mapping[str, bool],
    date_overrides: Mapping[str, float | None],
) -> PracticePlan:
    """Apply one scoped UI save while preserving concurrent, unedited values.

    ``day_states`` and ``date_overrides`` are independent changed-value maps.
    Passing ``None`` for ``default_hours`` preserves the latest persisted
    default, which is useful for a per-date editor that does not itself edit
    that value.
    """
    if not isinstance(plan_enabled, bool):
        raise PracticePlanError("Practice plan enabled state must be true or false")

    current_plan = load_practice_plan(settings)
    editable_day_dates = set()
    for date_key, enabled in day_states.items():
        editable_day_dates.add(validate_iso_date_key(date_key, "Practice-plan date"))
        if not isinstance(enabled, bool):
            raise PracticePlanError(f"Enabled state for {date_key} must be true or false")
    editable_override_dates = {
        validate_iso_date_key(date_key, "Practice-plan override date")
        for date_key in date_overrides
    }

    disabled_raw = settings.get("disabled_dates", [])
    if not isinstance(disabled_raw, list) or not all(isinstance(item, str) for item in disabled_raw):
        raise PracticePlanError("disabled_dates must be a list of YYYY-MM-DD strings")
    disabled_dates = {
        validate_iso_date_key(item, "Disabled date") for item in disabled_raw
    } - editable_day_dates
    disabled_dates.update(date_key for date_key, enabled in day_states.items() if not enabled)

    merged_overrides = {
        key: value
        for key, value in (current_plan.date_overrides or {}).items()
        if key not in editable_override_dates
    }
    for date_key, value in date_overrides.items():
        if value is not None:
            merged_overrides[date_key] = value

    candidate = {
        "enabled": plan_enabled,
        "default_hours": (
            current_plan.default_hours if default_hours is None else default_hours
        ),
        "date_overrides": merged_overrides,
    }
    validated = load_practice_plan({"practice_plan": candidate})
    settings["disabled_dates"] = sorted(disabled_dates)
    settings["practice_plan"] = {
        "enabled": validated.enabled,
        "default_hours": validated.default_hours,
        "date_overrides": validated.date_overrides or {},
    }
    return validated


def parse_room_preference_terms(value: str, label: str) -> tuple[str, ...]:
    """Parse a friendly comma/newline list using the strict saved schema rules."""

    if not isinstance(value, str):
        raise RoomPreferencesError(f"{label} must be text")
    values = [item.strip() for item in value.replace("\n", ",").split(",")]
    values = [item for item in values if item]
    # The shared loader owns printable-text and case-insensitive duplicate
    # validation, so values typed here cannot diverge from autonomous runs.
    field_name = {
        "instrument tags": "acceptable_instrument_tags",
        "room-type tags": "acceptable_room_type_tags",
        "required features": "required_feature_terms",
    }.get(label, "required_feature_terms")
    preferences = strict_load_room_preferences(
        {"room_preferences": {field_name: values}}
    )
    return tuple(getattr(preferences, field_name))


def move_room_preference_item(
    rooms: Sequence[str],
    selected_index: int,
    direction: int,
) -> tuple[tuple[str, ...], int]:
    """Move one ranked room by one row, returning the new order and selection."""

    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    ordered = list(rooms)
    if not ordered:
        return (), -1
    if isinstance(selected_index, bool) or not isinstance(selected_index, int):
        raise TypeError("selected_index must be an integer")
    if selected_index < 0 or selected_index >= len(ordered):
        return tuple(ordered), -1
    destination = selected_index + direction
    if destination < 0 or destination >= len(ordered):
        return tuple(ordered), selected_index
    ordered[selected_index], ordered[destination] = (
        ordered[destination],
        ordered[selected_index],
    )
    return tuple(ordered), destination


def changed_room_preference_fields(
    opening: RoomPreferences,
    candidate: RoomPreferences,
) -> dict[str, Any]:
    """Return only fields actually edited in an isolated preferences dialog."""

    opening_values = room_preferences_to_dict(opening)
    candidate_values = room_preferences_to_dict(candidate)
    return {
        field: value
        for field, value in candidate_values.items()
        if value != opening_values[field]
    }


def _catalog_entry_metadata(entry: Any) -> dict[str, list[str]]:
    """Extract the preference-facing metadata fields from one catalog room."""

    result = {}
    for field in ("instrument_tags", "room_type_tags", "features"):
        if isinstance(entry, Mapping):
            raw = entry.get(field, [])
        else:
            raw = getattr(entry, field, ())
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
            raise RoomPreferencesError(f"Catalog room {field} must be a list")
        result[field] = list(raw)
    return result


def _catalog_metadata(catalog: Any) -> dict[str, dict[str, list[str]]]:
    """Normalize RoomCatalog or its strict JSON representation for the GUI."""

    converter = getattr(catalog, "as_live_metadata", None)
    if callable(converter):
        raw_metadata = converter()
        if not isinstance(raw_metadata, Mapping):
            raise RoomPreferencesError("Room catalog metadata must be an object")
        return {
            room: _catalog_entry_metadata(metadata)
            for room, metadata in raw_metadata.items()
        }

    if isinstance(catalog, Mapping):
        rooms = catalog.get("rooms", catalog)
    else:
        rooms = getattr(catalog, "rooms", None)
    if isinstance(rooms, Mapping):
        return {
            room: _catalog_entry_metadata(metadata)
            for room, metadata in rooms.items()
        }
    if isinstance(rooms, Sequence) and not isinstance(
        rooms, (str, bytes, bytearray)
    ):
        metadata = {}
        for entry in rooms:
            if isinstance(entry, str):
                room = entry
            elif isinstance(entry, Mapping):
                room = entry.get("name") or entry.get("room_id") or entry.get("id")
            else:
                room = getattr(entry, "name", None) or getattr(entry, "room_id", None)
            if not isinstance(room, str):
                raise RoomPreferencesError("Each catalog room must have a room name")
            if room in metadata:
                raise RoomPreferencesError(f"Room catalog contains duplicate room {room}")
            metadata[room] = _catalog_entry_metadata(entry)
        return metadata
    raise RoomPreferencesError("Room catalog does not contain a room list")


def _catalog_attribute(catalog: Any, name: str, default: Any = None) -> Any:
    if isinstance(catalog, Mapping):
        return catalog.get(name, default)
    return getattr(catalog, name, default)


def room_catalog_ui_view(
    catalog: Any,
) -> tuple[dict[str, dict[str, list[str]]] | None, str]:
    """Return validated display metadata and an honest freshness sentence."""

    if catalog is None:
        return None, "Room catalog not loaded — using the saved room list."
    if _catalog_attribute(catalog, "complete", True) is not True:
        return None, "Room catalog is incomplete — using the saved room list."

    try:
        metadata = _catalog_metadata(catalog)
        if not metadata:
            raise RoomPreferencesError("Room catalog contains no rooms")
        # This walks every metadata record through the shared strict validator.
        filter_effective_rooms(RoomPreferences(), metadata)
    except (RoomPreferencesError, TypeError, ValueError) as exc:
        return None, f"Room catalog cannot be used safely: {exc}"

    observed_at = _catalog_attribute(
        catalog,
        "observed_at",
        _catalog_attribute(catalog, "fetched_at"),
    )
    if isinstance(observed_at, str):
        try:
            observed_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        except ValueError:
            observed_at = None
    observed_text = (
        observed_at.astimezone().strftime("%d %b %Y %H:%M")
        if isinstance(observed_at, datetime) and observed_at.tzinfo is not None
        else observed_at.strftime("%d %b %Y %H:%M")
        if isinstance(observed_at, datetime)
        else "time unknown"
    )
    source = _catalog_attribute(catalog, "source", "unknown")
    fresh = _catalog_attribute(catalog, "fresh", None)
    if source == "live" and fresh is not False:
        freshness = f"updated from Asimut {observed_text}"
    elif source == "cache" or fresh is False:
        freshness = f"saved observation {observed_text}; refresh required before booking"
    else:
        freshness = f"observed {observed_text}; freshness unknown"
    return metadata, f"Room catalog: {len(metadata)} rooms • {freshness}."


def catalog_booking_dates(
    catalog: Any,
    *,
    today: date | None = None,
) -> tuple[date, ...]:
    """Return UI dates from the catalog's absolute cutoff, never a fixed count."""

    if catalog is None:
        return ()
    today = today or datetime.now().date()
    if isinstance(today, datetime) or not isinstance(today, date):
        raise ValueError("today must be a date")
    booking_horizon = _catalog_attribute(catalog, "booking_horizon")
    if isinstance(booking_horizon, str):
        try:
            booking_horizon = datetime.fromisoformat(
                booking_horizon.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError("Room catalog booking_horizon is invalid") from exc
    if not isinstance(booking_horizon, datetime):
        raise ValueError("Room catalog has no booking_horizon")
    final_date = (
        booking_horizon.astimezone().date()
        if booking_horizon.tzinfo is not None
        else booking_horizon.date()
    )
    if final_date < today:
        return ()
    return tuple(
        today + timedelta(days=offset)
        for offset in range((final_date - today).days + 1)
    )


class AsimutBookerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("AsimutBooker Control Panel")
        self.root.geometry("1280x920")
        self.root.minsize(1000, 720)

        # Configure default font sizes - larger for better readability
        default_font = ("Segoe UI", 13)
        heading_font = ("Segoe UI", 14, "bold")

        # Apply to all ttk widgets
        style = ttk.Style()
        style.configure(".", font=default_font)
        style.configure("TLabel", font=default_font)
        style.configure("TButton", font=default_font, padding=8)
        style.configure("TLabelframe.Label", font=heading_font)
        style.configure("TCheckbutton", font=default_font)
        style.configure("TCombobox", font=default_font)
        style.configure("TEntry", font=default_font)

        # Set icon if available
        try:
            icon_path = APP_DIR / "assets" / "icon.ico"
            if icon_path.exists():
                self.root.iconbitmap(str(icon_path))
        except:
            pass

        # Variables
        self.running_process = None
        self.is_running = False
        self.settings_available = True
        self.settings_error = ""
        self._settings_error_reported = False
        self._settings_controls = []
        self.schedule_setup_in_progress = False
        self.schedule_remove_in_progress = False
        self.login_operation_in_progress = False
        self._schedule_status_generation = 0
        self._health_generation = 0
        self._health_refresh_after_id = None
        self._calendar_scan_generation = 0
        self._room_scan_generation = 0

        # Create UI
        self.create_menu()
        self.create_main_layout()
        self._apply_settings_availability_state()
        self.refresh_status()

    def create_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Open Config", command=self.open_config)
        file_menu.add_command(label="Open Logs Folder", command=self.open_logs_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)

        # Tools menu
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Tools", menu=tools_menu)
        tools_menu.add_command(label="Clean Up Old Files", command=self.show_cleanup_dialog)
        tools_menu.add_command(label="View Automatic Schedule", command=self.view_scheduled_tasks)
        tools_menu.add_separator()
        tools_menu.add_command(label="Install / Repair Automatic Schedule", command=self.run_setup_script)

        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self.show_about)

    def create_main_layout(self):
        # Main container with padding
        main_frame = ttk.Frame(self.root, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Keep day-to-day monitoring separate from preference editing. This
        # prevents the main window from becoming a long wall of controls and
        # lets the activity log use the remaining height on smaller screens.
        self.main_notebook = ttk.Notebook(main_frame)
        self.main_notebook.pack(fill=tk.BOTH, expand=True)
        overview_tab = ttk.Frame(self.main_notebook, padding="12")
        preferences_tab = ttk.Frame(self.main_notebook, padding="12")
        self.main_notebook.add(overview_tab, text="Overview")
        self.main_notebook.add(preferences_tab, text="Preferences")

        # Top section - Status
        status_frame = ttk.LabelFrame(overview_tab, text="Health Dashboard", padding="12")
        status_frame.pack(fill=tk.X, pady=(0, 15))

        health_header = ttk.Frame(status_frame)
        health_header.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(
            health_header,
            text="What is ready, what needs attention, and what is still unproven.",
            foreground="#555555",
            font=("Segoe UI", 10),
        ).pack(side=tk.LEFT)
        self.health_updated_var = tk.StringVar(value="Checking…")
        ttk.Label(
            health_header,
            textvariable=self.health_updated_var,
            foreground="#666666",
            font=("Segoe UI", 10),
        ).pack(side=tk.RIGHT, padx=(12, 0))
        ttk.Button(
            health_header,
            text="Refresh",
            command=self.refresh_status,
        ).pack(side=tk.RIGHT)

        status_grid = ttk.Frame(status_frame)
        status_grid.pack(fill=tk.X)
        for column in range(3):
            status_grid.columnconfigure(column, weight=1, uniform="health")

        labels = {
            "last_success": "Last successful run",
            "next_run": "Next automatic run",
            "saved_session": "Saved session",
            "auth_cooldown": "Authentication recovery",
            "pending_mutations": "Pending mutations",
            "physical_wake": "Physical wake test",
        }
        self.health_headline_vars = {}
        self.health_detail_vars = {}
        self.health_state_labels = {}
        for index, key in enumerate(HEALTH_CARD_ORDER):
            row, column = divmod(index, 3)
            card = ttk.Frame(status_grid, padding="9", relief="groove", borderwidth=1)
            card.grid(row=row, column=column, sticky="nsew", padx=4, pady=4)
            heading = ttk.Frame(card)
            heading.pack(fill=tk.X)
            state_label = ttk.Label(
                heading,
                text=HEALTH_STATE_SYMBOLS["unknown"],
                foreground=HEALTH_STATE_COLORS["unknown"],
                font=("Segoe UI", 12, "bold"),
            )
            state_label.pack(side=tk.LEFT, padx=(0, 6))
            ttk.Label(
                heading,
                text=labels[key],
                font=("Segoe UI", 10, "bold"),
            ).pack(side=tk.LEFT)
            headline = tk.StringVar(value="Checking…")
            detail = tk.StringVar(value="")
            ttk.Label(
                card,
                textvariable=headline,
                font=("Segoe UI", 10, "bold"),
                wraplength=340,
                justify=tk.LEFT,
            ).pack(fill=tk.X, anchor=tk.W, pady=(5, 2))
            ttk.Label(
                card,
                textvariable=detail,
                foreground="#555555",
                font=("Segoe UI", 9),
                wraplength=340,
                justify=tk.LEFT,
            ).pack(fill=tk.X, anchor=tk.W)
            self.health_headline_vars[key] = headline
            self.health_detail_vars[key] = detail
            self.health_state_labels[key] = state_label

        # Backward-compatible variable names used by scheduler/login callbacks.
        self.login_status_var = self.health_headline_vars["saved_session"]
        self.tasks_status_var = self.health_headline_vars["next_run"]
        self.last_run_var = self.health_headline_vars["last_success"]

        # Middle section - Run Controls
        controls_frame = ttk.LabelFrame(overview_tab, text="Run Booker", padding="15")
        controls_frame.pack(fill=tk.X, pady=(0, 15))

        # Row 1: Run/Stop buttons
        controls_row1 = ttk.Frame(controls_frame)
        controls_row1.pack(fill=tk.X, pady=(0, 8))

        # Run buttons
        self.run_visible_btn = ttk.Button(
            controls_row1,
            text="▶ Run with Visible Browser",
            command=lambda: self.run_booker(headless=False),
            width=26
        )
        self.run_visible_btn.pack(side=tk.LEFT, padx=5, pady=3)

        self.run_headless_btn = ttk.Button(
            controls_row1,
            text="▶ Run Headless (Background)",
            command=lambda: self.run_booker(headless=True),
            width=26
        )
        self.run_headless_btn.pack(side=tk.LEFT, padx=5, pady=3)

        self.stop_btn = ttk.Button(
            controls_row1,
            text="⏹ Stop",
            command=self.stop_booker,
            width=10,
            state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT, padx=5, pady=3)

        # Row 2: Other tools
        controls_row2 = ttk.Frame(controls_frame)
        controls_row2.pack(fill=tk.X)

        # Scan Available Rooms button - prominent placement
        ttk.Button(
            controls_row2,
            text="🔍 Scan Available Rooms",
            command=self.show_scan_rooms_dialog,
            width=22
        ).pack(side=tk.LEFT, padx=5, pady=3)

        # View Logs button
        ttk.Button(
            controls_row2,
            text="📋 View Logs",
            command=self.show_logs_viewer,
            width=14
        ).pack(side=tk.LEFT, padx=5, pady=3)

        # Automatic schedule button
        ttk.Button(
            controls_row2,
            text="⏰ Automatic Schedule",
            command=self.view_scheduled_tasks,
            width=20
        ).pack(side=tk.LEFT, padx=5, pady=3)

        self.login_check_btn = ttk.Button(
            controls_row2,
            text="🔐 Check / Repair Login",
            command=self.check_repair_login,
            width=22,
        )
        self.login_check_btn.pack(side=tk.LEFT, padx=5, pady=3)
        self.login_update_btn = ttk.Button(
            controls_row2,
            text="Update Secure Login",
            command=self.update_secure_login,
            width=21,
        )
        self.login_update_btn.pack(side=tk.LEFT, padx=5, pady=3)

        # Progress indicator
        self.progress_var = tk.StringVar(value="")
        ttk.Label(controls_frame, textvariable=self.progress_var, foreground="blue", font=("Segoe UI", 11)).pack(anchor=tk.W, pady=(8, 0))

        # Booking Days section - collapsible calendar
        days_frame = ttk.LabelFrame(preferences_tab, text="Booking Days", padding="15")
        days_frame.pack(fill=tk.X, pady=(0, 15))

        days_inner = ttk.Frame(days_frame)
        days_inner.pack(fill=tk.X)

        # Summary label showing selected days count
        self.days_summary_var = tk.StringVar(value="Loading...")
        ttk.Label(days_inner, textvariable=self.days_summary_var, font=("Segoe UI", 12)).pack(side=tk.LEFT, padx=(0, 20))

        # Open calendar button
        self.calendar_btn = ttk.Button(
            days_inner,
            text="📅 Open Calendar",
            command=self.show_calendar_dialog,
            width=18
        )
        self.calendar_btn.pack(side=tk.LEFT, padx=5)
        self._settings_controls.append(self.calendar_btn)

        # Initialize day vars (will be populated by load_booking_days)
        self.day_vars = {}  # {date_str: BooleanVar}

        # Load saved settings and update summary
        self.load_booking_days()
        self._update_days_summary()

        # Practice Plan section - a default target with optional per-date overrides.
        practice_frame = ttk.LabelFrame(preferences_tab, text="Practice Plan", padding="12")
        practice_frame.pack(fill=tk.X, pady=(0, 12))

        practice_inner = ttk.Frame(practice_frame)
        practice_inner.pack(fill=tk.X)

        self.practice_plan_enabled = tk.BooleanVar(value=False)
        self.practice_plan_enable_cb = ttk.Checkbutton(
            practice_inner,
            text="Aim for roughly",
            variable=self.practice_plan_enabled,
            command=self.on_practice_plan_changed,
        )
        self.practice_plan_enable_cb.pack(side=tk.LEFT, padx=(0, 8))

        self.practice_default_hours = tk.StringVar(value=format_practice_hours(DEFAULT_TARGET_HOURS))
        self.practice_default_spin = ttk.Spinbox(
            practice_inner,
            from_=MIN_TARGET_HOURS,
            to=MAX_TARGET_HOURS,
            increment=TARGET_STEP_HOURS,
            textvariable=self.practice_default_hours,
            width=5,
            justify="center",
            command=self.on_practice_plan_changed,
        )
        self.practice_default_spin.pack(side=tk.LEFT)
        self.practice_default_spin.bind("<FocusOut>", lambda _event: self.on_practice_plan_changed())
        self.practice_default_spin.bind("<Return>", lambda _event: self.on_practice_plan_changed())

        ttk.Label(practice_inner, text="hours on each enabled day").pack(side=tk.LEFT, padx=(8, 18))

        self.practice_plan_customize_btn = ttk.Button(
            practice_inner,
            text="Customize Booking Window…",
            command=self.show_practice_plan_dialog,
            width=24,
        )
        self.practice_plan_customize_btn.pack(side=tk.LEFT, padx=(0, 18))

        self.practice_plan_summary_var = tk.StringVar(value="Loading…")
        ttk.Label(
            practice_inner,
            textvariable=self.practice_plan_summary_var,
            foreground="#555555",
            font=("Segoe UI", 11),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.settings_status_var = tk.StringVar(value="")
        self.settings_status_label = ttk.Label(
            practice_frame,
            textvariable=self.settings_status_var,
            foreground="#b00020",
            font=("Segoe UI", 10),
            wraplength=1150,
        )
        self.settings_status_label.pack(fill=tk.X, pady=(6, 0))

        self._settings_controls.extend(
            [self.practice_plan_enable_cb, self.practice_default_spin, self.practice_plan_customize_btn]
        )
        self.practice_plan_overrides = {}
        self.load_practice_plan_settings()

        # Room preferences are edited in a focused dialog because ordering and
        # site-derived metadata need more space than a single settings row.
        rooms_frame = ttk.LabelFrame(preferences_tab, text="Rooms", padding="12")
        rooms_frame.pack(fill=tk.X, pady=(0, 12))
        rooms_inner = ttk.Frame(rooms_frame)
        rooms_inner.pack(fill=tk.X)
        self.room_preferences_summary_var = tk.StringVar(value="Loading…")
        ttk.Label(
            rooms_inner,
            textvariable=self.room_preferences_summary_var,
            font=("Segoe UI", 11),
            wraplength=900,
            justify=tk.LEFT,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 16))
        self.room_preferences_btn = ttk.Button(
            rooms_inner,
            text="Edit Room Preferences…",
            command=self.show_room_preferences_dialog,
            width=24,
        )
        self.room_preferences_btn.pack(side=tk.RIGHT)
        self._settings_controls.append(self.room_preferences_btn)
        self.room_catalog_status_var = tk.StringVar(value="")
        ttk.Label(
            rooms_frame,
            textvariable=self.room_catalog_status_var,
            foreground="#666666",
            font=("Segoe UI", 9),
            wraplength=1120,
            justify=tk.LEFT,
        ).pack(fill=tk.X, pady=(5, 0))
        if not hasattr(self, "room_catalog"):
            self.room_catalog = None
            self.room_catalog_load_error = ""
        self.load_room_catalog_cache()
        self.room_preferences = RoomPreferences()
        self.load_room_preferences_settings()

        # Time Preferences section
        time_prefs_frame = ttk.LabelFrame(preferences_tab, text="Time Preferences", padding="15")
        time_prefs_frame.pack(fill=tk.X, pady=(0, 15))

        # Row 1: Enable checkbox and preset dropdown
        time_prefs_row1 = ttk.Frame(time_prefs_frame)
        time_prefs_row1.pack(fill=tk.X, pady=(0, 10))

        # Enable checkbox
        self.time_prefs_enabled = tk.BooleanVar(value=False)
        self.time_prefs_enable_cb = ttk.Checkbutton(
            time_prefs_row1,
            text="Enable time preferences",
            variable=self.time_prefs_enabled,
            command=self.on_time_prefs_changed
        )
        self.time_prefs_enable_cb.pack(side=tk.LEFT, padx=(0, 30))

        # Preset dropdown
        ttk.Label(time_prefs_row1, text="Prefer:").pack(side=tk.LEFT, padx=(0, 10))

        self.time_prefs_preset = tk.StringVar(value="afternoon_evening")
        self.time_prefs_presets = {
            "Morning (07:00-12:00)": "morning",
            "Afternoon (12:00-18:00)": "afternoon",
            "Evening (18:00-22:00)": "evening",
            "Afternoon/Evening (14:00-22:00)": "afternoon_evening",
            "Custom...": "custom",
        }
        self.time_prefs_dropdown = ttk.Combobox(
            time_prefs_row1,
            textvariable=self.time_prefs_preset,
            values=list(self.time_prefs_presets.keys()),
            state="disabled",
            width=28,
            font=("Segoe UI", 13)
        )
        self.time_prefs_dropdown.set("Afternoon/Evening (14:00-22:00)")
        self.time_prefs_dropdown.pack(side=tk.LEFT, padx=(0, 30))
        self.time_prefs_dropdown.bind("<<ComboboxSelected>>", lambda e: self.on_time_prefs_changed())

        # Strict mode checkbox (on same row)
        self.time_prefs_strict = tk.BooleanVar(value=False)
        self.time_prefs_strict_cb = ttk.Checkbutton(
            time_prefs_row1,
            text="Strict mode (only book preferred times)",
            variable=self.time_prefs_strict,
            command=self.on_time_prefs_changed,
            state=tk.DISABLED
        )
        self.time_prefs_strict_cb.pack(side=tk.LEFT)

        # Row 2: Custom time range (hidden by default)
        self.custom_time_frame = ttk.Frame(time_prefs_frame)
        # Don't pack yet - will be shown/hidden based on selection

        ttk.Label(self.custom_time_frame, text="From:").pack(side=tk.LEFT, padx=(0, 10))

        # Start hour entry
        self.custom_start_hour = tk.StringVar(value="14")
        self.custom_start_hour_entry = ttk.Entry(
            self.custom_time_frame,
            textvariable=self.custom_start_hour,
            width=3,
            font=("Segoe UI", 13),
            justify="center"
        )
        self.custom_start_hour_entry.pack(side=tk.LEFT)
        self.custom_start_hour_entry.bind("<FocusOut>", lambda e: self.on_time_prefs_changed())
        self.custom_start_hour_entry.bind("<Return>", lambda e: self.on_time_prefs_changed())

        ttk.Label(self.custom_time_frame, text=":").pack(side=tk.LEFT)

        # Start minute entry
        self.custom_start_min = tk.StringVar(value="00")
        self.custom_start_min_entry = ttk.Entry(
            self.custom_time_frame,
            textvariable=self.custom_start_min,
            width=3,
            font=("Segoe UI", 13),
            justify="center"
        )
        self.custom_start_min_entry.pack(side=tk.LEFT)
        self.custom_start_min_entry.bind("<FocusOut>", lambda e: self.on_time_prefs_changed())
        self.custom_start_min_entry.bind("<Return>", lambda e: self.on_time_prefs_changed())

        ttk.Label(self.custom_time_frame, text="To:").pack(side=tk.LEFT, padx=(30, 10))

        # End hour entry
        self.custom_end_hour = tk.StringVar(value="22")
        self.custom_end_hour_entry = ttk.Entry(
            self.custom_time_frame,
            textvariable=self.custom_end_hour,
            width=3,
            font=("Segoe UI", 13),
            justify="center"
        )
        self.custom_end_hour_entry.pack(side=tk.LEFT)
        self.custom_end_hour_entry.bind("<FocusOut>", lambda e: self.on_time_prefs_changed())
        self.custom_end_hour_entry.bind("<Return>", lambda e: self.on_time_prefs_changed())

        ttk.Label(self.custom_time_frame, text=":").pack(side=tk.LEFT)

        # End minute entry
        self.custom_end_min = tk.StringVar(value="00")
        self.custom_end_min_entry = ttk.Entry(
            self.custom_time_frame,
            textvariable=self.custom_end_min,
            width=3,
            font=("Segoe UI", 13),
            justify="center"
        )
        self.custom_end_min_entry.pack(side=tk.LEFT)
        self.custom_end_min_entry.bind("<FocusOut>", lambda e: self.on_time_prefs_changed())
        self.custom_end_min_entry.bind("<Return>", lambda e: self.on_time_prefs_changed())

        # Load saved time preferences
        self.load_time_preferences()
        self._settings_controls.extend(
            [
                self.time_prefs_enable_cb,
                self.time_prefs_dropdown,
                self.time_prefs_strict_cb,
                self.custom_start_hour_entry,
                self.custom_start_min_entry,
                self.custom_end_hour_entry,
                self.custom_end_min_entry,
            ]
        )

        # Booking Strategy section
        strategy_frame = ttk.LabelFrame(preferences_tab, text="Booking Strategy", padding="15")
        strategy_frame.pack(fill=tk.X, pady=(0, 15))

        strategy_inner = ttk.Frame(strategy_frame)
        strategy_inner.pack(fill=tk.X)

        # Row 1: Reverse date order toggle
        strategy_row1 = ttk.Frame(strategy_inner)
        strategy_row1.pack(fill=tk.X, pady=(0, 8))

        self.reverse_date_order = tk.BooleanVar(value=False)
        self.reverse_date_order_cb = ttk.Checkbutton(
            strategy_row1,
            text="Book furthest dates first (prioritize newly available rooms)",
            variable=self.reverse_date_order,
            command=self.on_strategy_changed
        )
        self.reverse_date_order_cb.pack(side=tk.LEFT, padx=(0, 20))
        self._settings_controls.append(self.reverse_date_order_cb)

        # Explanation label
        ttk.Label(
            strategy_row1,
            text="(Start from the furthest enabled live-window date)",
            foreground="gray",
            font=("Segoe UI", 11)
        ).pack(side=tk.LEFT)

        # Row 2: Smart swap toggle
        # Smart swap feature (disabled for now - kept for future use)
        # strategy_row2 = ttk.Frame(strategy_inner)
        # strategy_row2.pack(fill=tk.X)
        # self.smart_swap_enabled = tk.BooleanVar(value=False)
        # self.smart_swap_cb = ttk.Checkbutton(
        #     strategy_row2,
        #     text="Smart swap (cancel reservations outside preferred times to book better slots)",
        #     variable=self.smart_swap_enabled,
        #     command=self.on_strategy_changed
        # )
        # self.smart_swap_cb.pack(side=tk.LEFT, padx=(0, 20))
        # ttk.Label(
        #     strategy_row2,
        #     text="(Only when < 4 hours quota remaining)",
        #     foreground="gray",
        #     font=("Segoe UI", 11)
        # ).pack(side=tk.LEFT)

        # Load saved strategy settings
        self.load_strategy_settings()

        # Bottom section - Output Log
        log_frame = ttk.LabelFrame(overview_tab, text="Output Log", padding="15")
        log_frame.pack(fill=tk.BOTH, expand=True)

        # Log text area - reduced height so buttons are visible on open
        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            wrap=tk.WORD,
            font=("Consolas", 13),
            height=8
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Configure tags for colored output
        self.log_text.tag_config("success", foreground="green")
        self.log_text.tag_config("error", foreground="red")
        self.log_text.tag_config("warning", foreground="orange")
        self.log_text.tag_config("info", foreground="blue")

        # Log controls
        log_controls = ttk.Frame(log_frame)
        log_controls.pack(fill=tk.X, pady=(10, 0))

        ttk.Button(log_controls, text="Clear Log", command=self.clear_log).pack(side=tk.LEFT, padx=5)
        ttk.Button(log_controls, text="Open Today's Log File", command=self.open_todays_log).pack(side=tk.LEFT, padx=5)
        ttk.Button(log_controls, text="View Booking History", command=self.show_history_dialog).pack(side=tk.LEFT, padx=5)
        self.manage_events_btn = ttk.Button(log_controls, text="Manage Events", command=self.show_events_dialog)
        self.manage_events_btn.pack(side=tk.LEFT, padx=5)
        self._settings_controls.append(self.manage_events_btn)

        if self.settings_error:
            self.log(self.settings_error, "error")

    def log(self, message, tag=None):
        """Add message to log with optional tag for coloring."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n", tag)
        self.log_text.see(tk.END)

    def clear_log(self):
        self.log_text.delete(1.0, tk.END)

    def refresh_status(self):
        """Refresh all status indicators."""
        # Scheduled and manual booker runs replace this display-only cache.
        # Reloading it with the one-minute dashboard refresh keeps room names,
        # metadata, and freshness visible without requiring a GUI restart.
        if hasattr(self, "room_preferences"):
            self.load_room_catalog_cache()
            self.load_booking_days()
            self._update_days_summary()
            self._update_practice_plan_summary()
            self.load_room_preferences_settings()
        self._start_local_health_refresh()
        self.check_scheduled_tasks()

    def _start_local_health_refresh(self):
        """Read file-backed health evidence away from the Tk event loop."""

        self._health_generation += 1
        generation = self._health_generation
        for key in HEALTH_CARD_ORDER:
            if key == "next_run":
                continue
            self.health_headline_vars[key].set("Checking…")
            self.health_detail_vars[key].set("")
        self.health_updated_var.set("Refreshing…")

        def worker():
            try:
                snapshot = collect_local_health()
                error = None
            except Exception as exc:
                snapshot = None
                error = exc
            try:
                self.root.after(
                    0,
                    self._apply_local_health_result,
                    generation,
                    snapshot,
                    error,
                )
            except (RuntimeError, tk.TclError):
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _apply_health_item(self, item):
        """Render one presentation-ready health item."""

        if item.key not in self.health_headline_vars:
            return
        self.health_headline_vars[item.key].set(item.headline)
        self.health_detail_vars[item.key].set(item.detail)
        label = self.health_state_labels[item.key]
        label.configure(
            text=HEALTH_STATE_SYMBOLS[item.state],
            foreground=HEALTH_STATE_COLORS[item.state],
        )

    def _apply_local_health_result(self, generation, snapshot, error):
        """Apply only the newest independently collected local snapshot."""

        if generation != self._health_generation:
            return
        if error is not None or snapshot is None:
            for key in HEALTH_CARD_ORDER:
                if key == "next_run":
                    continue
                self._apply_health_item(
                    HealthItem(
                        key,
                        key.replace("_", " ").title(),
                        "error",
                        "Health evidence could not be read",
                        "The local health collector failed before this card could be verified.",
                    )
                )
            self.health_updated_var.set("Refresh failed")
        else:
            for item in snapshot.items:
                self._apply_health_item(item)
            self.health_updated_var.set(
                f"Updated {snapshot.collected_at.astimezone():%H:%M:%S}"
            )

        # Keep the dashboard useful while it remains open, without invoking a
        # live login repair or any other state-changing action.
        if self._health_refresh_after_id is not None:
            try:
                self.root.after_cancel(self._health_refresh_after_id)
            except (RuntimeError, tk.TclError):
                pass
        self._health_refresh_after_id = self.root.after(60_000, self.refresh_status)

    def check_scheduled_tasks(self):
        """Start a non-blocking check of the supported recurring task."""
        self._start_scheduler_status_refresh()

    def _start_scheduler_status_refresh(self):
        """Query Task Scheduler off the Tk thread and apply only the newest result."""
        self._schedule_status_generation += 1
        generation = self._schedule_status_generation
        self.tasks_status_var.set("⏳ Checking schedule...")

        tree = getattr(self, "tasks_tree", None)
        if tree is not None:
            try:
                if tree.winfo_exists():
                    for item in tree.get_children():
                        tree.delete(item)
                    if hasattr(self, "task_count_var"):
                        self.task_count_var.set("Checking...")
            except tk.TclError:
                pass

        def worker():
            task = None
            error = None
            try:
                task = query_recurring_task_status()
            except Exception as exc:
                error = exc
            try:
                self.root.after(
                    0,
                    self._apply_scheduler_status_result,
                    generation,
                    task,
                    error,
                )
            except (RuntimeError, tk.TclError):
                # The app may have closed while the background query was running.
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _apply_scheduler_status_result(self, generation, task, error):
        """Apply one background status result on the Tk thread."""
        if generation != self._schedule_status_generation:
            return

        has_dashboard = hasattr(self, "health_headline_vars")
        if has_dashboard:
            self._apply_health_item(scheduler_health_item(task, error))

        if error is not None:
            if not has_dashboard:
                self.tasks_status_var.set("⚠️ Could not check schedule")
            self.log(f"Could not read automatic schedule status: {error}", "error")
        elif task is None:
            if not has_dashboard:
                self.tasks_status_var.set("❌ Not installed")
        elif not task["healthy"]:
            if not has_dashboard:
                self.tasks_status_var.set("⚠️ Installed but needs repair")
            self.log(
                f"Automatic schedule needs repair: {task['problem']}",
                "warning",
            )
        elif task["state"].casefold() == "running":
            if not has_dashboard:
                self.tasks_status_var.set("✅ Running (15-minute schedule)")
        else:
            if not has_dashboard:
                self.tasks_status_var.set("✅ Active (every 15 min)")

        tree = getattr(self, "tasks_tree", None)
        if tree is None:
            return
        try:
            if not tree.winfo_exists():
                return
            for item in tree.get_children():
                tree.delete(item)
            if error is not None:
                self.task_count_var.set(f"Error: {str(error)[:30]}")
            elif task is None:
                self.task_count_var.set("Not installed")
            else:
                tree.insert(
                    "",
                    tk.END,
                    values=(
                        task["task_name"],
                        RECURRING_SCHEDULE_TEXT,
                        task["state"] if task["healthy"] else "Needs repair",
                        task["next_run"],
                    ),
                )
                self.task_count_var.set(
                    "1 verified recurring task"
                    if task["healthy"]
                    else f"Needs repair: {task['problem'][:80]}"
                )
        except tk.TclError:
            pass

    def check_last_run(self):
        """Check when the booker last ran from history."""
        history = self.load_history()
        runs = history.get("runs", [])

        if runs:
            last_run = runs[0]
            try:
                timestamp = datetime.fromisoformat(last_run.get("timestamp", ""))
                time_str = timestamp.strftime("%Y-%m-%d %H:%M")
                bookings = last_run.get("bookings_made", 0)
                events = last_run.get("events_detected", 0)
                self.last_run_var.set(f"{time_str} - {bookings} bookings, {events} events detected")
                return
            except:
                pass

        # Fallback to scheduler log
        scheduler_log = LOGS_DIR / "scheduler.log"
        if scheduler_log.exists():
            try:
                with open(scheduler_log, 'r') as f:
                    lines = f.readlines()
                    if lines:
                        last_line = lines[-1].strip()
                        self.last_run_var.set(last_line)
                        return
            except:
                pass
        self.last_run_var.set("No runs recorded yet")

    def run_booker(self, headless=False):
        """Run the booker script."""
        if self.is_running:
            messagebox.showwarning("Already Running", "The booker is already running.")
            return

        self.is_running = True
        self.run_visible_btn.config(state=tk.DISABLED)
        self.run_headless_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)

        mode = "headless" if headless else "visible browser"
        self.log(f"Starting booker in {mode} mode...", "info")
        self.progress_var.set(f"Running in {mode} mode...")

        # Run in thread to keep UI responsive
        thread = threading.Thread(target=self._run_booker_thread, args=(headless,))
        thread.daemon = True
        thread.start()

    def _run_booker_thread(self, headless):
        """Thread function to run booker."""
        try:
            cmd = [sys.executable, str(APP_DIR / "book_week.py")]
            if headless:
                cmd.append("--headless")

            self.running_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(APP_DIR),
                creationflags=subprocess.CREATE_NO_WINDOW if headless else 0
            )

            # Read output line by line
            for line in iter(self.running_process.stdout.readline, ''):
                if line:
                    line = line.strip()

                    # Track statistics
                    if "SUCCESS:" in line or "Booked" in line:
                        self.root.after(0, lambda l=line: self.log(l, "success"))
                    elif "Found" in line and "unique events" in line:
                        self.root.after(0, lambda l=line: self.log(l, "info"))
                    elif "Total bookings made:" in line:
                        self.root.after(0, lambda l=line: self.log(l, "success"))
                    elif "ERROR" in line or "Error" in line or "failed" in line.lower():
                        self.root.after(0, lambda l=line: self.log(l, "error"))
                    elif "WARNING" in line or "Skip" in line:
                        self.root.after(0, lambda l=line: self.log(l, "warning"))
                    else:
                        self.root.after(0, lambda l=line: self.log(l))

            self.running_process.wait()
            exit_code = self.running_process.returncode

            if exit_code == 0:
                self.root.after(0, lambda: self.log("Booker finished successfully.", "success"))
            else:
                self.root.after(0, lambda: self.log(f"Booker finished with exit code {exit_code}", "warning"))

        except Exception as e:
            self.root.after(0, lambda: self.log(f"Error running booker: {e}", "error"))
        finally:
            self.root.after(0, self._on_booker_finished)

    def _on_booker_finished(self):
        """Called when booker finishes."""
        self.is_running = False
        self.running_process = None
        self.run_visible_btn.config(state=tk.NORMAL)
        self.run_headless_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.progress_var.set("")
        self.refresh_status()

    def stop_booker(self):
        """Stop the running booker process."""
        if self.running_process:
            self.running_process.terminate()
            self.log("Booker stopped by user.", "warning")

    def run_login_setup(self):
        """Backward-compatible alias for the safe login health/recovery action."""
        self.check_repair_login()

    def _begin_login_operation(self, status_text):
        """Reserve the GUI's one login-operation slot and disable both actions."""
        if self.login_operation_in_progress:
            messagebox.showwarning(
                "Login Operation Running",
                "A login check or secure-login update is already running.",
            )
            return False
        self.login_operation_in_progress = True
        self.login_check_btn.configure(state=tk.DISABLED)
        self.login_update_btn.configure(state=tk.DISABLED)
        self.progress_var.set(status_text)
        return True

    def check_repair_login(self):
        """Verify or autonomously repair login without opening a website window."""
        if not self._begin_login_operation("Checking and repairing saved login..."):
            return
        self.log("Checking saved Asimut login and repairing it if needed...", "info")
        threading.Thread(target=self._run_login_check_thread, daemon=True).start()

    def update_secure_login(self):
        """Open the masked Credential Manager setup prompt in an interactive console."""
        if not self._begin_login_operation("Secure login prompt is open..."):
            return
        self.log(
            "Opening the private secure-login prompt. Credentials stay in Windows Credential Manager.",
            "info",
        )
        threading.Thread(target=self._run_secure_login_update_thread, daemon=True).start()

    def _run_login_check_thread(self):
        """Run deterministic login verification/recovery off the Tk thread."""
        try:
            command = build_login_check_command()
            validate_login_command(command)
            result = subprocess.run(
                command,
                cwd=str(APP_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.root.after(
                0,
                self._finish_login_operation,
                "check",
                result.returncode,
                result.stdout or "",
                result.stderr or "",
                None,
            )
        except Exception as exc:
            self.root.after(
                0,
                self._finish_login_operation,
                "check",
                None,
                "",
                "",
                str(exc),
            )

    def _run_secure_login_update_thread(self):
        """Run the masked interactive credential prompt in a new console."""
        try:
            command = build_secure_login_update_command()
            validate_login_command(command)
            process = subprocess.Popen(
                command,
                cwd=str(APP_DIR),
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            return_code = process.wait()
            self.root.after(
                0,
                self._finish_login_operation,
                "update",
                return_code,
                "",
                "",
                None,
            )
        except Exception as exc:
            self.root.after(
                0,
                self._finish_login_operation,
                "update",
                None,
                "",
                "",
                str(exc),
            )

    def _finish_login_operation(
        self,
        operation,
        return_code,
        stdout,
        stderr,
        error_message,
    ):
        """Report one login operation and re-enable controls on the Tk thread."""
        self.login_operation_in_progress = False
        self.login_check_btn.configure(state=tk.NORMAL)
        self.login_update_btn.configure(state=tk.NORMAL)

        if error_message:
            summary = f"Login operation failed to start: {error_message}"
            self.log(summary, "error")
            self.progress_var.set("Login operation failed")
        elif return_code == 0 and operation == "check":
            output = "\n".join(part.strip() for part in (stdout, stderr) if part.strip())
            if output:
                self.log(output, "info")
            self.log("Saved Asimut login is ready.", "success")
            self.progress_var.set("Login ready")
        elif return_code == 0:
            self.log("Secure autonomous-login details were updated.", "success")
            self.progress_var.set("Secure login updated")
        else:
            output = "\n".join(part.strip() for part in (stdout, stderr) if part.strip())
            detail = f": {output}" if output else ""
            label = "Login check" if operation == "check" else "Secure login update"
            self.log(f"{label} exited with code {return_code}{detail}", "error")
            self.progress_var.set(f"{label} failed")

        self.refresh_status()

    def show_cleanup_dialog(self):
        """Show dialog to clean up old/unused files."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Clean Up Old Files")
        dialog.geometry("500x400")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text="Select files to delete:", font=("", 11, "bold")).pack(pady=10)

        # Find potentially removable files
        removable_files = []

        # Old Python files that might be test versions
        for pattern in ["test_*.py", "old_*.py", "*_backup.py", "*_old.py"]:
            removable_files.extend(APP_DIR.glob(pattern))

        # Old log files (older than 7 days)
        if LOGS_DIR.exists():
            for log_file in LOGS_DIR.glob("*.log"):
                if log_file.name != "scheduler.log":
                    try:
                        mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
                        if (datetime.now() - mtime).days > 7:
                            removable_files.append(log_file)
                    except:
                        pass

        # Checkboxes for each file
        frame = ttk.Frame(dialog)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        canvas = tk.Canvas(frame)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        scrollable = ttk.Frame(canvas)

        scrollable.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        file_vars = {}
        if removable_files:
            for f in removable_files:
                var = tk.BooleanVar(value=False)
                file_vars[f] = var
                rel_path = f.relative_to(APP_DIR) if f.is_relative_to(APP_DIR) else f
                cb = ttk.Checkbutton(scrollable, text=str(rel_path), variable=var)
                cb.pack(anchor=tk.W, pady=2)
        else:
            ttk.Label(scrollable, text="No old files found to clean up.").pack()

        # Buttons
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, pady=10, padx=10)

        def delete_selected():
            deleted = 0
            for f, var in file_vars.items():
                if var.get():
                    try:
                        f.unlink()
                        deleted += 1
                    except Exception as e:
                        messagebox.showerror("Error", f"Could not delete {f}: {e}")
            if deleted > 0:
                messagebox.showinfo("Done", f"Deleted {deleted} file(s).")
                dialog.destroy()

        ttk.Button(btn_frame, text="Delete Selected", command=delete_selected).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT)

    def view_scheduled_tasks(self):
        """Show scheduled tasks manager dialog."""
        self._show_scheduled_tasks_dialog()

    def _show_scheduled_tasks_dialog(self):
        """Show the single automatic recurring schedule."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Automatic Schedule")
        dialog.geometry("820x440")
        dialog.transient(self.root)

        # Main container
        main_frame = ttk.Frame(dialog, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Header
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(
            header_frame,
            text="AsimutBooker Automatic Schedule",
            font=("Segoe UI", 14, "bold"),
        ).pack(side=tk.LEFT)

        # Task count label
        self.task_count_var = tk.StringVar(value="Loading...")
        ttk.Label(header_frame, textvariable=self.task_count_var, foreground="gray").pack(side=tk.RIGHT)

        ttk.Label(
            main_frame,
            text=(
                "A single non-overlapping task checks for bookings every 15 minutes "
                "from 07:13 through 21:58. Use Install / Repair to create the correct "
                "schedule and replace obsolete per-time tasks."
            ),
            foreground="gray",
            justify=tk.LEFT,
            wraplength=780,
        ).pack(fill=tk.X, pady=(0, 12))

        # Treeview for the one supported task
        tree_frame = ttk.Frame(main_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Scrollbars
        tree_scroll_y = ttk.Scrollbar(tree_frame)
        tree_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

        tree_scroll_x = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL)
        tree_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)

        # Treeview with columns
        columns = ("task_name", "schedule", "status", "next_run")
        self.tasks_tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            yscrollcommand=tree_scroll_y.set,
            xscrollcommand=tree_scroll_x.set
        )

        # Configure columns
        self.tasks_tree.heading("task_name", text="Task Name")
        self.tasks_tree.heading("schedule", text="Schedule")
        self.tasks_tree.heading("status", text="Status")
        self.tasks_tree.heading("next_run", text="Next Run")

        self.tasks_tree.column("task_name", width=190, minwidth=160)
        self.tasks_tree.column("schedule", width=220, minwidth=180)
        self.tasks_tree.column("status", width=100, minwidth=80)
        self.tasks_tree.column("next_run", width=180, minwidth=120)

        self.tasks_tree.pack(fill=tk.BOTH, expand=True)

        tree_scroll_y.config(command=self.tasks_tree.yview)
        tree_scroll_x.config(command=self.tasks_tree.xview)

        # Button frame
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))

        # Left side buttons
        left_btns = ttk.Frame(btn_frame)
        left_btns.pack(side=tk.LEFT)

        self.schedule_setup_btn = ttk.Button(
            left_btns,
            text="Install / Repair Schedule",
            command=self.run_setup_script,
        )
        self.schedule_setup_btn.pack(side=tk.LEFT, padx=5)
        self.schedule_remove_btn = ttk.Button(
            left_btns,
            text="Remove Schedule",
            command=lambda: self._delete_scheduled_task(dialog),
        )
        self.schedule_remove_btn.pack(side=tk.LEFT, padx=5)

        # Right side buttons
        right_btns = ttk.Frame(btn_frame)
        right_btns.pack(side=tk.RIGHT)

        ttk.Button(right_btns, text="🔄 Refresh", command=self._refresh_tasks_list).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            right_btns,
            text="Open Task Scheduler",
            command=lambda: subprocess.Popen(["taskschd.msc"], shell=False),
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(right_btns, text="Close", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

        # Store dialog reference
        self.tasks_dialog = dialog

        # Populate the list
        self._refresh_tasks_list()

    def _refresh_tasks_list(self):
        """Refresh the recurring task row without blocking the Tk event loop."""
        self._start_scheduler_status_refresh()

    def _delete_scheduled_task(self, parent_dialog):
        """Remove and verify the recurring task through headless Agent UAC."""
        if self.schedule_remove_in_progress:
            messagebox.showinfo(
                "Remove Automatic Schedule",
                "Schedule removal is already running. Please wait for it to finish.",
                parent=parent_dialog,
            )
            return
        if self.schedule_setup_in_progress:
            messagebox.showinfo(
                "Remove Automatic Schedule",
                "Schedule setup is still running. Please wait for it to finish.",
                parent=parent_dialog,
            )
            return

        if not messagebox.askyesno(
            "Remove Automatic Schedule",
            "Remove the AsimutBooker automatic 15-minute schedule?\n\n"
            "Manual runs from the control panel will still work.",
            parent=parent_dialog,
        ):
            return

        try:
            command = build_scheduler_remove_command()
        except Exception as exc:
            messagebox.showerror(
                "Could Not Remove Schedule",
                str(exc),
                parent=parent_dialog,
            )
            return

        if not Path(command[0]).is_file():
            messagebox.showerror(
                "Could Not Remove Schedule",
                "Agent UAC is not installed, so the automatic schedule cannot be "
                f"removed safely. Expected:\n{command[0]}",
                parent=parent_dialog,
            )
            return

        self.schedule_remove_in_progress = True
        if hasattr(self, "schedule_remove_btn"):
            self.schedule_remove_btn.config(state=tk.DISABLED)
        if hasattr(self, "schedule_setup_btn"):
            self.schedule_setup_btn.config(state=tk.DISABLED)
        self.log("Removing and verifying the automatic schedule...", "info")

        def worker():
            error = None
            try:
                result = subprocess.run(
                    command,
                    cwd=str(APP_DIR),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=90,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if result.returncode != 0:
                    detail = (result.stderr or result.stdout).strip()
                    raise RuntimeError(
                        detail or f"Agent UAC exited with code {result.returncode}."
                    )
                if query_recurring_task_status() is not None:
                    raise RuntimeError(
                        "The automatic schedule still exists after the removal command."
                    )
            except Exception as exc:
                error = str(exc)

            try:
                self.root.after(0, self._finish_schedule_removal, error, parent_dialog)
            except (RuntimeError, tk.TclError):
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _finish_schedule_removal(self, error, parent_dialog):
        """Report a completed elevated removal and refresh status asynchronously."""
        self.schedule_remove_in_progress = False
        for button_name in ("schedule_remove_btn", "schedule_setup_btn"):
            button = getattr(self, button_name, None)
            if button is not None:
                try:
                    button.config(state=tk.NORMAL)
                except tk.TclError:
                    pass

        self._start_scheduler_status_refresh()
        if error:
            self.log(f"Automatic schedule removal failed: {error}", "error")
            messagebox.showerror(
                "Could Not Remove Schedule",
                error,
                parent=parent_dialog,
            )
            return

        self.log("Automatic schedule is absent and verified.", "success")
        messagebox.showinfo(
            "Schedule Removed",
            "The automatic schedule is absent and its removal was verified.",
            parent=parent_dialog,
        )

    def run_setup_script(self):
        """Install or repair the recurring schedule through headless Agent UAC."""
        if self.schedule_remove_in_progress:
            messagebox.showinfo(
                "Schedule Setup",
                "Schedule removal is still running. Please wait for it to finish.",
            )
            return
        if self.schedule_setup_in_progress:
            messagebox.showinfo(
                "Schedule Setup",
                "Schedule setup is already running. Please wait for it to finish.",
            )
            return

        script_path = APP_DIR / "setup_scheduled_tasks.ps1"
        if not script_path.exists():
            messagebox.showerror(
                "Schedule Setup Error",
                f"The setup script was not found:\n{script_path}",
            )
            return

        try:
            command = build_scheduler_setup_command(script_path)
        except Exception as exc:
            messagebox.showerror("Schedule Setup Error", str(exc))
            return

        if not Path(command[0]).is_file():
            messagebox.showerror(
                "Schedule Setup Error",
                "Agent UAC is not installed, so the automatic schedule cannot be "
                f"configured safely. Expected:\n{command[0]}",
            )
            return

        msg = (
            "Install or repair the automatic booking schedule?\n\n"
            "This will:\n"
            "• Replace obsolete AsimutBooker tasks with one recurring task\n"
            "• Run every 15 minutes from 07:13 through 21:58\n"
            "• Ignore overlapping starts and catch up after missed starts\n"
            "• Enable AC/DC wake-timer requests and plugged-in lid-close operation\n\n"
            "Wake-from-sleep still depends on Windows, firmware, and hardware support.\n\n"
            "The setup runs in the background and reports its final result here."
        )
        if not messagebox.askyesno("Install / Repair Schedule", msg):
            return

        self.schedule_setup_in_progress = True
        if hasattr(self, "schedule_setup_btn"):
            self.schedule_setup_btn.config(state=tk.DISABLED)
        if hasattr(self, "schedule_remove_btn"):
            self.schedule_remove_btn.config(state=tk.DISABLED)
        self.log("Installing or repairing the automatic schedule...", "info")

        def worker():
            try:
                result = subprocess.run(
                    command,
                    cwd=str(APP_DIR),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=180,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                self.root.after(0, self._finish_schedule_setup, result, None)
            except Exception as exc:
                self.root.after(0, self._finish_schedule_setup, None, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_schedule_setup(self, result, error):
        """Refresh scheduler UI and report the completed Agent UAC result."""
        self.schedule_setup_in_progress = False
        if hasattr(self, "schedule_setup_btn"):
            try:
                self.schedule_setup_btn.config(state=tk.NORMAL)
            except tk.TclError:
                pass
        if hasattr(self, "schedule_remove_btn"):
            try:
                self.schedule_remove_btn.config(state=tk.NORMAL)
            except tk.TclError:
                pass

        self._start_scheduler_status_refresh()

        if error:
            self.log(f"Automatic schedule setup failed: {error}", "error")
            messagebox.showerror("Schedule Setup Failed", error)
            return

        output = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if part.strip()
        )
        if result.returncode == 0:
            self.log("Automatic schedule installed and verified.", "success")
            messagebox.showinfo(
                "Schedule Ready",
                "The automatic schedule is installed and verified.\n\n"
                "It will run every 15 minutes from 07:13 through 21:58. "
                "AC/DC wake timers were requested; actual wake support depends on "
                "Windows and the PC hardware.",
            )
            return

        detail = output[-3000:] if output else f"Agent UAC exited with code {result.returncode}."
        self.log(
            f"Automatic schedule setup failed (exit {result.returncode}): {detail}",
            "error",
        )
        messagebox.showerror(
            "Schedule Setup Failed",
            f"The schedule was not installed (exit {result.returncode}).\n\n{detail}",
        )

    def open_config(self):
        """Open config file in default editor."""
        if CONFIG_FILE.exists():
            os.startfile(str(CONFIG_FILE))
        else:
            messagebox.showerror("Error", "Config file not found!")

    def open_logs_folder(self):
        """Open logs folder in Explorer."""
        if LOGS_DIR.exists():
            os.startfile(str(LOGS_DIR))
        else:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(LOGS_DIR))

    def open_todays_log(self):
        """Open today's log file."""
        today_log = LOGS_DIR / f"asimut_{datetime.now().strftime('%Y%m%d')}.log"
        if today_log.exists():
            os.startfile(str(today_log))
        else:
            messagebox.showinfo("No Log", "No log file for today yet.")

    def show_logs_viewer(self):
        """Show log viewer dialog with date folders and log files."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Log Viewer")
        dialog.geometry("1000x700")
        dialog.transient(self.root)

        # Main container
        main_frame = ttk.Frame(dialog, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Left panel - date/file tree
        left_frame = ttk.Frame(main_frame, width=250)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left_frame.pack_propagate(False)

        ttk.Label(left_frame, text="Log Files", font=("Segoe UI", 12, "bold")).pack(anchor=tk.W, pady=(0, 10))

        # Treeview for dates and log files
        tree_frame = ttk.Frame(left_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        tree_scroll = ttk.Scrollbar(tree_frame)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_tree = ttk.Treeview(tree_frame, yscrollcommand=tree_scroll.set, show="tree")
        self.log_tree.pack(fill=tk.BOTH, expand=True)
        tree_scroll.config(command=self.log_tree.yview)

        # Right panel - log content
        right_frame = ttk.Frame(main_frame)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Header with current file name
        header_frame = ttk.Frame(right_frame)
        header_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(header_frame, text="Log Content", font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)

        self.current_log_var = tk.StringVar(value="Select a log file")
        ttk.Label(header_frame, textvariable=self.current_log_var, foreground="gray").pack(side=tk.RIGHT)

        # Log content text area
        text_frame = ttk.Frame(right_frame)
        text_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(
            text_frame,
            wrap=tk.WORD,
            font=("Consolas", 10),
            state=tk.DISABLED
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Bottom buttons
        btn_frame = ttk.Frame(right_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Button(btn_frame, text="Open in Explorer", command=self.open_logs_folder).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Refresh", command=lambda: self._populate_log_tree()).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Close", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)

        # Bind tree selection
        self.log_tree.bind("<<TreeviewSelect>>", self._on_log_select)

        # Populate tree
        self._populate_log_tree()

    def _populate_log_tree(self):
        """Populate the log tree with date folders and log files."""
        # Clear existing items
        for item in self.log_tree.get_children():
            self.log_tree.delete(item)

        if not LOGS_DIR.exists():
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            return

        # Get all date folders (format: YYYY-MM-DD) and individual log files
        items = []

        for item in LOGS_DIR.iterdir():
            if item.is_dir() and len(item.name) == 10 and item.name[4] == '-':
                # Date folder
                items.append(('folder', item.name, item))
            elif item.is_file() and item.suffix == '.log':
                # Individual log file at root level
                items.append(('file', item.name, item))

        # Sort by name descending (newest first)
        items.sort(key=lambda x: x[1], reverse=True)

        for item_type, name, path in items:
            if item_type == 'folder':
                # Format date nicely: 2026-02-02 -> "Sun 2 Feb 2026"
                try:
                    date_obj = datetime.strptime(name, "%Y-%m-%d")
                    display_name = date_obj.strftime("%a %d %b %Y")
                except:
                    display_name = name

                folder_id = self.log_tree.insert("", tk.END, text=f"📁 {display_name}", values=(str(path),), open=False)

                # Add log files in this folder
                log_files = sorted(path.glob("*.log"), reverse=True)
                for log_file in log_files:
                    # Format time: 07-30-00.log -> "07:30:00"
                    time_name = log_file.stem.replace("-", ":")
                    self.log_tree.insert(folder_id, tk.END, text=f"📄 {time_name}", values=(str(log_file),))

            else:
                # Root-level log file
                self.log_tree.insert("", tk.END, text=f"📄 {name}", values=(str(path),))

    def _on_log_select(self, event):
        """Handle log tree selection."""
        selection = self.log_tree.selection()
        if not selection:
            return

        item = selection[0]
        values = self.log_tree.item(item, "values")

        if not values:
            return

        file_path = Path(values[0])

        # Only load if it's a file, not a folder
        if file_path.is_file():
            self._load_log_content(file_path)

    def _load_log_content(self, log_path):
        """Load and display log file content."""
        try:
            with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()

            self.log_text.config(state=tk.NORMAL)
            self.log_text.delete(1.0, tk.END)
            self.log_text.insert(tk.END, content)
            self.log_text.config(state=tk.DISABLED)

            # Update header with file info
            rel_path = log_path.relative_to(LOGS_DIR) if LOGS_DIR in log_path.parents or log_path.parent == LOGS_DIR else log_path.name
            self.current_log_var.set(str(rel_path))

        except Exception as e:
            self.log_text.config(state=tk.NORMAL)
            self.log_text.delete(1.0, tk.END)
            self.log_text.insert(tk.END, f"Error loading log: {e}")
            self.log_text.config(state=tk.DISABLED)
            self.current_log_var.set("Error")

    def show_about(self):
        """Show about dialog."""
        msg = "AsimutBooker Control Panel\n\n"
        msg += "Automated practice room booking for RWCMD.\n\n"
        msg += "Features:\n"
        msg += "• Auto-books rooms as they become available\n"
        msg += "• Respects all booking rules (quota, peak hours, etc.)\n"
        msg += "• Requests scheduled wake-from-sleep when Windows and hardware support it\n\n"
        msg += f"App Directory: {APP_DIR}"

        messagebox.showinfo("About AsimutBooker", msg)

    def load_history(self):
        """Load booking history from file."""
        try:
            return load_history_document(HISTORY_FILE)
        except SettingsError as exc:
            if hasattr(self, "log_text"):
                self.log(str(exc), "error")
            return {"runs": []}

    def save_history(self, history):
        """Atomically replace booking history under the booker's shared lock."""
        try:
            replace_history_document(history, HISTORY_FILE)
            return True
        except SettingsError as exc:
            if hasattr(self, "log_text"):
                self.log(str(exc), "error")
            messagebox.showerror("Booking History Error", str(exc))
            return False

    def load_settings(self):
        """Load settings strictly; an unreadable existing file disables all saves."""
        if not self.settings_available and self.settings_error:
            return {}
        try:
            return strict_load_settings(SETTINGS_FILE)
        except SettingsError as exc:
            self._set_settings_error(str(exc))
            return {}

    def _update_settings(self, mutator, *, interactive=True):
        """Run one locked, atomic, scoped settings update."""
        if not self.settings_available:
            if interactive:
                messagebox.showerror(
                    "Settings Unavailable",
                    self.settings_error or "Settings cannot be saved safely.",
                )
            return False
        try:
            atomic_update_settings(mutator, SETTINGS_FILE)
            return True
        except (SettingsError, PracticePlanError, TypeError, ValueError) as exc:
            self._set_settings_error(str(exc))
            if interactive:
                messagebox.showerror("Settings Error", str(exc))
            return False

    def _set_settings_error(self, message):
        """Fail closed and make the unsafe settings state visible in the GUI."""
        self.settings_available = False
        self.settings_error = f"Settings unavailable — saving disabled: {message}"

        def apply_state():
            self._apply_settings_availability_state()
            if hasattr(self, "log_text") and not self._settings_error_reported:
                self.log(self.settings_error, "error")
                self._settings_error_reported = True

        if threading.current_thread() is threading.main_thread():
            apply_state()
        else:
            self.root.after(0, apply_state)

    def _apply_settings_availability_state(self):
        """Disable every preference-writing control after a strict-load failure."""
        if hasattr(self, "settings_status_var"):
            self.settings_status_var.set("" if self.settings_available else self.settings_error)

        if not self.settings_available:
            for widget in self._settings_controls:
                try:
                    widget.configure(state=tk.DISABLED)
                except (tk.TclError, AttributeError):
                    pass
            return

        if hasattr(self, "practice_plan_enabled"):
            self._update_practice_plan_ui_state()
        if hasattr(self, "time_prefs_enabled"):
            self._update_time_prefs_ui_state()

    def load_booking_days(self):
        """Load enabled booking days from settings."""
        settings = self.load_settings()
        disabled_raw = settings.get("disabled_dates", [])
        if not isinstance(disabled_raw, list) or not all(isinstance(item, str) for item in disabled_raw):
            self._set_settings_error("disabled_dates must be a list of YYYY-MM-DD strings")
            disabled_raw = []
        try:
            disabled_dates = {
                validate_iso_date_key(item, "Disabled date") for item in disabled_raw
            }
        except PracticePlanError as exc:
            self._set_settings_error(str(exc))
            disabled_dates = set()

        today = datetime.now().date()
        if not hasattr(self, "room_catalog"):
            self.room_catalog = None
            self.room_catalog_load_error = ""
            self.load_room_catalog_cache()
        try:
            current_dates = catalog_booking_dates(self.room_catalog, today=today)
        except ValueError as exc:
            current_dates = ()
            self.room_catalog_load_error = f"Room booking window is unavailable: {exc}"
        self.booking_dates = current_dates
        self.day_vars = {}
        for target_date in current_dates:
            date_str = target_date.strftime('%Y-%m-%d')
            # Default to enabled unless in disabled list
            var = tk.BooleanVar(value=(date_str not in disabled_dates))
            self.day_vars[date_str] = var

    def save_booking_days(self, day_states: Mapping[str, bool]):
        """Save only dates edited in this dialog against the latest settings."""

        if not day_states:
            self.load_booking_days()
            self._update_days_summary()
            self._update_practice_plan_summary()
            return True

        if self._update_settings(
            lambda settings: apply_booking_day_updates(settings, day_states)
        ):
            # Refresh every displayed date from the merged document so a
            # concurrent out-of-scope edit is immediately visible in this GUI.
            self.load_booking_days()
            self._update_days_summary()
            self._update_practice_plan_summary()
            return True
        return False

    def _update_days_summary(self):
        """Update the days summary label."""
        current_dates = tuple(getattr(self, "booking_dates", ()))
        if not current_dates:
            self.days_summary_var.set(
                "Booking window unavailable — run a read-only site refresh"
            )
            return
        enabled_count = 0
        total_count = len(current_dates)

        for target_date in current_dates:
            date_str = target_date.strftime('%Y-%m-%d')
            if date_str in self.day_vars and self.day_vars[date_str].get():
                enabled_count += 1

        if enabled_count == total_count:
            self.days_summary_var.set(f"All {total_count} days enabled for booking")
        elif enabled_count == 0:
            self.days_summary_var.set("No days enabled for booking")
        else:
            self.days_summary_var.set(f"{enabled_count}/{total_count} days enabled for booking")

    def load_practice_plan_settings(self):
        """Load and validate the optional per-day practice plan."""
        settings = self.load_settings()
        try:
            plan = load_practice_plan(settings)
        except PracticePlanError as exc:
            self._set_settings_error(str(exc))
            plan = PracticePlan()

        self._install_confirmed_practice_plan(plan)

    def _install_confirmed_practice_plan(self, plan: PracticePlan):
        """Synchronize every compact control from one successfully saved plan."""
        self.practice_plan = plan
        self.practice_plan_overrides = dict(plan.date_overrides or {})
        self.practice_plan_enabled.set(plan.enabled)
        self.practice_default_hours.set(format_practice_hours(plan.default_hours))
        self._update_practice_plan_ui_state()
        self._update_practice_plan_summary()

    def _validated_default_practice_hours(self):
        """Return the main control's target using the shared schema validator."""
        try:
            raw_hours = float(self.practice_default_hours.get().strip())
        except ValueError as exc:
            raise PracticePlanError("Default daily target must be a number") from exc
        plan = load_practice_plan(
            {"practice_plan": {"enabled": True, "default_hours": raw_hours}}
        )
        return plan.default_hours

    def on_practice_plan_changed(self):
        """Validate and persist the compact practice-plan controls."""
        if not self.settings_available:
            return
        try:
            default_hours = self._validated_default_practice_hours()
        except PracticePlanError as exc:
            self._restore_practice_plan_controls()
            messagebox.showerror("Invalid Practice Target", str(exc))
            return

        enabled = self.practice_plan_enabled.get()
        previous_plan = self.practice_plan
        enabled_changed = enabled != previous_plan.enabled
        default_changed = default_hours != previous_plan.default_hours
        saved_plan = []

        def mutate(settings):
            current_plan = load_practice_plan(settings)
            candidate = {
                "enabled": enabled if enabled_changed else current_plan.enabled,
                "default_hours": (
                    default_hours if default_changed else current_plan.default_hours
                ),
                "date_overrides": current_plan.date_overrides or {},
            }
            plan = load_practice_plan({"practice_plan": candidate})
            settings["practice_plan"] = {
                "enabled": plan.enabled,
                "default_hours": plan.default_hours,
                "date_overrides": plan.date_overrides or {},
            }
            saved_plan.append(plan)

        if self._update_settings(mutate):
            self._install_confirmed_practice_plan(saved_plan[0])
        else:
            # The click/spinbox callback has already changed the Tk variables.
            # Never leave those unsaved values visible after a failed write.
            self._restore_practice_plan_controls()

    def _restore_practice_plan_controls(self):
        """Restore compact controls to the last plan confirmed in settings."""
        self._install_confirmed_practice_plan(self.practice_plan)

    def _update_practice_plan_ui_state(self):
        """Reflect whether daily targets and settings persistence are available."""
        if not hasattr(self, "practice_default_spin"):
            return
        if not self.settings_available:
            self.practice_plan_enable_cb.configure(state=tk.DISABLED)
            self.practice_default_spin.configure(state=tk.DISABLED)
            self.practice_plan_customize_btn.configure(state=tk.DISABLED)
            return

        self.practice_plan_enable_cb.configure(state=tk.NORMAL)
        self.practice_default_spin.configure(
            state=tk.NORMAL if self.practice_plan_enabled.get() else tk.DISABLED
        )
        self.practice_plan_customize_btn.configure(state=tk.NORMAL)

    def _update_practice_plan_summary(self):
        """Show a concise summary for the catalog-derived booking window."""
        if not hasattr(self, "practice_plan_summary_var"):
            return
        if not self.practice_plan_enabled.get():
            self.practice_plan_summary_var.set("Daily targets off — using legacy allocation")
            return

        current_dates = tuple(getattr(self, "booking_dates", ()))
        if not current_dates:
            self.practice_plan_summary_var.set("Booking window unavailable")
            return
        enabled_count = 0
        override_parts = []
        for target_date in current_dates:
            date_key = target_date.isoformat()
            if date_key in self.day_vars and self.day_vars[date_key].get():
                enabled_count += 1
                if date_key in self.practice_plan_overrides:
                    override_parts.append(
                        f"{target_date.strftime('%a')} {format_practice_hours(self.practice_plan_overrides[date_key])}h"
                    )

        default_text = format_practice_hours(self.practice_plan.default_hours)
        summary = f"{enabled_count}/{len(current_dates)} days • {default_text}h default"
        if override_parts:
            shown = ", ".join(override_parts[:3])
            if len(override_parts) > 3:
                shown += f" +{len(override_parts) - 3} more"
            summary += f" • {shown}"
        self.practice_plan_summary_var.set(summary)

    def load_room_catalog_cache(self):
        """Load the strict display-only cache without authorizing booking policy."""

        try:
            # Keep this import local so a missing optional timezone dependency
            # leaves the control panel usable with its saved preference list.
            from room_catalog import load_cached_catalog

            self.room_catalog = load_cached_catalog(missing_ok=True)
            self.room_catalog_load_error = ""
        except Exception as exc:
            self.room_catalog = None
            self.room_catalog_load_error = (
                f"Room catalog is unavailable: {exc}. Using the saved room list."
            )
        return self.room_catalog

    def load_room_preferences_settings(self):
        """Load strict room preferences and reconcile site-discovered rooms."""

        settings = self.load_settings()
        try:
            preferences = strict_load_room_preferences(settings)
        except RoomPreferencesError as exc:
            self._set_settings_error(str(exc))
            preferences = RoomPreferences()
        self._install_confirmed_room_preferences(preferences)

    def _install_confirmed_room_preferences(
        self,
        preferences: RoomPreferences,
    ) -> None:
        """Install one confirmed model and refresh both room summary labels."""

        catalog = getattr(self, "room_catalog", None)
        metadata, catalog_status = room_catalog_ui_view(catalog)
        if catalog is None and getattr(self, "room_catalog_load_error", ""):
            catalog_status = self.room_catalog_load_error
        if metadata is not None:
            try:
                preferences = reconcile_room_preferences(preferences, metadata)
            except RoomPreferencesError as exc:
                metadata = None
                catalog_status = f"Room catalog cannot be reconciled safely: {exc}"

        self.room_preferences = preferences
        self.room_catalog_metadata = metadata
        try:
            summary = summarize_room_preferences(preferences, metadata)
            catalog_is_current_live = (
                _catalog_attribute(catalog, "source") == "live"
                and _catalog_attribute(catalog, "fresh", True) is not False
            )
            if metadata is not None and not catalog_is_current_live:
                summary = summary.replace(" live eligible room", " catalog eligible room")
        except (RoomPreferencesError, TypeError, ValueError) as exc:
            # A display-only metadata problem must never turn an otherwise
            # valid saved preference document into a silently different one.
            summary = summarize_room_preferences(preferences)
            catalog_status = f"Room catalog cannot be used safely: {exc}"
        if hasattr(self, "room_preferences_summary_var"):
            self.room_preferences_summary_var.set(summary)
        if hasattr(self, "room_catalog_status_var"):
            self.room_catalog_status_var.set(catalog_status)

    def save_room_preferences_settings(
        self,
        candidate: RoomPreferences,
        *,
        opening_preferences: RoomPreferences | None = None,
    ) -> bool:
        """Persist only edited room fields and restore confirmed state on failure."""

        previous = self.room_preferences
        opening = opening_preferences or previous
        try:
            # Round-trip through the strict schema even when the caller built a
            # dataclass. This keeps this public save boundary fail-closed.
            candidate = strict_load_room_preferences(
                {"room_preferences": room_preferences_to_dict(candidate)}
            )
            updates = changed_room_preference_fields(opening, candidate)
        except (RoomPreferencesError, TypeError, ValueError) as exc:
            self._install_confirmed_room_preferences(previous)
            messagebox.showerror("Invalid Room Preferences", str(exc))
            return False

        if not updates:
            # A no-op Save still resynchronizes from disk so concurrent changes
            # do not leave this window displaying a stale in-memory model.
            self.load_room_preferences_settings()
            return self.settings_available

        saved_preferences = []

        def mutate(settings):
            scoped_updates = dict(updates)
            if "excluded_rooms" in scoped_updates and "ordered_rooms" not in scoped_updates:
                # A newly discovered catalog room may be excluded before its
                # rank was ever persisted. Append only that missing dependency
                # to the latest concurrent order instead of replacing it with
                # the dialog's unedited opening order.
                current = strict_load_room_preferences(settings)
                missing = [
                    room
                    for room in candidate.excluded_rooms
                    if room not in current.ordered_rooms
                ]
                if missing:
                    scoped_updates["ordered_rooms"] = list(current.ordered_rooms) + missing
            saved_preferences.append(
                apply_room_preferences_update(settings, scoped_updates)
            )

        if not self._update_settings(mutate):
            self._install_confirmed_room_preferences(previous)
            return False
        self._install_confirmed_room_preferences(saved_preferences[0])
        return True

    def show_room_preferences_dialog(self):
        """Edit room ranking, exclusions, requirements, and session shape."""

        if not self.settings_available:
            messagebox.showerror("Settings Unavailable", self.settings_error)
            return

        opening_preferences = self.room_preferences
        catalog_metadata = getattr(self, "room_catalog_metadata", None)
        dialog = tk.Toplevel(self.root)
        dialog.title("Room Preferences")
        dialog.geometry("980x720")
        dialog.minsize(860, 650)
        dialog.transient(self.root)
        dialog.grab_set()

        content = ttk.Frame(dialog, padding="18")
        content.pack(fill=tk.BOTH, expand=True)
        content.columnconfigure(0, weight=3)
        content.columnconfigure(1, weight=2)
        content.rowconfigure(2, weight=1)
        ttk.Label(content, text="Room Preferences", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 4)
        )
        ttk.Label(
            content,
            text=(
                "Rooms at the top are tried first. Excluded rooms are never selected. "
                "Tag filters accept any listed tag; every required feature phrase must match."
            ),
            foreground="#555555",
            wraplength=900,
            justify=tk.LEFT,
        ).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(0, 14))

        rooms_frame = ttk.LabelFrame(content, text="Room order and exclusions", padding="10")
        rooms_frame.grid(row=2, column=0, sticky="nsew", padx=(0, 12))
        rooms_frame.columnconfigure(0, weight=1)
        rooms_frame.rowconfigure(1, weight=1)
        ttk.Label(
            rooms_frame,
            text="Use Up and Down to set priority. Double-click a room to include or exclude it.",
            foreground="#555555",
            wraplength=520,
            justify=tk.LEFT,
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))

        tree = ttk.Treeview(
            rooms_frame,
            columns=("priority", "room", "status"),
            show="headings",
            selectmode="browse",
            height=16,
        )
        tree.heading("priority", text="#")
        tree.heading("room", text="Room")
        tree.heading("status", text="Booking")
        tree.column("priority", width=45, minwidth=40, anchor=tk.CENTER, stretch=False)
        tree.column("room", width=180, anchor=tk.W)
        tree.column("status", width=110, anchor=tk.CENTER, stretch=False)
        tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(rooms_frame, orient=tk.VERTICAL, command=tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)

        working_order = list(opening_preferences.ordered_rooms)
        working_excluded = set(opening_preferences.excluded_rooms)
        excluded_status_var = tk.StringVar()

        def selected_index():
            selection = tree.selection()
            if not selection:
                return -1
            try:
                return int(selection[0])
            except (TypeError, ValueError):
                return -1

        def refresh_rooms(select_at=-1):
            children = tree.get_children()
            if children:
                tree.delete(*children)
            for index, room in enumerate(working_order):
                tree.insert(
                    "",
                    tk.END,
                    iid=str(index),
                    values=(index + 1, room, "Excluded" if room in working_excluded else "Use"),
                    tags=("excluded",) if room in working_excluded else (),
                )
            tree.tag_configure("excluded", foreground="#777777")
            excluded_status_var.set(
                f"{len(working_excluded)} excluded • "
                f"{len(working_order) - len(working_excluded)} available before filters"
            )
            if 0 <= select_at < len(working_order):
                item = str(select_at)
                tree.selection_set(item)
                tree.focus(item)
                tree.see(item)

        def move_selected(direction):
            current = selected_index()
            moved, destination = move_room_preference_item(
                working_order, current, direction
            )
            working_order[:] = moved
            refresh_rooms(destination)

        def toggle_selected(_event=None):
            index = selected_index()
            if index < 0:
                return
            room = working_order[index]
            if room in working_excluded:
                working_excluded.remove(room)
            else:
                working_excluded.add(room)
            refresh_rooms(index)

        room_buttons = ttk.Frame(rooms_frame)
        room_buttons.grid(row=2, column=0, columnspan=2, sticky=tk.EW, pady=(9, 0))
        ttk.Button(
            room_buttons,
            text="↑ Up",
            command=lambda: move_selected(-1),
            width=9,
        ).pack(side=tk.LEFT)
        ttk.Button(
            room_buttons,
            text="↓ Down",
            command=lambda: move_selected(1),
            width=9,
        ).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(
            room_buttons,
            text="Include / Exclude",
            command=toggle_selected,
            width=18,
        ).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(
            rooms_frame,
            textvariable=excluded_status_var,
            foreground="#555555",
            font=("Segoe UI", 9),
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(7, 0))
        tree.bind("<Double-1>", toggle_selected)
        tree.bind("<Alt-Up>", lambda _event: move_selected(-1))
        tree.bind("<Alt-Down>", lambda _event: move_selected(1))

        requirements = ttk.LabelFrame(content, text="Room and session requirements", padding="12")
        requirements.grid(row=2, column=1, sticky="nsew")
        requirements.columnconfigure(0, weight=1)
        instrument_var = tk.StringVar()
        room_type_var = tk.StringVar()
        features_var = tk.StringVar()
        minimum_block_var = tk.StringVar()
        fragmented_var = tk.BooleanVar()

        def add_text_control(row, title, explanation, variable):
            ttk.Label(requirements, text=title, font=("Segoe UI", 10, "bold")).grid(
                row=row, column=0, sticky=tk.W, pady=(0, 2)
            )
            ttk.Entry(requirements, textvariable=variable).grid(
                row=row + 1, column=0, sticky=tk.EW
            )
            ttk.Label(
                requirements,
                text=explanation,
                foreground="#555555",
                font=("Segoe UI", 9),
                wraplength=340,
                justify=tk.LEFT,
            ).grid(row=row + 2, column=0, sticky=tk.W, pady=(2, 11))

        add_text_control(
            0,
            "Acceptable instrument tags",
            "Comma separated. Blank accepts any instrument; otherwise a room may match any listed tag.",
            instrument_var,
        )
        add_text_control(
            3,
            "Acceptable room-type tags",
            "Comma separated. Blank accepts any type; otherwise a room may match any listed tag.",
            room_type_var,
        )
        add_text_control(
            6,
            "Required features",
            "Comma separated. Every phrase is required, for example: grand piano, adjustable stool.",
            features_var,
        )

        ttk.Label(requirements, text="Minimum useful block", font=("Segoe UI", 10, "bold")).grid(
            row=9, column=0, sticky=tk.W, pady=(1, 2)
        )
        minimum_block = ttk.Combobox(
            requirements,
            textvariable=minimum_block_var,
            values=tuple(f"{minutes} minutes" for minutes in ROOM_MINIMUM_BLOCK_CHOICES),
            state="readonly",
            width=20,
        )
        minimum_block.grid(row=10, column=0, sticky=tk.W)
        ttk.Label(
            requirements,
            text="Shorter openings will be ignored.",
            foreground="#555555",
            font=("Segoe UI", 9),
        ).grid(row=11, column=0, sticky=tk.W, pady=(2, 11))
        ttk.Checkbutton(
            requirements,
            text="Allow fragmented sessions",
            variable=fragmented_var,
        ).grid(row=12, column=0, sticky=tk.W)
        ttk.Label(
            requirements,
            text="When off, the booker looks for one continuous block instead of combining shorter openings.",
            foreground="#555555",
            font=("Segoe UI", 9),
            wraplength=340,
            justify=tk.LEFT,
        ).grid(row=13, column=0, sticky=tk.W, pady=(2, 10))

        known_values = {}
        for key in ("instrument_tags", "room_type_tags", "features"):
            known_values[key] = sorted(
                {
                    value
                    for metadata in (catalog_metadata or {}).values()
                    for value in metadata.get(key, [])
                },
                key=str.casefold,
            )
        detected = []
        if known_values["instrument_tags"]:
            detected.append("Instruments: " + ", ".join(known_values["instrument_tags"][:6]))
        if known_values["room_type_tags"]:
            detected.append("Types: " + ", ".join(known_values["room_type_tags"][:6]))
        if known_values["features"]:
            detected.append("Features: " + ", ".join(known_values["features"][:6]))
        ttk.Label(
            requirements,
            text=("Detected on the site — " + " • ".join(detected)) if detected else "No site metadata is loaded yet; saved terms can still be edited.",
            foreground="#555555",
            font=("Segoe UI", 8),
            wraplength=340,
            justify=tk.LEFT,
        ).grid(row=14, column=0, sticky=tk.W, pady=(5, 0))

        def reset_working(preferences):
            working_order[:] = preferences.ordered_rooms
            working_excluded.clear()
            working_excluded.update(preferences.excluded_rooms)
            instrument_var.set(", ".join(preferences.acceptable_instrument_tags))
            room_type_var.set(", ".join(preferences.acceptable_room_type_tags))
            features_var.set(", ".join(preferences.required_feature_terms))
            minimum_block_var.set(f"{preferences.minimum_block_minutes} minutes")
            fragmented_var.set(preferences.allow_fragmented_sessions)
            refresh_rooms(0 if working_order else -1)

        reset_working(opening_preferences)

        buttons = ttk.Frame(dialog, padding=(18, 8, 18, 18))
        buttons.pack(fill=tk.X)

        def save_and_close():
            try:
                instrument_tags = parse_room_preference_terms(
                    instrument_var.get(), "instrument tags"
                )
                room_type_tags = parse_room_preference_terms(
                    room_type_var.get(), "room-type tags"
                )
                required_features = parse_room_preference_terms(
                    features_var.get(), "required features"
                )
                minimum_text = minimum_block_var.get().removesuffix(" minutes")
                candidate = strict_load_room_preferences(
                    {
                        "room_preferences": {
                            "ordered_rooms": list(working_order),
                            "excluded_rooms": [
                                room for room in working_order if room in working_excluded
                            ],
                            "acceptable_instrument_tags": list(instrument_tags),
                            "acceptable_room_type_tags": list(room_type_tags),
                            "required_feature_terms": list(required_features),
                            "minimum_block_minutes": int(minimum_text),
                            "allow_fragmented_sessions": fragmented_var.get(),
                        }
                    }
                )
            except (RoomPreferencesError, TypeError, ValueError) as exc:
                reset_working(self.room_preferences)
                messagebox.showerror(
                    "Invalid Room Preferences", str(exc), parent=dialog
                )
                return

            if not self.save_room_preferences_settings(
                candidate,
                opening_preferences=opening_preferences,
            ):
                reset_working(self.room_preferences)
                return
            self.log("Room preferences saved", "info")
            dialog.destroy()

        ttk.Button(
            buttons,
            text="Save Preferences",
            command=save_and_close,
            width=18,
        ).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(
            buttons,
            text="Cancel",
            command=dialog.destroy,
            width=10,
        ).pack(side=tk.RIGHT)
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

    def show_practice_plan_dialog(self):
        """Edit enabled days and per-date targets using isolated working copies."""
        if not self.settings_available:
            messagebox.showerror("Settings Unavailable", self.settings_error)
            return
        window_dates = tuple(getattr(self, "booking_dates", ()))
        if not window_dates:
            messagebox.showerror(
                "Booking Window Unavailable",
                "Run a read-only site refresh before editing date-specific targets.",
            )
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Customize Practice Plan")
        dialog.geometry("720x570")
        dialog.minsize(650, 520)
        dialog.transient(self.root)
        dialog.grab_set()

        content = ttk.Frame(dialog, padding="18")
        content.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            content,
            text=f"Current Booking Window ({len(window_dates)} days)",
            font=("Segoe UI", 16, "bold"),
        ).grid(
            row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 4)
        )
        ttk.Label(
            content,
            text=(
                "Turn days off or choose a target that replaces the default for that date. "
                "Saving this screen turns daily targets on. Targets are aims: existing events, "
                "room availability, and RWCMD booking limits can mean less is booked."
            ),
            foreground="#555555",
            wraplength=660,
            justify=tk.LEFT,
        ).grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(0, 14))
        ttk.Label(content, text="Book", font=("Segoe UI", 11, "bold")).grid(row=2, column=0, sticky=tk.W)
        ttk.Label(content, text="Date", font=("Segoe UI", 11, "bold")).grid(row=2, column=1, sticky=tk.W)
        ttk.Label(content, text="Desired practice", font=("Segoe UI", 11, "bold")).grid(row=2, column=2, sticky=tk.W)

        try:
            default_hours = self._validated_default_practice_hours()
        except PracticePlanError as exc:
            messagebox.showerror("Invalid Practice Target", str(exc))
            dialog.destroy()
            return
        default_choice = f"Default ({format_practice_hours(default_hours)}h)"
        choices = (default_choice,) + PRACTICE_TARGET_CHOICES
        day_working = {}
        target_working = {}
        target_widgets = {}
        opening_day_states = {}
        opening_overrides = {}
        today = window_dates[0]

        def update_row_state(date_key):
            target_widgets[date_key].configure(
                state="readonly" if day_working[date_key].get() else tk.DISABLED
            )

        for offset, target_date in enumerate(window_dates):
            date_key = target_date.isoformat()
            row = offset + 3
            enabled = self.day_vars.get(date_key).get() if date_key in self.day_vars else True
            opening_day_states[date_key] = enabled
            day_working[date_key] = tk.BooleanVar(value=enabled)
            target_value = self.practice_plan_overrides.get(date_key)
            opening_overrides[date_key] = target_value
            selected = (
                f"{format_practice_hours(target_value)}h"
                if target_value is not None
                else default_choice
            )
            target_working[date_key] = tk.StringVar(value=selected)

            ttk.Checkbutton(
                content,
                variable=day_working[date_key],
                command=lambda key=date_key: update_row_state(key),
            ).grid(row=row, column=0, sticky=tk.W, padx=(4, 16), pady=5)
            label = f"{target_date.strftime('%A')} {target_date.day} {target_date.strftime('%B')}"
            if offset == 0:
                label += " (Today)"
            ttk.Label(content, text=label).grid(row=row, column=1, sticky=tk.W, padx=(0, 24), pady=5)
            target_widgets[date_key] = ttk.Combobox(
                content,
                textvariable=target_working[date_key],
                values=choices,
                state="readonly" if enabled else tk.DISABLED,
                width=19,
            )
            target_widgets[date_key].grid(row=row, column=2, sticky=tk.W, pady=5)

        content.columnconfigure(1, weight=1)

        buttons = ttk.Frame(dialog, padding=(18, 8, 18, 18))
        buttons.pack(fill=tk.X)

        def save_and_close():
            states = {key: var.get() for key, var in day_working.items()}
            overrides = {}
            try:
                for date_key, var in target_working.items():
                    choice = var.get()
                    overrides[date_key] = (
                        None if choice == default_choice else parse_practice_target_choice(choice)
                    )
            except PracticePlanError as exc:
                messagebox.showerror("Invalid Practice Target", str(exc), parent=dialog)
                return

            changed_states = {
                date_key: enabled
                for date_key, enabled in states.items()
                if enabled != opening_day_states[date_key]
            }
            changed_overrides = {
                date_key: target
                for date_key, target in overrides.items()
                if target != opening_overrides[date_key]
            }

            saved_plan = []

            def mutate(settings):
                saved_plan.append(
                    apply_practice_plan_update(
                        settings,
                        # Saving a customized plan must never create silently
                        # inactive targets.  The top-level checkbox remains the
                        # explicit way to turn daily targets off again.
                        plan_enabled=True,
                        # This dialog does not edit the default. Preserve a
                        # concurrent top-level/default change made elsewhere.
                        default_hours=None,
                        day_states=changed_states,
                        date_overrides=changed_overrides,
                    )
                )

            if not self._update_settings(mutate):
                return

            # Reload the merged day document so concurrent, unedited rows are
            # reflected instead of leaving stale in-memory BooleanVars behind.
            self.load_booking_days()
            self._install_confirmed_practice_plan(saved_plan[0])
            self._update_days_summary()
            self.log("Practice plan saved", "info")
            dialog.destroy()

        ttk.Button(buttons, text="Save Plan", command=save_and_close, width=14).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy, width=10).pack(side=tk.RIGHT)
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

    def load_time_preferences(self):
        """Load time preferences strictly and fail closed on malformed values."""
        settings = self.load_settings()
        try:
            prefs = validate_time_preferences_section(settings)
        except SettingsError as exc:
            self._set_settings_error(str(exc))
            prefs = validate_time_preferences_section({})

        # Set enabled state
        self.time_prefs_enabled.set(prefs["enabled"])

        # Set preset
        preset = prefs["preset"]
        # Find the display name for this preset
        for display_name, preset_key in self.time_prefs_presets.items():
            if preset_key == preset:
                self.time_prefs_dropdown.set(display_name)
                break

        # Set custom hours and minutes
        self.custom_start_hour.set(str(prefs["custom_start_hour"]))
        self.custom_start_min.set(str(prefs["custom_start_min"]).zfill(2))
        self.custom_end_hour.set(str(prefs["custom_end_hour"]))
        self.custom_end_min.set(str(prefs["custom_end_min"]).zfill(2))

        # Set strict mode
        self.time_prefs_strict.set(prefs["strict_mode"])

        # Update UI state
        self._update_time_prefs_ui_state()

    def save_time_preferences(self):
        """Validate and save time preferences without silently coercing input."""
        # Get the preset key from the display name
        display_name = self.time_prefs_dropdown.get()
        preset_key = self.time_prefs_presets.get(display_name)
        if preset_key is None:
            messagebox.showerror(
                "Invalid Time Preferences",
                "Choose one of the listed time preference presets.",
            )
            return False

        # Get custom hours and minutes (validate)
        try:
            candidate = {
                "enabled": self.time_prefs_enabled.get(),
                "preset": preset_key,
                "custom_start_hour": int(self.custom_start_hour.get()),
                "custom_start_min": int(self.custom_start_min.get()),
                "custom_end_hour": int(self.custom_end_hour.get()),
                "custom_end_min": int(self.custom_end_min.get()),
                "strict_mode": self.time_prefs_strict.get(),
            }
            time_preferences = validate_time_preferences_section(
                {"time_preferences": candidate}
            )
        except (SettingsError, TypeError, ValueError) as exc:
            messagebox.showerror("Invalid Time Preferences", str(exc))
            return False

        self.custom_start_hour.set(str(time_preferences["custom_start_hour"]))
        self.custom_start_min.set(str(time_preferences["custom_start_min"]).zfill(2))
        self.custom_end_hour.set(str(time_preferences["custom_end_hour"]))
        self.custom_end_min.set(str(time_preferences["custom_end_min"]).zfill(2))

        return self._update_settings(
            lambda settings: settings.__setitem__("time_preferences", time_preferences)
        )

    def on_time_prefs_changed(self):
        """Handle changes to time preferences."""
        self._update_time_prefs_ui_state()
        self.save_time_preferences()

    def _update_time_prefs_ui_state(self):
        """Update enabled/disabled state of time preference controls."""
        if not self.settings_available:
            self.time_prefs_dropdown.config(state=tk.DISABLED)
            self.time_prefs_strict_cb.config(state=tk.DISABLED)
            return
        enabled = self.time_prefs_enabled.get()
        is_custom = self.time_prefs_dropdown.get() == "Custom..."

        if enabled:
            self.time_prefs_dropdown.config(state="readonly")
            self.time_prefs_strict_cb.config(state=tk.NORMAL)
        else:
            self.time_prefs_dropdown.config(state="disabled")
            self.time_prefs_strict_cb.config(state=tk.DISABLED)

        # Show/hide custom time frame (it's on a separate row now)
        if enabled and is_custom:
            self.custom_time_frame.pack(fill=tk.X, pady=(5, 0))
        else:
            self.custom_time_frame.pack_forget()

    def load_strategy_settings(self):
        """Load booking strategy strictly and fail closed on malformed values."""
        settings = self.load_settings()
        try:
            strategy = validate_booking_strategy_section(settings)
        except SettingsError as exc:
            self._set_settings_error(str(exc))
            strategy = validate_booking_strategy_section({})
        self.reverse_date_order.set(strategy["reverse_date_order"])
        # Smart swap disabled for now
        # self.smart_swap_enabled.set(strategy.get("smart_swap_enabled", False))

    def save_strategy_settings(self):
        """Save booking strategy settings to settings file."""
        try:
            booking_strategy = validate_booking_strategy_section(
                {
                    "booking_strategy": {
                        "reverse_date_order": self.reverse_date_order.get()
                    }
                }
            )
        except SettingsError as exc:
            messagebox.showerror("Invalid Booking Strategy", str(exc))
            return False
        return self._update_settings(
            lambda settings: settings.__setitem__("booking_strategy", booking_strategy)
        )

    def on_strategy_changed(self):
        """Handle changes to booking strategy settings."""
        self.save_strategy_settings()

    def add_history_entry(self, bookings_made, events_detected, details=""):
        """Add a new entry to booking history."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "bookings_made": bookings_made,
            "events_detected": events_detected,
            "details": details
        }
        try:
            append_history_entry(entry, HISTORY_FILE)
            return True
        except SettingsError as exc:
            if hasattr(self, "log_text"):
                self.log(str(exc), "error")
            messagebox.showerror("Booking History Error", str(exc))
            return False

    def show_history_dialog(self):
        """Show dialog with booking history."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Booking History")
        dialog.geometry("800x600")
        dialog.transient(self.root)

        # Header
        header_frame = ttk.Frame(dialog, padding="10")
        header_frame.pack(fill=tk.X)

        ttk.Label(header_frame, text="Booking History", font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)
        ttk.Button(header_frame, text="Refresh", command=lambda: self._refresh_history_list(tree)).pack(side=tk.RIGHT)
        ttk.Button(header_frame, text="Clear History", command=lambda: self._clear_history(tree)).pack(side=tk.RIGHT, padx=5)

        # Treeview for history
        tree_frame = ttk.Frame(dialog, padding="10")
        tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("Date/Time", "Bookings", "Events", "Details")
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=20)

        tree.heading("Date/Time", text="Date/Time")
        tree.heading("Bookings", text="Bookings Made")
        tree.heading("Events", text="Events Detected")
        tree.heading("Details", text="Details")

        tree.column("Date/Time", width=180)
        tree.column("Bookings", width=100, anchor=tk.CENTER)
        tree.column("Events", width=120, anchor=tk.CENTER)
        tree.column("Details", width=350)

        # Scrollbar
        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Load history
        self._refresh_history_list(tree)

        # Summary at bottom
        summary_frame = ttk.Frame(dialog, padding="10")
        summary_frame.pack(fill=tk.X)

        history = self.load_history()
        total_bookings = sum(r.get("bookings_made", 0) for r in history.get("runs", []))
        total_runs = len(history.get("runs", []))

        ttk.Label(summary_frame, text=f"Total runs: {total_runs}  |  Total bookings made: {total_bookings}",
                 font=("Segoe UI", 11)).pack(side=tk.LEFT)

        ttk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=10)

    def _refresh_history_list(self, tree):
        """Refresh the history treeview."""
        # Clear existing items
        for item in tree.get_children():
            tree.delete(item)

        history = self.load_history()

        for entry in history.get("runs", []):
            try:
                timestamp = datetime.fromisoformat(entry.get("timestamp", ""))
                date_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
            except:
                date_str = entry.get("timestamp", "Unknown")

            bookings = entry.get("bookings_made", 0)
            events = entry.get("events_detected", 0)
            details = entry.get("details", "")[:50]  # Truncate long details

            # Color based on success
            tag = "success" if bookings > 0 else "normal"
            tree.insert("", tk.END, values=(date_str, bookings, events, details), tags=(tag,))

        tree.tag_configure("success", foreground="green")
        tree.tag_configure("normal", foreground="black")

    def _clear_history(self, tree):
        """Clear all booking history."""
        if messagebox.askyesno("Clear History", "Are you sure you want to clear all booking history?"):
            if self.save_history({"runs": []}):
                self._refresh_history_list(tree)
                self.log("Booking history cleared.", "info")

    def show_calendar_dialog(self):
        """Show calendar dialog for selecting booking days."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Booking Calendar")
        dialog.geometry("1100x750")
        dialog.transient(self.root)
        dialog.grab_set()
        day_snapshot = {date_key: var.get() for date_key, var in self.day_vars.items()}

        # Store dialog reference for updates
        self.calendar_dialog = dialog

        # Calendar state
        self.calendar_view = tk.StringVar(value="month")  # month, fortnight, week, 3days
        self.calendar_start_date = datetime.now().date()
        self.calendar_events = {}  # {date_str: [events]}

        # Header frame
        header_frame = ttk.Frame(dialog, padding="10")
        header_frame.pack(fill=tk.X)

        ttk.Label(
            header_frame,
            text="Booking Calendar",
            font=("Segoe UI", 16, "bold")
        ).pack(side=tk.LEFT)

        # View selector
        view_frame = ttk.Frame(header_frame)
        view_frame.pack(side=tk.RIGHT)

        ttk.Label(view_frame, text="View:", font=("Segoe UI", 11)).pack(side=tk.LEFT, padx=(0, 10))

        views = [("Month", "month"), ("Fortnight", "fortnight"), ("Week", "week"), ("3 Days", "3days")]
        for text, value in views:
            rb = ttk.Radiobutton(
                view_frame,
                text=text,
                variable=self.calendar_view,
                value=value,
                command=self._refresh_calendar
            )
            rb.pack(side=tk.LEFT, padx=5)

        # Navigation frame
        nav_frame = ttk.Frame(dialog, padding="10")
        nav_frame.pack(fill=tk.X)

        ttk.Button(
            nav_frame,
            text="◀ Previous",
            command=lambda: self._navigate_calendar(-1),
            width=12
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            nav_frame,
            text="Today",
            command=self._go_to_today,
            width=10
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            nav_frame,
            text="Next ▶",
            command=lambda: self._navigate_calendar(1),
            width=12
        ).pack(side=tk.LEFT, padx=5)

        # Current period label
        self.calendar_period_var = tk.StringVar(value="")
        ttk.Label(
            nav_frame,
            textvariable=self.calendar_period_var,
            font=("Segoe UI", 14, "bold")
        ).pack(side=tk.LEFT, padx=30)

        # Selection buttons
        ttk.Button(
            nav_frame,
            text="Select All Visible",
            command=lambda: self._select_visible_days(True),
            width=16
        ).pack(side=tk.RIGHT, padx=5)

        ttk.Button(
            nav_frame,
            text="Deselect All Visible",
            command=lambda: self._select_visible_days(False),
            width=18
        ).pack(side=tk.RIGHT, padx=5)

        # Scan status
        self.calendar_scan_var = tk.StringVar(value="Scanning events...")
        ttk.Label(
            nav_frame,
            textvariable=self.calendar_scan_var,
            foreground="blue"
        ).pack(side=tk.RIGHT, padx=20)

        # Legend
        legend_frame = ttk.Frame(dialog, padding="5")
        legend_frame.pack(fill=tk.X)

        ttk.Label(legend_frame, text="Legend:", font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(10, 15))

        # Blue = selected
        legend_selected = tk.Frame(legend_frame, bg="#4a90d9", width=20, height=20)
        legend_selected.pack(side=tk.LEFT, padx=(0, 5))
        legend_selected.pack_propagate(False)
        ttk.Label(legend_frame, text="= Selected for booking", font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(0, 20))

        # White = deselected
        legend_deselected = tk.Frame(legend_frame, bg="white", width=20, height=20, highlightbackground="gray", highlightthickness=1)
        legend_deselected.pack(side=tk.LEFT, padx=(0, 5))
        legend_deselected.pack_propagate(False)
        ttk.Label(legend_frame, text="= Not selected", font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(0, 20))

        # Event indicator
        ttk.Label(legend_frame, text="•", font=("Segoe UI", 14), foreground="#e67e22").pack(side=tk.LEFT, padx=(0, 5))
        ttk.Label(legend_frame, text="= Has events", font=("Segoe UI", 10)).pack(side=tk.LEFT)

        # Calendar container with scrollbar
        calendar_container = ttk.Frame(dialog, padding="10")
        calendar_container.pack(fill=tk.BOTH, expand=True)

        # Canvas for calendar grid
        self.calendar_canvas = tk.Canvas(calendar_container, bg="white", highlightthickness=1, highlightbackground="gray")
        calendar_scrollbar_y = ttk.Scrollbar(calendar_container, orient="vertical", command=self.calendar_canvas.yview)
        calendar_scrollbar_x = ttk.Scrollbar(calendar_container, orient="horizontal", command=self.calendar_canvas.xview)

        self.calendar_canvas.configure(yscrollcommand=calendar_scrollbar_y.set, xscrollcommand=calendar_scrollbar_x.set)

        calendar_scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        calendar_scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.calendar_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Frame inside canvas for calendar content - use tk.Frame for bg color support
        self.calendar_frame = tk.Frame(self.calendar_canvas, bg="white")
        self.calendar_canvas_window = self.calendar_canvas.create_window((0, 0), window=self.calendar_frame, anchor="nw")

        # Update scroll region when frame changes
        def on_frame_configure(e):
            self.calendar_canvas.configure(scrollregion=self.calendar_canvas.bbox("all"))
        self.calendar_frame.bind("<Configure>", on_frame_configure)

        # Resize calendar frame to match canvas size
        def on_canvas_configure(e):
            # Make the frame fill the canvas
            canvas_width = e.width
            canvas_height = e.height
            self.calendar_canvas.itemconfig(self.calendar_canvas_window, width=canvas_width, height=canvas_height)
            # Store canvas height for cell sizing and refresh
            if hasattr(self, '_last_canvas_height') and self._last_canvas_height != canvas_height:
                self._last_canvas_height = canvas_height
                self._refresh_calendar()
            else:
                self._last_canvas_height = canvas_height
        self.calendar_canvas.bind("<Configure>", on_canvas_configure)

        # Enable mousewheel scrolling
        def _on_mousewheel(event):
            self.calendar_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.calendar_canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def cancel_and_close():
            # Calendar cells edit the live BooleanVars, so restore the opening snapshot.
            for date_key, enabled in day_snapshot.items():
                if date_key in self.day_vars:
                    self.day_vars[date_key].set(enabled)
            self._calendar_scan_generation += 1
            self.calendar_canvas.unbind_all("<MouseWheel>")
            self.calendar_dialog = None
            dialog.destroy()

        # Bottom buttons
        bottom_frame = ttk.Frame(dialog, padding="10")
        bottom_frame.pack(fill=tk.X)

        ttk.Button(
            bottom_frame,
            text="Save & Close",
            command=lambda: self._save_calendar_and_close(dialog, day_snapshot),
            width=15
        ).pack(side=tk.RIGHT, padx=5)

        ttk.Button(
            bottom_frame,
            text="Cancel",
            command=cancel_and_close,
            width=10
        ).pack(side=tk.RIGHT, padx=5)

        dialog.protocol("WM_DELETE_WINDOW", cancel_and_close)

        # Load cached events first (instant display)
        self._load_cached_events()

        # Initial render with cached data
        self._refresh_calendar()

        # Scan events in background (will update when complete)
        self._scan_calendar_events()

    def _load_cached_events(self):
        """Load cached events from settings for instant display."""
        settings = self.load_settings()
        cached_events = settings.get("cached_events", [])
        cache_timestamp = settings.get("cached_events_timestamp")

        if cached_events:
            # Group events by date
            events_by_date = {}
            for event in cached_events:
                date = event.get('date')
                if date:
                    if date not in events_by_date:
                        events_by_date[date] = []
                    events_by_date[date].append(event)

            self.calendar_events = events_by_date
            total_events = sum(len(events) for events in events_by_date.values())

            # Format cache age for display
            cache_info = ""
            if cache_timestamp:
                try:
                    cache_time = datetime.fromisoformat(cache_timestamp)
                    age = datetime.now() - cache_time
                    if age.total_seconds() < 60:
                        cache_info = " (from just now)"
                    elif age.total_seconds() < 3600:
                        mins = int(age.total_seconds() / 60)
                        cache_info = f" (from {mins}m ago)"
                    elif age.total_seconds() < 86400:
                        hours = int(age.total_seconds() / 3600)
                        cache_info = f" (from {hours}h ago)"
                    else:
                        days = int(age.total_seconds() / 86400)
                        cache_info = f" (from {days}d ago)"
                except:
                    pass

            self.calendar_scan_var.set(f"Showing {total_events} cached events{cache_info} - updating...")
        else:
            self.calendar_events = {}
            self.calendar_scan_var.set("No cached events. Scanning...")

    def _navigate_calendar(self, direction):
        """Navigate calendar forward or backward."""
        view = self.calendar_view.get()
        if view == "month":
            # Move by ~30 days
            self.calendar_start_date += timedelta(days=30 * direction)
            # Snap to first of month
            self.calendar_start_date = self.calendar_start_date.replace(day=1)
        elif view == "fortnight":
            self.calendar_start_date += timedelta(days=14 * direction)
        elif view == "week":
            self.calendar_start_date += timedelta(days=7 * direction)
        else:  # 3days
            self.calendar_start_date += timedelta(days=3 * direction)
        self._refresh_calendar()

    def _go_to_today(self):
        """Go to today's date."""
        self.calendar_start_date = datetime.now().date()
        if self.calendar_view.get() == "month":
            self.calendar_start_date = self.calendar_start_date.replace(day=1)
        self._refresh_calendar()

    def _select_visible_days(self, select: bool):
        """Select or deselect all visible days."""
        view = self.calendar_view.get()
        today = datetime.now().date()

        if view == "month":
            # Get all days in the month
            year = self.calendar_start_date.year
            month = self.calendar_start_date.month
            _, num_days = cal.monthrange(year, month)
            dates = [datetime(year, month, d).date() for d in range(1, num_days + 1)]
        elif view == "fortnight":
            dates = [self.calendar_start_date + timedelta(days=i) for i in range(14)]
        elif view == "week":
            dates = [self.calendar_start_date + timedelta(days=i) for i in range(7)]
        else:  # 3days
            dates = [self.calendar_start_date + timedelta(days=i) for i in range(3)]

        for d in dates:
            date_str = d.strftime('%Y-%m-%d')
            if date_str in self.day_vars:
                self.day_vars[date_str].set(select)

        self._refresh_calendar()

    def _refresh_calendar(self):
        """Refresh the calendar display."""
        # Clear existing content
        for widget in self.calendar_frame.winfo_children():
            widget.destroy()

        view = self.calendar_view.get()
        today = datetime.now().date()

        # Get available height for calendar content
        self.calendar_canvas.update_idletasks()
        available_height = getattr(self, '_last_canvas_height', None)
        if not available_height or available_height < 100:
            available_height = self.calendar_canvas.winfo_height()
        if available_height < 100:
            available_height = 450  # Default fallback

        # Set the frame to fill canvas and not shrink
        self.calendar_frame.grid_propagate(False)

        if view == "month":
            self._render_month_view(today, available_height)
        elif view == "fortnight":
            self._render_days_view(14, today, available_height)
        elif view == "week":
            self._render_days_view(7, today, available_height)
        else:  # 3days
            self._render_days_view(3, today, available_height)

    def _render_month_view(self, today, available_height):
        """Render month view calendar."""
        start_date = self.calendar_start_date
        year = start_date.year
        month = start_date.month

        # Update period label
        month_name = cal.month_name[month]
        self.calendar_period_var.set(f"{month_name} {year}")

        # Day headers
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        header_height = 30
        for col, day in enumerate(days):
            lbl = tk.Label(
                self.calendar_frame,
                text=day,
                font=("Segoe UI", 12, "bold"),
                width=14,
                bg="#f0f0f0",
                relief="solid",
                borderwidth=1
            )
            lbl.grid(row=0, column=col, sticky="nsew", padx=1, pady=1)

        # Get first day of month and number of days
        first_day_weekday = cal.monthrange(year, month)[0]  # 0=Monday
        num_days = cal.monthrange(year, month)[1]

        # Calculate number of rows needed
        total_cells = first_day_weekday + num_days
        num_rows = (total_cells + 6) // 7  # Ceiling division

        # Calculate cell height based on available space
        cell_height = max(80, (available_height - header_height - 20) // num_rows)

        # Render days
        row = 1
        col = first_day_weekday

        for day in range(1, num_days + 1):
            current_date = datetime(year, month, day).date()
            date_str = current_date.strftime('%Y-%m-%d')

            self._render_day_cell(row, col, current_date, date_str, today, cell_height=cell_height)

            col += 1
            if col > 6:
                col = 0
                row += 1

        # Configure grid weights and row heights
        self.calendar_frame.rowconfigure(0, weight=0, minsize=header_height)  # Header row fixed
        for c in range(7):
            self.calendar_frame.columnconfigure(c, weight=1)
        for r in range(1, num_rows + 1):
            self.calendar_frame.rowconfigure(r, weight=1, minsize=cell_height)

    def _render_days_view(self, num_days, today, available_height):
        """Render days view (week, fortnight, 3days)."""
        start_date = self.calendar_start_date
        end_date = start_date + timedelta(days=num_days - 1)

        # Update period label
        start_str = start_date.strftime("%d %b")
        end_str = end_date.strftime("%d %b %Y")
        self.calendar_period_var.set(f"{start_str} - {end_str}")

        # Determine columns (max 7 per row for readability)
        cols_per_row = min(num_days, 7)
        rows_needed = (num_days + cols_per_row - 1) // cols_per_row

        # Calculate cell height based on available space
        cell_height = max(100, (available_height - 20) // rows_needed)

        day_index = 0
        for row in range(rows_needed):
            for col in range(cols_per_row):
                if day_index >= num_days:
                    break

                current_date = start_date + timedelta(days=day_index)
                date_str = current_date.strftime('%Y-%m-%d')

                self._render_day_cell(row, col, current_date, date_str, today, show_day_name=True, cell_height=cell_height)

                day_index += 1

        # Configure grid weights and row heights
        for c in range(cols_per_row):
            self.calendar_frame.columnconfigure(c, weight=1)
        for r in range(rows_needed):
            self.calendar_frame.rowconfigure(r, weight=1, minsize=cell_height)

    def _render_day_cell(self, row, col, current_date, date_str, today, show_day_name=False, cell_height=100):
        """Render a single day cell."""
        # Check if selected
        is_selected = date_str in self.day_vars and self.day_vars[date_str].get()
        is_today = current_date == today
        is_past = current_date < today

        # Get events for this day
        events = self.calendar_events.get(date_str, [])

        # Background color
        if is_selected:
            bg_color = "#4a90d9"  # Blue for selected
            fg_color = "white"
        else:
            bg_color = "white"
            fg_color = "black"

        if is_past:
            bg_color = "#e0e0e0"  # Gray for past days
            fg_color = "#888888"

        # Create cell frame - use sticky="nsew" to fill grid cell
        cell = tk.Frame(
            self.calendar_frame,
            bg=bg_color,
            relief="solid",
            borderwidth=1,
            cursor="hand2" if not is_past else ""
        )
        cell.grid(row=row, column=col, sticky="nsew", padx=1, pady=1)

        # Day number and name
        day_num = current_date.day
        header_text = str(day_num)
        if show_day_name:
            header_text = f"{current_date.strftime('%a')} {day_num}"

        if is_today:
            header_text += " (Today)"

        day_label = tk.Label(
            cell,
            text=header_text,
            font=("Segoe UI", 12, "bold" if is_today else "normal"),
            bg=bg_color,
            fg=fg_color,
            anchor="w"
        )
        day_label.pack(fill=tk.X, padx=5, pady=(5, 2))

        # Calculate how many events can fit based on cell height
        # Header takes ~25px, each event line ~18px, "+more" line ~16px
        header_space = 30
        event_line_height = 18
        available_for_events = cell_height - header_space
        max_events_to_show = max(1, (available_for_events - 16) // event_line_height)

        # Events display
        if events:
            events_frame = tk.Frame(cell, bg=bg_color)
            events_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)

            events_to_display = min(len(events), max_events_to_show)
            for event in events[:events_to_display]:
                time_str = f"{event['startTime']}-{event['endTime']}"
                title = event.get('title', 'Event')
                if len(title) > 15:
                    title = title[:14] + "…"

                is_reservation = event.get('isReservation', False)
                # Use lighter colors on blue background for better contrast
                if is_selected:
                    event_color = "#ffcc80" if not is_reservation else "#a5d6a7"  # Light orange/green on blue
                else:
                    event_color = "#e67e22" if not is_reservation else "#27ae60"  # Standard orange/green

                event_lbl = tk.Label(
                    events_frame,
                    text=f"• {time_str} {title}",
                    font=("Segoe UI", 9),
                    bg=bg_color,
                    fg=event_color,
                    anchor="w"
                )
                event_lbl.pack(fill=tk.X)

            if len(events) > events_to_display:
                more_lbl = tk.Label(
                    events_frame,
                    text=f"  +{len(events) - events_to_display} more",
                    font=("Segoe UI", 8, "italic"),
                    bg=bg_color,
                    fg=fg_color,
                    anchor="w"
                )
                more_lbl.pack(fill=tk.X)

        # Click handler for day selection (only for future days)
        if not is_past:
            def toggle_day(ds=date_str, c=cell):
                if ds in self.day_vars:
                    current = self.day_vars[ds].get()
                    self.day_vars[ds].set(not current)
                    self._refresh_calendar()

            cell.bind("<Button-1>", lambda e: toggle_day())
            day_label.bind("<Button-1>", lambda e: toggle_day())
            for child in cell.winfo_children():
                child.bind("<Button-1>", lambda e, ds=date_str: toggle_day())

    def _scan_calendar_events(self):
        """Scan events for calendar display in background."""
        self.calendar_scan_var.set("Scanning events...")
        self._calendar_scan_generation += 1
        generation = self._calendar_scan_generation
        thread = threading.Thread(
            target=self._scan_calendar_events_thread,
            args=(generation,),
        )
        thread.daemon = True
        thread.start()

    def _scan_calendar_events_thread(self, generation):
        """Background thread to scan events for calendar."""
        try:
            from playwright.sync_api import sync_playwright
            from book_week import (
                authenticated_runtime_context_options,
                page_is_authenticated,
                persist_storage_state,
                restore_page_authentication,
                safe_goto,
            )
            from room_catalog import refresh_from_site as refresh_room_catalog

            today = datetime.now().date()
            context_options = authenticated_runtime_context_options()

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(**context_options)
                page = context.new_page()
                restore_page_authentication(page)
                catalog = refresh_room_catalog(page)
                live_dates = catalog_booking_dates(catalog, today=today)
                if not live_dates:
                    raise RuntimeError(
                        "Asimut returned no current booking-window dates; "
                        "the existing event cache was preserved"
                    )
                safe_goto(page, ASIMUT_AGENDA_URL)
                require_authenticated_scan_page(
                    page,
                    page_is_authenticated,
                    "/agenda",
                )
                page.wait_for_timeout(1000)

                # The agenda window follows the current absolute cutoff from
                # Asimut.  No fixed seven/eight-day (or other) assumption is
                # allowed to authorize the GUI's event cache.
                target_date = live_dates[-1]
                allowed_date_strings = [value.isoformat() for value in live_dates]
                live_room_names = [room.name for room in catalog.rooms]
                target_date_str = target_date.strftime("%d %B %Y").lstrip("0")
                alternate_target_date_str = target_date.strftime("%d %B %Y")

                max_scrolls = 60
                scroll_count = 0
                last_height = 0
                stalled_count = 0
                target_reached = False

                while scroll_count < max_scrolls:
                    page_text = page.evaluate("() => document.body.innerText")
                    if (
                        target_date_str in page_text
                        or alternate_target_date_str in page_text
                    ):
                        target_reached = True
                        break

                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(800)

                    new_height = page.evaluate("() => document.body.scrollHeight")
                    if new_height == last_height:
                        stalled_count += 1
                        if stalled_count >= 3:
                            break
                    else:
                        stalled_count = 0
                    last_height = new_height
                    scroll_count += 1

                if not target_reached:
                    raise RuntimeError(
                        f"Calendar agenda scan did not reach {target_date.isoformat()}; "
                        "the existing event cache was preserved"
                    )

                # Final pass
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(500)
                for _ in range(max(scroll_count + 5, 15)):
                    page.evaluate("window.scrollBy(0, window.innerHeight)")
                    page.wait_for_timeout(300)

                # Extract events (same JS as before)
                events_data = page.evaluate("""(scanPolicy) => {
                    const events = [];
                    const allowedDateSet = new Set(scanPolicy.allowedDates);
                    const configuredRooms = scanPolicy.roomNames;

                    const monthNames = ['January', 'February', 'March', 'April', 'May', 'June',
                                      'July', 'August', 'September', 'October', 'November', 'December'];

                    function configuredRoomFromText(value) {
                        const text = String(value || '').toLocaleLowerCase();
                        const boundary = character => !character || !/[A-Za-z0-9.]/.test(character);
                        const matches = configuredRooms.filter(room => {
                            const needle = room.toLocaleLowerCase();
                            let index = text.indexOf(needle);
                            while (index >= 0) {
                                if (boundary(text[index - 1]) && boundary(text[index + needle.length])) {
                                    return true;
                                }
                                index = text.indexOf(needle, index + 1);
                            }
                            return false;
                        });
                        if (!matches.length) return null;
                        const longest = Math.max(...matches.map(room => room.length));
                        const longestMatches = matches.filter(room => room.length === longest);
                        return longestMatches.length === 1 ? longestMatches[0] : null;
                    }

                    function isCancelled(element) {
                        let el = element;
                        while (el && el !== document.body) {
                            const style = window.getComputedStyle(el);
                            if (style.textDecoration.includes('line-through') ||
                                style.textDecorationLine.includes('line-through')) return true;
                            el = el.parentElement;
                        }
                        return false;
                    }

                    function getEventTitle(element) {
                        let container = element;
                        for (let i = 0; i < 10 && container; i++) {
                            if (container.tagName === 'APP-AS-EVENT' ||
                                container.classList.contains('as-event-panel')) {
                                const displayName = container.querySelector('[data-cy="event-display-name"]');
                                if (displayName) {
                                    const nameText = (displayName.textContent || '').trim();
                                    return (nameText === 'Reservation' || nameText.includes('Reservation'))
                                        ? 'Reservation'
                                        : nameText;
                                }
                                return 'Event';
                            }
                            container = container.parentElement;
                        }
                        return 'Event';
                    }

                    function getEventRoom(element) {
                        let container = element;
                        for (let i = 0; i < 10 && container; i++) {
                            if (container.tagName === 'APP-AS-EVENT' ||
                                container.classList.contains('as-event-panel')) {
                                const locationLink = container.querySelector('.location-link, [data-cy="event-location-link"]');
                                if (locationLink) {
                                    const locationText = locationLink.textContent || '';
                                    const configuredRoom = configuredRoomFromText(locationText);
                                    if (configuredRoom) return configuredRoom;
                                }
                                return null;
                            }
                            container = container.parentElement;
                        }
                        return null;
                    }

                    const allElements = document.querySelectorAll('*');
                    let currentDate = null;

                    function localDateString(value) {
                        const year = value.getFullYear();
                        const month = String(value.getMonth() + 1).padStart(2, '0');
                        const day = String(value.getDate()).padStart(2, '0');
                        return `${year}-${month}-${day}`;
                    }

                    for (const element of allElements) {
                        if (element.children.length > 0) {
                            const hasTextChild = Array.from(element.children).some(child =>
                                child.textContent.trim().length > 0);
                            if (hasTextChild) continue;
                        }

                        const text = element.textContent.trim();

                        const dateMatch = text.match(/^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\\s+(\\d{1,2})\\s+(January|February|March|April|May|June|July|August|September|October|November|December)\\s+(\\d{4})$/i);
                        if (dateMatch) {
                            const day = parseInt(dateMatch[2]);
                            const month = monthNames.indexOf(dateMatch[3]);
                            const year = parseInt(dateMatch[4]);
                            currentDate = new Date(year, month, day);
                            continue;
                        }

                        const timeMatch = text.match(/^(\\d{2}:\\d{2})\\s*[-–]\\s*(\\d{2}:\\d{2})$/);
                        if (timeMatch && currentDate) {
                            if (isCancelled(element)) continue;

                            const dateString = localDateString(currentDate);
                            if (allowedDateSet.has(dateString)) {
                                events.push({
                                    date: dateString,
                                    startTime: timeMatch[1],
                                    endTime: timeMatch[2],
                                    title: getEventTitle(element),
                                    isReservation: getEventTitle(element) === 'Reservation',
                                    room: getEventRoom(element)
                                });
                            }
                        }
                    }
                    return events;
                }""", {
                    "allowedDates": allowed_date_strings,
                    "roomNames": live_room_names,
                })
                require_authenticated_scan_page(
                    page,
                    page_is_authenticated,
                    "/agenda",
                )
                persist_storage_state(context)
                browser.close()

            # Group events by date
            events_by_date = {}
            for event in events_data:
                date = event['date']
                if date not in events_by_date:
                    events_by_date[date] = []
                events_by_date[date].append(event)

            # Also update cached_events in settings
            unique_events = deduplicate_events(events_data)
            cache_timestamp = datetime.now().isoformat()
            if generation != self._calendar_scan_generation:
                return

            cache_applied = []
            def update_cache(settings):
                if generation != self._calendar_scan_generation:
                    return
                settings["cached_events"] = unique_events
                settings["cached_events_timestamp"] = cache_timestamp
                cache_applied.append(True)

            if not self._update_settings(update_cache, interactive=False) or not cache_applied:
                return

            self.root.after(
                0,
                self._on_calendar_scan_complete,
                generation,
                events_by_date,
            )

        except Exception as exc:
            error_message = str(exc)
            try:
                self.root.after(
                    0,
                    self._on_calendar_scan_error,
                    generation,
                    error_message,
                )
            except (RuntimeError, tk.TclError):
                pass

    def _on_calendar_scan_complete(self, generation, events_by_date):
        """Called when calendar event scan completes."""
        if generation != self._calendar_scan_generation:
            return
        dialog = getattr(self, "calendar_dialog", None)
        if dialog is None or not dialog.winfo_exists():
            return
        self.calendar_events = events_by_date
        total_events = sum(len(events) for events in self.calendar_events.values())
        self.calendar_scan_var.set(f"✓ {total_events} events (updated just now)")
        self._refresh_calendar()

    def _on_calendar_scan_error(self, generation, error_msg):
        """Called when calendar event scan fails."""
        if generation != self._calendar_scan_generation:
            return
        dialog = getattr(self, "calendar_dialog", None)
        if dialog is not None and dialog.winfo_exists():
            self.calendar_scan_var.set(f"⚠ Scan failed: {error_msg[:30]}")
        self.log(f"Calendar scan error: {error_msg}", "error")

    def _save_calendar_and_close(self, dialog, day_snapshot):
        """Save calendar selections and close."""
        changed_states = {
            date_key: var.get()
            for date_key, var in self.day_vars.items()
            if date_key in day_snapshot and var.get() != day_snapshot[date_key]
        }
        if not self.save_booking_days(changed_states):
            return
        self._calendar_scan_generation += 1
        self.calendar_canvas.unbind_all("<MouseWheel>")
        self.calendar_dialog = None
        dialog.destroy()
        self.log("Calendar settings saved", "info")

    def show_events_dialog(self):
        """Show dialog to manage events (ignore/include for booking conflicts)."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Manage Events")
        dialog.geometry("900x650")
        dialog.transient(self.root)

        # Header
        header_frame = ttk.Frame(dialog, padding="10")
        header_frame.pack(fill=tk.X)

        ttk.Label(
            header_frame,
            text="Manage Events",
            font=("Segoe UI", 14, "bold")
        ).pack(side=tk.LEFT)

        ttk.Label(
            header_frame,
            text="Uncheck events to ignore them (allow booking over them)",
            font=("Segoe UI", 10),
            foreground="gray"
        ).pack(side=tk.LEFT, padx=(20, 0))

        # Button frame
        btn_frame = ttk.Frame(dialog, padding="10")
        btn_frame.pack(fill=tk.X)

        self.scan_btn = ttk.Button(
            btn_frame,
            text="🔄 Scan My Agenda",
            command=lambda: self._scan_events(dialog, events_frame)
        )
        self.scan_btn.pack(side=tk.LEFT, padx=5)

        ttk.Button(
            btn_frame,
            text="Select All",
            command=lambda: self._select_all_events(True)
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            btn_frame,
            text="Deselect All",
            command=lambda: self._select_all_events(False)
        ).pack(side=tk.LEFT, padx=5)

        # Progress label
        self.events_progress_var = tk.StringVar(value="")
        ttk.Label(btn_frame, textvariable=self.events_progress_var, foreground="blue").pack(side=tk.LEFT, padx=20)

        # Scrollable frame for events
        container = ttk.Frame(dialog, padding="10")
        container.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(container)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        events_frame = ttk.Frame(canvas)

        events_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=events_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Enable mousewheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Store reference to events frame and checkboxes
        self.events_frame = events_frame
        self.event_vars = {}  # {event_key: BooleanVar}

        # Load existing ignored events and display them
        self._load_and_display_events(events_frame)

        # Bottom buttons
        bottom_frame = ttk.Frame(dialog, padding="10")
        bottom_frame.pack(fill=tk.X)

        ttk.Button(
            bottom_frame,
            text="Save",
            command=lambda: self._save_events_and_close(dialog)
        ).pack(side=tk.RIGHT, padx=5)

        ttk.Button(
            bottom_frame,
            text="Cancel",
            command=dialog.destroy
        ).pack(side=tk.RIGHT, padx=5)

        # Cleanup mousewheel binding when dialog closes
        def on_close():
            canvas.unbind_all("<MouseWheel>")
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", on_close)

    def _load_and_display_events(self, events_frame):
        """Load cached events and ignored events from settings, display in frame."""
        settings = self.load_settings()
        cached_events = settings.get("cached_events", [])
        ignored_events = settings.get("ignored_events", [])

        # Clear existing widgets
        for widget in events_frame.winfo_children():
            widget.destroy()
        self.event_vars = {}

        if not cached_events:
            ttk.Label(
                events_frame,
                text="No events cached. Click 'Scan My Agenda' to load events.",
                font=("Segoe UI", 11),
                foreground="gray"
            ).pack(pady=20)
            return

        preference_rows, ignore_resolution = build_event_preference_rows(
            cached_events,
            ignored_events,
        )
        if ignore_resolution.ambiguous_legacy_keys:
            ttk.Label(
                events_frame,
                text=(
                    "An older ignore selection matches multiple events at the same "
                    "time. Those events were safely left enabled; review and Save."
                ),
                foreground="#b26a00",
                wraplength=680,
            ).pack(fill=tk.X, padx=5, pady=(0, 8))
            self.log(
                "Ambiguous older event ignore selection left enabled for review",
                "warning",
            )

        # Group events by date
        events_by_date = {}
        for event, event_key, is_enabled in preference_rows:
            date = event.get("date", "Unknown")
            if date not in events_by_date:
                events_by_date[date] = []
            events_by_date[date].append((event, event_key, is_enabled))

        # Display events grouped by date
        for date in sorted(events_by_date.keys()):
            # Date header
            try:
                date_obj = datetime.strptime(date, "%Y-%m-%d")
                day_name = date_obj.strftime("%A")
                date_display = f"{day_name}, {date_obj.strftime('%d %B %Y')}"
            except:
                date_display = date

            date_frame = ttk.LabelFrame(events_frame, text=date_display, padding="10")
            date_frame.pack(fill=tk.X, pady=5, padx=5)

            for event, event_key, is_enabled in events_by_date[date]:
                is_reservation = event.get("isReservation", False)
                room = event.get("room", "")
                title = event.get("title", "Event")

                # Create checkbox - default checked unless in ignored list
                var = tk.BooleanVar(value=is_enabled)
                self.event_vars[event_key] = var

                # Format display text
                time_str = f"{event['startTime']} - {event['endTime']}"
                if is_reservation:
                    display_text = f"[Reservation] {time_str}"
                    if room:
                        display_text += f" in {room}"
                else:
                    display_text = f"{time_str} - {title}"
                    if room:
                        display_text += f" in {room}"

                cb = ttk.Checkbutton(
                    date_frame,
                    text=display_text,
                    variable=var
                )
                cb.pack(anchor=tk.W, pady=2)

    def _select_all_events(self, select: bool):
        """Select or deselect all event checkboxes."""
        for var in self.event_vars.values():
            var.set(select)

    def _scan_events(self, dialog, events_frame):
        """Scan the agenda for events using Playwright."""
        self.scan_btn.config(state=tk.DISABLED)
        self.events_progress_var.set("Scanning agenda (this may take a moment)...")

        # Run scan in background thread
        thread = threading.Thread(target=self._scan_events_thread, args=(dialog, events_frame))
        thread.daemon = True
        thread.start()

    def _scan_events_thread(self, dialog, events_frame):
        """Background thread to scan events."""
        try:
            # Import here to avoid startup delay
            from playwright.sync_api import sync_playwright
            from book_week import (
                authenticated_runtime_context_options,
                page_is_authenticated,
                persist_storage_state,
                restore_page_authentication,
                safe_goto,
            )
            from room_catalog import refresh_from_site as refresh_room_catalog

            events = []
            today = datetime.now().date()
            context_options = authenticated_runtime_context_options()

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(**context_options)
                page = context.new_page()

                # Refresh the authoritative room/window policy before reading
                # the agenda.  The saved catalog remains display-only.
                self.root.after(
                    0,
                    lambda: self.events_progress_var.set(
                        "Refreshing booking window from Asimut..."
                    ),
                )
                restore_page_authentication(page)
                catalog = refresh_room_catalog(page)
                live_dates = catalog_booking_dates(catalog, today=today)
                if not live_dates:
                    raise RuntimeError(
                        "Asimut returned no current booking-window dates; "
                        "the existing event cache was preserved"
                    )
                allowed_date_strings = [value.isoformat() for value in live_dates]
                live_room_names = [room.name for room in catalog.rooms]
                self.root.after(0, lambda: self.events_progress_var.set("Loading agenda page..."))
                safe_goto(page, ASIMUT_AGENDA_URL)
                require_authenticated_scan_page(
                    page,
                    page_is_authenticated,
                    "/agenda",
                )
                page.wait_for_timeout(1000)

                # Scroll only as far as the absolute cutoff supplied by Asimut.
                target_date = live_dates[-1]
                target_date_str = target_date.strftime("%d %B %Y").lstrip("0")

                self.root.after(0, lambda: self.events_progress_var.set("Scrolling to load events..."))

                max_scrolls = 50
                scroll_count = 0
                last_height = 0
                stalled_count = 0
                target_reached = False

                while scroll_count < max_scrolls:
                    page_text = page.evaluate("() => document.body.innerText")
                    if target_date_str in page_text:
                        target_reached = True
                        break

                    alt_date_str = target_date.strftime("%d %B %Y")
                    if alt_date_str in page_text:
                        target_reached = True
                        break

                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(1000)

                    new_height = page.evaluate("() => document.body.scrollHeight")
                    if new_height == last_height:
                        stalled_count += 1
                        if stalled_count >= 3:
                            break
                        page.wait_for_timeout(500)
                    else:
                        stalled_count = 0
                    last_height = new_height
                    scroll_count += 1

                if not target_reached:
                    raise RuntimeError(
                        f"Agenda scan did not reach {target_date.isoformat()}; "
                        "the existing event cache was preserved"
                    )

                # Scroll back to top and do final pass
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(1000)

                min_scrolls = max(scroll_count + 5, 10)
                for i in range(min_scrolls):
                    page.evaluate("window.scrollBy(0, window.innerHeight)")
                    page.wait_for_timeout(400)

                page.wait_for_timeout(1000)

                self.root.after(0, lambda: self.events_progress_var.set("Extracting events..."))

                # Extract events using same JavaScript as book_week.py
                events_data = page.evaluate("""(scanPolicy) => {
                    const events = [];
                    const allowedDateSet = new Set(scanPolicy.allowedDates);
                    const configuredRooms = scanPolicy.roomNames;
                    const today = new Date();
                    today.setHours(0, 0, 0, 0);

                    const monthNames = ['January', 'February', 'March', 'April', 'May', 'June',
                                      'July', 'August', 'September', 'October', 'November', 'December'];

                    function configuredRoomFromText(value) {
                        const text = String(value || '').toLocaleLowerCase();
                        const boundary = character => !character || !/[A-Za-z0-9.]/.test(character);
                        const matches = configuredRooms.filter(room => {
                            const needle = room.toLocaleLowerCase();
                            let index = text.indexOf(needle);
                            while (index >= 0) {
                                if (boundary(text[index - 1]) && boundary(text[index + needle.length])) {
                                    return true;
                                }
                                index = text.indexOf(needle, index + 1);
                            }
                            return false;
                        });
                        if (!matches.length) return null;
                        const longest = Math.max(...matches.map(room => room.length));
                        const longestMatches = matches.filter(room => room.length === longest);
                        return longestMatches.length === 1 ? longestMatches[0] : null;
                    }

                    function isCancelled(element) {
                        let el = element;
                        while (el && el !== document.body) {
                            const style = window.getComputedStyle(el);
                            if (style.textDecoration.includes('line-through') ||
                                style.textDecorationLine.includes('line-through')) {
                                return true;
                            }
                            const className = el.className || '';
                            if (className.includes('cancelled') || className.includes('canceled') ||
                                className.includes('deleted') || className.includes('removed')) {
                                return true;
                            }
                            el = el.parentElement;
                        }
                        return false;
                    }

                    function getEventTitle(element) {
                        let container = element;
                        for (let i = 0; i < 10 && container; i++) {
                            if (container.tagName === 'APP-AS-EVENT' ||
                                container.classList.contains('as-event-panel') ||
                                container.classList.contains('event-details-expanded')) {

                                const displayName = container.querySelector('[data-cy="event-display-name"]');
                                if (displayName) {
                                    const nameText = (displayName.textContent || '').trim();
                                    if (nameText === 'Reservation' || nameText.includes('Reservation')) {
                                        return 'Reservation';
                                    }
                                    return nameText;
                                }
                                return 'Event';
                            }
                            container = container.parentElement;
                        }
                        return 'Event';
                    }

                    function getEventRoom(element) {
                        let container = element;
                        for (let i = 0; i < 10 && container; i++) {
                            if (container.tagName === 'APP-AS-EVENT' ||
                                container.classList.contains('as-event-panel') ||
                                container.classList.contains('event-details-expanded')) {

                                const locationLink = container.querySelector('.location-link, [data-cy="event-location-link"]');
                                if (locationLink) {
                                    const locText = (locationLink.textContent || '').trim();
                                    const configuredRoom = configuredRoomFromText(locText);
                                    if (configuredRoom) return configuredRoom;
                                }
                                return null;
                            }
                            container = container.parentElement;
                        }
                        return null;
                    }

                    const allElements = document.querySelectorAll('*');
                    let currentDate = null;

                    function localDateString(value) {
                        const year = value.getFullYear();
                        const month = String(value.getMonth() + 1).padStart(2, '0');
                        const day = String(value.getDate()).padStart(2, '0');
                        return `${year}-${month}-${day}`;
                    }

                    for (const element of allElements) {
                        if (element.children.length > 0) {
                            const hasTextChild = Array.from(element.children).some(child =>
                                child.textContent.trim().length > 0
                            );
                            if (hasTextChild) continue;
                        }

                        const text = element.textContent.trim();

                        const dateMatch = text.match(/^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\\s+(\\d{1,2})\\s+(January|February|March|April|May|June|July|August|September|October|November|December)\\s+(\\d{4})$/i);
                        if (dateMatch) {
                            const day = parseInt(dateMatch[2]);
                            const month = monthNames.indexOf(dateMatch[3]);
                            const year = parseInt(dateMatch[4]);
                            currentDate = new Date(year, month, day);
                            continue;
                        }

                        const timeMatch = text.match(/^(\\d{2}:\\d{2})\\s*[-–]\\s*(\\d{2}:\\d{2})$/);
                        if (timeMatch && currentDate) {
                            if (isCancelled(element)) {
                                continue;
                            }

                            const startTime = timeMatch[1];
                            const endTime = timeMatch[2];
                            const eventTitle = getEventTitle(element);
                            const eventRoom = getEventRoom(element);

                            const daysDiff = Math.round((currentDate - today) / (1000 * 60 * 60 * 24));
                            const dateString = localDateString(currentDate);
                            if (allowedDateSet.has(dateString)) {
                                events.push({
                                    date: dateString,
                                    daysDiff: daysDiff,
                                    startTime: startTime,
                                    endTime: endTime,
                                    title: eventTitle,
                                    isReservation: eventTitle === 'Reservation',
                                    room: eventRoom
                                });
                            }
                        }
                    }

                    return events;
                }""", {
                    "allowedDates": allowed_date_strings,
                    "roomNames": live_room_names,
                })
                require_authenticated_scan_page(
                    page,
                    page_is_authenticated,
                    "/agenda",
                )
                persist_storage_state(context)
                browser.close()

            # Deduplicate events
            unique_events = deduplicate_events(events_data)

            # Save to settings
            if not self._update_settings(
                lambda settings: settings.__setitem__("cached_events", unique_events),
                interactive=False,
            ):
                raise RuntimeError("Scanned events could not be saved safely")

            # Update UI
            self.root.after(0, lambda: self._on_scan_complete(events_frame, len(unique_events)))

        except Exception as exc:
            error_message = str(exc)
            try:
                self.root.after(0, self._on_scan_error, error_message)
            except (RuntimeError, tk.TclError):
                pass

    def _on_scan_complete(self, events_frame, count):
        """Called when event scan completes."""
        self.scan_btn.config(state=tk.NORMAL)
        self.events_progress_var.set(f"Found {count} events")
        self._load_and_display_events(events_frame)
        self.log(f"Event scan complete: found {count} events", "info")

    def _on_scan_error(self, error_msg):
        """Called when event scan fails."""
        self.scan_btn.config(state=tk.NORMAL)
        self.events_progress_var.set(f"Error: {error_msg[:50]}")
        self.log(f"Event scan error: {error_msg}", "error")
        messagebox.showerror("Scan Error", f"Failed to scan events:\n{error_msg}")

    def _save_events_and_close(self, dialog):
        """Save ignored events to settings and close dialog."""
        ignored_events = ignored_v2_keys_from_selection(
            {event_key: bool(var.get()) for event_key, var in self.event_vars.items()}
        )

        if self._update_settings(
            lambda settings: settings.__setitem__("ignored_events", ignored_events)
        ):
            self.log(f"Saved {len(ignored_events)} ignored events", "info")
            dialog.destroy()

    # =========================================================================
    # SCAN AVAILABLE ROOMS FEATURE
    # =========================================================================

    def show_scan_rooms_dialog(self):
        """Show dialog for scanning available rooms with date range selection."""
        window_dates = tuple(getattr(self, "booking_dates", ()))
        if not window_dates:
            messagebox.showerror(
                "Booking Window Unavailable",
                "No site-derived booking dates are available yet. Run a read-only "
                "check or refresh status after the next successful scheduled run.",
            )
            return
        self._room_scan_generation += 1
        dialog = tk.Toplevel(self.root)
        dialog.title("Scan Available Rooms")
        dialog.geometry("900x700")
        dialog.transient(self.root)
        dialog.grab_set()

        # Store dialog reference
        self.scan_rooms_dialog = dialog

        # Header
        header_frame = ttk.Frame(dialog, padding="15")
        header_frame.pack(fill=tk.X)

        ttk.Label(
            header_frame,
            text="Scan Available Rooms",
            font=("Segoe UI", 14, "bold")
        ).pack(anchor=tk.W)

        ttk.Label(
            header_frame,
            text="Select which days to scan for available practice rooms",
            font=("Segoe UI", 10),
            foreground="gray"
        ).pack(anchor=tk.W, pady=(5, 0))

        # Date selection frame
        dates_frame = ttk.LabelFrame(dialog, text="Select Days to Scan", padding="15")
        dates_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)

        # Initialize scan date vars
        self.scan_date_vars = {}
        today = datetime.now().date()

        # Create scrollable frame for dates
        canvas = tk.Canvas(dates_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(dates_frame, orient="vertical", command=canvas.yview)
        dates_inner = ttk.Frame(canvas)

        dates_inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=dates_inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Enable mousewheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # Dates come only from the latest site-derived absolute cutoff.
        for i, target_date in enumerate(window_dates):
            date_str = target_date.strftime('%Y-%m-%d')
            day_name = target_date.strftime('%A')

            # Format display text
            if i == 0:
                display_text = f"Today - {day_name}, {target_date.strftime('%d %B %Y')}"
            elif i == 1:
                display_text = f"Tomorrow - {day_name}, {target_date.strftime('%d %B %Y')}"
            else:
                display_text = f"{day_name}, {target_date.strftime('%d %B %Y')}"

            var = tk.BooleanVar(value=True)  # Default all selected
            self.scan_date_vars[date_str] = var

            cb = ttk.Checkbutton(
                dates_inner,
                text=display_text,
                variable=var
            )
            cb.pack(anchor=tk.W, pady=3)

        # Quick selection buttons
        quick_frame = ttk.Frame(dialog, padding="15")
        quick_frame.pack(fill=tk.X)

        ttk.Button(
            quick_frame,
            text="Select All",
            command=lambda: self._select_all_scan_dates(True),
            width=12
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            quick_frame,
            text="Deselect All",
            command=lambda: self._select_all_scan_dates(False),
            width=12
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            quick_frame,
            text="Today Only",
            command=self._select_today_only,
            width=12
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            quick_frame,
            text="Next 3 Days",
            command=lambda: self._select_next_n_days(3),
            width=12
        ).pack(side=tk.LEFT, padx=5)

        # Progress indicator
        self.scan_rooms_progress_var = tk.StringVar(value="")
        ttk.Label(
            dialog,
            textvariable=self.scan_rooms_progress_var,
            foreground="blue",
            font=("Segoe UI", 10)
        ).pack(pady=5)

        # Action buttons
        btn_frame = ttk.Frame(dialog, padding="15")
        btn_frame.pack(fill=tk.X)

        self.scan_rooms_btn = ttk.Button(
            btn_frame,
            text="🔍 Start Scan",
            command=lambda: self._start_rooms_scan(dialog),
            width=15
        )
        self.scan_rooms_btn.pack(side=tk.RIGHT, padx=5)

        ttk.Button(
            btn_frame,
            text="Cancel",
            command=dialog.destroy,
            width=10
        ).pack(side=tk.RIGHT, padx=5)

        # Cleanup mousewheel binding on close
        def on_close():
            self._room_scan_generation += 1
            if getattr(self, "scan_rooms_dialog", None) is dialog:
                self.scan_rooms_dialog = None
            canvas.unbind_all("<MouseWheel>")
            dialog.destroy()
        dialog.protocol("WM_DELETE_WINDOW", on_close)

    def _select_all_scan_dates(self, select: bool):
        """Select or deselect all scan dates."""
        for var in self.scan_date_vars.values():
            var.set(select)

    def _select_today_only(self):
        """Select only today for scanning."""
        today_str = datetime.now().date().strftime('%Y-%m-%d')
        for date_str, var in self.scan_date_vars.items():
            var.set(date_str == today_str)

    def _select_next_n_days(self, n: int):
        """Select the next N days for scanning."""
        selected_dates = set(sorted(self.scan_date_vars)[:n])

        for date_str, var in self.scan_date_vars.items():
            var.set(date_str in selected_dates)

    def _start_rooms_scan(self, setup_dialog):
        """Start the room scanning process."""
        # Get selected dates
        selected_dates = [date_str for date_str, var in self.scan_date_vars.items() if var.get()]

        if not selected_dates:
            messagebox.showwarning("No Dates Selected", "Please select at least one day to scan.", parent=setup_dialog)
            return

        # Disable scan button
        self.scan_rooms_btn.config(state=tk.DISABLED)
        self.scan_rooms_progress_var.set("Starting scan...")
        self._room_scan_generation += 1
        generation = self._room_scan_generation

        # Run scan in background thread
        thread = threading.Thread(
            target=self._scan_rooms_thread,
            args=(setup_dialog, sorted(selected_dates), generation),
        )
        thread.daemon = True
        thread.start()

    def _scan_rooms_thread(self, setup_dialog, selected_dates, generation):
        """Background thread to scan available rooms."""
        if generation != getattr(self, "_room_scan_generation", None):
            return
        browser = None
        try:
            from playwright.sync_api import sync_playwright
            from book_week import (
                authenticated_runtime_context_options,
                get_available_slots,
                navigate_to_day,
                open_practice_room_overview,
                page_is_authenticated,
                persist_storage_state,
                refresh_live_room_policy,
                restore_page_authentication,
                wait_for_practice_room_grid,
            )

            all_slots = []  # List of {date, room, start_time, end_time, duration}
            scan_started_at = datetime.now()
            today = scan_started_at.date()
            current_time_hour = scan_started_at.hour + scan_started_at.minute / 60.0
            room_preferences = self.room_preferences
            context_options = authenticated_runtime_context_options()

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(**context_options)
                page = context.new_page()

                # Revalidate the date choices and install the same immutable,
                # fresh site policy used by mutation-capable booking runs.
                self.root.after(
                    0,
                    lambda: self.scan_rooms_progress_var.set(
                        "Refreshing booking window from Asimut..."
                    ),
                )
                restore_page_authentication(page)
                policy = refresh_live_room_policy(
                    page,
                    room_preferences,
                    today=today,
                )
                live_date_strings = {
                    value.isoformat()
                    for value in policy.booking_dates(today)
                }
                if not live_date_strings:
                    raise RuntimeError("Asimut returned no current booking-window dates")
                outside_live_window = sorted(set(selected_dates) - live_date_strings)
                if outside_live_window:
                    raise RuntimeError(
                        "The booking window changed while this dialog was open. "
                        "Close it, refresh status, and choose dates again."
                    )
                self.root.after(
                    0,
                    lambda: self.scan_rooms_progress_var.set("Loading booking page..."),
                )
                open_practice_room_overview(page, today)
                require_authenticated_scan_page(
                    page,
                    page_is_authenticated,
                    "/overview",
                    expected_query="locationGroupId=10",
                )
                current_url = page.url
                self.root.after(
                    0,
                    lambda u=current_url: self.log(
                        f"[Scan Debug] Verified overview: {u[:80]}", "info"
                    ),
                )

                current_calendar_day = 0  # Start at today

                for date_idx, date_str in enumerate(selected_dates):
                    target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    days_ahead = (target_date - today).days

                    if days_ahead < 0:
                        raise RuntimeError(
                            f"Selected scan date {date_str} is before this run's base date"
                        )

                    self.root.after(0, lambda d=date_str, idx=date_idx, total=len(selected_dates):
                        self.scan_rooms_progress_var.set(f"Scanning {d} ({idx + 1}/{total})..."))

                    if days_ahead != current_calendar_day:
                        current_calendar_day = navigate_to_day(
                            page,
                            days_ahead,
                            current_calendar_day,
                            base_date=today,
                        )
                    if current_calendar_day != days_ahead:
                        raise RuntimeError(
                            f"Calendar navigation did not reach {target_date.isoformat()}"
                        )
                    wait_for_practice_room_grid(page, target_date)
                    require_authenticated_scan_page(
                        page,
                        page_is_authenticated,
                        "/overview",
                        expected_query="locationGroupId=10",
                    )

                    # Use the production renderer abstraction so current SVG
                    # and legacy overview layouts share one verified parser.
                    slots_data = get_available_slots(page)

                    # Log how many rooms with slots were found
                    self.root.after(0, lambda sd=slots_data, d=date_str:
                        self.log(f"[Scan Debug] {d}: Found {len(sd)} rooms with available slots", "info"))

                    # Process slots
                    day_slot_count = 0
                    for room_data in slots_data:
                        room_name = room_data["room"]
                        for slot in room_data["slots"]:
                            start_hour = slot['startHour']
                            end_hour = slot['endHour']
                            duration_mins = (end_hour - start_hour) * 60

                            # Report only blocks that satisfy the same selected
                            # live-policy minimum used by the booking runtime.
                            if duration_mins < policy.minimum_block_minutes:
                                continue

                            # For today, skip slots that have already started
                            if days_ahead == 0 and start_hour <= current_time_hour:
                                continue

                            # Format times
                            start_h = int(start_hour)
                            start_m = int((start_hour % 1) * 60)
                            end_h = int(end_hour)
                            end_m = int((end_hour % 1) * 60)

                            all_slots.append({
                                'date': date_str,
                                'room': room_name,
                                'start_time': f"{start_h:02d}:{start_m:02d}",
                                'end_time': f"{end_h:02d}:{end_m:02d}",
                                'start_hour': start_hour,
                                'duration_mins': duration_mins
                            })
                            day_slot_count += 1

                    self.root.after(0, lambda c=day_slot_count, d=date_str:
                        self.log(f"[Scan Debug] {d}: Added {c} valid slots", "info"))

                require_authenticated_scan_page(
                    page,
                    page_is_authenticated,
                    "/overview",
                    expected_query="locationGroupId=10",
                )
                if generation != self._room_scan_generation:
                    return
                persist_storage_state(context)

            # Sort slots by date, then time, then room
            all_slots.sort(key=lambda s: (s['date'], s['start_hour'], s['room']))

            if generation != self._room_scan_generation:
                return
            self.root.after(
                0,
                self._on_scan_rooms_complete,
                generation,
                setup_dialog,
                all_slots,
                selected_dates,
            )

        except Exception as exc:
            error_message = str(exc)
            if generation != getattr(self, "_room_scan_generation", None):
                return
            try:
                self.root.after(
                    0,
                    self._on_scan_rooms_error,
                    generation,
                    setup_dialog,
                    error_message,
                )
            except (RuntimeError, tk.TclError):
                pass
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass

    def _room_scan_dialog_is_current(self, generation, setup_dialog):
        """Return whether a room-scan callback still belongs to its live dialog."""

        if generation != getattr(self, "_room_scan_generation", None):
            return False
        if getattr(self, "scan_rooms_dialog", None) is not setup_dialog:
            return False
        try:
            return bool(setup_dialog.winfo_exists())
        except (RuntimeError, tk.TclError):
            return False

    def _on_scan_rooms_complete(
        self,
        generation,
        setup_dialog,
        all_slots,
        scanned_dates,
    ):
        """Publish room-scan results only while the originating dialog is current."""

        if not self._room_scan_dialog_is_current(generation, setup_dialog):
            return
        self._show_scan_results(setup_dialog, all_slots, scanned_dates)

    def _on_scan_rooms_error(self, generation, setup_dialog, error_msg):
        """Handle scan error."""
        if not self._room_scan_dialog_is_current(generation, setup_dialog):
            return
        self.scan_rooms_btn.config(state=tk.NORMAL)
        self.scan_rooms_progress_var.set(f"Error: {error_msg[:40]}")
        self.log(f"Room scan error: {error_msg}", "error")
        messagebox.showerror("Scan Error", f"Failed to scan rooms:\n{error_msg}", parent=setup_dialog)

    def _show_scan_results(self, setup_dialog, all_slots, scanned_dates):
        """Show the scan results in a new window."""
        # Close the setup dialog
        try:
            setup_dialog.destroy()
        except:
            pass
        if getattr(self, "scan_rooms_dialog", None) is setup_dialog:
            self.scan_rooms_dialog = None

        # Create results window
        results = tk.Toplevel(self.root)
        results.title("Available Rooms")
        results.geometry("1200x900")
        results.transient(self.root)

        # Store for filtering
        self.scan_results_all_slots = all_slots
        self.scan_results_scanned_dates = scanned_dates

        # Header
        header_frame = ttk.Frame(results, padding="15")
        header_frame.pack(fill=tk.X)

        ttk.Label(
            header_frame,
            text="Available Practice Rooms",
            font=("Segoe UI", 16, "bold")
        ).pack(side=tk.LEFT)

        total_slots = len(all_slots)
        total_hours = sum(s['duration_mins'] for s in all_slots) / 60
        ttk.Label(
            header_frame,
            text=f"{total_slots} slots found ({total_hours:.1f} hours total)",
            font=("Segoe UI", 11),
            foreground="gray"
        ).pack(side=tk.RIGHT)

        # Filter frame
        filter_frame = ttk.LabelFrame(results, text="Filter", padding="10")
        filter_frame.pack(fill=tk.X, padx=15, pady=10)

        filter_inner = ttk.Frame(filter_frame)
        filter_inner.pack(fill=tk.X)

        # Date filter
        ttk.Label(filter_inner, text="Date:", font=("Segoe UI", 11)).pack(side=tk.LEFT, padx=(0, 10))

        self.scan_filter_date = tk.StringVar(value="All Dates")
        date_options = ["All Dates"] + scanned_dates
        date_combo = ttk.Combobox(
            filter_inner,
            textvariable=self.scan_filter_date,
            values=date_options,
            state="readonly",
            width=15,
            font=("Segoe UI", 11)
        )
        date_combo.pack(side=tk.LEFT, padx=(0, 20))
        date_combo.bind("<<ComboboxSelected>>", lambda e: self._apply_scan_filters(results_tree))

        # Room filter
        ttk.Label(filter_inner, text="Room:", font=("Segoe UI", 11)).pack(side=tk.LEFT, padx=(0, 10))

        unique_rooms = sorted(set(s['room'] for s in all_slots))
        self.scan_filter_room = tk.StringVar(value="All Rooms")
        room_options = ["All Rooms"] + unique_rooms
        room_combo = ttk.Combobox(
            filter_inner,
            textvariable=self.scan_filter_room,
            values=room_options,
            state="readonly",
            width=12,
            font=("Segoe UI", 11)
        )
        room_combo.pack(side=tk.LEFT, padx=(0, 20))
        room_combo.bind("<<ComboboxSelected>>", lambda e: self._apply_scan_filters(results_tree))

        # Min duration filter
        ttk.Label(filter_inner, text="Min Duration:", font=("Segoe UI", 11)).pack(side=tk.LEFT, padx=(0, 10))

        self.scan_filter_min_duration = tk.StringVar(value="Any")
        duration_options = ["Any", "30 min", "1 hour", "1.5 hours", "2 hours"]
        duration_combo = ttk.Combobox(
            filter_inner,
            textvariable=self.scan_filter_min_duration,
            values=duration_options,
            state="readonly",
            width=10,
            font=("Segoe UI", 11)
        )
        duration_combo.pack(side=tk.LEFT, padx=(0, 20))
        duration_combo.bind("<<ComboboxSelected>>", lambda e: self._apply_scan_filters(results_tree))

        # Results count label
        self.scan_results_count_var = tk.StringVar(value=f"Showing {total_slots} slots")
        ttk.Label(
            filter_inner,
            textvariable=self.scan_results_count_var,
            foreground="blue",
            font=("Segoe UI", 10)
        ).pack(side=tk.RIGHT)

        # Results treeview
        tree_frame = ttk.Frame(results, padding="15")
        tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("date", "day", "room", "time", "duration")
        results_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=20)

        results_tree.heading("date", text="Date")
        results_tree.heading("day", text="Day")
        results_tree.heading("room", text="Room")
        results_tree.heading("time", text="Time")
        results_tree.heading("duration", text="Duration")

        results_tree.column("date", width=100, anchor=tk.CENTER)
        results_tree.column("day", width=100, anchor=tk.CENTER)
        results_tree.column("room", width=100, anchor=tk.CENTER)
        results_tree.column("time", width=150, anchor=tk.CENTER)
        results_tree.column("duration", width=100, anchor=tk.CENTER)

        # Scrollbars
        tree_scroll_y = ttk.Scrollbar(tree_frame, orient="vertical", command=results_tree.yview)
        tree_scroll_x = ttk.Scrollbar(tree_frame, orient="horizontal", command=results_tree.xview)
        results_tree.configure(yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set)

        results_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

        # Store tree reference
        self.scan_results_tree = results_tree

        # Populate results
        self._populate_scan_results(results_tree, all_slots)

        # Bottom buttons
        bottom_frame = ttk.Frame(results, padding="15")
        bottom_frame.pack(fill=tk.X)

        ttk.Button(
            bottom_frame,
            text="Export to CSV",
            command=lambda: self._export_scan_results_csv(all_slots),
            width=15
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            bottom_frame,
            text="New Scan",
            command=lambda: self._new_scan_from_results(results),
            width=12
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            bottom_frame,
            text="Close",
            command=results.destroy,
            width=10
        ).pack(side=tk.RIGHT, padx=5)

        self.log(f"Room scan complete: found {total_slots} available slots", "success")

    def _populate_scan_results(self, tree, slots):
        """Populate the results treeview with slots."""
        # Clear existing items
        for item in tree.get_children():
            tree.delete(item)

        for slot in slots:
            try:
                date_obj = datetime.strptime(slot['date'], '%Y-%m-%d')
                day_name = date_obj.strftime('%a')
                date_display = date_obj.strftime('%d %b')
            except:
                day_name = ""
                date_display = slot['date']

            time_str = f"{slot['start_time']} - {slot['end_time']}"
            duration_hrs = slot['duration_mins'] / 60

            if duration_hrs == int(duration_hrs):
                duration_str = f"{int(duration_hrs)} hour{'s' if duration_hrs != 1 else ''}"
            else:
                duration_str = f"{duration_hrs:.1f} hours"

            tree.insert("", tk.END, values=(date_display, day_name, slot['room'], time_str, duration_str))

    def _apply_scan_filters(self, tree):
        """Apply filters to scan results."""
        date_filter = self.scan_filter_date.get()
        room_filter = self.scan_filter_room.get()
        duration_filter = self.scan_filter_min_duration.get()

        # Parse min duration
        min_duration_mins = 0
        if duration_filter == "30 min":
            min_duration_mins = 30
        elif duration_filter == "1 hour":
            min_duration_mins = 60
        elif duration_filter == "1.5 hours":
            min_duration_mins = 90
        elif duration_filter == "2 hours":
            min_duration_mins = 120

        # Filter slots
        filtered_slots = []
        for slot in self.scan_results_all_slots:
            # Date filter
            if date_filter != "All Dates" and slot['date'] != date_filter:
                continue

            # Room filter
            if room_filter != "All Rooms" and slot['room'] != room_filter:
                continue

            # Duration filter
            if slot['duration_mins'] < min_duration_mins:
                continue

            filtered_slots.append(slot)

        # Update display
        self._populate_scan_results(tree, filtered_slots)

        # Update count
        total_hours = sum(s['duration_mins'] for s in filtered_slots) / 60
        self.scan_results_count_var.set(f"Showing {len(filtered_slots)} slots ({total_hours:.1f}h)")

    def _export_scan_results_csv(self, slots):
        """Export scan results to CSV file."""
        from tkinter import filedialog

        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfilename=f"available_rooms_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        )

        if not filename:
            return

        try:
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Date", "Day", "Room", "Start Time", "End Time", "Duration (mins)"])

                for slot in slots:
                    try:
                        date_obj = datetime.strptime(slot['date'], '%Y-%m-%d')
                        day_name = date_obj.strftime('%A')
                    except:
                        day_name = ""

                    writer.writerow([
                        slot['date'],
                        day_name,
                        slot['room'],
                        slot['start_time'],
                        slot['end_time'],
                        int(slot['duration_mins'])
                    ])

            self.log(f"Exported {len(slots)} slots to {filename}", "success")
            messagebox.showinfo("Export Complete", f"Exported {len(slots)} slots to:\n{filename}")

        except Exception as e:
            self.log(f"Export error: {e}", "error")
            messagebox.showerror("Export Error", f"Failed to export:\n{e}")

    def _new_scan_from_results(self, results_window):
        """Close results and open new scan dialog."""
        results_window.destroy()
        self.show_scan_rooms_dialog()


def main():
    root = tk.Tk()

    # Set DPI awareness for Windows
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except:
        pass

    app = AsimutBookerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
