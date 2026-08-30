import json
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest import mock

import book_week
import asimut_auth
from asimut_auth import (
    ASIMUT_BASE_URL,
    AutonomousLoginError,
    AuthRecoveryCircuitBreaker,
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


class _MemoryCircuitBreaker:
    def __init__(self, *, blocked=False):
        self.blocked = blocked
        self.checked = []
        self.clears = 0
        self.credential_rejections = 0
        self.transient_failures = 0

    def ensure_retry_allowed(self, *, explicit_retry=False):
        self.checked.append(explicit_retry)
        if self.blocked and not explicit_retry:
            raise AutonomousLoginError("SANITIZED CIRCUIT OPEN")

    def clear(self):
        self.blocked = False
        self.clears += 1

    def record_credential_rejection(self):
        self.blocked = True
        self.credential_rejections += 1

    def record_transient_failure(self):
        self.blocked = True
        self.transient_failures += 1


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


class _PageContext:
    def __init__(self, pages=None):
        self.pages = list(pages or [])


class _StaticPage(_SessionPage):
    def __init__(self, url, *, authenticated=False):
        super().__init__(authenticated)
        self.url = url
        self.context = _PageContext([self])

    def goto(self, _url, **_kwargs):
        return None


class _FlowLocator:
    def __init__(self, page, kind=None, count=0):
        self.page = page
        self.kind = kind
        self._count = count

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def nth(self, _index):
        return self

    def is_visible(self):
        return bool(self._count)

    def is_checked(self):
        return False

    def uncheck(self, **_kwargs):
        return None

    def fill(self, value, **_kwargs):
        if self.kind == self.page.fill_failure_kind:
            raise asimut_auth.PlaywrightTimeoutError(
                f'locator.fill("{value}") RAW-DOM-CANARY'
            )
        self.page.fills.append((self.kind, value))

    def click(self, **_kwargs):
        self.page.clicks.append(self.kind)
        if self.kind == "email-submit":
            self.page.stage = "password"
        elif self.kind == "password-submit":
            if not self.page.block_password_submit:
                self.page.stage = "otp"
                self.page.url = (
                    "https://login.microsoftonline.com/common/verify"
                    "?phone=PHONE-CANARY"
                )
            if self.page.timeout_on_password_submit:
                raise asimut_auth.PlaywrightTimeoutError("RAW-DOM-CANARY")
        elif self.kind == "otp-submit":
            if not self.page.block_otp_submit:
                if self.page.authenticated_continuation is not None:
                    self.page.authenticated_continuation.authenticated = True
                    self.page._closed = True
                    if self.page.raise_when_popup_closes:
                        raise RuntimeError("RAW-POPUP-CLOSE-CANARY")
                else:
                    self.page.stage = "authenticated"
                    self.page.url = ASIMUT_BASE_URL


class _PasswordOtpFlowPage:
    def __init__(
        self,
        *,
        both_email_and_password=False,
        timeout_on_password_submit=False,
        block_password_submit=False,
        block_otp_submit=False,
        fill_failure_kind=None,
        authenticated_continuation=None,
        raise_when_popup_closes=False,
    ):
        self.url = (
            "https://login.microsoftonline.com/common/oauth2/v2.0/authorize/"
            "PATH-CANARY?login_hint=EMAIL-CANARY"
        )
        self.stage = "combined" if both_email_and_password else "password"
        self.both_email_and_password = both_email_and_password
        self.timeout_on_password_submit = timeout_on_password_submit
        self.block_password_submit = block_password_submit
        self.block_otp_submit = block_otp_submit
        self.fill_failure_kind = fill_failure_kind
        self.authenticated_continuation = authenticated_continuation
        self.raise_when_popup_closes = raise_when_popup_closes
        self._closed = False
        pages = [self]
        if authenticated_continuation is not None:
            pages.insert(0, authenticated_continuation)
        self.context = _PageContext(pages)
        if authenticated_continuation is not None:
            authenticated_continuation.context = self.context
        self.fills = []
        self.clicks = []
        self.label_queries = 0

    def goto(self, _url, **_kwargs):
        return None

    def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def wait_for_timeout(self, _timeout):
        if self._closed:
            raise RuntimeError("RAW-CLOSED-PAGE-CANARY")
        return None

    def is_closed(self):
        return self._closed

    def locator(self, selector):
        authenticated_indicators = {
            "[data-cy='menu-item-locations']",
            "[data-cy='menu-item-agenda']",
            "[data-cy^='menu-item-']",
            "a[href='/agenda']",
            "app-root router-outlet",
            "app-wrapper",
        }
        if selector in authenticated_indicators:
            return _FlowLocator(
                self,
                "authenticated-shell",
                1 if self.stage == "authenticated" else 0,
            )
        if selector in (
            "#passwordError",
            "[data-testid='passwordError']",
            "[data-bind*='passwordError']",
        ):
            return _FlowLocator(
                self,
                "password-error",
                1 if self.stage == "password" and self.block_password_submit else 0,
            )
        if selector in ("input[name='passwd']", "input[type='password']"):
            return _FlowLocator(
                self,
                "password",
                1 if self.stage in ("combined", "password") else 0,
            )
        if selector in ("input[name='loginfmt']", "input[type='email']"):
            return _FlowLocator(
                self,
                "email",
                1
                if self.both_email_and_password
                and self.stage in ("combined", "password")
                else 0,
            )
        if selector in (
            "input[name='otc']",
            "input[name='otcInput']",
            "input[autocomplete='one-time-code']",
            "input[placeholder*='code' i]",
            "input[aria-label*='code' i]",
        ):
            return _FlowLocator(
                self,
                "otp",
                1 if self.stage == "otp" else 0,
            )
        return _FlowLocator(self)

    def get_by_role(self, _role, *, name, exact=True):
        del exact
        if self.stage == "combined" and name == "Next":
            return _FlowLocator(self, "email-submit", 1)
        if self.stage == "password" and name == "Sign in":
            return _FlowLocator(self, "password-submit", 1)
        if self.stage == "otp" and name == "Verify":
            return _FlowLocator(self, "otp-submit", 1)
        return _FlowLocator(self)

    def get_by_text(self, *_args, **_kwargs):
        return _FlowLocator(self)

    def get_by_label(self, *_args, **_kwargs):
        self.label_queries += 1
        return _FlowLocator(self)


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
        credential = StoredCredential("EMAIL-CANARY@example.edu", "PASSWORD-CANARY")
        self.assertNotIn("EMAIL-CANARY", repr(credential))
        self.assertNotIn("PASSWORD-CANARY", repr(credential))


class SessionFirstTests(unittest.TestCase):
    def test_valid_saved_session_never_reads_the_password_or_sms_bridge(self):
        page = _SessionPage(authenticated=True)
        credential_reader = mock.Mock()
        sms_bridge = mock.Mock()

        result = ensure_asimut_session(
            page,
            credential_reader=credential_reader,
            sms_bridge=sms_bridge,
            circuit_breaker=_MemoryCircuitBreaker(),
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
                circuit_breaker=_MemoryCircuitBreaker(),
            )


class AuthObservabilityAndPageSelectionTests(unittest.TestCase):
    def test_alternate_method_fallback_accepts_only_known_text_pattern(self):
        candidate = mock.Mock()
        candidate.is_visible.return_value = True
        matches = mock.Mock()
        matches.count.return_value = 1
        matches.nth.return_value = candidate
        page = mock.Mock()
        page.get_by_role.return_value.count.return_value = 0
        page.get_by_text.return_value = matches

        self.assertIs(asimut_auth._first_visible_alternate_method(page), candidate)
        pattern = page.get_by_text.call_args.args[0]
        self.assertIsNotNone(pattern.search("I can’t use Microsoft Authenticator right now"))
        self.assertIsNotNone(pattern.search("I can’t use the app right now"))
        self.assertIsNotNone(pattern.search("I can’t use my security\napp right now"))
        self.assertIsNone(pattern.search("Approve this Authenticator request"))

    def test_submit_prefers_the_fields_own_form_over_a_global_decoy(self):
        form_button = mock.Mock()
        form_button.is_visible.return_value = True
        form_matches = mock.Mock()
        form_matches.count.return_value = 1
        form_matches.nth.return_value = form_button
        form = mock.Mock()
        form.count.return_value = 1
        form.locator.return_value = form_matches
        field = mock.Mock()
        field.locator.return_value = form
        page = mock.Mock()

        self.assertTrue(
            asimut_auth._click_submit(
                page,
                ("Sign in",),
                field=field,
                timeout_ms=1000,
                failure_message="sanitized failure",
            )
        )

        form_button.click.assert_called_once_with(timeout=1000)
        page.get_by_role.assert_not_called()

    def test_single_trusted_microsoft_popup_is_followed(self):
        original = _StaticPage(ASIMUT_BASE_URL)
        popup = _StaticPage(
            "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        )
        context = _PageContext([original, popup])
        original.context = context
        popup.context = context

        self.assertIs(asimut_auth._select_trusted_auth_page(original), popup)

    def test_ambiguous_or_untrusted_page_sets_fail_closed(self):
        original = _StaticPage(ASIMUT_BASE_URL)
        first = _StaticPage("https://login.microsoftonline.com/common/login")
        second = _StaticPage("https://login.microsoftonline.com/common/mfa")
        context = _PageContext([original, first, second])
        for page in context.pages:
            page.context = context
        with self.assertRaisesRegex(AutonomousLoginError, "ambiguous trusted page set"):
            asimut_auth._select_trusted_auth_page(original)

        foreign = _StaticPage("https://example.invalid/login?secret=URL-CANARY")
        context = _PageContext([original, foreign])
        original.context = context
        foreign.context = context
        with self.assertRaisesRegex(AutonomousLoginError, "untrusted page set") as raised:
            asimut_auth._select_trusted_auth_page(original)
        self.assertNotIn("URL-CANARY", str(raised.exception))

    def test_untrusted_redirect_never_reads_the_credential(self):
        page = _StaticPage("https://example.invalid/login?hint=EMAIL-CANARY")
        credential_reader = mock.Mock(
            return_value=StoredCredential("EMAIL-CANARY", "PASSWORD-CANARY")
        )
        with self.assertRaisesRegex(AutonomousLoginError, "untrusted page set") as raised:
            ensure_asimut_session(
                page,
                credential_reader=credential_reader,
                sms_bridge=mock.Mock(),
                timeout_seconds=1,
                telemetry_writer=mock.Mock(),
                circuit_breaker=_MemoryCircuitBreaker(),
            )
        credential_reader.assert_not_called()
        self.assertNotIn("EMAIL-CANARY", str(raised.exception))

    def test_transition_telemetry_is_deduplicated_and_normalized(self):
        page = _StaticPage(
            "https://login.microsoftonline.com/common/oauth2/v2.0/authorize/"
            "PATH-CANARY?hint=QUERY-CANARY"
        )
        lines = []
        telemetry = asimut_auth._AuthTelemetry(0, 100, lines.append)
        with mock.patch("asimut_auth.time.monotonic", return_value=10):
            telemetry.report(page, "password")
            telemetry.report(page, "password")
            telemetry.report(page, "otp")

        self.assertEqual(len(lines), 2)
        self.assertIn("origin=microsoft-login", lines[0])
        self.assertIn("path=oauth-authorize", lines[0])
        self.assertIn("elapsed=10s remaining=90s", lines[0])
        self.assertNotIn("PATH-CANARY", "\n".join(lines))
        self.assertNotIn("QUERY-CANARY", "\n".join(lines))

    def test_deepest_control_wins_and_telemetry_redacts_every_canary(self):
        page = _PasswordOtpFlowPage(both_email_and_password=True)
        bridge = mock.Mock()
        bridge.wait_for_code.return_value = "654321"
        lines = []

        result = ensure_asimut_session(
            page,
            credential_reader=lambda: StoredCredential(
                "EMAIL-CANARY@example.invalid",
                "PASSWORD-CANARY",
            ),
            sms_bridge=bridge,
            timeout_seconds=5,
            telemetry_writer=lines.append,
            circuit_breaker=_MemoryCircuitBreaker(),
        )

        self.assertTrue(result.used_password)
        self.assertTrue(result.used_sms)
        self.assertEqual(
            [kind for kind, _value in page.fills],
            ["email", "password", "otp"],
        )
        self.assertEqual(page.clicks.count("password-submit"), 1)
        self.assertEqual(page.clicks.count("email-submit"), 1)
        emitted = "\n".join(lines)
        for canary in (
            "EMAIL-CANARY",
            "PASSWORD-CANARY",
            "654321",
            "PHONE-CANARY",
            "PATH-CANARY",
        ):
            self.assertNotIn(canary, emitted)

    def test_click_timeout_is_accepted_only_after_semantic_advance(self):
        page = _PasswordOtpFlowPage(timeout_on_password_submit=True)
        bridge = mock.Mock()
        bridge.wait_for_code.return_value = "654321"

        result = ensure_asimut_session(
            page,
            credential_reader=lambda: StoredCredential(
                "student@example.invalid",
                "PASSWORD-CANARY",
            ),
            sms_bridge=bridge,
            timeout_seconds=5,
            telemetry_writer=mock.Mock(),
            circuit_breaker=_MemoryCircuitBreaker(),
        )
        self.assertTrue(result.recovered)
        self.assertEqual(page.clicks.count("password-submit"), 1)

        blocked = _PasswordOtpFlowPage(
            timeout_on_password_submit=True,
            block_password_submit=True,
        )
        with self.assertRaisesRegex(
            AutonomousLoginError,
            "password submit control did not advance safely",
        ) as raised:
            ensure_asimut_session(
                blocked,
                credential_reader=lambda: StoredCredential(
                    "student@example.invalid",
                    "PASSWORD-CANARY",
                ),
                sms_bridge=mock.Mock(),
                timeout_seconds=5,
                telemetry_writer=mock.Mock(),
                circuit_breaker=_MemoryCircuitBreaker(),
            )
        self.assertNotIn("RAW-DOM-CANARY", str(raised.exception))

    def test_every_secret_fill_failure_is_sanitized_from_none(self):
        cases = (
            ("email", {"both_email_and_password": True}, "EMAIL-CANARY@example.invalid"),
            ("password", {}, "PASSWORD-CANARY"),
            ("otp", {}, "654321"),
        )
        for kind, page_options, canary in cases:
            with self.subTest(kind=kind):
                page = _PasswordOtpFlowPage(
                    fill_failure_kind=kind,
                    **page_options,
                )
                bridge = mock.Mock()
                bridge.wait_for_code.return_value = "654321"
                circuit = _MemoryCircuitBreaker()
                with self.assertRaises(AutonomousLoginError) as raised:
                    ensure_asimut_session(
                        page,
                        credential_reader=lambda: StoredCredential(
                            "EMAIL-CANARY@example.invalid",
                            "PASSWORD-CANARY",
                        ),
                        sms_bridge=bridge,
                        timeout_seconds=5,
                        telemetry_writer=mock.Mock(),
                        circuit_breaker=circuit,
                    )

                rendered = "".join(
                    traceback.format_exception(
                        type(raised.exception),
                        raised.exception,
                        raised.exception.__traceback__,
                    )
                )
                for secret in (
                    canary,
                    "RAW-DOM-CANARY",
                ):
                    self.assertNotIn(secret, rendered)
                self.assertTrue(raised.exception.__suppress_context__)
                self.assertEqual(circuit.transient_failures, 1)

    def test_closed_microsoft_popup_requires_and_follows_authenticated_handoff(self):
        original = _StaticPage(ASIMUT_BASE_URL, authenticated=False)
        popup = _PasswordOtpFlowPage(
            authenticated_continuation=original,
            raise_when_popup_closes=True,
        )
        bridge = mock.Mock()
        bridge.wait_for_code.return_value = "654321"
        circuit = _MemoryCircuitBreaker()

        result = ensure_asimut_session(
            popup,
            credential_reader=lambda: StoredCredential(
                "student@example.invalid",
                "PASSWORD-CANARY",
            ),
            sms_bridge=bridge,
            timeout_seconds=5,
            telemetry_writer=mock.Mock(),
            circuit_breaker=circuit,
        )

        self.assertTrue(result.recovered)
        self.assertTrue(original.authenticated)
        self.assertTrue(popup.is_closed())
        self.assertEqual(popup.clicks.count("otp-submit"), 1)
        self.assertEqual(circuit.clears, 1)

    def test_closed_popup_without_authenticated_handoff_fails_closed(self):
        original = _StaticPage(ASIMUT_BASE_URL, authenticated=False)
        popup = _PasswordOtpFlowPage(authenticated_continuation=original)
        popup._closed = True
        with mock.patch("asimut_auth.time.sleep"):
            with self.assertRaisesRegex(
                AutonomousLoginError,
                "popup closed without a proven trusted continuation",
            ):
                asimut_auth._wait_on_trusted_auth_page(
                    popup,
                    delay_ms=1,
                    handoff_timeout_ms=1,
                )

    def test_mfa_remember_device_choice_is_never_unchecked(self):
        page = _PasswordOtpFlowPage()
        bridge = mock.Mock()
        bridge.wait_for_code.return_value = "654321"

        ensure_asimut_session(
            page,
            credential_reader=lambda: StoredCredential(
                "student@example.invalid",
                "PASSWORD-CANARY",
            ),
            sms_bridge=bridge,
            timeout_seconds=5,
            telemetry_writer=mock.Mock(),
            circuit_breaker=_MemoryCircuitBreaker(),
        )

        self.assertEqual(page.label_queries, 0)

    def test_rejected_password_latches_but_otp_rejection_only_cools_down(self):
        credential_circuit = _MemoryCircuitBreaker()
        with mock.patch.object(
            asimut_auth,
            "MICROSOFT_POST_SUBMIT_GRACE_SECONDS",
            0,
        ):
            with self.assertRaisesRegex(AutonomousLoginError, "rejected"):
                ensure_asimut_session(
                    _PasswordOtpFlowPage(block_password_submit=True),
                    credential_reader=lambda: StoredCredential(
                        "student@example.invalid",
                        "PASSWORD-CANARY",
                    ),
                    sms_bridge=mock.Mock(),
                    timeout_seconds=5,
                    telemetry_writer=mock.Mock(),
                    circuit_breaker=credential_circuit,
                )
        self.assertEqual(credential_circuit.credential_rejections, 1)
        self.assertEqual(credential_circuit.transient_failures, 0)

        otp_circuit = _MemoryCircuitBreaker()
        bridge = mock.Mock()
        bridge.wait_for_code.return_value = "654321"
        with mock.patch.object(
            asimut_auth,
            "MICROSOFT_POST_SUBMIT_GRACE_SECONDS",
            0,
        ):
            with self.assertRaisesRegex(AutonomousLoginError, "verification code"):
                ensure_asimut_session(
                    _PasswordOtpFlowPage(block_otp_submit=True),
                    credential_reader=lambda: StoredCredential(
                        "student@example.invalid",
                        "PASSWORD-CANARY",
                    ),
                    sms_bridge=bridge,
                    timeout_seconds=5,
                    telemetry_writer=mock.Mock(),
                    circuit_breaker=otp_circuit,
                )
        self.assertEqual(otp_circuit.credential_rejections, 0)
        self.assertEqual(otp_circuit.transient_failures, 1)


class AuthRecoveryCircuitBreakerTests(unittest.TestCase):
    def test_transient_cooldown_expires_and_document_contains_no_canaries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            now = [1000.0]
            path = Path(temp_dir) / "auth.json"
            circuit = AuthRecoveryCircuitBreaker(
                path,
                transient_backoff_seconds=60,
                clock=lambda: now[0],
            )
            circuit.record_transient_failure()
            with self.assertRaisesRegex(AutonomousLoginError, "temporarily paused"):
                circuit.ensure_retry_allowed()
            circuit.ensure_retry_allowed(explicit_retry=True)

            encoded = path.read_text(encoding="utf-8")
            self.assertEqual(
                set(json.loads(encoded)),
                {"version", "status", "category", "retry_after"},
            )
            for canary in (
                "EMAIL-CANARY",
                "PASSWORD-CANARY",
                "654321",
                "RAW-DOM-CANARY",
            ):
                self.assertNotIn(canary, encoded)

            now[0] += 61
            circuit.ensure_retry_allowed()
            self.assertEqual(json.loads(path.read_text())["status"], "ready")

    def test_credential_rejection_remains_latched_until_explicit_clear(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            now = [1000.0]
            path = Path(temp_dir) / "auth.json"
            circuit = AuthRecoveryCircuitBreaker(path, clock=lambda: now[0])
            circuit.record_credential_rejection()
            now[0] += 365 * 24 * 60 * 60
            with self.assertRaisesRegex(AutonomousLoginError, "stored login was rejected"):
                circuit.ensure_retry_allowed()
            circuit.ensure_retry_allowed(explicit_retry=True)
            circuit.clear()
            circuit.ensure_retry_allowed()

    def test_nonfinite_retry_timestamp_fails_closed_but_explicit_repair_can_retry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "auth.json"
            path.write_text(
                '{"version":1,"status":"blocked","category":"transient",'
                '"retry_after":NaN}',
                encoding="utf-8",
            )
            circuit = AuthRecoveryCircuitBreaker(path)
            with self.assertRaisesRegex(AutonomousLoginError, "state is invalid"):
                circuit.ensure_retry_allowed()
            circuit.ensure_retry_allowed(explicit_retry=True)

    def test_regular_recovery_obeys_block_but_explicit_repair_gets_one_attempt(self):
        blocked = _MemoryCircuitBreaker(blocked=True)
        credential_reader = mock.Mock(
            return_value=StoredCredential(
                "student@example.invalid",
                "PASSWORD-CANARY",
            )
        )
        with self.assertRaisesRegex(AutonomousLoginError, "CIRCUIT OPEN"):
            ensure_asimut_session(
                _SessionPage(authenticated=False),
                credential_reader=credential_reader,
                sms_bridge=mock.Mock(),
                timeout_seconds=1,
                telemetry_writer=mock.Mock(),
                circuit_breaker=blocked,
            )
        credential_reader.assert_not_called()

        page = _PasswordOtpFlowPage()
        bridge = mock.Mock()
        bridge.wait_for_code.return_value = "654321"
        result = ensure_asimut_session(
            page,
            credential_reader=credential_reader,
            sms_bridge=bridge,
            timeout_seconds=5,
            telemetry_writer=mock.Mock(),
            circuit_breaker=blocked,
            explicit_retry=True,
        )
        self.assertTrue(result.recovered)
        self.assertEqual(blocked.checked, [False, True])
        self.assertEqual(blocked.clears, 1)

    def test_valid_session_clears_block_without_reading_credential(self):
        circuit = _MemoryCircuitBreaker(blocked=True)
        credential_reader = mock.Mock()
        result = ensure_asimut_session(
            _SessionPage(authenticated=True),
            credential_reader=credential_reader,
            sms_bridge=mock.Mock(),
            circuit_breaker=circuit,
        )
        self.assertFalse(result.recovered)
        credential_reader.assert_not_called()
        self.assertEqual(circuit.checked, [])
        self.assertEqual(circuit.clears, 1)

    def test_explicit_credential_configuration_clears_prior_block(self):
        circuit = _MemoryCircuitBreaker(blocked=True)
        with (
            mock.patch.object(asimut_auth.sys.stdin, "isatty", return_value=True),
            mock.patch("builtins.input", return_value="student@example.invalid"),
            mock.patch(
                "asimut_auth.getpass.getpass",
                side_effect=["PASSWORD-CANARY", "PASSWORD-CANARY"],
            ),
            mock.patch("asimut_auth.write_windows_credential") as write_credential,
        ):
            asimut_auth.configure_windows_credential_interactive(
                circuit_breaker=circuit
            )

        write_credential.assert_called_once_with(
            "student@example.invalid",
            "PASSWORD-CANARY",
        )
        self.assertEqual(circuit.clears, 1)


class RuntimeAuthPreflightTests(unittest.TestCase):
    def test_login_only_is_an_explicit_single_retry_and_persists_success(self):
        page = mock.Mock()
        context = mock.Mock()
        context.new_page.return_value = page
        browser = mock.Mock()
        browser.new_context.return_value = context
        playwright = mock.Mock()
        playwright.chromium.launch.return_value = browser
        manager = mock.MagicMock()
        manager.__enter__.return_value = playwright

        with (
            mock.patch.object(
                book_week,
                "authenticated_runtime_context_options",
                return_value={},
            ),
            mock.patch.object(book_week, "sync_playwright", return_value=manager),
            mock.patch.object(
                book_week,
                "ensure_asimut_session",
                return_value=asimut_auth.AuthenticationResult(False, False, False),
            ) as ensure,
            mock.patch.object(book_week, "persist_storage_state") as persist,
        ):
            self.assertEqual(book_week.login_only(headless=True), 0)

        ensure.assert_called_once_with(page, explicit_retry=True)
        persist.assert_called_once_with(context)

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
