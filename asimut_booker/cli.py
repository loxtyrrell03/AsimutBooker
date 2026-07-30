"""Canonical command-line interface for scheduled and interactive operation."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from .config import AppConfig, ConfigError, ScheduleConfig, load_config
from .coordinator import BookingCoordinator, RunSummary
from .database import Database, DatabaseError, RecordNotFoundError
from .errors import (
    AmbiguousBookingResult,
    AuthenticationRequired,
    NavigationError,
    PageContractError,
)
from .gateway import AsimutGateway, GatewaySettings
from .locking import AlreadyLockedError, FileLock
from .notifications import send_run_notification

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_AUTH = 3
EXIT_ALREADY_RUNNING = 4
EXIT_PAGE_CONTRACT = 5
EXIT_AMBIGUOUS_WRITE = 6
EXIT_UNEXPECTED = 7
EXIT_DATABASE = 8

LOG = logging.getLogger(__name__)


class JsonEmitter:
    """One JSON object per line for the GUI, wrapper logs, and humans."""

    def __call__(
        self,
        level: str,
        event: str,
        detail: dict[str, object] | None = None,
        *,
        message: str | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "timestamp": datetime.now().astimezone().isoformat(),
            "level": level,
            "event": event,
            "message": message or event.replace("_", " ").capitalize(),
        }
        if detail:
            payload["detail"] = detail
            payload.update(
                {
                    key: value
                    for key, value in detail.items()
                    if key in {"run_id", "status", "progress", "error_code"}
                }
            )
        print(
            json.dumps(payload, default=_json_default, ensure_ascii=False),
            flush=True,
        )


EMIT = JsonEmitter()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asimut-booker",
        description="Reliable RWCMD Asimut room booking automation",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml (defaults to config/config.yaml)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Scan, extend, and book rooms")
    run.add_argument("--headless", action="store_true")
    run.add_argument("--scheduled", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--extensions-only", action="store_true")

    login = commands.add_parser("login", help="Open a headed browser and save Microsoft SSO state")
    login.add_argument("--timeout-seconds", type=int, default=900)

    doctor = commands.add_parser(
        "doctor", help="Validate config, session, HTML contracts, and storage"
    )
    doctor.add_argument("--json", action="store_true")

    status = commands.add_parser("status", help="Read local operational health and recent runs")
    status.add_argument("--json", action="store_true")

    extensions = commands.add_parser("extensions", help="Manage or reconcile tracked extensions")
    extensions.add_argument("action", choices=("reconcile", "retry", "cancel"))
    extensions.add_argument(
        "--id",
        action="append",
        dest="ids",
        required=True,
        help="Extension ID (repeat for several)",
    )
    extensions.add_argument("--headless", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        _configure_logging(config)
        if args.command == "run":
            return _command_run(config, args)
        if args.command == "login":
            return _command_login(config, args)
        if args.command == "doctor":
            return _command_doctor(config, args)
        if args.command == "status":
            return _command_status(config, args)
        if args.command == "extensions":
            return _command_extensions(config, args)
        raise AssertionError(f"unhandled command: {args.command}")
    except KeyboardInterrupt:
        EMIT("warning", "interrupted", message="Command interrupted.")
        return 130
    except ConfigError as exc:
        EMIT(
            "error",
            "configuration_error",
            {"error_code": type(exc).__name__, "error": str(exc)},
            message=f"Configuration error: {exc}",
        )
        return EXIT_CONFIG
    except AlreadyLockedError as exc:
        holder = asdict(exc.holder) if exc.holder else None
        EMIT(
            "warning",
            "already_running",
            {"error": str(exc), "holder": holder},
            message="Another AsimutBooker process is already running.",
        )
        return EXIT_ALREADY_RUNNING
    except AuthenticationRequired as exc:
        EMIT(
            "error",
            "authentication_required",
            {"error_code": type(exc).__name__, "error": str(exc)},
            message=f"Login required: {exc}",
        )
        return EXIT_AUTH
    except AmbiguousBookingResult as exc:
        EMIT(
            "error",
            "ambiguous_remote_write",
            {"error_code": type(exc).__name__, "error": str(exc)},
            message=(
                "Asimut may have accepted a write, but its exact result could "
                "not be proven. Automatic retry was disabled."
            ),
        )
        return EXIT_AMBIGUOUS_WRITE
    except (PageContractError, NavigationError) as exc:
        EMIT(
            "error",
            "page_contract_error",
            {"error_code": type(exc).__name__, "error": str(exc)},
            message=f"Asimut page verification failed: {exc}",
        )
        return EXIT_PAGE_CONTRACT
    except DatabaseError as exc:
        EMIT(
            "error",
            "database_error",
            {"error_code": type(exc).__name__, "error": str(exc)},
            message=f"Local state database failed: {exc}",
        )
        return EXIT_DATABASE
    except Exception as exc:
        LOG.exception("Unexpected CLI failure")
        EMIT(
            "error",
            "unexpected_error",
            {"error_code": type(exc).__name__, "error": str(exc)},
            message=f"Unexpected failure: {type(exc).__name__}: {exc}",
        )
        return EXIT_UNEXPECTED


def _command_run(config: AppConfig, args: argparse.Namespace) -> int:
    if args.scheduled and not _scheduled_now(config):
        with Database(config.browser.database_path) as database:
            run = database.start_run(trigger="scheduled", dry_run=args.dry_run)
            database.finish_run(
                run.id,
                status="skipped",
                summary={"reason": "schedule disabled or outside active window"},
            )
        EMIT(
            "warning",
            "scheduled_run_skipped",
            {"run_id": run.id},
            message="Scheduled run is disabled or outside its active window.",
        )
        return EXIT_OK

    trigger = "scheduled" if args.scheduled else "manual"
    headless = bool(args.headless or args.scheduled)
    with FileLock(config.browser.lock_path):
        with Database(config.browser.database_path) as database:
            gateway = _gateway(config, headless=headless)
            gateway.open()
            try:
                if not headless:
                    _ensure_headed_authentication(gateway)
                coordinator = BookingCoordinator(config, database, gateway, emit=_coordinator_emit)
                summary = coordinator.run(
                    trigger=trigger,
                    dry_run=args.dry_run,
                    extensions_only=args.extensions_only,
                )
                _notify(config, database, summary)
                return EXIT_OK
            except Exception as exc:
                _capture(gateway, type(exc).__name__)
                raise
            finally:
                gateway.close(save_state=True)


def _command_login(config: AppConfig, args: argparse.Namespace) -> int:
    if args.timeout_seconds < 30:
        raise ConfigError("login timeout must be at least 30 seconds")
    with FileLock(config.browser.lock_path):
        gateway = _gateway(
            config,
            headless=False,
            require_saved_session=False,
        )
        gateway.open()
        try:
            EMIT(
                "info",
                "login_waiting",
                message="Complete Microsoft sign-in in the opened browser.",
            )
            gateway.wait_for_interactive_login(timeout_seconds=args.timeout_seconds)
            EMIT(
                "success",
                "login_saved",
                {
                    "profile_path": str(config.browser.profile_path),
                    "state_path": str(config.browser.state_path),
                },
                message="Asimut login verified; the persistent booker profile is ready.",
            )
            return EXIT_OK
        finally:
            gateway.close(save_state=True)


def _command_doctor(config: AppConfig, args: argparse.Namespace) -> int:
    with FileLock(config.browser.lock_path):
        with Database(config.browser.database_path) as database:
            gateway = _gateway(config, headless=True)
            gateway.open()
            try:
                gateway.verify_authenticated()
                now = datetime.now(ZoneInfo(config.browser.timezone))
                today = now.date()
                agenda = gateway.observe_agenda(
                    today,
                    today + timedelta(days=config.preferences.max_horizon_days + 1),
                )
                overview = gateway.observe_overview(today)
                configured = {item.room_id for item in config.enabled_rooms}
                observed = set(overview.rooms_by_name)
                detail = {
                    "status": "healthy",
                    "config": str(config.config_path),
                    "database": str(database.path),
                    "database_schema": database.schema_version,
                    "session_state": str(config.browser.state_path),
                    "session_profile": str(config.browser.profile_path),
                    "agenda_contract": agenda.contract_version,
                    "agenda_days": agenda.day_count,
                    "overview_contract": overview.contract_version,
                    "overview_date": overview.observed_date,
                    "observed_rooms": len(overview.rooms),
                    "configured_rooms": len(configured),
                    "missing_configured_rooms": sorted(configured.difference(observed)),
                    "rolling_quota_remaining": (agenda.rolling_quota_remaining),
                    "peak_quota_remaining": overview.peak_quota_remaining,
                }
                health_status = "healthy" if not detail["missing_configured_rooms"] else "degraded"
                database.set_health(
                    "doctor",
                    health_status,
                    message="Live Asimut contracts verified",
                    details=detail,
                )
                EMIT(
                    "success" if health_status == "healthy" else "warning",
                    "doctor_complete",
                    detail,
                    message=("Live Asimut session and HTML contracts verified."),
                )
                return EXIT_OK
            except Exception as exc:
                database.set_health(
                    "doctor",
                    "unhealthy",
                    message=str(exc),
                    details={"error_code": type(exc).__name__},
                )
                _capture(gateway, f"doctor-{type(exc).__name__}")
                raise
            finally:
                gateway.close(save_state=True)


def _command_status(config: AppConfig, args: argparse.Namespace) -> int:
    detail: dict[str, object] = {
        "status": "unknown",
        "config": str(config.config_path),
        "database": str(config.browser.database_path),
        "session_state_exists": (
            config.browser.state_path.is_file()
            or (config.browser.profile_path / ".asimut-booker-authenticated").is_file()
        ),
        "schedule_enabled": config.schedule.enabled,
        "rooms_enabled": len(config.enabled_rooms),
    }
    if config.browser.database_path.is_file():
        with Database(config.browser.database_path, read_only=True) as database:
            detail["database_schema"] = database.schema_version
            detail["recent_runs"] = [asdict(item) for item in database.list_runs(limit=10)]
            health: dict[str, object] = {}
            for component in (
                "authentication",
                "room_inventory",
                "last_run",
                "doctor",
            ):
                try:
                    health[component] = asdict(database.get_health(component))
                except RecordNotFoundError:
                    continue
            detail["health"] = health
    detail["status"] = _derive_overall_status(
        session_state_exists=bool(detail["session_state_exists"]),
        database_exists=config.browser.database_path.is_file(),
        health=detail.get("health", {}),
    )
    EMIT(
        {
            "healthy": "success",
            "degraded": "warning",
            "unhealthy": "error",
        }[str(detail["status"])],
        "status",
        detail,
        message="Local AsimutBooker status loaded.",
    )
    return EXIT_OK


def _derive_overall_status(
    *,
    session_state_exists: bool,
    database_exists: bool,
    health: object,
) -> str:
    """Roll local prerequisites and live component health into one status."""

    if not session_state_exists or not database_exists:
        return "degraded"
    if not isinstance(health, dict):
        return "degraded"
    live_status = _latest_live_health_status(health)
    if live_status == "unhealthy":
        return "unhealthy"
    component_states = {
        str(item.get("status", "")).casefold()
        for component, item in health.items()
        if component not in {"authentication", "doctor"} and isinstance(item, dict)
    }
    if "unhealthy" in component_states:
        return "unhealthy"
    if live_status == "degraded" or "degraded" in component_states:
        return "degraded"
    # A cookie file alone is not proof of a usable session.
    return "healthy" if live_status == "healthy" else "degraded"


def _latest_live_health_status(health: dict[object, object]) -> str | None:
    """Return the newest doctor/auth result, failing closed if order is unknown."""

    records: list[tuple[str, datetime | None]] = []
    for component in ("authentication", "doctor"):
        raw = health.get(component)
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status", "")).casefold()
        if not status:
            continue
        timestamp: datetime | None = None
        raw_timestamp = raw.get("updated_at")
        if raw_timestamp:
            try:
                timestamp = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
            except ValueError:
                timestamp = None
        records.append((status, timestamp))
    if not records:
        return None
    if len(records) == 1:
        return records[0][0]
    if all(timestamp is not None for _, timestamp in records):
        return max(records, key=lambda item: item[1])[0]
    states = {status for status, _ in records}
    if "unhealthy" in states:
        return "unhealthy"
    if "degraded" in states:
        return "degraded"
    return "healthy" if "healthy" in states else None


def _command_extensions(config: AppConfig, args: argparse.Namespace) -> int:
    identifiers = frozenset(args.ids)
    with FileLock(config.browser.lock_path):
        with Database(config.browser.database_path) as database:
            records = []
            for identifier in identifiers:
                records.append(database.get_extension(identifier))
            if args.action in {"retry", "cancel"}:
                status = "pending" if args.action == "retry" else "cancelled"
                for record in records:
                    database.update_extension(
                        record.id,
                        status=status,
                        error_code=None,
                        error_message=None,
                    )
                EMIT(
                    "success",
                    f"extensions_{args.action}",
                    {"count": len(records), "ids": sorted(identifiers)},
                    message=(
                        f"{len(records)} extension job(s) marked {status}. "
                        "Existing Asimut bookings were not cancelled."
                    ),
                )
                return EXIT_OK

            gateway = _gateway(config, headless=bool(args.headless))
            gateway.open()
            try:
                if not args.headless:
                    _ensure_headed_authentication(gateway)
                coordinator = BookingCoordinator(config, database, gateway, emit=_coordinator_emit)
                summary = coordinator.run(
                    trigger="extension_reconcile",
                    extensions_only=True,
                    extension_ids=identifiers,
                )
                EMIT(
                    "success",
                    "extensions_reconciled",
                    summary.as_dict(),
                    message=(f"Reconciled {len(identifiers)} selected extension job(s)."),
                )
                return EXIT_OK
            except Exception as exc:
                _capture(gateway, f"extensions-{type(exc).__name__}")
                raise
            finally:
                gateway.close(save_state=True)


def _ensure_headed_authentication(
    gateway: AsimutGateway,
    *,
    timeout_seconds: int = 900,
) -> None:
    """Keep a headed command alive through Microsoft MFA, then resume it."""

    try:
        gateway.verify_authenticated()
        return
    except AuthenticationRequired:
        EMIT(
            "warning",
            "interactive_login_waiting",
            {"timeout_seconds": timeout_seconds},
            message=(
                "The booker session needs Microsoft authentication. "
                "This window will remain open while sign-in is completed."
            ),
        )
    gateway.wait_for_interactive_login(
        timeout_seconds=timeout_seconds,
        navigate=False,
    )
    gateway.verify_authenticated()
    EMIT(
        "success",
        "interactive_login_saved",
        message="Booker authentication was saved; continuing the requested command.",
    )


def _gateway(
    config: AppConfig,
    *,
    headless: bool,
    require_saved_session: bool = True,
) -> AsimutGateway:
    settings = GatewaySettings(
        base_url=f"{config.institution.base_url}/",
        location_group_id=config.institution.location_group_id,
        storage_state_path=config.browser.state_path,
        profile_path=config.browser.profile_path,
        artifacts_dir=config.browser.artifacts_dir,
        executable_path=config.browser.executable_path,
        timezone=config.browser.timezone,
        timeout_ms=max(
            config.browser.navigation_timeout_ms,
            config.browser.action_timeout_ms,
        ),
    )
    return AsimutGateway(
        settings,
        headless=headless,
        require_saved_session=require_saved_session,
    )


def _scheduled_now(config: AppConfig) -> bool:
    now = datetime.now(ZoneInfo(config.browser.timezone))
    return _is_valid_scheduled_launch(config.schedule, now)


def _is_valid_scheduled_launch(
    schedule: ScheduleConfig,
    now: datetime,
) -> bool:
    """Accept only the configured lead/late window around a 15-minute target."""

    if not schedule.enabled:
        return False
    current_second = now.hour * 60 * 60 + now.minute * 60 + now.second + now.microsecond / 1_000_000
    cadence_seconds = schedule.cadence_minutes * 60
    for target_minute in range(
        schedule.active_start_minute,
        schedule.active_end_minute + 1,
        schedule.cadence_minutes,
    ):
        offset = current_second - target_minute * 60
        if -schedule.lead_seconds <= offset <= schedule.max_lateness_seconds:
            return True
        if target_minute * 60 - current_second > cadence_seconds:
            break
    return False


def _coordinator_emit(level: str, event: str, detail: dict[str, object]) -> None:
    display_level = "success" if event.endswith("_verified") else level
    EMIT(display_level, event, detail)


def _notify(
    config: AppConfig,
    database: Database,
    summary: RunSummary,
) -> None:
    if not config.notifications.enabled:
        return
    try:
        send_run_notification(config.notifications, summary)
        database.set_health(
            "notifications",
            "healthy",
            message="Run notification delivered",
        )
    except Exception as exc:
        LOG.exception("Notification delivery failed")
        database.set_health(
            "notifications",
            "degraded",
            message=str(exc),
            details={"error_code": type(exc).__name__},
        )
        EMIT(
            "warning",
            "notification_failed",
            {"error": str(exc)},
            message="The booking run completed, but its notification failed.",
        )


def _capture(gateway: AsimutGateway, label: str) -> None:
    try:
        paths = gateway.capture_diagnostics(label)
        if paths:
            EMIT(
                "warning",
                "diagnostics_captured",
                {"paths": [str(path) for path in paths]},
                message="Local diagnostic artifacts were captured.",
            )
    except Exception:
        LOG.exception("Diagnostic capture failed")


def _configure_logging(config: AppConfig) -> None:
    config.logging.directory.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, config.logging.level))
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    handler = TimedRotatingFileHandler(
        config.logging.directory / "asimut_booker.log",
        when="midnight",
        backupCount=config.logging.retention_days,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)


def _json_default(value: object) -> object:
    if isinstance(value, (datetime, date, Path)):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return repr(value)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
