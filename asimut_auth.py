"""Deterministic RWCMD Microsoft sign-in recovery for AsimutBooker.

The normal path is always the persisted Playwright storage state.  This module
is used only after that state redirects back to Microsoft.  It reads the
password from Windows Credential Manager and consumes a single SMS code from
the existing local RWCMD bridge; neither value is written to files or logs.
"""

from __future__ import annotations

import ctypes
import getpass
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from urllib.parse import urlsplit

from app_settings import InterProcessFileLock, SettingsError, atomic_write_json


ASIMUT_BASE_URL = "https://rwcmd.asimut.net"
CREDENTIAL_TARGET = "AsimutBooker/RWCMD-Microsoft365"
SMS_BRIDGE_TASK = "RWCMD SMS Bridge"
SMS_BRIDGE_BASE_URL = "http://127.0.0.1:48733"
MICROSOFT_LOGIN_HOSTS = frozenset({"login.microsoftonline.com"})
MICROSOFT_POST_SUBMIT_GRACE_SECONDS = 15
AUTH_RECOVERY_STATE_FILE = (
    Path(__file__).resolve().parent / "data" / "auth_recovery_state.json"
)
AUTH_TRANSIENT_BACKOFF_SECONDS = 30 * 60

_ERROR_NOT_FOUND = 1168
_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_CODE_PATTERN = re.compile(r"^\d{6}$")


class AutonomousLoginError(RuntimeError):
    """Raised when deterministic sign-in cannot complete safely."""


class _AuthCircuitOpenError(AutonomousLoginError):
    """Raised when a prior autonomous failure intentionally blocks this run."""


@dataclass(frozen=True)
class StoredCredential:
    username: str = field(repr=False)
    password: str = field(repr=False)


@dataclass(frozen=True)
class AuthenticationResult:
    recovered: bool
    used_password: bool
    used_sms: bool


class AuthRecoveryCircuitBreaker:
    """Persist a small, no-secret auth retry policy across scheduled runs."""

    _VERSION = 1
    _READY = "ready"
    _BLOCKED = "blocked"
    _CREDENTIAL = "credential"
    _TRANSIENT = "transient"

    def __init__(
        self,
        path: Path = AUTH_RECOVERY_STATE_FILE,
        *,
        transient_backoff_seconds: int = AUTH_TRANSIENT_BACKOFF_SECONDS,
        clock=time.time,
    ) -> None:
        self.path = Path(path)
        self.transient_backoff_seconds = int(transient_backoff_seconds)
        if self.transient_backoff_seconds <= 0:
            raise ValueError("transient_backoff_seconds must be positive")
        self.clock = clock

    @property
    def _lock_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".lock")

    @classmethod
    def _ready_document(cls) -> dict:
        return {"version": cls._VERSION, "status": cls._READY}

    def _load_locked(self) -> dict:
        if not self.path.exists():
            return self._ready_document()
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise AutonomousLoginError(
                "The autonomous-login retry state is unreadable; run an explicit "
                "login repair"
            ) from None

        if not isinstance(document, dict) or document.get("version") != self._VERSION:
            raise AutonomousLoginError(
                "The autonomous-login retry state is invalid; run an explicit login repair"
            ) from None
        status = document.get("status")
        if status == self._READY and set(document) == {"version", "status"}:
            return document
        if status != self._BLOCKED or document.get("category") not in (
            self._CREDENTIAL,
            self._TRANSIENT,
        ):
            raise AutonomousLoginError(
                "The autonomous-login retry state is invalid; run an explicit login repair"
            ) from None
        if document["category"] == self._CREDENTIAL:
            if set(document) != {"version", "status", "category"}:
                raise AutonomousLoginError(
                    "The autonomous-login retry state is invalid; run an explicit login repair"
                ) from None
            return document
        retry_after = document.get("retry_after")
        if (
            set(document) != {"version", "status", "category", "retry_after"}
            or isinstance(retry_after, bool)
            or not isinstance(retry_after, (int, float))
            or not math.isfinite(retry_after)
        ):
            raise AutonomousLoginError(
                "The autonomous-login retry state is invalid; run an explicit login repair"
            ) from None
        return document

    def _write_locked(self, document: dict) -> None:
        try:
            atomic_write_json(self.path, document)
        except SettingsError:
            raise AutonomousLoginError(
                "The autonomous-login retry state could not be saved safely"
            ) from None

    def ensure_retry_allowed(self, *, explicit_retry: bool = False) -> None:
        """Fail closed unless the block expired or this is an explicit repair."""

        if explicit_retry:
            return
        try:
            with InterProcessFileLock(self._lock_path):
                document = self._load_locked()
                if document["status"] == self._READY:
                    return
                if document["category"] == self._CREDENTIAL:
                    raise _AuthCircuitOpenError(
                        "Autonomous login is paused after the stored login was rejected; "
                        "update the secure login or run an explicit login repair"
                    )
                if float(document["retry_after"]) > self.clock():
                    raise _AuthCircuitOpenError(
                        "Autonomous login is temporarily paused after a verification or "
                        "bridge failure; use Check / Repair Login to retry now"
                    )
                self._write_locked(self._ready_document())
        except _AuthCircuitOpenError:
            raise
        except AutonomousLoginError:
            raise
        except (OSError, SettingsError):
            raise AutonomousLoginError(
                "The autonomous-login retry state could not be checked safely"
            ) from None

    def record_credential_rejection(self) -> None:
        document = {
            "version": self._VERSION,
            "status": self._BLOCKED,
            "category": self._CREDENTIAL,
        }
        try:
            with InterProcessFileLock(self._lock_path):
                self._write_locked(document)
        except AutonomousLoginError:
            raise
        except (OSError, SettingsError):
            raise AutonomousLoginError(
                "The autonomous-login retry state could not be saved safely"
            ) from None

    def record_transient_failure(self) -> None:
        """Apply a bounded cooldown without weakening a credential rejection latch."""

        try:
            with InterProcessFileLock(self._lock_path):
                current = self._load_locked()
                if (
                    current.get("status") == self._BLOCKED
                    and current.get("category") == self._CREDENTIAL
                ):
                    return
                self._write_locked(
                    {
                        "version": self._VERSION,
                        "status": self._BLOCKED,
                        "category": self._TRANSIENT,
                        "retry_after": self.clock() + self.transient_backoff_seconds,
                    }
                )
        except AutonomousLoginError:
            raise
        except (OSError, SettingsError):
            raise AutonomousLoginError(
                "The autonomous-login retry state could not be saved safely"
            ) from None

    def clear(self) -> None:
        try:
            with InterProcessFileLock(self._lock_path):
                if not self.path.exists():
                    return
                self._write_locked(self._ready_document())
        except AutonomousLoginError:
            raise
        except (OSError, SettingsError):
            raise AutonomousLoginError(
                "The autonomous-login retry state could not be cleared safely"
            ) from None


DEFAULT_AUTH_CIRCUIT_BREAKER = AuthRecoveryCircuitBreaker()


def clear_auth_recovery_block() -> None:
    """Clear a prior no-secret auth block after an explicit successful repair."""

    DEFAULT_AUTH_CIRCUIT_BREAKER.clear()


@dataclass(frozen=True)
class _NormalizedAuthStage:
    origin: str
    path: str
    control: str

    def summary(self) -> str:
        return f"origin={self.origin} path={self.path} control={self.control}"


class _AuthTelemetry:
    """Emit redacted auth state transitions without page or secret content."""

    def __init__(self, started_at: float, deadline: float, writer=None) -> None:
        self.started_at = started_at
        self.deadline = deadline
        self.writer = writer or self._write_flushed
        self.last_stage: _NormalizedAuthStage | None = None

    @staticmethod
    def _write_flushed(message: str) -> None:
        print(message, flush=True)

    def report(self, page, control: str) -> _NormalizedAuthStage:
        origin, path = _normalized_auth_location(page)
        stage = _NormalizedAuthStage(origin, path, control)
        if stage != self.last_stage:
            now = time.monotonic()
            elapsed = max(0, int(now - self.started_at))
            remaining = max(0, int(self.deadline - now))
            message = (
                f"AUTH stage {stage.summary()} elapsed={elapsed}s "
                f"remaining={remaining}s"
            )
            try:
                self.writer(message)
            except Exception:
                # Observability must never become an authentication dependency.
                pass
            self.last_stage = stage
        return stage


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _credential_api():
    if os.name != "nt":
        raise AutonomousLoginError("Windows Credential Manager is required")
    api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    api.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
    ]
    api.CredReadW.restype = wintypes.BOOL
    api.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    return api


def read_windows_credential(target: str = CREDENTIAL_TARGET) -> StoredCredential | None:
    """Read the per-user generic credential without printing either field."""

    api = _credential_api()
    credential_pointer = ctypes.POINTER(_CREDENTIALW)()
    if not api.CredReadW(
        target,
        _CRED_TYPE_GENERIC,
        0,
        ctypes.byref(credential_pointer),
    ):
        error = ctypes.get_last_error()
        if error == _ERROR_NOT_FOUND:
            return None
        raise AutonomousLoginError(
            f"Windows Credential Manager could not read the login (error {error})"
        )

    try:
        credential = credential_pointer.contents
        blob = ctypes.string_at(
            credential.CredentialBlob,
            credential.CredentialBlobSize,
        )
        try:
            password = blob.decode("utf-16-le")
        except UnicodeDecodeError as exc:
            raise AutonomousLoginError(
                "The stored Windows credential has an invalid password payload"
            ) from exc
        return StoredCredential(credential.UserName or "", password)
    finally:
        api.CredFree(credential_pointer)


def windows_credential_is_configured(target: str = CREDENTIAL_TARGET) -> bool:
    """Check for a complete saved login without materializing its password.

    Regular booking startup uses this only when there is no usable Playwright
    state.  A present state therefore remains the primary path and does not
    cause the password blob to be read into Python memory.
    """

    api = _credential_api()
    credential_pointer = ctypes.POINTER(_CREDENTIALW)()
    if not api.CredReadW(
        target,
        _CRED_TYPE_GENERIC,
        0,
        ctypes.byref(credential_pointer),
    ):
        error = ctypes.get_last_error()
        if error == _ERROR_NOT_FOUND:
            return False
        raise AutonomousLoginError(
            f"Windows Credential Manager could not inspect the login (error {error})"
        )

    try:
        credential = credential_pointer.contents
        return bool(
            (credential.UserName or "").strip()
            and credential.CredentialBlob
            and credential.CredentialBlobSize > 0
        )
    finally:
        api.CredFree(credential_pointer)


def write_windows_credential(
    username: str,
    password: str,
    target: str = CREDENTIAL_TARGET,
) -> None:
    """Store the login encrypted by Windows for the current user."""

    username = username.strip()
    if not username or "@" not in username:
        raise AutonomousLoginError("Enter the full RWCMD Microsoft email address")
    if not password:
        raise AutonomousLoginError("The password cannot be empty")

    api = _credential_api()
    password_blob = bytearray(password.encode("utf-16-le"))
    blob_buffer = (ctypes.c_ubyte * len(password_blob)).from_buffer(password_blob)
    credential = _CREDENTIALW()
    credential.Type = _CRED_TYPE_GENERIC
    credential.TargetName = target
    credential.Comment = "Autonomous RWCMD Microsoft sign-in for AsimutBooker"
    credential.CredentialBlobSize = len(password_blob)
    credential.CredentialBlob = ctypes.cast(
        blob_buffer,
        ctypes.POINTER(ctypes.c_ubyte),
    )
    credential.Persist = _CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = username
    try:
        if not api.CredWriteW(ctypes.byref(credential), 0):
            error = ctypes.get_last_error()
            raise AutonomousLoginError(
                f"Windows Credential Manager could not save the login (error {error})"
            )
    finally:
        for index in range(len(password_blob)):
            password_blob[index] = 0


def configure_windows_credential_interactive(
    *,
    circuit_breaker: AuthRecoveryCircuitBreaker = DEFAULT_AUTH_CIRCUIT_BREAKER,
) -> None:
    """Prompt locally so the password never enters argv, config, or logs."""

    if not sys.stdin.isatty():
        raise AutonomousLoginError(
            "Run credential setup in an interactive private terminal"
        )
    username = input("RWCMD Microsoft email: ").strip()
    password = getpass.getpass("Password (stored only in Windows Credential Manager): ")
    confirmation = getpass.getpass("Confirm password: ")
    try:
        if password != confirmation:
            raise AutonomousLoginError("The two password entries did not match")
        write_windows_credential(username, password)
        circuit_breaker.clear()
    finally:
        password = None
        confirmation = None


class SmsBridgeClient:
    """Bounded client for the existing in-memory RWCMD SMS bridge."""

    def __init__(
        self,
        base_url: str = SMS_BRIDGE_BASE_URL,
        task_name: str = SMS_BRIDGE_TASK,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.task_name = task_name

    def _get_json(self, path: str, timeout_seconds: float = 3) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            headers={"Accept": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
        document = json.loads(payload)
        if not isinstance(document, dict):
            raise ValueError("bridge response is not an object")
        return document

    def is_healthy(self) -> bool:
        try:
            return self._get_json("/health").get("status") == "ok"
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
            return False

    def ensure_ready(self, timeout_seconds: float = 12) -> None:
        if self.is_healthy():
            return
        try:
            completed = subprocess.run(
                ["schtasks.exe", "/Run", "/TN", self.task_name],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if os.name == "nt"
                    else 0
                ),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise AutonomousLoginError(
                "The existing RWCMD SMS bridge could not be started"
            ) from exc
        if completed.returncode != 0:
            raise AutonomousLoginError(
                "The existing RWCMD SMS bridge task did not start successfully"
            )

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.is_healthy():
                return
            time.sleep(0.5)
        raise AutonomousLoginError("The RWCMD SMS bridge did not become healthy")

    def wait_for_code(self, timeout_seconds: float = 25) -> str:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                payload = self._get_json("/latest")
                code = str(payload.get("code", ""))
                if payload.get("available") and _CODE_PATTERN.fullmatch(code):
                    return code
            except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
                pass
            time.sleep(0.75)
        raise AutonomousLoginError(
            "No Microsoft verification code arrived through the RWCMD SMS bridge"
        )


def _normalized_auth_location(page) -> tuple[str, str]:
    """Classify a page without returning any raw URL component."""

    try:
        if page.is_closed():
            return "closed", "none"
        parsed = urlsplit(page.url)
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
        ):
            return "untrusted", "other"
        hostname = (parsed.hostname or "").lower()
        path = (parsed.path or "/").lower()
    except Exception:
        return "untrusted", "other"

    if hostname == "rwcmd.asimut.net":
        if path in ("", "/"):
            path_kind = "root"
        elif path.startswith("/agenda"):
            path_kind = "agenda"
        elif path.startswith("/arrangement"):
            path_kind = "arrangement"
        elif "login" in path or "signin" in path:
            path_kind = "login"
        else:
            path_kind = "other"
        return "asimut", path_kind

    if hostname in MICROSOFT_LOGIN_HOSTS:
        if "oauth2" in path and "authorize" in path:
            path_kind = "oauth-authorize"
        elif "kmsi" in path:
            path_kind = "stay-signed-in"
        elif any(token in path for token in ("proof", "mfa", "verify")):
            path_kind = "verification"
        elif "login" in path:
            path_kind = "login"
        else:
            path_kind = "other"
        return "microsoft-login", path_kind

    return "untrusted", "other"


def _context_pages(page) -> list:
    """Return the current context's open pages, tolerating lightweight test fakes."""

    try:
        pages = list(page.context.pages)
    except Exception:
        pages = [page]
    if not any(candidate is page for candidate in pages):
        pages.append(page)
    unique_pages = []
    seen = set()
    for candidate in pages:
        if id(candidate) in seen:
            continue
        seen.add(id(candidate))
        try:
            if candidate.is_closed():
                continue
        except Exception:
            continue
        unique_pages.append(candidate)
    return unique_pages


def _select_trusted_auth_page(page):
    """Follow one trusted SSO popup and reject ambiguous or foreign page sets."""

    pages = _context_pages(page)
    trusted = []
    untrusted = []
    for candidate in pages:
        origin, _path = _normalized_auth_location(candidate)
        if origin in ("asimut", "microsoft-login"):
            trusted.append(candidate)
        else:
            untrusted.append(candidate)

    if untrusted:
        raise AutonomousLoginError(
            "Autonomous sign-in stopped at a normalized untrusted page set "
            "(origin=untrusted path=other control=untrusted-page)"
        )
    if not trusted:
        raise AutonomousLoginError(
            "Autonomous sign-in found no trusted page "
            "(origin=untrusted path=other control=no-trusted-page)"
        )

    authenticated = [
        candidate
        for candidate in trusted
        if _normalized_auth_location(candidate)[0] == "asimut"
        and page_is_authenticated(candidate)
    ]
    if len(authenticated) == 1:
        return authenticated[0]
    if len(authenticated) > 1:
        raise AutonomousLoginError(
            "Autonomous sign-in found an ambiguous trusted page set "
            "(origin=asimut path=other control=ambiguous-pages)"
        )

    if page in trusted:
        alternatives = [candidate for candidate in trusted if candidate is not page]
        if not alternatives:
            return page
        current_origin = _normalized_auth_location(page)[0]
        if len(alternatives) == 1:
            alternate = alternatives[0]
            alternate_origin = _normalized_auth_location(alternate)[0]
            if current_origin == "asimut" and alternate_origin == "microsoft-login":
                return alternate
            if current_origin == "microsoft-login" and alternate_origin == "asimut":
                return page

    if page not in trusted and len(trusted) == 1:
        return trusted[0]

    raise AutonomousLoginError(
        "Autonomous sign-in found an ambiguous trusted page set "
        "(origin=untrusted path=other control=ambiguous-pages)"
    )


def page_is_authenticated(page) -> bool:
    """Return whether the page shows the authenticated RWCMD Asimut shell."""

    try:
        origin, _path = _normalized_auth_location(page)
        if origin != "asimut":
            return False
        indicators = (
            "[data-cy='menu-item-locations']",
            "[data-cy='menu-item-agenda']",
            "[data-cy^='menu-item-']",
            "a[href='/agenda']",
            "app-root router-outlet",
            "app-wrapper",
        )
        return any(page.locator(selector).count() > 0 for selector in indicators)
    except Exception:
        return False


def _first_visible_locator(page, selectors):
    for selector in selectors:
        try:
            matches = page.locator(selector)
            for index in range(min(matches.count(), 5)):
                candidate = matches.nth(index)
                if candidate.is_visible():
                    return candidate
        except Exception:
            continue
    return None


def _first_visible_named(page, roles, names, *, exact=True):
    for role in roles:
        for name in names:
            try:
                matches = page.get_by_role(role, name=name, exact=exact)
                for index in range(min(matches.count(), 5)):
                    candidate = matches.nth(index)
                    if candidate.is_visible():
                        return candidate
            except Exception:
                continue
    return None


def _fill_sensitive(locator, value: str, *, timeout_ms: int, failure_message: str) -> None:
    """Fill a secret-bearing control without ever propagating Playwright's value log."""

    try:
        locator.fill(value, timeout=timeout_ms)
    except Exception:
        # Playwright's call log includes fill("<literal value>"). Suppress the
        # original exception context so email, password, and OTP values cannot
        # reach the console or scheduler log.
        raise AutonomousLoginError(failure_message) from None


def _authenticated_context_continuation(page) -> bool:
    """Prove that a closed SSO popup handed off to one authenticated Asimut page."""

    try:
        return page_is_authenticated(_select_trusted_auth_page(page))
    except Exception:
        return False


def _wait_on_trusted_auth_page(
    page,
    *,
    delay_ms: int,
    handoff_timeout_ms: int,
):
    """Wait briefly, following a closed popup only after a trusted handoff."""

    try:
        page.wait_for_timeout(delay_ms)
        return page
    except Exception:
        try:
            if not page.is_closed():
                raise AutonomousLoginError(
                    "Microsoft sign-in could not wait for the next trusted stage"
                ) from None
        except AutonomousLoginError:
            raise
        except Exception:
            raise AutonomousLoginError(
                "Microsoft sign-in could not verify the active page safely"
            ) from None

    deadline = time.monotonic() + max(0.001, handoff_timeout_ms / 1000)
    while time.monotonic() < deadline:
        try:
            continuation = _select_trusted_auth_page(page)
            origin, _path = _normalized_auth_location(continuation)
            if origin == "microsoft-login" or page_is_authenticated(continuation):
                return continuation
        except AutonomousLoginError:
            raise
        except Exception:
            pass
        time.sleep(0.05)
    raise AutonomousLoginError(
        "Microsoft sign-in popup closed without a proven trusted continuation"
    ) from None


def _click_action(
    locator,
    *,
    active_page=None,
    timeout_ms: int,
    advanced_check=None,
    failure_message: str,
) -> None:
    """Click once; forgive only a timeout with a proven semantic transition."""

    try:
        locator.click(timeout=timeout_ms)
    except PlaywrightTimeoutError as exc:
        if advanced_check is not None:
            try:
                if advanced_check():
                    return
            except Exception:
                pass
        raise AutonomousLoginError(failure_message) from None
    except Exception:
        page_closed = False
        if active_page is not None:
            try:
                page_closed = active_page.is_closed()
            except Exception:
                page_closed = False
        if page_closed and advanced_check is not None:
            try:
                if advanced_check():
                    return
            except Exception:
                pass
        raise AutonomousLoginError(failure_message) from None


def _click_submit(
    page,
    names,
    *,
    field=None,
    timeout_ms=10000,
    advanced_check=None,
    failure_message="Microsoft submit control did not complete",
) -> bool:
    button = None
    if field is not None:
        try:
            form = field.locator("xpath=ancestor::form[1]")
            if form.count():
                button = _first_visible_locator(
                    form,
                    (
                        "input[type='submit']",
                        "button[type='submit']",
                    ),
                )
        except Exception:
            button = None
    if button is None:
        button = _first_visible_named(page, ("button", "link"), names)
    if button is None:
        button = _first_visible_locator(
            page,
            (
                "input[type='submit']",
                "button[type='submit']",
            ),
        )
    if button is None:
        return False
    _click_action(
        button,
        active_page=page,
        timeout_ms=timeout_ms,
        advanced_check=advanced_check,
        failure_message=failure_message,
    )
    return True


def _form_submit_semantic(page, field) -> str:
    """Classify the field's own submit control without returning its text."""

    scope = page
    try:
        form = field.locator("xpath=ancestor::form[1]")
        if form.count():
            scope = form
    except Exception:
        pass
    for semantic, names in (
        ("next", ("Next",)),
        ("sign-in", ("Sign in",)),
        ("continue", ("Continue",)),
        ("verify", ("Verify",)),
    ):
        if _first_visible_named(scope, ("button",), names) is not None:
            return semantic
    if _first_visible_locator(
        scope,
        ("input[type='submit']", "button[type='submit']"),
    ) is not None:
        return "other"
    return "none"


def _first_visible_alternate_method(page):
    """Return only a known Microsoft alternate-verification control."""

    control = _first_visible_named(
        page,
        ("button", "link"),
        (
            "Use another way",
            "Sign in another way",
            "Choose another way to sign in",
            "Use a different verification option",
            "Choose a different verification method",
            "Other ways to sign in",
            "I can't use my Microsoft Authenticator app right now",
            "I can't use the app right now",
        ),
        exact=False,
    )
    if control is not None:
        return control
    try:
        matches = page.get_by_text(
            re.compile(
                r"(?:can(?:'|’)?t|cannot)\s+use(?:[\s\S]{0,80}authenticator|"
                r"[\s\S]{0,80}\bapp\s+right\s+now)|"
                r"sign in (?:in )?another way|"
                r"use (?:another|a different) (?:way|method|verification option)|"
                r"other ways to sign in",
                re.IGNORECASE,
            )
        )
        for index in range(min(matches.count(), 5)):
            candidate = matches.nth(index)
            if candidate.is_visible():
                return candidate
    except Exception:
        pass
    return None


def _password_rejection_is_visible(page) -> bool:
    """Recognize Microsoft's password-error control without reading its text."""

    return _first_visible_locator(
        page,
        (
            "#passwordError",
            "[data-testid='passwordError']",
            "[data-bind*='passwordError']",
        ),
    ) is not None


def ensure_asimut_session(
    page,
    *,
    credential_reader=read_windows_credential,
    sms_bridge: SmsBridgeClient | None = None,
    timeout_seconds: float = 180,
    telemetry_writer=None,
    circuit_breaker: AuthRecoveryCircuitBreaker = DEFAULT_AUTH_CIRCUIT_BREAKER,
    explicit_retry: bool = False,
) -> AuthenticationResult:
    """Reuse a valid session or recover it through Microsoft password + SMS.

    The routine makes at most one password submission and consumes at most one
    code.  It never signs out merely to test recovery and never retries a
    rejected credential or verification code.
    """

    sms_bridge = sms_bridge or SmsBridgeClient()
    started_at = time.monotonic()
    deadline = started_at + timeout_seconds
    telemetry = _AuthTelemetry(started_at, deadline, telemetry_writer)

    def remaining_timeout_ms(cap_ms: int) -> int:
        remaining_ms = int(max(0, deadline - time.monotonic()) * 1000)
        return max(1, min(cap_ms, remaining_ms))

    def wait_for_next_stage(active_page):
        return _wait_on_trusted_auth_page(
            active_page,
            delay_ms=remaining_timeout_ms(500),
            handoff_timeout_ms=remaining_timeout_ms(3000),
        )

    def timeout_error() -> AutonomousLoginError:
        stage = telemetry.last_stage or _NormalizedAuthStage(
            "untrusted", "other", "none"
        )
        return AutonomousLoginError(
            "Autonomous Microsoft sign-in timed out before returning to Asimut "
            f"(last stage: {stage.summary()})"
        )

    try:
        page.goto(
            ASIMUT_BASE_URL,
            wait_until="domcontentloaded",
            timeout=remaining_timeout_ms(60000),
        )
        try:
            page.wait_for_load_state(
                "networkidle",
                timeout=remaining_timeout_ms(10000),
            )
        except PlaywrightTimeoutError:
            pass
        page = _select_trusted_auth_page(page)
    except AutonomousLoginError:
        circuit_breaker.record_transient_failure()
        raise
    except Exception:
        circuit_breaker.record_transient_failure()
        raise AutonomousLoginError(
            "Autonomous sign-in could not reach a trusted Asimut or Microsoft page"
        ) from None
    if page_is_authenticated(page):
        telemetry.report(page, "authenticated-shell")
        circuit_breaker.clear()
        return AuthenticationResult(False, False, False)

    circuit_breaker.ensure_retry_allowed(explicit_retry=explicit_retry)

    # The credential is not inspected until navigation has proven that the
    # active page set contains only exact trusted Asimut/Microsoft origins.
    telemetry.report(page, "session-check")
    try:
        credential = credential_reader()
    except AutonomousLoginError:
        circuit_breaker.record_transient_failure()
        raise
    except Exception:
        circuit_breaker.record_transient_failure()
        raise AutonomousLoginError(
            "The stored autonomous login could not be read safely"
        ) from None
    if credential is None:
        circuit_breaker.record_credential_rejection()
        raise AutonomousLoginError(
            "No autonomous login is stored. Run: python book_week.py "
            "--configure-autonomous-login"
        )
    if not credential.username or not credential.password:
        credential = None
        circuit_breaker.record_credential_rejection()
        raise AutonomousLoginError("The stored autonomous login is incomplete")

    username = credential.username
    password = credential.password
    credential = None
    email_submitted = False
    password_submitted = False
    password_submitted_at = None
    sms_method_selected = False
    sms_ready = False
    code_submitted = False
    code_submitted_at = None
    used_sms = False
    failure_recorded = False

    try:
        while time.monotonic() < deadline:
            page = _select_trusted_auth_page(page)
            if page_is_authenticated(page):
                telemetry.report(page, "authenticated-shell")
                circuit_breaker.clear()
                return AuthenticationResult(True, password_submitted, used_sms)

            origin, _path = _normalized_auth_location(page)

            if origin == "asimut":
                asimut_login = _first_visible_named(
                    page,
                    ("button", "link"),
                    (
                        "Sign in",
                        "Log in",
                        "Login",
                        "Microsoft 365",
                        "Sign in with Microsoft",
                    ),
                )
                if asimut_login is not None:
                    telemetry.report(page, "asimut-login")
                    _click_action(
                        asimut_login,
                        active_page=page,
                        timeout_ms=remaining_timeout_ms(10000),
                        advanced_check=lambda: _normalized_auth_location(
                            _select_trusted_auth_page(page)
                        )[0]
                        == "microsoft-login",
                        failure_message="Asimut sign-in control did not advance safely",
                    )
                    page = wait_for_next_stage(page)
                    continue
                telemetry.report(page, "none")
                page = wait_for_next_stage(page)
                continue

            email_input = _first_visible_locator(
                page,
                ("input[name='loginfmt']", "input[type='email']"),
            )
            password_input = _first_visible_locator(
                page,
                ("input[name='passwd']", "input[type='password']"),
            )
            if (
                email_input is not None
                and password_input is not None
                and not email_submitted
                and _form_submit_semantic(page, email_input) == "next"
            ):
                # Microsoft's combined shell can expose the future password
                # control while its one submit button is still the username
                # "Next" action.  Submit that phase once, then wait for the
                # same form's semantic action to become "Sign in".
                telemetry.report(page, "email")
                _fill_sensitive(
                    email_input,
                    username,
                    timeout_ms=remaining_timeout_ms(10000),
                    failure_message="Microsoft email field could not be filled safely",
                )
                if not _click_submit(
                    page,
                    ("Next",),
                    field=email_input,
                    timeout_ms=remaining_timeout_ms(10000),
                    advanced_check=lambda: _form_submit_semantic(
                        page,
                        _first_visible_locator(
                            page,
                            ("input[name='loginfmt']", "input[type='email']"),
                        )
                        or email_input,
                    )
                    != "next",
                    failure_message="Microsoft email submit control did not advance safely",
                ):
                    raise AutonomousLoginError(
                        "Microsoft email form has no supported submit control"
                    )
                email_submitted = True
                page = wait_for_next_stage(page)
                continue

            code_input = _first_visible_locator(
                page,
                (
                    "input[name='otc']",
                    "input[name='otcInput']",
                    "input[autocomplete='one-time-code']",
                    "input[placeholder*='code' i]",
                    "input[aria-label*='code' i]",
                ),
            )
            if code_input is not None:
                telemetry.report(page, "otp")
                if code_submitted:
                    if (
                        time.monotonic() - code_submitted_at
                        >= MICROSOFT_POST_SUBMIT_GRACE_SECONDS
                    ):
                        circuit_breaker.record_transient_failure()
                        failure_recorded = True
                        raise AutonomousLoginError(
                            "Microsoft did not accept the one-time verification code"
                        )
                else:
                    if not sms_ready:
                        sms_bridge.ensure_ready(
                            timeout_seconds=min(12, max(0.1, deadline - time.monotonic()))
                        )
                        sms_ready = True
                    code = sms_bridge.wait_for_code(
                        timeout_seconds=min(25, max(0.1, deadline - time.monotonic()))
                    )
                    try:
                        _fill_sensitive(
                            code_input,
                            code,
                            timeout_ms=remaining_timeout_ms(10000),
                            failure_message=(
                                "Microsoft verification field could not be filled safely"
                            ),
                        )
                    finally:
                        code = None
                    if not _click_submit(
                        page,
                        ("Verify", "Next", "Continue", "Sign in"),
                        field=code_input,
                        timeout_ms=remaining_timeout_ms(10000),
                        advanced_check=lambda: page_is_authenticated(page)
                        or _authenticated_context_continuation(page),
                        failure_message=(
                            "Microsoft verification submit control did not advance safely"
                        ),
                    ):
                        raise AutonomousLoginError(
                            "Microsoft verification form has no supported submit control"
                        )
                    code_submitted = True
                    code_submitted_at = time.monotonic()
                    used_sms = True
                page = wait_for_next_stage(page)
                continue

            if password_input is not None:
                if _form_submit_semantic(page, password_input) == "next":
                    telemetry.report(page, "email")
                    page = wait_for_next_stage(page)
                    continue
                telemetry.report(page, "password")
                if password_submitted:
                    if (
                        time.monotonic() - password_submitted_at
                        >= MICROSOFT_POST_SUBMIT_GRACE_SECONDS
                    ):
                        failure_recorded = True
                        if _password_rejection_is_visible(page):
                            circuit_breaker.record_credential_rejection()
                            raise AutonomousLoginError(
                                "Microsoft rejected the stored login; update it with "
                                "--configure-autonomous-login"
                            )
                        circuit_breaker.record_transient_failure()
                        raise AutonomousLoginError(
                            "Microsoft password sign-in did not advance safely"
                        )
                else:
                    if not sms_ready:
                        sms_bridge.ensure_ready(
                            timeout_seconds=min(12, max(0.1, deadline - time.monotonic()))
                        )
                        sms_ready = True
                    # Some Microsoft tenants render a combined form where both
                    # loginfmt and passwd are visible.  Password is still the
                    # deepest semantic stage, but the visible email field must
                    # be populated before the single form submission.
                    combined_email_input = _first_visible_locator(
                        page,
                        ("input[name='loginfmt']", "input[type='email']"),
                    )
                    if combined_email_input is not None and not email_submitted:
                        _fill_sensitive(
                            combined_email_input,
                            username,
                            timeout_ms=remaining_timeout_ms(10000),
                            failure_message=(
                                "Microsoft email field could not be filled safely"
                            ),
                        )
                        email_submitted = True
                    _fill_sensitive(
                        password_input,
                        password,
                        timeout_ms=remaining_timeout_ms(10000),
                        failure_message=(
                            "Microsoft password field could not be filled safely"
                        ),
                    )
                    password = None
                    if not _click_submit(
                        page,
                        ("Sign in", "Next", "Continue"),
                        field=password_input,
                        timeout_ms=remaining_timeout_ms(10000),
                        advanced_check=lambda: (
                            _first_visible_locator(
                                page,
                                (
                                    "input[name='otc']",
                                    "input[name='otcInput']",
                                    "input[autocomplete='one-time-code']",
                                    "input[placeholder*='code' i]",
                                    "input[aria-label*='code' i]",
                                ),
                            )
                            is not None
                            or page_is_authenticated(page)
                            or _authenticated_context_continuation(page)
                        ),
                        failure_message=(
                            "Microsoft password submit control did not advance safely"
                        ),
                    ):
                        raise AutonomousLoginError(
                            "Microsoft password form has no supported submit control"
                        )
                    password_submitted = True
                    password_submitted_at = time.monotonic()
                page = wait_for_next_stage(page)
                continue

            stay_signed_in = _first_visible_named(
                page,
                ("button",),
                ("Yes",),
            )
            if stay_signed_in is not None:
                telemetry.report(page, "stay-signed-in")
                _click_action(
                    stay_signed_in,
                    active_page=page,
                    timeout_ms=remaining_timeout_ms(10000),
                    advanced_check=lambda: page_is_authenticated(page)
                    or _authenticated_context_continuation(page),
                    failure_message=(
                        "Microsoft stay-signed-in control did not advance safely"
                    ),
                )
                page = wait_for_next_stage(page)
                continue

            sms_option = _first_visible_named(
                page,
                ("button", "link", "radio"),
                (
                    "Text",
                    "Text a code to my mobile phone",
                    "Send a code to my phone",
                    "SMS",
                ),
                exact=False,
            )
            if sms_option is None and not sms_method_selected:
                try:
                    matches = page.get_by_text(
                        re.compile(r"\b(text|sms)\b", re.IGNORECASE)
                    )
                    for index in range(min(matches.count(), 5)):
                        candidate = matches.nth(index)
                        if candidate.is_visible():
                            sms_option = candidate
                            break
                except Exception:
                    sms_option = None
            if sms_option is not None and not sms_method_selected:
                telemetry.report(page, "sms-option")
                sms_bridge.ensure_ready(
                    timeout_seconds=min(12, max(0.1, deadline - time.monotonic()))
                )
                sms_ready = True
                _click_action(
                    sms_option,
                    active_page=page,
                    timeout_ms=remaining_timeout_ms(10000),
                    advanced_check=lambda: _first_visible_locator(
                        page,
                        (
                            "input[name='otc']",
                            "input[name='otcInput']",
                            "input[autocomplete='one-time-code']",
                            "input[placeholder*='code' i]",
                            "input[aria-label*='code' i]",
                        ),
                    )
                    is not None,
                    failure_message="Microsoft SMS method control did not advance safely",
                )
                sms_method_selected = True
                page = wait_for_next_stage(page)
                continue

            use_another_way = _first_visible_alternate_method(page)
            if use_another_way is not None and not sms_method_selected:
                telemetry.report(page, "alternate-method")
                _click_action(
                    use_another_way,
                    active_page=page,
                    timeout_ms=remaining_timeout_ms(10000),
                    advanced_check=lambda: _first_visible_named(
                        page,
                        ("button", "link", "radio"),
                        ("Text", "Text a code to my mobile phone", "Send a code to my phone", "SMS"),
                        exact=False,
                    )
                    is not None,
                    failure_message=(
                        "Microsoft alternate-method control did not advance safely"
                    ),
                )
                page = wait_for_next_stage(page)
                continue

            if not password_submitted:
                account_choice = None
                try:
                    matches = page.get_by_text(username, exact=True)
                    for index in range(min(matches.count(), 5)):
                        candidate = matches.nth(index)
                        if candidate.is_visible():
                            account_choice = candidate
                            break
                except Exception:
                    account_choice = None
                if account_choice is not None:
                    telemetry.report(page, "account-choice")
                    _click_action(
                        account_choice,
                        active_page=page,
                        timeout_ms=remaining_timeout_ms(10000),
                        advanced_check=lambda: _first_visible_locator(
                            page,
                            ("input[name='passwd']", "input[type='password']"),
                        )
                        is not None,
                        failure_message=(
                            "Microsoft account-choice control did not advance safely"
                        ),
                    )
                    page = wait_for_next_stage(page)
                    continue

            if email_input is not None and not email_submitted:
                telemetry.report(page, "email")
                _fill_sensitive(
                    email_input,
                    username,
                    timeout_ms=remaining_timeout_ms(10000),
                    failure_message="Microsoft email field could not be filled safely",
                )
                if not _click_submit(
                    page,
                    ("Next", "Sign in", "Continue"),
                    field=email_input,
                    timeout_ms=remaining_timeout_ms(10000),
                    advanced_check=lambda: _first_visible_locator(
                        page,
                        ("input[name='passwd']", "input[type='password']"),
                    )
                    is not None,
                    failure_message="Microsoft email submit control did not advance safely",
                ):
                    raise AutonomousLoginError(
                        "Microsoft email form has no supported submit control"
                    )
                email_submitted = True
                page = wait_for_next_stage(page)
                continue

            telemetry.report(page, "none")
            page = wait_for_next_stage(page)

        raise timeout_error()
    except AutonomousLoginError:
        if not failure_recorded:
            circuit_breaker.record_transient_failure()
        raise
    except Exception:
        if not failure_recorded:
            circuit_breaker.record_transient_failure()
        raise AutonomousLoginError(
            "Autonomous Microsoft sign-in stopped after a sanitized automation failure"
        ) from None
    finally:
        username = None
        password = None
