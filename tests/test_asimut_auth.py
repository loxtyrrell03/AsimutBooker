import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import book_week
from asimut_auth import (
    ASIMUT_BASE_URL,
    AutonomousLoginError,
    SmsBridgeClient,
    StoredCredential,
    ensure_asimut_session,
    write_windows_credential,
)


class _JsonResponse:
    def __init__(self, document):
        self.payload = json.dumps(document).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class _CountLocator:
    def __init__(self, count):
        self._count = count

    def count(self):
        return self._count


class _SessionPage:
    def __init__(self, authenticated):
        self.url = "about:blank"
        self.authenticated = authenticated

    def goto(self, url, **_kwargs):
        self.url = url

    def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def is_closed(self):
        return False

    def locator(self, _selector):
        return _CountLocator(1 if self.authenticated else 0)


class SmsBridgeClientTests(unittest.TestCase):
    def test_health_uses_only_exact_ok_status(self):
        client = SmsBridgeClient()
        with mock.patch(
            "asimut_auth.urllib.request.urlopen",
            return_value=_JsonResponse({"status": "ok"}),
        ):
            self.assertTrue(client.is_healthy())
        with mock.patch(
            "asimut_auth.urllib.request.urlopen",
            return_value=_JsonResponse({"status": "starting"}),
        ):
            self.assertFalse(client.is_healthy())

    def test_wait_for_code_ignores_unavailable_and_malformed_values(self):
        client = SmsBridgeClient()
        client._get_json = mock.Mock(
            side_effect=[
                {"available": False},
                {"available": True, "code": "12345"},
                {"available": True, "code": "123456"},
            ]
        )
        with mock.patch("asimut_auth.time.sleep"):
            self.assertEqual(client.wait_for_code(timeout_seconds=1), "123456")
        self.assertEqual(client._get_json.call_count, 3)

    def test_wait_for_code_times_out_without_echoing_bridge_payload(self):
        client = SmsBridgeClient()
        client._get_json = mock.Mock(
            return_value={"available": True, "code": "not-a-code"}
        )
        with mock.patch(
            "asimut_auth.time.monotonic",
            side_effect=[0, 0, 2],
        ), mock.patch("asimut_auth.time.sleep"):
            with self.assertRaisesRegex(
                AutonomousLoginError,
                "No Microsoft verification code arrived",
            ):
                client.wait_for_code(timeout_seconds=1)


class CredentialInputValidationTests(unittest.TestCase):
    def test_empty_or_non_email_username_fails_before_windows_api(self):
        for username in ("", "student"):
            with self.subTest(username=username):
                with mock.patch("asimut_auth._credential_api") as api:
                    with self.assertRaises(AutonomousLoginError):
                        write_windows_credential(username, "secret")
                    api.assert_not_called()

    def test_empty_password_fails_before_windows_api(self):
        with mock.patch("asimut_auth._credential_api") as api:
            with self.assertRaises(AutonomousLoginError):
                write_windows_credential("student@example.edu", "")
            api.assert_not_called()

    def test_stored_credential_repr_redacts_password(self):
        credential = StoredCredential("student@example.edu", "secret")
        self.assertNotIn("secret", repr(credential))


class SessionFirstTests(unittest.TestCase):
    def test_valid_saved_session_never_reads_the_password_or_sms_bridge(self):
        page = _SessionPage(authenticated=True)
        credential_reader = mock.Mock()
        sms_bridge = mock.Mock()

        result = ensure_asimut_session(
            page,
            credential_reader=credential_reader,
            sms_bridge=sms_bridge,
        )

        self.assertEqual(page.url, ASIMUT_BASE_URL)
        self.assertFalse(result.recovered)
        credential_reader.assert_not_called()
        sms_bridge.ensure_ready.assert_not_called()

    def test_expired_session_without_secure_credential_fails_closed(self):
        page = _SessionPage(authenticated=False)
        with self.assertRaisesRegex(
            AutonomousLoginError,
            "No autonomous login is stored",
        ):
            ensure_asimut_session(
                page,
                credential_reader=lambda: None,
                sms_bridge=mock.Mock(),
                timeout_seconds=0.1,
            )


class RuntimeAuthPreflightTests(unittest.TestCase):
    def test_valid_saved_state_is_primary_and_does_not_inspect_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            saved_state = Path(temp_dir) / "state.json"
            saved_state.write_text(
                json.dumps({"cookies": [], "origins": []}),
                encoding="utf-8",
            )
            with (
                mock.patch.object(book_week, "state_file", saved_state),
                mock.patch.object(
                    book_week,
                    "windows_credential_is_configured",
                ) as credential_probe,
            ):
                options = book_week.authenticated_runtime_context_options()

        self.assertEqual(options["storage_state"], str(saved_state))
        credential_probe.assert_not_called()

    def test_missing_state_without_explicit_credential_fails_before_playwright(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_state = Path(temp_dir) / "state.json"
            with (
                mock.patch.object(book_week, "state_file", missing_state),
                mock.patch.object(
                    book_week,
                    "windows_credential_is_configured",
                    return_value=False,
                ),
                mock.patch.object(book_week, "sync_playwright") as playwright,
            ):
                with self.assertRaisesRegex(
                    AutonomousLoginError,
                    "No saved Asimut browser session",
                ):
                    book_week.login_only(headless=True)

        playwright.assert_not_called()

    def test_missing_state_uses_only_an_already_configured_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_state = Path(temp_dir) / "state.json"
            with (
                mock.patch.object(book_week, "state_file", missing_state),
                mock.patch.object(
                    book_week,
                    "windows_credential_is_configured",
                    return_value=True,
                ) as credential_probe,
            ):
                options = book_week.authenticated_runtime_context_options()

        self.assertNotIn("storage_state", options)
        credential_probe.assert_called_once_with()

    def test_malformed_existing_state_is_preserved_and_never_falls_back(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            malformed_state = Path(temp_dir) / "state.json"
            original = b'{"cookies": "not-a-list"}'
            malformed_state.write_bytes(original)
            with (
                mock.patch.object(book_week, "state_file", malformed_state),
                mock.patch.object(
                    book_week,
                    "windows_credential_is_configured",
                ) as credential_probe,
            ):
                with self.assertRaisesRegex(
                    AutonomousLoginError,
                    "was preserved",
                ):
                    book_week.authenticated_runtime_context_options()

            self.assertEqual(malformed_state.read_bytes(), original)
        credential_probe.assert_not_called()

    def test_invalid_playwright_state_never_overwrites_existing_state(self):
        context = mock.Mock()
        context.storage_state.return_value = {"cookies": []}
        with tempfile.TemporaryDirectory() as temp_dir:
            saved_state = Path(temp_dir) / "state.json"
            original = b'{"cookies": [], "origins": []}'
            saved_state.write_bytes(original)
            with mock.patch.object(book_week, "state_file", saved_state):
                with self.assertRaisesRegex(RuntimeError, "invalid browser storage state"):
                    book_week.persist_storage_state(context)

            self.assertEqual(saved_state.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
