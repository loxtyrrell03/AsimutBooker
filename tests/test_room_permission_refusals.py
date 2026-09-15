"""Room-specific rejections remain retryable only before a proven Save boundary."""
import unittest
from unittest import mock

import book_week as b
from upgrade_validation import (RoomPermissionRefusal, room_permission_refusal_text,
                                response_room_permission_refusal)


DENIED = "You are not allowed to book this room."


def denial_document(message=DENIED):
    return {"response": {"success": False, "bookingrules": {
        "issues": [{"class": "message-warning", "message": message}]}}}


class PermissionClassificationTests(unittest.TestCase):
    def test_explicit_room_refusal_variants(self):
        for wording in (DENIED, "You're not allowed to book this room",
                        "You’re not permitted to reserve the selected room",
                        "You are not allowed to make a booking in this room",
                        "You do not have permission to book this location",
                        "You don't have permission to reserve this room",
                        "You are not authorised to use this room",
                        "Bookings in this room are not permitted",
                        "This room cannot be booked by you"):
            with self.subTest(wording=wording):
                self.assertEqual(room_permission_refusal_text(wording), wording)

    def test_generic_auth_service_and_quota_messages_are_not_room_permissions(self):
        for wording in (None, {}, "Access denied", "403 Forbidden", "You are not allowed",
                        "You are not allowed to book more than two hours in this room",
                        "Maximum room booking quota reached", "This room is unavailable",
                        "You are not allowed to access this room: sign in again",
                        DENIED + " Your session has expired.",
                        DENIED + " Server error."):
            with self.subTest(wording=wording):
                self.assertIsNone(room_permission_refusal_text(wording))

    def test_slot_qualifiers_do_not_establish_a_room_wide_permission_refusal(self):
        for suffix in (" for more than 30 minutes.", " more than five days in advance.",
                       " for longer than two hours.", " at this time.",
                       " on the selected date.", " during peak hours.",
                       ": maximum booking duration exceeded.",
                       ": your booking length is too short.",
                       ": you have reached your daily limit.",
                       ": your weekly quota is exhausted.",
                       " outside the booking horizon."):
            wording = DENIED.rstrip('.') + suffix
            with self.subTest(wording=wording):
                self.assertIsNone(room_permission_refusal_text(wording))
                self.assertIsNone(response_room_permission_refusal(denial_document(wording)))

    def test_room_restrictions_with_reason_explanations_remain_permissions(self):
        for suffix in (" This room is reserved for teaching staff.",
                       " Only authorised recital organisers may reserve it.",
                       " Your course does not have access to this location."):
            wording = DENIED + suffix
            with self.subTest(wording=wording):
                self.assertEqual(room_permission_refusal_text(wording), wording)
                self.assertEqual(response_room_permission_refusal(denial_document(wording)), wording)

    def test_separate_slot_rule_prevents_room_wide_backoff(self):
        document = denial_document()
        document['response']['bookingrules']['issues'].append({
            'class': 'message-warning', 'message': 'Maximum booking duration is 30 minutes.'})
        self.assertIsNone(response_room_permission_refusal(document))

    def test_response_requires_explicit_failure_and_known_fields(self):
        self.assertEqual(response_room_permission_refusal(denial_document()), DENIED)
        for document in ({}, None, {"error": DENIED},
                         {"response": {"success": True, "message": DENIED}},
                         {"response": {"success": 0, "message": DENIED}},
                         {"response": {"success": False, "untrusted_data": DENIED}},
                         {"response": {"success": False, "bookingrules": None}},
                         {"response": {"success": False}, "messages": "not authenticated"}):
            self.assertIsNone(response_room_permission_refusal(document))
        document = denial_document()
        document["messages"] = {"errors": ["Authentication failed"]}
        self.assertIsNone(response_room_permission_refusal(document))

    def test_result_is_false_and_keeps_reason_without_changing_any_rank(self):
        refusal = RoomPermissionRefusal("Best", DENIED)
        self.assertFalse(refusal)
        self.assertEqual(refusal.room, "Best")
        self.assertEqual(refusal.reason, DENIED)


class PermissionRuntimeTests(unittest.TestCase):
    def page_with_message(self, text):
        page = mock.MagicMock()
        element = page.locator.return_value.first
        element.count.return_value = 1
        element.is_visible.return_value = True
        element.text_content.return_value = text
        return page

    def test_visible_permission_detection_is_specific_and_nonmutating(self):
        page = self.page_with_message("You do not have permission to book this room")
        refusal = b._visible_room_permission_refusal(page, "Best")
        self.assertIsInstance(refusal, RoomPermissionRefusal)
        self.assertEqual(refusal.room, "Best")
        page.locator.return_value.first.click.assert_not_called()
        for text in ("Session expired", DENIED + " Log in again.", "503 Server error"):
            self.assertIsNone(b._visible_room_permission_refusal(self.page_with_message(text), "Best"))

    def test_generic_error_or_login_message_cannot_close_a_pending_save(self):
        for text in ("Session expired", "You are not allowed to access this page",
                     "Server error", "Booking clashes. Please log in again."):
            self.assertIsNone(b._visible_save_rejection(self.page_with_message(text)))

    def test_permission_after_create_save_stays_pending_without_a_false_failure_claim(self):
        page = self.page_with_message(DENIED)
        page.url = "https://rwcmd.asimut.net/event?eventId=0"
        with mock.patch.object(b, "resolve_mutation_receipt") as resolve:
            with self.assertRaisesRegex(b.BookingVerificationError, "create outcome is not proven"):
                b.wait_for_created_booking_outcome(page, {"id": "pending"}, "Best",
                    "2026-09-21", "12:00", "12:30")
        resolve.assert_not_called()

    def test_new_boundary_check_returns_specific_room_denial_without_save(self):
        page = self.page_with_message(DENIED)
        response = mock.Mock(url="https://rwcmd.asimut.net/services/v2/event/type=check", ok=True)
        response.json.return_value = denial_document()
        page.expect_response.return_value.__enter__.return_value.value = response
        ok, detail = b.refresh_new_booking_validation(page, "12:30")
        self.assertFalse(ok)
        self.assertEqual(detail, DENIED)
        page.locator.return_value.first.click.assert_not_called()

    def test_room_refusal_allows_existing_room_fallback_to_try_next_candidate(self):
        slot = {"room": "Best"}
        backup = {"room": "Next"}
        result = {"receipt_id": "verified"}
        with (mock.patch.object(b, "try_book_slot", side_effect=[RoomPermissionRefusal("Best", DENIED), result]) as attempt,
              mock.patch.object(b, "list_pending_mutation_receipts", return_value=[]),
              mock.patch.object(b, "go_back"), mock.patch.object(b, "assert_calendar_date"),
              mock.patch.object(b, "get_available_slots", return_value=[]),
              mock.patch.object(b, "same_time_room_backups", return_value=[mock.Mock(start_text="12:00", end_text="12:30")]),
              mock.patch.object(b, "_opportunity_to_normal_slot", return_value=backup),
              mock.patch.object(b, "PRIORITY_ROOMS", ["Best", "Next"])):
            actual, selected = b.attempt_booking_with_room_fallback(
                mock.Mock(), slot, b.date(2026, 9, 21), mock.Mock(), 6,
                time_prefs=None, daily_planning=mock.Mock())
        self.assertEqual(actual, result)
        self.assertEqual(selected, backup)
        self.assertEqual(attempt.call_count, 2)

    def test_pending_intent_still_blocks_fallback_after_a_false_result(self):
        with (mock.patch.object(b, "try_book_slot", return_value=RoomPermissionRefusal("Best", DENIED)) as attempt,
              mock.patch.object(b, "list_pending_mutation_receipts", return_value=[{"id": "uncertain"}]),
              self.assertRaises(b.BookingVerificationError)):
            b.attempt_booking_with_room_fallback(mock.Mock(), {"room": "Best"},
                b.date(2026, 9, 21), mock.Mock(), 6, time_prefs=None, daily_planning=mock.Mock())
        self.assertEqual(attempt.call_count, 1)
