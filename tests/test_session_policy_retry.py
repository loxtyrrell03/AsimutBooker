"""Strict session-policy parsing after one stale minute response; no ASIMUT."""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import room_catalog as catalog
from tests.test_room_catalog import session_payload, _FakePage, OBSERVED


class SessionPolicyRetryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(catalog.SITE_TIMEZONE).replace(second=30, microsecond=0)
        self.observed = self.now.replace(second=0)
        self.page = Mock()

    def payload(self, offset=0):
        value = session_payload()
        value['response']['session_context']['me']['booking_horizon'] = (
            self.observed + timedelta(days=7, minutes=offset)).isoformat()
        return value

    def read(self, responses):
        return patch.object(catalog, '_get_json', side_effect=[(value, self.now) for value in responses])

    def test_one_stale_response_is_replaced_by_a_fresh_strictly_validated_read(self):
        with self.read([self.payload(-1), self.payload()]) as get:
            payload, policy, bounds = catalog._read_live_session_policy(self.page)
        self.assertEqual(policy.observed_at, self.observed)
        self.assertEqual(policy.global_horizon_minutes, 10080)
        self.assertEqual(payload, self.payload())
        self.assertIsNotNone(bounds)
        self.assertEqual(get.call_count, 2)
        self.page.wait_for_timeout.assert_called_once_with(1000)
        self.assertTrue(all(call.args[1] == catalog.SESSION_CONTEXT_PATH for call in get.call_args_list))
        self.assertTrue(all(call.kwargs['timeout_ms'] == 15000 for call in get.call_args_list))

    def test_valid_first_read_needs_no_wait_or_extra_request(self):
        with self.read([self.payload()]) as get:
            _, policy, _ = catalog._read_live_session_policy(self.page)
        self.assertEqual(policy.global_horizon_minutes, 10080)
        get.assert_called_once()
        self.page.wait_for_timeout.assert_not_called()

    def test_full_catalog_refresh_recovers_once_and_checks_rooms_without_saving(self):
        page = _FakePage()
        page.wait_for_timeout = Mock()
        original_get = page.request.get
        session_reads = []
        def get(url, **kwargs):
            response = original_get(url, **kwargs)
            if url.endswith(catalog.SESSION_CONTEXT_PATH):
                session_reads.append(url)
                if len(session_reads) == 1:
                    response._payload['response']['session_context']['me']['booking_horizon'] = (
                        OBSERVED + timedelta(days=7, minutes=-1)).isoformat()
            return response
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(page.request, 'get', side_effect=get), \
             patch.object(catalog, '_request_observation_candidates', return_value=(OBSERVED,)):
            path = Path(directory) / 'catalog.json'
            result = catalog.refresh_from_site(page, path=path)
            self.assertTrue(result.fresh)
            self.assertEqual(result.global_horizon_minutes, 10080)
            self.assertTrue(path.exists())
        self.assertEqual(len(session_reads), 2)
        self.assertEqual(page.checked_ids, [11, 12, 13, 201, 202])
        self.assertFalse(any('type=save' in call[1] for call in page.request.calls))
        page.wait_for_timeout.assert_called_once_with(1000)

    def test_persistent_mismatch_stops_after_two_gets_without_form_or_cache_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'
            original = '{"previous":"preserve"}'
            path.write_text(original, encoding='utf-8')
            with self.read([self.payload(-1), self.payload(-1)]) as get:
                with self.assertRaisesRegex(catalog.RoomCatalogError, 'could not be aligned'):
                    catalog.refresh_from_site(self.page, path=path)
            self.assertEqual(path.read_text(encoding='utf-8'), original)
        self.assertEqual(get.call_count, 2)
        self.page.wait_for_timeout.assert_called_once_with(1000)
        self.page.goto.assert_not_called()
        self.page.request.post.assert_not_called()

    def test_schema_errors_are_not_retried(self):
        with self.read([{}]) as get:
            with self.assertRaises(catalog.RoomCatalogError):
                catalog._read_live_session_policy(self.page)
        get.assert_called_once()
        self.page.wait_for_timeout.assert_not_called()

    def test_invalid_booking_duration_is_not_a_clock_retry(self):
        invalid = self.payload()
        invalid['response']['session_context']['me']['minimum_booking_length'] = 25
        with self.read([invalid]) as get:
            with self.assertRaisesRegex(catalog.RoomCatalogError, 'minimum_booking_length'):
                catalog._read_live_session_policy(self.page)
        get.assert_called_once()
        self.page.wait_for_timeout.assert_not_called()

    def test_retry_cannot_accept_another_invalid_policy(self):
        invalid = self.payload()
        invalid['response']['session_context']['me']['maximum_booking_length'] = 0
        with self.read([self.payload(-1), invalid]) as get:
            with self.assertRaisesRegex(catalog.RoomCatalogError, 'maximum_booking_length'):
                catalog._read_live_session_policy(self.page)
        self.assertEqual(get.call_count, 2)

    def test_ambiguous_observation_is_not_retried_or_selected(self):
        with self.read([self.payload()]) as get, \
             patch.object(catalog, '_request_observation_candidates', return_value=(self.observed, self.observed)), \
             self.assertRaisesRegex(catalog.RoomCatalogError, 'more than one'):
            catalog._read_live_session_policy(self.page)
        get.assert_called_once()
        self.page.wait_for_timeout.assert_not_called()


if __name__ == '__main__':
    unittest.main()
