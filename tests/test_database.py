from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Barrier, Thread

import pytest

from asimut_booker.database import (
    Database,
    DatabaseError,
    IdempotencyConflictError,
    StateTransitionError,
)

NOW = datetime(2026, 7, 29, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def database(tmp_path: Path):
    with Database(tmp_path / "booker.sqlite3") as repository:
        yield repository


def test_migrates_to_wal_and_creates_expected_tables(
    database: Database,
) -> None:
    assert database.schema_version == 1

    connection = sqlite3.connect(database.path)
    try:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        connection.close()

    assert journal_mode.lower() == "wal"
    assert {
        "schema_migrations",
        "runs",
        "observations",
        "events",
        "intents",
        "extensions",
        "health",
    }.issubset(tables)


def test_run_lifecycle_is_typed_idempotent_and_listable(
    database: Database,
) -> None:
    run = database.start_run(
        run_id="run-1",
        trigger="manual",
        dry_run=True,
        started_at=NOW,
    )
    duplicate = database.start_run(
        run_id="run-1",
        trigger="manual",
        dry_run=True,
        started_at=NOW,
    )

    assert duplicate == run
    assert run.status == "running"
    completed = database.finish_run(
        run.id,
        status="succeeded",
        bookings_created=1,
        extensions_completed=2,
        summary={"verified": True},
        finished_at=NOW,
    )
    assert completed.status == "succeeded"
    assert completed.bookings_created == 1
    assert completed.summary == {"verified": True}
    assert database.list_runs(limit=1) == [completed]

    with pytest.raises(IdempotencyConflictError):
        database.start_run(run_id="run-1", trigger="scheduler", dry_run=True)

    with pytest.raises(StateTransitionError, match="terminal state"):
        database.finish_run(run.id, status="failed")


def test_observation_and_event_upserts_preserve_first_seen(
    database: Database,
) -> None:
    run = database.start_run(trigger="test", started_at=NOW)
    observation = database.record_observation(
        observation_key="overview-2026-07-30",
        run_id=run.id,
        kind="overview",
        status="valid",
        observed_at=NOW,
        requested_date=date(2026, 7, 30),
        observed_date=date(2026, 7, 30),
        contract_version="overview-v2",
        payload={"room_count": 23},
    )
    duplicate = database.record_observation(
        observation_key="overview-2026-07-30",
        run_id=run.id,
        kind="overview",
        status="valid",
        observed_at=NOW,
        requested_date=date(2026, 7, 30),
        observed_date=date(2026, 7, 30),
        contract_version="overview-v2",
        payload={"room_count": 23},
    )
    assert duplicate == observation

    first = database.upsert_event(
        event_key="reservation-123",
        external_event_id="123",
        event_date="2026-07-30",
        room_id="B0.29",
        start_minute=600,
        end_minute=660,
        event_type="reservation",
        source_observation_id=observation.id,
        observed_at=NOW,
        payload={"source": "agenda"},
    )
    second = database.upsert_event(
        event_key="reservation-123",
        external_event_id="123",
        event_date="2026-07-30",
        room_id="B0.29",
        start_minute=600,
        end_minute=675,
        event_type="reservation",
        source_observation_id=observation.id,
        observed_at="2026-07-29T20:15:00Z",
        payload={"source": "reconciled"},
    )

    assert first.first_seen_at == second.first_seen_at
    assert second.end_minute == 675
    assert second.payload["source"] == "reconciled"
    assert database.list_events(start_date="2026-07-30")[0] == second


def test_intent_natural_key_is_idempotent_and_conflicts_fail_closed(
    database: Database,
) -> None:
    intent = database.create_intent(
        kind="booking",
        requested_date="2026-08-01",
        room_id="B0.29",
        start_minute=600,
        end_minute=630,
        target_end_minute=720,
        payload={"horizon_days": 5},
        created_at=NOW,
    )
    duplicate = database.create_intent(
        kind="booking",
        requested_date="2026-08-01",
        room_id="B0.29",
        start_minute=600,
        end_minute=630,
        target_end_minute=720,
        payload={"horizon_days": 5},
        created_at=NOW,
    )
    assert duplicate == intent

    with pytest.raises(IdempotencyConflictError):
        database.create_intent(
            intent_key="a-different-caller-key",
            kind="booking",
            requested_date="2026-08-01",
            room_id="B0.29",
            start_minute=600,
            end_minute=660,
            target_end_minute=720,
            created_at=NOW,
        )


def test_intent_status_uses_compare_and_swap(database: Database) -> None:
    intent = database.create_intent(
        kind="booking",
        requested_date="2026-08-01",
        room_id="B1.09",
        start_minute=540,
        end_minute=570,
        created_at=NOW,
    )

    submitting = database.update_intent_status(
        intent.id,
        "submitting",
        expected_status="planned",
        increment_attempt=True,
    )
    assert submitting.attempt_count == 1

    with pytest.raises(StateTransitionError):
        database.update_intent_status(
            intent.id,
            "verified",
            expected_status="planned",
        )

    verified = database.update_intent_status(
        intent.id,
        "verified",
        expected_status="submitting",
        event_key="reservation-999",
        verified_at=NOW,
    )
    assert verified.status == "verified"
    assert verified.event_key == "reservation-999"
    assert verified.verified_at == "2026-07-29T20:00:00.000000Z"


def test_extension_lifecycle_and_due_query(database: Database) -> None:
    intent = database.create_intent(
        kind="extension",
        requested_date="2026-08-01",
        room_id="B0.29",
        start_minute=600,
        end_minute=630,
        target_end_minute=720,
        created_at=NOW,
    )
    extension = database.upsert_extension(
        intent_id=intent.id,
        event_key="reservation-123",
        current_end_minute=630,
        target_end_minute=720,
        next_attempt_at="2026-07-29T20:05:00Z",
        updated_at=NOW,
    )
    assert database.get_extension_for_intent(intent.id) == extension
    assert database.list_extensions(status="pending", due_before="2026-07-29T20:06:00Z") == [
        extension
    ]

    retried = database.update_extension(
        extension.id,
        status="retryable",
        expected_status="pending",
        current_end_minute=645,
        increment_attempt=True,
        error_code="taken",
    )
    assert retried.current_end_minute == 645
    assert retried.attempt_count == 1
    assert retried.last_error_code == "taken"

    with pytest.raises(ValueError, match="may not regress"):
        database.update_extension(
            extension.id,
            status="retryable",
            current_end_minute=630,
        )
    with pytest.raises(StateTransitionError, match="move backwards"):
        database.upsert_extension(
            intent_id=intent.id,
            current_end_minute=630,
            target_end_minute=720,
        )


def test_health_supports_component_and_gui_mapping_reads(
    database: Database,
) -> None:
    parser = database.set_health(
        "overview_parser",
        "healthy",
        message="contract valid",
        details={"version": "v2"},
        updated_at=NOW,
    )
    auth = database.set_health("authentication", "degraded", updated_at=NOW)

    assert database.get_health("overview_parser") == parser
    assert database.get_health() == {
        "authentication": auth,
        "overview_parser": parser,
    }


def test_nested_transaction_rolls_back_only_failed_savepoint(
    database: Database,
) -> None:
    with database.transaction():
        database.set_health("outer-before", "healthy", updated_at=NOW)
        with pytest.raises(RuntimeError):
            with database.transaction():
                database.set_health("inner", "healthy", updated_at=NOW)
                raise RuntimeError("rollback savepoint")
        database.set_health("outer-after", "healthy", updated_at=NOW)

    health = database.get_health()
    assert "outer-before" in health
    assert "outer-after" in health
    assert "inner" not in health


def test_concurrent_idempotent_intent_creation_returns_one_row(
    database: Database,
) -> None:
    barrier = Barrier(4)
    identifiers: list[str] = []
    errors: list[BaseException] = []

    def create() -> None:
        try:
            barrier.wait()
            record = database.create_intent(
                kind="booking",
                requested_date="2026-08-02",
                room_id="B0.29",
                start_minute=720,
                end_minute=750,
                created_at=NOW,
            )
            identifiers.append(record.id)
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [Thread(target=create) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(set(identifiers)) == 1
    assert len(database.list_intents()) == 1


def test_backup_and_read_only_access(tmp_path: Path) -> None:
    source_path = tmp_path / "source.sqlite3"
    backup_path = tmp_path / "backup.sqlite3"
    with Database(source_path) as writable:
        writable.set_health("worker", "healthy", updated_at=NOW)
        writable.backup_to(backup_path)

    with Database(backup_path, read_only=True) as read_only:
        assert read_only.get_health("worker").status == "healthy"
        with pytest.raises(DatabaseError, match="read-only"):
            read_only.set_health("worker", "degraded")


def test_newer_unknown_schema_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "future.sqlite3"
    with Database(path):
        pass
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            INSERT INTO schema_migrations(version, applied_at)
            VALUES (999, '2026-07-29T20:00:00.000000Z')
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DatabaseError, match="newer than supported"):
        Database(path)
