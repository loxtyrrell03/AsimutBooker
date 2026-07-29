from datetime import datetime

from asimut_booker.cli import _derive_overall_status, _is_valid_scheduled_launch
from asimut_booker.config import ScheduleConfig
from asimut_booker.policies import LONDON


def test_status_never_calls_an_expired_live_session_healthy() -> None:
    status = _derive_overall_status(
        session_state_exists=True,
        database_exists=True,
        health={
            "doctor": {
                "status": "unhealthy",
                "message": "Saved session expired",
            }
        },
    )

    assert status == "unhealthy"


def test_status_requires_positive_live_validation() -> None:
    assert (
        _derive_overall_status(
            session_state_exists=True,
            database_exists=True,
            health={},
        )
        == "degraded"
    )
    assert (
        _derive_overall_status(
            session_state_exists=True,
            database_exists=True,
            health={"authentication": {"status": "healthy"}},
        )
        == "healthy"
    )


def test_newer_successful_authentication_supersedes_old_failed_doctor() -> None:
    status = _derive_overall_status(
        session_state_exists=True,
        database_exists=True,
        health={
            "doctor": {
                "status": "unhealthy",
                "updated_at": "2026-07-29T08:00:00Z",
            },
            "authentication": {
                "status": "healthy",
                "updated_at": "2026-07-29T09:00:00Z",
            },
            "last_run": {
                "status": "healthy",
                "updated_at": "2026-07-29T09:01:00Z",
            },
        },
    )

    assert status == "healthy"


def test_scheduled_launch_honours_lead_and_lateness_windows() -> None:
    schedule = ScheduleConfig(
        enabled=True,
        cadence_minutes=15,
        lead_seconds=120,
        max_lateness_seconds=180,
        active_start_minute=7 * 60 + 30,
        active_end_minute=22 * 60 + 15,
        timeout_minutes=12,
    )

    assert not _is_valid_scheduled_launch(
        schedule,
        datetime(2026, 7, 29, 7, 27, 59, tzinfo=LONDON),
    )
    assert _is_valid_scheduled_launch(
        schedule,
        datetime(2026, 7, 29, 7, 28, tzinfo=LONDON),
    )
    assert _is_valid_scheduled_launch(
        schedule,
        datetime(2026, 7, 29, 7, 33, tzinfo=LONDON),
    )
    assert not _is_valid_scheduled_launch(
        schedule,
        datetime(2026, 7, 29, 7, 33, 1, tzinfo=LONDON),
    )
    assert _is_valid_scheduled_launch(
        schedule,
        datetime(2026, 7, 29, 7, 43, tzinfo=LONDON),
    )
