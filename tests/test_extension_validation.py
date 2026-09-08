import copy
import unittest
from unittest import mock

import book_week as b


class ExtensionValidationTests(unittest.TestCase):
    def setUp(self):
        self.booking = {"eventId": 42, "date": "2026-09-15", "startTime": "12:45"}
        self.data = {"event": {"id": 42, "st": "2026-09-15T12:45:00+01:00",
                               "en": "2026-09-15T14:15:00.000+01:00"}}
        self.page = mock.MagicMock()
        self.end = mock.Mock()
        self.response = mock.Mock(
            url="https://rwcmd.asimut.net/services/v2/event/event_id=42;type=check",
            ok=True,
        )
        self.response.request.method = "PATCH"
        self.response.request.post_data_json = self.data
        self.response.finished.return_value = None
        self.page.expect_response.return_value.__enter__.return_value.value = self.response
        self.save = self.page.locator.return_value.first
        self.save.count.return_value = 1
        self.save.is_visible.return_value = True
        self.save.is_enabled.return_value = True

    def validate(self):
        return b.refresh_extension_validation(self.page, self.end, self.booking, "14:15")

    def test_waits_for_delayed_save_enable_after_exact_response(self):
        self.save.is_enabled.side_effect = [False, False, True]
        self.assertTrue(self.validate()[0])
        self.assertEqual(self.page.wait_for_timeout.call_count, 2)
        self.end.fill.assert_called_once_with("14:15")
        self.end.press.assert_called_once_with("Tab")
        self.response.finished.assert_called_once()
        self.save.click.assert_not_called()

    def test_ignores_initial_stale_and_unrelated_validation_responses(self):
        self.assertTrue(self.validate()[0])
        predicate = self.page.expect_response.call_args.args[0]
        self.assertTrue(predicate(self.response))
        for url in [
            "https://rwcmd.asimut.net/services/v2/event/type=check",
            "https://rwcmd.asimut.net/services/v2/event/event_id=43;type=check",
            "https://foreign.invalid/services/v2/event/event_id=42;type=check",
            self.response.url + "?other=1",
        ]:
            response = copy.copy(self.response)
            response.url = url
            self.assertFalse(predicate(response))
        for key, value in [("id", 43), ("id", "42"), ("st", "2026-09-15T12:30:00+01:00"),
                           ("en", "2026-09-15T13:45:00+01:00"),
                           ("en", "2026-09-16T14:15:00+01:00"), ("en", "bad"),
                           ("en", "2026-09-15T14:15:00"),
                           ("en", "2026-09-15T14:15:01+01:00")]:
            data = copy.deepcopy(self.data)
            data["event"][key] = value
            self.response.request.post_data_json = data
            self.assertFalse(predicate(self.response), (key, value))

    def test_validation_timeout_or_http_failure_stops(self):
        self.page.expect_response.side_effect = TimeoutError("no check")
        self.assertFalse(self.validate()[0])
        self.save.is_enabled.assert_not_called()
        self.page.expect_response.side_effect = None
        self.response.ok = False
        self.assertFalse(self.validate()[0])
        self.save.is_enabled.assert_not_called()

    def test_incomplete_response_body_stops(self):
        self.response.finished.return_value = "network failure"
        self.assertFalse(self.validate()[0])
        self.save.is_enabled.assert_not_called()

    def test_permanently_disabled_save_is_never_forced(self):
        self.save.is_enabled.return_value = False
        with mock.patch.object(b.time, "monotonic", side_effect=[0, 1, 2, 6]):
            ok, detail = self.validate()
        self.assertFalse(ok)
        self.assertIn("remained disabled", detail)
        self.save.click.assert_not_called()
