import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from room_access import named_room_refusal_text, response_named_room_refusal
from room_catalog import build_catalog, load_cached_catalog, save_catalog
from live_room_policy import build_live_room_policy, LiveRoomPolicyError
from room_preferences import load_room_preferences
from upgrade_validation import response_room_permission_refusal
from tests.test_room_catalog import (
    OBSERVED, complete_checks, session_payload, meta_payload, info_payload, issue,
)


def refused(room):
    return f"You are not allowed to create or modify bookings in {room}"


def catalog(denied=()):
    checks = complete_checks()
    for room_id, room in ((11, 'B0.11'), (12, 'B0.29'), (13, 'B1.09')):
        if room in denied:
            checks[room_id]['response']['bookingrules']['issues'].extend([
                issue(refused(room), issue_type='general'),
                issue('Requested booking exceeds your quota', issue_type='category'),
            ])
    return build_catalog(session_payload=session_payload(),
        location_meta_payload=meta_payload(), location_info_payload=info_payload(),
        check_payloads=checks, observed_at=OBSERVED, booking_category_id=56)


class RoomAccessTests(unittest.TestCase):
    def test_new_booking_reports_exact_named_refusal_without_save(self):
        import book_week as engine
        page = mock.MagicMock()
        page.locator.return_value.first.count.return_value = 1
        page.locator.return_value.first.is_visible.return_value = True
        response = mock.Mock(url='https://rwcmd.asimut.net/services/v2/event/type=check', ok=True)
        response.json.return_value = {'response': {'success': False, 'bookingrules': {'issues': [
            issue(refused('B0.29'), issue_type='general')]}}}
        page.expect_response.return_value.__enter__.return_value.value = response
        ok, detail = engine.refresh_new_booking_validation(page, '12:45', expected_room='B0.29')
        self.assertFalse(ok)
        self.assertEqual(detail, refused('B0.29'))
        page.locator.return_value.first.click.assert_not_called()

    def test_exact_named_warning_survives_independent_horizon_and_quota_warnings(self):
        document = complete_checks()[12]
        document['response']['bookingrules']['issues'].extend([
            issue(refused('B0.29'), issue_type='general'),
            issue('Requested booking exceeds your quota', issue_type='category'),
        ])
        self.assertEqual(response_named_room_refusal(document, 'B0.29'), refused('B0.29'))
        self.assertEqual(response_room_permission_refusal(document, 'B0.29'), refused('B0.29'))
        document['messages'] = {'errors': ['Session expired']}
        self.assertIsNone(response_room_permission_refusal(document, 'B0.29'))

    def test_bound_name_horizon_and_slot_qualifiers_do_not_deny_access(self):
        for value in (refused('B0.28'), refused('B0.29') + ' that end later than 29/9/26 08:00',
                      refused('B0.29') + ' during peak hours',
                      refused('B0.29') + ' for more than 30 minutes',
                      refused('B0.29') + ': session expired'):
            with self.subTest(value=value):
                self.assertIsNone(named_room_refusal_text(value, 'B0.29'))
        self.assertEqual(named_room_refusal_text('<p>' + refused('B0.29') + '</p>', 'B0.29'), refused('B0.29'))

    def test_only_explicit_rejected_general_warning_counts(self):
        base = {'response': {'success': False, 'bookingrules': {'issues': [
            issue(refused('B0.29'), issue_type='general')]}}}
        for field, value in (('type', 'date-time'), ('class', 'message-info'), ('text', '403 Forbidden')):
            document = copy.deepcopy(base)
            document['response']['bookingrules']['issues'][0][field] = value
            self.assertIsNone(response_named_room_refusal(document, 'B0.29'))
        base['response']['success'] = True
        self.assertIsNone(response_named_room_refusal(base, 'B0.29'))
        for malformed in (None, {}, {'response': []}, {'response': {'success': False, 'bookingrules': []}}):
            self.assertIsNone(response_named_room_refusal(malformed, 'B0.29'))

    def test_fresh_catalog_retains_all_grid_identities_but_filters_denied_choices(self):
        preferences = load_room_preferences({'room_preferences': {
            'ordered_rooms': ['B0.29', 'B1.09', 'B0.11']}})
        source = catalog(['B0.29'])
        policy = build_live_room_policy(source, preferences)
        self.assertEqual(policy.room_order, ('B1.09', 'B0.11'))
        self.assertIn('B0.29', policy.all_room_location_ids)
        self.assertIn('B0.29', source.room_names)
        self.assertNotIn('B0.29', policy.room_horizon_minutes)
        self.assertEqual(preferences.ordered_rooms[0], 'B0.29')
        restored = build_live_room_policy(catalog(), preferences)
        self.assertEqual(restored.room_order[0], 'B0.29')

    def test_all_denied_stops_before_any_booking(self):
        with self.assertRaisesRegex(LiveRoomPolicyError, 'permissions leave no eligible'):
            build_live_room_policy(catalog(['B0.11', 'B0.29', 'B1.09']), load_room_preferences({}))

    def test_display_cache_cannot_reuse_access_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'catalog.json'
            save_catalog(catalog(['B0.29']), path)
            cached = load_cached_catalog(path)
            self.assertTrue(all(room.permission_refusal is None for room in cached.rooms))
            with self.assertRaisesRegex(LiveRoomPolicyError, 'freshly observed'):
                build_live_room_policy(cached, load_room_preferences({}))


if __name__ == '__main__':
    unittest.main()
