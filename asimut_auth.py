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
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from dataclasses import dataclass, field
from urllib.parse import urlsplit


ASIMUT_BASE_URL = "https://rwcmd.asimut.net"
CREDENTIAL_TARGET = "AsimutBooker/RWCMD-Microsoft365"
SMS_BRIDGE_TASK = "RWCMD SMS Bridge"
SMS_BRIDGE_BASE_URL = "http://127.0.0.1:48733"

_ERROR_NOT_FOUND = 1168
_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_CODE_PATTERN = re.compile(r"^\d{6}$")


class AutonomousLoginError(RuntimeError):
    """Raised when deterministic sign-in cannot complete safely."""


@dataclass(frozen=True)
class StoredCredential:
    username: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class AuthenticationResult:
    recovered: bool
    used_password: bool
    used_sms: bool


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


def configure_windows_credential_interactive() -> None:
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


def page_is_authenticated(page) -> bool:
    """Return whether the page shows the authenticated RWCMD Asimut shell."""

    try:
        parsed = urlsplit(page.url)
        if (
            page.is_closed()
            or parsed.scheme != "https"
            or parsed.hostname != "rwcmd.asimut.net"
        ):
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


def _first_visible_named(page, roles, names):
    for role in roles:
        for name in names:
            try:
                matches = page.get_by_role(role, name=name, exact=True)
                for index in range(min(matches.count(), 5)):
                    candidate = matches.nth(index)
                    if candidate.is_visible():
                        return candidate
            except Exception:
                continue
    return None


def _click_submit(page, names) -> bool:
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
    button.click(timeout=10000)
    return True


def _uncheck_long_mfa_remember_option(page) -> None:
    labels = (
        "Don't ask again for 60 days",
        "Do not ask again for 60 days",
    )
    for label in labels:
        try:
            checkbox = page.get_by_label(label, exact=False)
            if checkbox.count() and checkbox.first.is_visible() and checkbox.first.is_checked():
                checkbox.first.uncheck()
                return
        except Exception:
            continue


def ensure_asimut_session(
    page,
    *,
    credential_reader=read_windows_credential,
    sms_bridge: SmsBridgeClient | None = None,
    timeout_seconds: float = 180,
) -> AuthenticationResult:
    """Reuse a valid session or recover it through Microsoft password + SMS.

    The routine makes at most one password submission and consumes at most one
    code.  It never signs out merely to test recovery and never retries a
    rejected credential or verification code.
    """

    sms_bridge = sms_bridge or SmsBridgeClient()
    page.goto(ASIMUT_BASE_URL, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    if page_is_authenticated(page):
        return AuthenticationResult(False, False, False)

    credential = credential_reader()
    if credential is None:
        raise AutonomousLoginError(
            "No autonomous login is stored. Run: python book_week.py "
            "--configure-autonomous-login"
        )
    if not credential.username or not credential.password:
        credential = None
        raise AutonomousLoginError("The stored autonomous login is incomplete")

    username = credential.username
    password = credential.password
    credential = None
    password_submitted = False
    password_submitted_at = None
    sms_method_selected = False
    sms_ready = False
    code_submitted = False
    code_submitted_at = None
    used_sms = False
    deadline = time.monotonic() + timeout_seconds

    try:
        while time.monotonic() < deadline:
            if page_is_authenticated(page):
                return AuthenticationResult(True, password_submitted, used_sms)

            email_input = _first_visible_locator(
                page,
                ("input[name='loginfmt']", "input[type='email']"),
            )
            if email_input is not None:
                email_input.fill(username)
                _click_submit(page, ("Next", "Sign in", "Continue"))
                page.wait_for_timeout(500)
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
                    account_choice.click(timeout=10000)
                    page.wait_for_timeout(500)
                    continue

            password_input = _first_visible_locator(
                page,
                ("input[name='passwd']", "input[type='password']"),
            )
            if password_input is not None:
                if password_submitted:
                    if time.monotonic() - password_submitted_at >= 4:
                        raise AutonomousLoginError(
                            "Microsoft did not accept the stored login; update it with "
                            "--configure-autonomous-login"
                        )
                else:
                    # Microsoft can transition directly from password submission
                    # to sending the tenant's default SMS challenge.
                    if not sms_ready:
                        sms_bridge.ensure_ready()
                        sms_ready = True
                    password_input.fill(password)
                    password = None
                    _click_submit(page, ("Sign in", "Next", "Continue"))
                    password_submitted = True
                    password_submitted_at = time.monotonic()
                page.wait_for_timeout(500)
                continue

            use_another_way = _first_visible_named(
                page,
                ("button", "link"),
                ("Use another way", "I can't use my Microsoft Authenticator app right now"),
            )
            if use_another_way is not None and not sms_method_selected:
                use_another_way.click(timeout=10000)
                page.wait_for_timeout(500)
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
                sms_bridge.ensure_ready()
                sms_ready = True
                sms_option.click(timeout=10000)
                sms_method_selected = True
                page.wait_for_timeout(500)
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
                if code_submitted:
                    if time.monotonic() - code_submitted_at >= 4:
                        raise AutonomousLoginError(
                            "Microsoft did not accept the one-time verification code"
                        )
                else:
                    if not sms_ready:
                        sms_bridge.ensure_ready()
                        sms_ready = True
                    code = sms_bridge.wait_for_code()
                    try:
                        code_input.fill(code)
                    finally:
                        code = None
                    _uncheck_long_mfa_remember_option(page)
                    _click_submit(page, ("Verify", "Next", "Continue", "Sign in"))
                    code_submitted = True
                    code_submitted_at = time.monotonic()
                    used_sms = True
                page.wait_for_timeout(500)
                continue

            stay_signed_in = _first_visible_named(
                page,
                ("button",),
                ("Yes",),
            )
            if stay_signed_in is not None:
                stay_signed_in.click(timeout=10000)
                page.wait_for_timeout(500)
                continue

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
                asimut_login.click(timeout=10000)
                page.wait_for_timeout(500)
                continue

            page.wait_for_timeout(500)

        raise AutonomousLoginError(
            "Autonomous Microsoft sign-in timed out before returning to Asimut"
        )
    finally:
        username = None
        password = None
