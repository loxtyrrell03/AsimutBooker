"""Transactional SQLite state for AsimutBooker.

SQLite is used as a local coordination and audit store, never as the source of
truth for whether Asimut accepted a booking.  Successful writes still need to
be reconciled against the remote agenda.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4

RUN_STATUSES = frozenset(
    {"running", "succeeded", "completed", "partial", "no_op", "failed", "aborted", "skipped"}
)
OBSERVATION_STATUSES = frozenset({"valid", "invalid", "degraded"})
INTENT_STATUSES = frozenset(
    {
        "planned",
        "revalidating",
        "ready",
        "submitting",
        "ambiguous",
        "verified",
        "rejected",
        "retryable",
        "failed",
        "cancelled",
    }
)
EXTENSION_STATUSES = frozenset(
    {
        "pending",
        "in_progress",
        "retryable",
        "blocked",
        "complete",
        "verified",
        "failed",
        "cancelled",
    }
)
HEALTH_STATUSES = frozenset({"healthy", "degraded", "unhealthy", "unknown"})
INTENT_KINDS = frozenset({"booking", "create", "extension", "extend"})


class DatabaseError(RuntimeError):
    """Base class for repository errors."""


class RecordNotFoundError(DatabaseError, KeyError):
    """The requested row does not exist."""


class IdempotencyConflictError(DatabaseError):
    """An idempotency key was reused for different data."""


class StateTransitionError(DatabaseError):
    """A compare-and-swap state transition failed."""


@dataclass(frozen=True, slots=True)
class RunRecord:
    id: str
    started_at: str
    finished_at: str | None
    status: str
    trigger: str
    dry_run: bool
    bookings_created: int
    extensions_completed: int
    error_code: str | None
    error_message: str | None
    summary: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    id: str
    observation_key: str
    run_id: str | None
    observed_at: str
    kind: str
    status: str
    page_url: str | None
    requested_date: str | None
    observed_date: str | None
    contract_version: str | None
    artifact_path: str | None
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EventRecord:
    event_key: str
    external_event_id: str | None
    event_date: str
    room_id: str | None
    start_minute: int
    end_minute: int
    event_type: str
    status: str
    title: str | None
    is_cancelled: bool
    source_observation_id: str | None
    first_seen_at: str
    last_seen_at: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class IntentRecord:
    id: str
    intent_key: str
    kind: str
    requested_date: str
    room_id: str
    start_minute: int
    end_minute: int
    target_end_minute: int | None
    event_key: str | None
    run_id: str | None
    status: str
    attempt_count: int
    last_error_code: str | None
    last_error_message: str | None
    created_at: str
    updated_at: str
    verified_at: str | None
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ExtensionRecord:
    id: str
    intent_id: str
    event_key: str | None
    current_end_minute: int
    target_end_minute: int
    status: str
    next_attempt_at: str | None
    attempt_count: int
    last_error_code: str | None
    last_error_message: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class HealthRecord:
    component: str
    status: str
    message: str | None
    details: dict[str, Any]
    updated_at: str


_MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        1,
        (
            """
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                trigger TEXT NOT NULL,
                dry_run INTEGER NOT NULL CHECK (dry_run IN (0, 1)),
                bookings_created INTEGER NOT NULL DEFAULT 0 CHECK (bookings_created >= 0),
                extensions_completed INTEGER NOT NULL DEFAULT 0 CHECK (extensions_completed >= 0),
                error_code TEXT,
                error_message TEXT,
                summary_json TEXT NOT NULL DEFAULT '{}'
            )
            """,
            "CREATE INDEX IF NOT EXISTS runs_started_at_idx ON runs(started_at DESC)",
            "CREATE INDEX IF NOT EXISTS runs_status_idx ON runs(status, started_at DESC)",
            """
            CREATE TABLE IF NOT EXISTS observations (
                id TEXT PRIMARY KEY,
                observation_key TEXT NOT NULL UNIQUE,
                run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
                observed_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                page_url TEXT,
                requested_date TEXT,
                observed_date TEXT,
                contract_version TEXT,
                artifact_path TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}'
            )
            """,
            "CREATE INDEX IF NOT EXISTS observations_run_idx ON observations(run_id, observed_at)",
            "CREATE INDEX IF NOT EXISTS observations_kind_idx ON observations(kind, observed_at DESC)",
            """
            CREATE TABLE IF NOT EXISTS events (
                event_key TEXT PRIMARY KEY,
                external_event_id TEXT,
                event_date TEXT NOT NULL,
                room_id TEXT,
                start_minute INTEGER NOT NULL CHECK (start_minute >= 0 AND start_minute < 1440),
                end_minute INTEGER NOT NULL CHECK (end_minute > 0 AND end_minute <= 1440),
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT,
                is_cancelled INTEGER NOT NULL CHECK (is_cancelled IN (0, 1)),
                source_observation_id TEXT REFERENCES observations(id) ON DELETE SET NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                CHECK (end_minute > start_minute)
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS events_external_id_idx
            ON events(external_event_id)
            WHERE external_event_id IS NOT NULL
            """,
            "CREATE INDEX IF NOT EXISTS events_date_idx ON events(event_date, start_minute)",
            """
            CREATE TABLE IF NOT EXISTS intents (
                id TEXT PRIMARY KEY,
                intent_key TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                requested_date TEXT NOT NULL,
                room_id TEXT NOT NULL,
                start_minute INTEGER NOT NULL CHECK (start_minute >= 0 AND start_minute < 1440),
                end_minute INTEGER NOT NULL CHECK (end_minute > 0 AND end_minute <= 1440),
                target_end_minute INTEGER,
                event_key TEXT,
                run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
                last_error_code TEXT,
                last_error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                verified_at TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                CHECK (end_minute > start_minute),
                CHECK (target_end_minute IS NULL OR
                       (target_end_minute >= end_minute AND target_end_minute <= 1440))
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS intents_natural_key_idx
            ON intents(kind, requested_date, room_id, start_minute)
            """,
            "CREATE INDEX IF NOT EXISTS intents_status_idx ON intents(status, updated_at)",
            "CREATE INDEX IF NOT EXISTS intents_run_idx ON intents(run_id, created_at)",
            """
            CREATE TABLE IF NOT EXISTS extensions (
                id TEXT PRIMARY KEY,
                intent_id TEXT NOT NULL UNIQUE REFERENCES intents(id) ON DELETE CASCADE,
                event_key TEXT,
                current_end_minute INTEGER NOT NULL CHECK (current_end_minute > 0 AND current_end_minute <= 1440),
                target_end_minute INTEGER NOT NULL CHECK (target_end_minute > 0 AND target_end_minute <= 1440),
                status TEXT NOT NULL,
                next_attempt_at TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
                last_error_code TEXT,
                last_error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (target_end_minute >= current_end_minute)
            )
            """,
            "CREATE INDEX IF NOT EXISTS extensions_due_idx ON extensions(status, next_attempt_at)",
            """
            CREATE TABLE IF NOT EXISTS health (
                component TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                message TEXT,
                details_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            )
            """,
        ),
    ),
)


def default_database_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "asimut_booker.sqlite3"


class Database:
    """Thread-safe SQLite repository with cross-process WAL concurrency."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        timeout_seconds: float = 10.0,
        read_only: bool = False,
    ) -> None:
        self.path = Path(path) if path is not None else default_database_path()
        self.path = self.path.expanduser().resolve()
        self.read_only = read_only
        self._lock = threading.RLock()
        self._closed = False
        self._savepoint_counter = 0

        if read_only:
            if not self.path.is_file():
                raise DatabaseError(f"database does not exist: {self.path}")
            uri = f"{self.path.as_uri()}?mode=ro"
            self._connection = sqlite3.connect(
                uri,
                uri=True,
                timeout=timeout_seconds,
                isolation_level=None,
                check_same_thread=False,
            )
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(
                str(self.path),
                timeout=timeout_seconds,
                isolation_level=None,
                check_same_thread=False,
            )
        try:
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute(f"PRAGMA busy_timeout = {int(timeout_seconds * 1000)}")
            if read_only:
                self._connection.execute("PRAGMA query_only = ON")
                self._verify_schema()
            else:
                self._connection.execute("PRAGMA journal_mode = WAL")
                self._connection.execute("PRAGMA synchronous = FULL")
                self._connection.execute("PRAGMA wal_autocheckpoint = 1000")
                self._migrate()
        except BaseException:
            self._connection.close()
            self._closed = True
            raise

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    @property
    def schema_version(self) -> int:
        row = self._query_one("SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations")
        return int(row["version"]) if row is not None else 0

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        """Open a transaction; nested calls use savepoints."""

        if self.read_only:
            raise DatabaseError("cannot start a write transaction on a read-only database")
        self._ensure_open()
        with self._lock:
            connection = self._connection
            nested = connection.in_transaction
            savepoint = ""
            if nested:
                self._savepoint_counter += 1
                savepoint = f"sp_{self._savepoint_counter}"
                connection.execute(f"SAVEPOINT {savepoint}")
            else:
                connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield connection
            except BaseException:
                if nested:
                    connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                else:
                    connection.rollback()
                raise
            else:
                if nested:
                    connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                else:
                    connection.commit()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def checkpoint(self, *, truncate: bool = False) -> tuple[int, int, int]:
        """Checkpoint the WAL and return SQLite's result tuple."""

        self._ensure_open()
        if self.read_only:
            raise DatabaseError("cannot checkpoint a read-only database")
        mode = "TRUNCATE" if truncate else "PASSIVE"
        with self._lock:
            row = self._connection.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        return tuple(int(value) for value in row)

    def start_run(
        self,
        *,
        trigger: str,
        dry_run: bool = False,
        run_id: str | None = None,
        started_at: datetime | str | None = None,
    ) -> RunRecord:
        supplied_run_id = run_id is not None
        run_id = run_id or uuid4().hex
        if supplied_run_id:
            row = self._query_one("SELECT * FROM runs WHERE id = ?", (run_id,))
            if row is not None:
                existing = _run_record(row)
                if existing.trigger != trigger or existing.dry_run != dry_run:
                    raise IdempotencyConflictError(
                        f"run id {run_id!r} was reused for different run data"
                    )
                if started_at is not None and existing.started_at != _timestamp(started_at):
                    raise IdempotencyConflictError(
                        f"run id {run_id!r} was reused with a different start time"
                    )
                return existing
        timestamp = _timestamp(started_at)
        with self.transaction() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO runs (
                        id, started_at, status, trigger, dry_run, summary_json
                    ) VALUES (?, ?, 'running', ?, ?, '{}')
                    """,
                    (run_id, timestamp, _required_text(trigger, "trigger"), int(dry_run)),
                )
            except sqlite3.IntegrityError:
                existing = self.get_run(run_id)
                if (
                    existing.trigger != trigger
                    or existing.dry_run != dry_run
                    or existing.started_at != timestamp
                ):
                    raise IdempotencyConflictError(
                        f"run id {run_id!r} was reused for different run data"
                    ) from None
                return existing
        return self.get_run(run_id)

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        bookings_created: int = 0,
        extensions_completed: int = 0,
        error_code: str | None = None,
        error_message: str | None = None,
        summary: Mapping[str, Any] | None = None,
        finished_at: datetime | str | None = None,
    ) -> RunRecord:
        _status(status, RUN_STATUSES, "run")
        if status == "running":
            raise ValueError("finish_run status may not be 'running'")
        bookings_created = _nonnegative_int(bookings_created, "bookings_created")
        extensions_completed = _nonnegative_int(extensions_completed, "extensions_completed")
        finished_at_text = _timestamp(finished_at)
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE runs
                SET finished_at = ?, status = ?, bookings_created = ?,
                    extensions_completed = ?, error_code = ?, error_message = ?,
                    summary_json = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    finished_at_text,
                    status,
                    bookings_created,
                    extensions_completed,
                    error_code,
                    error_message,
                    _json(summary),
                    run_id,
                ),
            )
            if cursor.rowcount != 1:
                row = connection.execute(
                    "SELECT status FROM runs WHERE id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    raise RecordNotFoundError(f"run not found: {run_id}")
                raise StateTransitionError(
                    f"run {run_id} is already in terminal state {row['status']!r}"
                )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> RunRecord:
        row = self._query_one("SELECT * FROM runs WHERE id = ?", (run_id,))
        if row is None:
            raise RecordNotFoundError(f"run not found: {run_id}")
        return _run_record(row)

    def list_runs(self, *, limit: int = 100, status: str | None = None) -> list[RunRecord]:
        limit = _positive_limit(limit)
        parameters: list[Any] = []
        where = ""
        if status is not None:
            _status(status, RUN_STATUSES, "run")
            where = "WHERE status = ?"
            parameters.append(status)
        parameters.append(limit)
        rows = self._query_all(
            f"SELECT * FROM runs {where} ORDER BY started_at DESC LIMIT ?",
            parameters,
        )
        return [_run_record(row) for row in rows]

    def record_observation(
        self,
        *,
        kind: str,
        status: str,
        run_id: str | None = None,
        observation_key: str | None = None,
        observed_at: datetime | str | None = None,
        page_url: str | None = None,
        requested_date: date | str | None = None,
        observed_date: date | str | None = None,
        contract_version: str | None = None,
        artifact_path: str | Path | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> ObservationRecord:
        _status(status, OBSERVATION_STATUSES, "observation")
        observed_at_text = _timestamp(observed_at)
        observation_key = observation_key or uuid4().hex
        values = (
            uuid4().hex,
            observation_key,
            run_id,
            observed_at_text,
            _required_text(kind, "kind"),
            status,
            page_url,
            _date_text(requested_date),
            _date_text(observed_date),
            contract_version,
            str(artifact_path) if artifact_path is not None else None,
            _json(payload),
        )
        with self.transaction() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO observations (
                        id, observation_key, run_id, observed_at, kind, status,
                        page_url, requested_date, observed_date, contract_version,
                        artifact_path, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            except sqlite3.IntegrityError as exc:
                existing = self.get_observation_by_key(observation_key)
                comparable = (
                    existing.run_id,
                    existing.observed_at,
                    existing.kind,
                    existing.status,
                    existing.page_url,
                    existing.requested_date,
                    existing.observed_date,
                    existing.contract_version,
                    existing.artifact_path,
                    existing.payload,
                )
                supplied = (
                    run_id,
                    observed_at_text,
                    kind,
                    status,
                    page_url,
                    _date_text(requested_date),
                    _date_text(observed_date),
                    contract_version,
                    str(artifact_path) if artifact_path is not None else None,
                    dict(payload or {}),
                )
                if comparable != supplied:
                    raise IdempotencyConflictError(
                        f"observation key {observation_key!r} was reused"
                    ) from exc
                return existing
        return self.get_observation_by_key(observation_key)

    def get_observation(self, observation_id: str) -> ObservationRecord:
        row = self._query_one("SELECT * FROM observations WHERE id = ?", (observation_id,))
        if row is None:
            raise RecordNotFoundError(f"observation not found: {observation_id}")
        return _observation_record(row)

    def get_observation_by_key(self, observation_key: str) -> ObservationRecord:
        row = self._query_one(
            "SELECT * FROM observations WHERE observation_key = ?",
            (observation_key,),
        )
        if row is None:
            raise RecordNotFoundError(f"observation not found for key: {observation_key}")
        return _observation_record(row)

    def list_observations(
        self, *, run_id: str | None = None, kind: str | None = None, limit: int = 500
    ) -> list[ObservationRecord]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            parameters.append(run_id)
        if kind is not None:
            clauses.append("kind = ?")
            parameters.append(kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(_positive_limit(limit))
        rows = self._query_all(
            f"SELECT * FROM observations {where} ORDER BY observed_at DESC LIMIT ?",
            parameters,
        )
        return [_observation_record(row) for row in rows]

    def upsert_event(
        self,
        *,
        event_key: str,
        event_date: date | str,
        start_minute: int,
        end_minute: int,
        event_type: str,
        status: str = "active",
        external_event_id: str | None = None,
        room_id: str | None = None,
        title: str | None = None,
        is_cancelled: bool = False,
        source_observation_id: str | None = None,
        observed_at: datetime | str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> EventRecord:
        _minutes(start_minute, end_minute)
        key = _required_text(event_key, "event_key")
        timestamp = _timestamp(observed_at)
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO events (
                    event_key, external_event_id, event_date, room_id,
                    start_minute, end_minute, event_type, status, title,
                    is_cancelled, source_observation_id, first_seen_at,
                    last_seen_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_key) DO UPDATE SET
                    external_event_id = excluded.external_event_id,
                    event_date = excluded.event_date,
                    room_id = excluded.room_id,
                    start_minute = excluded.start_minute,
                    end_minute = excluded.end_minute,
                    event_type = excluded.event_type,
                    status = excluded.status,
                    title = excluded.title,
                    is_cancelled = excluded.is_cancelled,
                    source_observation_id = excluded.source_observation_id,
                    last_seen_at = excluded.last_seen_at,
                    payload_json = excluded.payload_json
                """,
                (
                    key,
                    external_event_id,
                    _date_text(event_date),
                    room_id,
                    start_minute,
                    end_minute,
                    _required_text(event_type, "event_type"),
                    _required_text(status, "status"),
                    title,
                    int(is_cancelled),
                    source_observation_id,
                    timestamp,
                    timestamp,
                    _json(payload),
                ),
            )
        return self.get_event(key)

    def get_event(self, event_key: str) -> EventRecord:
        row = self._query_one("SELECT * FROM events WHERE event_key = ?", (event_key,))
        if row is None:
            raise RecordNotFoundError(f"event not found: {event_key}")
        return _event_record(row)

    def list_events(
        self,
        *,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
        include_cancelled: bool = True,
        limit: int = 1000,
    ) -> list[EventRecord]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if start_date is not None:
            clauses.append("event_date >= ?")
            parameters.append(_date_text(start_date))
        if end_date is not None:
            clauses.append("event_date <= ?")
            parameters.append(_date_text(end_date))
        if not include_cancelled:
            clauses.append("is_cancelled = 0")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(_positive_limit(limit))
        rows = self._query_all(
            f"""
            SELECT * FROM events {where}
            ORDER BY event_date, start_minute, room_id
            LIMIT ?
            """,
            parameters,
        )
        return [_event_record(row) for row in rows]

    def create_intent(
        self,
        *,
        kind: str,
        requested_date: date | str,
        room_id: str,
        start_minute: int,
        end_minute: int,
        target_end_minute: int | None = None,
        intent_key: str | None = None,
        event_key: str | None = None,
        run_id: str | None = None,
        status: str = "planned",
        payload: Mapping[str, Any] | None = None,
        created_at: datetime | str | None = None,
    ) -> IntentRecord:
        _status(kind, INTENT_KINDS, "intent kind")
        _status(status, INTENT_STATUSES, "intent")
        _minutes(start_minute, end_minute)
        if target_end_minute is not None:
            if type(target_end_minute) is not int or not end_minute <= target_end_minute <= 1440:
                raise ValueError("target_end_minute must be between end_minute and 1440")
        requested = _date_text(requested_date)
        room_id = _required_text(room_id, "room_id")
        intent_key = intent_key or (f"{kind}|{requested}|{room_id}|{start_minute}")
        timestamp = _timestamp(created_at)
        identifier = uuid4().hex
        supplied = {
            "kind": kind,
            "requested_date": requested,
            "room_id": room_id,
            "start_minute": start_minute,
            "end_minute": end_minute,
            "target_end_minute": target_end_minute,
            "event_key": event_key,
            "run_id": run_id,
            "payload": dict(payload or {}),
        }
        with self.transaction() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO intents (
                        id, intent_key, kind, requested_date, room_id,
                        start_minute, end_minute, target_end_minute, event_key,
                        run_id, status, created_at, updated_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        intent_key,
                        kind,
                        requested,
                        room_id,
                        start_minute,
                        end_minute,
                        target_end_minute,
                        event_key,
                        run_id,
                        status,
                        timestamp,
                        timestamp,
                        _json(payload),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                row = self._query_one(
                    """
                    SELECT * FROM intents
                    WHERE intent_key = ?
                       OR (kind = ? AND requested_date = ? AND room_id = ?
                           AND start_minute = ?)
                    """,
                    (intent_key, kind, requested, room_id, start_minute),
                )
                if row is None:
                    raise DatabaseError("could not create intent") from exc
                existing = _intent_record(row)
                comparable = {
                    "kind": existing.kind,
                    "requested_date": existing.requested_date,
                    "room_id": existing.room_id,
                    "start_minute": existing.start_minute,
                    "end_minute": existing.end_minute,
                    "target_end_minute": existing.target_end_minute,
                    "event_key": existing.event_key,
                    "run_id": existing.run_id,
                    "payload": existing.payload,
                }
                if comparable != supplied:
                    raise IdempotencyConflictError(
                        "intent key or natural booking key was reused for different data"
                    ) from exc
                return existing
        return self.get_intent(identifier)

    def get_intent(self, intent_id: str) -> IntentRecord:
        row = self._query_one("SELECT * FROM intents WHERE id = ?", (intent_id,))
        if row is None:
            raise RecordNotFoundError(f"intent not found: {intent_id}")
        return _intent_record(row)

    def get_intent_by_key(self, intent_key: str) -> IntentRecord:
        row = self._query_one("SELECT * FROM intents WHERE intent_key = ?", (intent_key,))
        if row is None:
            raise RecordNotFoundError(f"intent not found for key: {intent_key}")
        return _intent_record(row)

    def update_intent_status(
        self,
        intent_id: str,
        status: str,
        *,
        expected_status: str | None = None,
        increment_attempt: bool = False,
        error_code: str | None = None,
        error_message: str | None = None,
        event_key: str | None = None,
        verified_at: datetime | str | None = None,
        updated_at: datetime | str | None = None,
    ) -> IntentRecord:
        _status(status, INTENT_STATUSES, "intent")
        if expected_status is not None:
            _status(expected_status, INTENT_STATUSES, "intent")
        verified = (
            _timestamp(verified_at)
            if verified_at is not None
            else (_timestamp() if status == "verified" else None)
        )
        parameters: list[Any] = [
            status,
            int(increment_attempt),
            error_code,
            error_message,
            event_key,
            event_key,
            verified,
            _timestamp(updated_at),
            intent_id,
        ]
        expected_clause = ""
        if expected_status is not None:
            expected_clause = " AND status = ?"
            parameters.append(expected_status)
        with self.transaction() as connection:
            cursor = connection.execute(
                f"""
                UPDATE intents
                SET status = ?,
                    attempt_count = attempt_count + ?,
                    last_error_code = ?,
                    last_error_message = ?,
                    event_key = CASE WHEN ? IS NULL THEN event_key ELSE ? END,
                    verified_at = COALESCE(?, verified_at),
                    updated_at = ?
                WHERE id = ?{expected_clause}
                """,
                parameters,
            )
            if cursor.rowcount != 1:
                if self._query_one("SELECT id FROM intents WHERE id = ?", (intent_id,)) is None:
                    raise RecordNotFoundError(f"intent not found: {intent_id}")
                raise StateTransitionError(
                    f"intent {intent_id} was not in expected state {expected_status!r}"
                )
        return self.get_intent(intent_id)

    def list_intents(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        limit: int = 500,
    ) -> list[IntentRecord]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if status is not None:
            _status(status, INTENT_STATUSES, "intent")
            clauses.append("status = ?")
            parameters.append(status)
        if kind is not None:
            _status(kind, INTENT_KINDS, "intent kind")
            clauses.append("kind = ?")
            parameters.append(kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(_positive_limit(limit))
        rows = self._query_all(
            f"SELECT * FROM intents {where} ORDER BY created_at DESC LIMIT ?",
            parameters,
        )
        return [_intent_record(row) for row in rows]

    def upsert_extension(
        self,
        *,
        intent_id: str,
        current_end_minute: int,
        target_end_minute: int,
        event_key: str | None = None,
        status: str = "pending",
        next_attempt_at: datetime | str | None = None,
        updated_at: datetime | str | None = None,
    ) -> ExtensionRecord:
        _status(status, EXTENSION_STATUSES, "extension")
        if (
            type(current_end_minute) is not int
            or type(target_end_minute) is not int
            or not 0 < current_end_minute <= target_end_minute <= 1440
        ):
            raise ValueError("extension minutes must satisfy 0 < current <= target <= 1440")
        timestamp = _timestamp(updated_at)
        next_attempt = _timestamp(next_attempt_at) if next_attempt_at is not None else None
        with self.transaction() as connection:
            existing_row = connection.execute(
                "SELECT * FROM extensions WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            if existing_row is not None:
                existing = _extension_record(existing_row)
                if current_end_minute < existing.current_end_minute:
                    raise StateTransitionError("extension progress may not move backwards")
            connection.execute(
                """
                INSERT INTO extensions (
                    id, intent_id, event_key, current_end_minute,
                    target_end_minute, status, next_attempt_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(intent_id) DO UPDATE SET
                    event_key = excluded.event_key,
                    current_end_minute = excluded.current_end_minute,
                    target_end_minute = excluded.target_end_minute,
                    status = excluded.status,
                    next_attempt_at = excluded.next_attempt_at,
                    updated_at = excluded.updated_at
                """,
                (
                    uuid4().hex,
                    intent_id,
                    event_key,
                    current_end_minute,
                    target_end_minute,
                    status,
                    next_attempt,
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_extension_for_intent(intent_id)

    def get_extension(self, extension_id: str) -> ExtensionRecord:
        row = self._query_one("SELECT * FROM extensions WHERE id = ?", (extension_id,))
        if row is None:
            raise RecordNotFoundError(f"extension not found: {extension_id}")
        return _extension_record(row)

    def get_extension_for_intent(self, intent_id: str) -> ExtensionRecord:
        row = self._query_one("SELECT * FROM extensions WHERE intent_id = ?", (intent_id,))
        if row is None:
            raise RecordNotFoundError(f"extension not found for intent: {intent_id}")
        return _extension_record(row)

    def update_extension(
        self,
        extension_id: str,
        *,
        status: str,
        expected_status: str | None = None,
        current_end_minute: int | None = None,
        next_attempt_at: datetime | str | None = None,
        increment_attempt: bool = False,
        error_code: str | None = None,
        error_message: str | None = None,
        updated_at: datetime | str | None = None,
    ) -> ExtensionRecord:
        _status(status, EXTENSION_STATUSES, "extension")
        if expected_status is not None:
            _status(expected_status, EXTENSION_STATUSES, "extension")
        next_attempt = _timestamp(next_attempt_at) if next_attempt_at is not None else None
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM extensions WHERE id = ?", (extension_id,)
            ).fetchone()
            if row is None:
                raise RecordNotFoundError(f"extension not found: {extension_id}")
            existing = _extension_record(row)
            new_end = (
                existing.current_end_minute if current_end_minute is None else current_end_minute
            )
            if (
                type(new_end) is not int
                or not existing.current_end_minute <= new_end <= existing.target_end_minute
            ):
                raise ValueError("current_end_minute may not regress or exceed the target")
            parameters: list[Any] = [
                status,
                new_end,
                next_attempt,
                int(increment_attempt),
                error_code,
                error_message,
                _timestamp(updated_at),
                extension_id,
            ]
            expected_clause = ""
            if expected_status is not None:
                expected_clause = " AND status = ?"
                parameters.append(expected_status)
            cursor = connection.execute(
                f"""
                UPDATE extensions
                SET status = ?, current_end_minute = ?, next_attempt_at = ?,
                    attempt_count = attempt_count + ?,
                    last_error_code = ?, last_error_message = ?, updated_at = ?
                WHERE id = ?{expected_clause}
                """,
                parameters,
            )
            if cursor.rowcount != 1:
                raise StateTransitionError(
                    f"extension {extension_id} was not in expected state {expected_status!r}"
                )
        return self.get_extension(extension_id)

    def list_extensions(
        self,
        *,
        status: str | None = None,
        due_before: datetime | str | None = None,
        limit: int = 500,
    ) -> list[ExtensionRecord]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if status is not None:
            _status(status, EXTENSION_STATUSES, "extension")
            clauses.append("status = ?")
            parameters.append(status)
        if due_before is not None:
            clauses.append("(next_attempt_at IS NULL OR next_attempt_at <= ?)")
            parameters.append(_timestamp(due_before))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(_positive_limit(limit))
        rows = self._query_all(
            f"""
            SELECT * FROM extensions {where}
            ORDER BY COALESCE(next_attempt_at, created_at), created_at
            LIMIT ?
            """,
            parameters,
        )
        return [_extension_record(row) for row in rows]

    def set_health(
        self,
        component: str,
        status: str,
        *,
        message: str | None = None,
        details: Mapping[str, Any] | None = None,
        updated_at: datetime | str | None = None,
    ) -> HealthRecord:
        _status(status, HEALTH_STATUSES, "health")
        component = _required_text(component, "component")
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO health (
                    component, status, message, details_json, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(component) DO UPDATE SET
                    status = excluded.status,
                    message = excluded.message,
                    details_json = excluded.details_json,
                    updated_at = excluded.updated_at
                """,
                (
                    component,
                    status,
                    message,
                    _json(details),
                    _timestamp(updated_at),
                ),
            )
        result = self.get_health(component)
        assert isinstance(result, HealthRecord)
        return result

    def get_health(self, component: str | None = None) -> HealthRecord | dict[str, HealthRecord]:
        if component is not None:
            row = self._query_one("SELECT * FROM health WHERE component = ?", (component,))
            if row is None:
                raise RecordNotFoundError(f"health component not found: {component}")
            return _health_record(row)
        rows = self._query_all("SELECT * FROM health ORDER BY component")
        return {row["component"]: _health_record(row) for row in rows}

    def backup_to(self, destination: str | Path) -> Path:
        """Create a consistent online backup."""

        self._ensure_open()
        destination_path = Path(destination).expanduser().resolve()
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            target = sqlite3.connect(str(destination_path))
            try:
                self._connection.backup(target)
            finally:
                target.close()
        return destination_path

    def _migrate(self) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
        applied_rows = self._query_all("SELECT version FROM schema_migrations")
        applied = {int(row["version"]) for row in applied_rows}
        supported = max(version for version, _ in _MIGRATIONS)
        if applied and max(applied) > supported:
            raise DatabaseError(
                f"database schema {max(applied)} is newer than supported schema {supported}"
            )
        for version, statements in _MIGRATIONS:
            if version in applied:
                continue
            with self.transaction() as connection:
                for statement in statements:
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                    VALUES (?, ?)
                    """,
                    (version, _timestamp()),
                )

    def _verify_schema(self) -> None:
        try:
            row = self._query_one(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            )
        except sqlite3.DatabaseError as exc:
            raise DatabaseError("database has no recognized schema") from exc
        current = int(row["version"]) if row is not None else 0
        required = max(version for version, _ in _MIGRATIONS)
        if current != required:
            direction = "older" if current < required else "newer"
            raise DatabaseError(
                f"database schema {current} is {direction} than supported schema {required}"
            )

    def _query_one(self, query: str, parameters: Any = ()) -> sqlite3.Row | None:
        self._ensure_open()
        with self._lock:
            return self._connection.execute(query, parameters).fetchone()

    def _query_all(self, query: str, parameters: Any = ()) -> list[sqlite3.Row]:
        self._ensure_open()
        with self._lock:
            return list(self._connection.execute(query, parameters).fetchall())

    def _ensure_open(self) -> None:
        if self._closed:
            raise DatabaseError("database is closed")


def _run_record(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        id=row["id"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        status=row["status"],
        trigger=row["trigger"],
        dry_run=bool(row["dry_run"]),
        bookings_created=int(row["bookings_created"]),
        extensions_completed=int(row["extensions_completed"]),
        error_code=row["error_code"],
        error_message=row["error_message"],
        summary=_load_json(row["summary_json"]),
    )


def _observation_record(row: sqlite3.Row) -> ObservationRecord:
    return ObservationRecord(
        id=row["id"],
        observation_key=row["observation_key"],
        run_id=row["run_id"],
        observed_at=row["observed_at"],
        kind=row["kind"],
        status=row["status"],
        page_url=row["page_url"],
        requested_date=row["requested_date"],
        observed_date=row["observed_date"],
        contract_version=row["contract_version"],
        artifact_path=row["artifact_path"],
        payload=_load_json(row["payload_json"]),
    )


def _event_record(row: sqlite3.Row) -> EventRecord:
    return EventRecord(
        event_key=row["event_key"],
        external_event_id=row["external_event_id"],
        event_date=row["event_date"],
        room_id=row["room_id"],
        start_minute=int(row["start_minute"]),
        end_minute=int(row["end_minute"]),
        event_type=row["event_type"],
        status=row["status"],
        title=row["title"],
        is_cancelled=bool(row["is_cancelled"]),
        source_observation_id=row["source_observation_id"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        payload=_load_json(row["payload_json"]),
    )


def _intent_record(row: sqlite3.Row) -> IntentRecord:
    return IntentRecord(
        id=row["id"],
        intent_key=row["intent_key"],
        kind=row["kind"],
        requested_date=row["requested_date"],
        room_id=row["room_id"],
        start_minute=int(row["start_minute"]),
        end_minute=int(row["end_minute"]),
        target_end_minute=(
            int(row["target_end_minute"]) if row["target_end_minute"] is not None else None
        ),
        event_key=row["event_key"],
        run_id=row["run_id"],
        status=row["status"],
        attempt_count=int(row["attempt_count"]),
        last_error_code=row["last_error_code"],
        last_error_message=row["last_error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        verified_at=row["verified_at"],
        payload=_load_json(row["payload_json"]),
    )


def _extension_record(row: sqlite3.Row) -> ExtensionRecord:
    return ExtensionRecord(
        id=row["id"],
        intent_id=row["intent_id"],
        event_key=row["event_key"],
        current_end_minute=int(row["current_end_minute"]),
        target_end_minute=int(row["target_end_minute"]),
        status=row["status"],
        next_attempt_at=row["next_attempt_at"],
        attempt_count=int(row["attempt_count"]),
        last_error_code=row["last_error_code"],
        last_error_message=row["last_error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _health_record(row: sqlite3.Row) -> HealthRecord:
    return HealthRecord(
        component=row["component"],
        status=row["status"],
        message=row["message"],
        details=_load_json(row["details_json"]),
        updated_at=row["updated_at"],
    )


def _timestamp(value: datetime | str | None = None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("timestamp may not be empty")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid ISO timestamp: {value}") from exc
    else:
        raise TypeError("timestamp must be a datetime, ISO string, or None")
    if parsed.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _date_text(value: date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"invalid ISO date: {value}") from exc
    else:
        raise TypeError("date value must be a date, ISO string, or None")
    return parsed.isoformat()


def _json(value: Mapping[str, Any] | None) -> str:
    return json.dumps(
        dict(value or {}),
        default=_json_default,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (Path, date, datetime)):
        return value.isoformat() if not isinstance(value, Path) else str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _load_json(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DatabaseError("database contains invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise DatabaseError("database JSON payload must be an object")
    return parsed


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _status(value: str, allowed: frozenset[str], name: str) -> str:
    if value not in allowed:
        raise ValueError(
            f"invalid {name} status/value {value!r}; expected one of {', '.join(sorted(allowed))}"
        )
    return value


def _minutes(start_minute: int, end_minute: int) -> None:
    if (
        type(start_minute) is not int
        or type(end_minute) is not int
        or not 0 <= start_minute < end_minute <= 1440
    ):
        raise ValueError("minutes must satisfy 0 <= start < end <= 1440")


def _nonnegative_int(value: int, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _positive_limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 100_000:
        raise ValueError("limit must be an integer from 1 to 100000")
    return value
