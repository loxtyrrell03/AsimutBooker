"""Safe shared settings persistence for the GUI and autonomous booker."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar


APP_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = APP_DIR / "data" / "settings.json"


class SettingsError(RuntimeError):
    """Raised when settings cannot be trusted or safely persisted."""


T = TypeVar("T")


class InterProcessFileLock:
    """Small cross-process lock backed by a one-byte lock file."""

    def __init__(self, path: Path, timeout: float = 10.0):
        self.path = Path(path)
        self.timeout = timeout
        self._file = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, "a+b")
        if self._file.seek(0, os.SEEK_END) == 0:
            self._file.write(b"\0")
            self._file.flush()

        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    self.release()
                    raise SettingsError(f"Timed out waiting for settings lock: {self.path}")
                time.sleep(0.05)

    def release(self) -> None:
        if self._file is None:
            return
        try:
            self._file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self._file.close()
            self._file = None

    def __enter__(self) -> "InterProcessFileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()


def _validate_document(value: Any, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SettingsError(f"Settings must contain a JSON object: {path}")
    return value


def load_settings(path: Path = SETTINGS_FILE, *, missing_ok: bool = True) -> dict[str, Any]:
    """Load settings strictly; malformed existing JSON never falls back to defaults."""

    path = Path(path)
    if not path.exists():
        if missing_ok:
            return {}
        raise SettingsError(f"Settings file does not exist: {path}")

    try:
        with open(path, "r", encoding="utf-8") as handle:
            return _validate_document(json.load(handle), path)
    except SettingsError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        backup_note = f" A backup is available at {path}.bak." if path.with_suffix(path.suffix + ".bak").exists() else ""
        raise SettingsError(f"Settings are unreadable; autonomous booking stopped for safety: {exc}.{backup_note}") from exc


def atomic_write_json(path: Path, value: Any, *, backup: bool = False) -> None:
    """Write JSON with fsync + atomic replacement, optionally preserving a backup."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(value, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        if backup and path.exists():
            backup_path = path.with_suffix(path.suffix + ".bak")
            backup_temp = backup_path.with_suffix(backup_path.suffix + ".tmp")
            shutil.copy2(path, backup_temp)
            os.replace(backup_temp, backup_path)

        os.replace(temp_path, path)
        temp_path = None
    except (OSError, TypeError, ValueError) as exc:
        raise SettingsError(f"Could not safely write JSON file {path}: {exc}") from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def save_settings(settings: dict[str, Any], path: Path = SETTINGS_FILE) -> None:
    """Atomically replace the settings document while holding the shared lock."""

    path = Path(path)
    _validate_document(settings, path)
    with InterProcessFileLock(path.with_suffix(path.suffix + ".lock")):
        atomic_write_json(path, settings, backup=True)


def update_settings(
    mutator: Callable[[dict[str, Any]], T],
    path: Path = SETTINGS_FILE,
) -> T:
    """Apply a read-modify-write update without losing unrelated concurrent keys."""

    path = Path(path)
    with InterProcessFileLock(path.with_suffix(path.suffix + ".lock")):
        settings = load_settings(path)
        result = mutator(settings)
        atomic_write_json(path, settings, backup=True)
        return result


@contextmanager
def settings_transaction(path: Path = SETTINGS_FILE) -> Iterator[dict[str, Any]]:
    """Yield a locked mutable document and persist it only on successful exit."""

    path = Path(path)
    with InterProcessFileLock(path.with_suffix(path.suffix + ".lock")):
        settings = load_settings(path)
        yield settings
        atomic_write_json(path, settings, backup=True)
