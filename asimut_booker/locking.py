"""Cross-process single-worker lock.

The lock is advisory, non-blocking by default, and backed by the operating
system.  A crashed process releases the kernel lock automatically.
"""

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

if os.name == "nt":  # pragma: no cover - platform branch
    import msvcrt
else:  # pragma: no cover - exercised on non-Windows CI
    import fcntl


class LockError(RuntimeError):
    """Base lock failure."""


class AlreadyLockedError(LockError):
    """Another process or lock handle already owns the file lock."""

    def __init__(self, path: Path, holder: "LockMetadata | None" = None):
        self.path = path
        self.holder = holder
        description = f"another AsimutBooker worker holds {path}"
        if holder is not None:
            description += f" (pid={holder.pid}, host={holder.hostname})"
        super().__init__(description)


@dataclass(frozen=True, slots=True)
class LockMetadata:
    pid: int
    hostname: str
    acquired_at: str


def default_lock_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "asimut_booker.lock"


class FileLock:
    """An exclusive advisory lock on the first byte of a file."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = (Path(path) if path is not None else default_lock_path()).expanduser().resolve()
        self._handle: BinaryIO | None = None
        self._metadata: LockMetadata | None = None

    @property
    def acquired(self) -> bool:
        return self._handle is not None

    @property
    def metadata(self) -> LockMetadata | None:
        return self._metadata

    def acquire(
        self,
        *,
        blocking: bool = False,
        timeout: float | None = None,
        poll_interval: float = 0.05,
    ) -> "FileLock":
        """Acquire the lock.

        ``blocking=False`` is the safe scheduler default.  When blocking is
        enabled, an optional timeout bounds polling.
        """

        if self.acquired:
            raise LockError("this FileLock instance is already acquired")
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if not blocking and timeout is not None:
            raise ValueError("timeout is only meaningful when blocking=True")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.touch(exist_ok=True)
            handle = self.path.open("r+b", buffering=0)
        except OSError as exc:
            raise LockError(f"could not open lock file {self.path}: {exc}") from exc

        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()

            deadline = None if timeout is None else time.monotonic() + timeout
            while True:
                if _try_lock(handle):
                    break
                if not blocking:
                    raise AlreadyLockedError(self.path, self.read_metadata())
                if deadline is not None and time.monotonic() >= deadline:
                    raise AlreadyLockedError(self.path, self.read_metadata())
                remaining = (
                    poll_interval
                    if deadline is None
                    else min(poll_interval, max(0.0, deadline - time.monotonic()))
                )
                if remaining:
                    time.sleep(remaining)

            metadata = LockMetadata(
                pid=os.getpid(),
                hostname=socket.gethostname(),
                acquired_at=(
                    datetime.now(timezone.utc)
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z")
                ),
            )
            try:
                _write_metadata(handle, metadata)
            except BaseException:
                _unlock(handle)
                raise
            self._handle = handle
            self._metadata = metadata
            return self
        except BaseException:
            handle.close()
            raise

    def try_acquire(self) -> bool:
        try:
            self.acquire(blocking=False)
        except AlreadyLockedError:
            return False
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        handle = self._handle
        self._handle = None
        self._metadata = None
        try:
            try:
                handle.seek(1)
                handle.truncate()
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                _unlock(handle)
        finally:
            handle.close()

    def read_metadata(self) -> LockMetadata | None:
        """Read best-effort owner diagnostics without claiming the lock."""

        try:
            with self.path.open("rb") as handle:
                handle.seek(1)
                raw = handle.read()
            if not raw:
                return None
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                return None
            return LockMetadata(
                pid=int(value["pid"]),
                hostname=str(value["hostname"]),
                acquired_at=str(value["acquired_at"]),
            )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None

    def __enter__(self) -> "FileLock":
        return self.acquire(blocking=False)

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()

    def __del__(self) -> None:  # pragma: no cover - best-effort safety net
        try:
            self.release()
        except Exception:
            pass


def _try_lock(handle: BinaryIO) -> bool:
    handle.seek(0)
    try:
        if os.name == "nt":  # pragma: no branch - selected at import
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover - exercised on non-Windows CI
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        return False
    return True


def _unlock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":  # pragma: no branch - selected at import
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:  # pragma: no cover - exercised on non-Windows CI
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_metadata(handle: BinaryIO, metadata: LockMetadata) -> None:
    payload = json.dumps(
        {
            "pid": metadata.pid,
            "hostname": metadata.hostname,
            "acquired_at": metadata.acquired_at,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    handle.seek(1)
    handle.truncate()
    handle.write(payload)
    handle.flush()
    os.fsync(handle.fileno())
