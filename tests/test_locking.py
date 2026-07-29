from __future__ import annotations

import multiprocessing
import os
import time
from pathlib import Path

import pytest

from asimut_booker.locking import (
    AlreadyLockedError,
    FileLock,
    LockError,
)


def _hold_lock(
    path: str,
    ready: multiprocessing.Queue,
    release: multiprocessing.Event,
) -> None:
    with FileLock(path) as lock:
        assert lock.metadata is not None
        ready.put(lock.metadata.pid)
        release.wait(10)


def _acquire_then_exit(path: str, ready: multiprocessing.Event) -> None:
    lock = FileLock(path).acquire()
    assert lock.metadata is not None
    ready.set()
    os._exit(0)


def test_context_manager_acquires_metadata_and_releases(tmp_path: Path) -> None:
    path = tmp_path / "worker.lock"
    lock = FileLock(path)

    with lock:
        assert lock.acquired
        assert lock.metadata is not None
        assert lock.metadata.pid == os.getpid()
        assert lock.read_metadata() == lock.metadata

    assert not lock.acquired
    assert lock.metadata is None
    assert lock.read_metadata() is None


def test_second_handle_fails_nonblocking_and_reports_holder(
    tmp_path: Path,
) -> None:
    path = tmp_path / "worker.lock"
    first = FileLock(path).acquire()
    second = FileLock(path)
    try:
        with pytest.raises(AlreadyLockedError) as caught:
            second.acquire()

        assert caught.value.path == path.resolve()
        assert caught.value.holder == first.metadata
        assert not second.acquired
    finally:
        first.release()


def test_try_acquire_returns_false_without_leaking_handle(tmp_path: Path) -> None:
    path = tmp_path / "worker.lock"
    with FileLock(path):
        contender = FileLock(path)
        assert contender.try_acquire() is False
        assert not contender.acquired

    assert contender.try_acquire() is True
    contender.release()


def test_reacquiring_same_instance_is_rejected(tmp_path: Path) -> None:
    lock = FileLock(tmp_path / "worker.lock").acquire()
    try:
        with pytest.raises(LockError, match="already acquired"):
            lock.acquire()
    finally:
        lock.release()


def test_blocking_timeout_is_bounded(tmp_path: Path) -> None:
    path = tmp_path / "worker.lock"
    with FileLock(path):
        contender = FileLock(path)
        started = time.monotonic()
        with pytest.raises(AlreadyLockedError):
            contender.acquire(
                blocking=True,
                timeout=0.1,
                poll_interval=0.01,
            )
        elapsed = time.monotonic() - started

    assert 0.08 <= elapsed < 1.0


def test_lock_excludes_another_process_and_recovers_after_release(
    tmp_path: Path,
) -> None:
    path = tmp_path / "worker.lock"
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    release = context.Event()
    process = context.Process(
        target=_hold_lock,
        args=(str(path), ready, release),
    )
    process.start()
    try:
        holder_pid = ready.get(timeout=10)
        contender = FileLock(path)
        with pytest.raises(AlreadyLockedError) as caught:
            contender.acquire()
        assert caught.value.holder is not None
        assert caught.value.holder.pid == holder_pid
    finally:
        release.set()
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    assert process.exitcode == 0
    with FileLock(path) as acquired:
        assert acquired.acquired


def test_kernel_releases_lock_when_owner_process_crashes(tmp_path: Path) -> None:
    path = tmp_path / "worker.lock"
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(
        target=_acquire_then_exit,
        args=(str(path), ready),
    )
    process.start()
    assert ready.wait(timeout=10)
    process.join(timeout=10)
    assert not process.is_alive()

    # Metadata can be stale after a hard exit, but the operating-system lock
    # must be gone so the next scheduled run can recover autonomously.
    with FileLock(path) as acquired:
        assert acquired.acquired


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"blocking": False, "timeout": 1}, "blocking=True"),
        ({"blocking": True, "timeout": -1}, "non-negative"),
        ({"blocking": True, "poll_interval": 0}, "positive"),
    ],
)
def test_invalid_acquire_options_are_rejected(
    tmp_path: Path,
    kwargs: dict[str, object],
    message: str,
) -> None:
    lock = FileLock(tmp_path / "worker.lock")

    with pytest.raises(ValueError, match=message):
        lock.acquire(**kwargs)
