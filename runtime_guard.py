"""Small runtime safety primitives for autonomous AsimutBooker runs.

This module deliberately has no dependency on Playwright or the booking entry
point.  It can therefore be exercised without opening a browser or touching a
real Asimut account.
"""

from __future__ import annotations

import errno
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from types import TracebackType
from collections.abc import Mapping
from typing import IO, TextIO
from urllib.parse import parse_qsl, urlsplit


_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_HHMM_RE = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
_CONFIRMED_EVENT_QUERY_RE = re.compile(r"eventId=([1-9][0-9]*)")
_PREFILLED_EVENT_START_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]\."
    r"[0-9]{3}[+-](?:0[0-9]|1[0-4]):[0-5][0-9]"
)
_ASIMUT_HOST = "rwcmd.asimut.net"
_LOCK_CONTENTION_ERRNOS = {
    errno.EACCES,
    errno.EAGAIN,
    getattr(errno, "EDEADLK", errno.EACCES),
}


def _configure_utf8_stream(stream: TextIO | None) -> bool:
    """Reconfigure one Python text stream without replacing or closing it."""
    if stream is None:
        return False

    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        # StringIO and test-capture streams already accept Unicode text and do
        # not expose a byte encoding to change.
        return False

    try:
        stream.flush()
        reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, OSError, TypeError, ValueError):
        # Closed, detached, or custom streams must not prevent the application
        # from starting.  Callers can inspect the returned status if desired.
        return False
    return True


def configure_utf8_stdio(
    *, stdout: TextIO | None = None, stderr: TextIO | None = None
) -> tuple[bool, bool]:
    """Make stdout and stderr UTF-8 safe, including redirected Windows output.

    ``stdout`` and ``stderr`` are injectable for isolated tests.  When omitted,
    the current ``sys.stdout`` and ``sys.stderr`` objects are used.  Streams
    such as ``io.StringIO`` that do not need byte-level configuration are left
    intact.

    Returns:
        A pair indicating whether stdout and stderr were reconfigured.
    """
    stdout_stream = sys.stdout if stdout is None else stdout
    stderr_stream = sys.stderr if stderr is None else stderr
    return (
        _configure_utf8_stream(stdout_stream),
        _configure_utf8_stream(stderr_stream),
    )


class SingleInstanceAlreadyRunning(RuntimeError):
    """Raised by the context-manager API when another process owns the lock."""


class SingleInstanceLock:
    """A nonblocking, process-scoped advisory lock backed by the operating system.

    The lock file remains on disk after release.  Ownership is represented by
    the OS file lock, not file existence, so crashes automatically release it
    without stale lock-file recovery heuristics.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._handle: IO[bytes] | None = None
        self._backend: str | None = None

    @property
    def acquired(self) -> bool:
        """Whether this object currently owns the lock."""
        return self._handle is not None

    def acquire(self) -> bool:
        """Try once to acquire the lock, returning immediately on contention."""
        if self.acquired:
            return True

        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b", buffering=0)

        try:
            if os.name == "nt":
                import msvcrt

                # msvcrt.locking locks bytes from the current file position.
                # Ensure byte zero exists and every participant locks it.
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                backend = "windows"
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                backend = "posix"
        except OSError as exc:
            handle.close()
            if isinstance(exc, BlockingIOError) or exc.errno in _LOCK_CONTENTION_ERRNOS:
                return False
            raise
        except Exception:
            handle.close()
            raise

        self._handle = handle
        self._backend = backend
        return True

    def release(self) -> None:
        """Release the lock. Calling this on an unlocked object is harmless."""
        handle = self._handle
        backend = self._backend
        if handle is None:
            return

        self._handle = None
        self._backend = None
        try:
            if backend == "windows":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            elif backend == "posix":
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> "SingleInstanceLock":
        if not self.acquire():
            raise SingleInstanceAlreadyRunning(
                f"Another process owns the runtime lock: {self.path}"
            )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            # Interpreter shutdown and already-closed handles are best effort.
            pass


def normalize_date(value: date | datetime | str) -> date:
    """Return a calendar date from a date, datetime, or strict ISO date string."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        if _ISO_DATE_RE.fullmatch(value) is None:
            raise ValueError(f"Expected an ISO date in YYYY-MM-DD form, got {value!r}")
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"Invalid calendar date: {value!r}") from exc
    raise TypeError(f"Expected date, datetime, or ISO date string, got {type(value).__name__}")


def _is_trusted_asimut_location(url: str):
    """Return parsed URL parts only for relative or canonical RWCMD Asimut URLs."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None

    if not parsed.scheme and not parsed.netloc:
        return parsed
    if parsed.scheme != "https" or parsed.hostname != _ASIMUT_HOST:
        return None
    try:
        if parsed.port is not None:
            return None
    except ValueError:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return parsed


def parse_confirmed_event_id(url: str) -> int | None:
    """Extract a confirmed positive event id from an exact post-save URL.

    Accepted URLs have exactly the path and query
    ``/arrangement?eventId=<positive integer>``.  The pre-save form URL,
    unrelated paths, event id zero, duplicate/extra parameters, and fragments
    are all rejected.
    """
    if not isinstance(url, str) or not url or url != url.strip():
        return None

    parsed = _is_trusted_asimut_location(url)
    if parsed is None:
        return None
    if parsed.path != "/arrangement" or parsed.fragment:
        return None

    match = _CONFIRMED_EVENT_QUERY_RE.fullmatch(parsed.query)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def is_confirmed_post_save_url(url: str) -> bool:
    """Return whether ``url`` proves creation of a positive Asimut event id."""
    return parse_confirmed_event_id(url) is not None


def is_new_booking_form_url(url: str) -> bool:
    """Return whether ``url`` is a canonical unsaved Asimut booking form.

    Asimut's current overview adds a complete prefill tuple to the legacy
    ``eventId=0`` route. Only that exact known tuple is accepted; duplicate,
    partial, malformed, or additional parameters still fail closed.
    """

    if not isinstance(url, str) or not url or url != url.strip():
        return False
    parsed = _is_trusted_asimut_location(url)
    if parsed is None:
        return False
    if parsed.path != "/event" or parsed.fragment:
        return False
    if parsed.query == "eventId=0":
        return True
    try:
        pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
    except ValueError:
        return False
    keys = [key for key, _value in pairs]
    expected_keys = {
        "eventId",
        "prefillTime",
        "start",
        "categoryId",
        "locationId",
    }
    if len(keys) != len(expected_keys) or set(keys) != expected_keys:
        return False
    values = dict(pairs)
    if values["eventId"] != "0" or values["prefillTime"] != "true":
        return False
    if re.fullmatch(r"[1-9][0-9]*", values["categoryId"]) is None:
        return False
    if re.fullmatch(r"[1-9][0-9]*", values["locationId"]) is None:
        return False
    start = values["start"]
    if _PREFILLED_EVENT_START_RE.fullmatch(start) is None:
        return False
    try:
        parsed_start = datetime.fromisoformat(start)
    except ValueError:
        return False
    return parsed_start.tzinfo is not None and parsed_start.utcoffset() is not None


def hhmm_values_match(requested: str, actual: str) -> bool:
    """Compare two canonical 24-hour ``HH:MM`` values without normalization."""
    if not isinstance(requested, str) or not isinstance(actual, str):
        return False
    if _HHMM_RE.fullmatch(requested) is None or _HHMM_RE.fullmatch(actual) is None:
        return False
    return requested == actual


def booking_times_match(
    requested_start: str,
    requested_end: str,
    actual_start: str,
    actual_end: str,
) -> bool:
    """Verify both start and end input values exactly before a booking save."""
    return hhmm_values_match(requested_start, actual_start) and hhmm_values_match(
        requested_end, actual_end
    )


def booking_identity_matches(
    snapshot: Mapping[str, object],
    room: str,
    booking_date: date | datetime | str,
) -> bool:
    """Match exact structured room/date fields from one booking form or event.

    Free-form page text is deliberately not accepted.  A room, date, or time
    mentioned elsewhere in the page must never be able to confirm the active
    booking form or persisted event.
    """

    if not isinstance(snapshot, Mapping) or not isinstance(room, str) or not room:
        return False
    actual_room = snapshot.get("room")
    actual_date = snapshot.get("date")
    if (
        not isinstance(actual_room, str)
        or not actual_room
        or actual_room != actual_room.strip()
        or actual_room.casefold() != room.casefold()
    ):
        return False
    try:
        target_date = normalize_date(booking_date)
        persisted_date = normalize_date(actual_date)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return persisted_date == target_date


def booking_summary_matches(
    snapshot: Mapping[str, object],
    room: str,
    booking_date: date | datetime | str,
    start_time: str,
    end_time: str,
) -> bool:
    """Match all structured fields from one persisted reservation record."""

    if not all(isinstance(value, str) and value for value in (room, start_time, end_time)):
        return False
    if _HHMM_RE.fullmatch(start_time) is None or _HHMM_RE.fullmatch(end_time) is None:
        return False
    if not booking_identity_matches(snapshot, room, booking_date):
        return False
    actual_start = snapshot.get("start")
    actual_end = snapshot.get("end")
    return (
        isinstance(actual_start, str)
        and isinstance(actual_end, str)
        and booking_times_match(start_time, end_time, actual_start, actual_end)
    )


__all__ = [
    "SingleInstanceAlreadyRunning",
    "SingleInstanceLock",
    "booking_identity_matches",
    "booking_summary_matches",
    "booking_times_match",
    "configure_utf8_stdio",
    "hhmm_values_match",
    "is_confirmed_post_save_url",
    "is_new_booking_form_url",
    "normalize_date",
    "parse_confirmed_event_id",
]
