"""Boundary seed guards and real intercepted duration-edit browser coverage."""
import contextlib
import copy
import unittest
from datetime import date
from types import SimpleNamespace
from unittest import mock

import book_week as b
import mutation_receipts as receipts
from progressive_browser import (PreparedSeed, prepare_seed, save_seed,
                                 preflight_transfer_destination, prepare_transfer_destination)
from progressive_transactions import TransferEdit, reservation
from room_upgrades import Reservation
from upgrade_validation import RoomPermissionRefusal
from tests.test_progressive_transactions import transfer_fixture
from tests import test_upgrade_editor as editor_fixture


class ProgressiveSeedGuardTests(unittest.TestCase):
    def setUp(self):
        self.parent = {"id": "parent", "kind": "transfer", "transfer": transfer_fixture()}
        self.desired = reservation(self.parent["transfer"]["replacement"])
        self.page = mock.MagicMock(url="https://rwcmd.asimut.net/event")
        self.save = mock.Mock()
        self.save.count.return_value = 1
        self.save.is_enabled.return_value = True
        self.page.get_by_role.return_value = self.save
        self.engine = mock.Mock()
        self.engine.BookingVerificationError = b.BookingVerificationError
        self.engine.get_room_slot_coordinates.return_value = {"x": 10, "y": 20}
        self.engine.enter_new_booking_form.return_value = True
        self.engine.booking_summary_matches.return_value = True
        self.engine._visible_room_permission_refusal.return_value = None
        self.engine._visible_save_rejection.return_value = None
        self.engine.list_pending_mutation_receipts.return_value = [self.parent]
        self.engine.is_new_booking_form_url.return_value = True
        self.engine.refresh_new_booking_validation.return_value = (True, "checked")
        self.engine.booking_save_boundary.return_value = contextlib.nullcontext()
        self.engine.wait_for_created_booking_outcome.return_value = True
        self.engine.parse_confirmed_event_id.return_value = 99
        self.prepared = PreparedSeed(self.page, self.desired)
        self.child = {"id": "child"}
        patch = mock.patch.object(receipts, "record_pending_create", return_value=self.child)
        self.record = patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(receipts, 'mark_transfer_step')
        self.mark = patch.start()
        self.addCleanup(patch.stop)

    def test_preparation_sets_exact_times_but_never_saves_or_journals(self):
        prepared = prepare_seed(self.engine, self.page, self.desired)
        self.assertEqual(prepared, self.prepared)
        self.page.mouse.click.assert_called_once_with(10, 20)
        self.assertEqual(self.save.fill.call_args_list, [mock.call("12:00"), mock.call("12:30")])
        self.save.click.assert_not_called()
        self.record.assert_not_called()

    def test_preflight_permission_denial_is_a_skip_without_save(self):
        denied = RoomPermissionRefusal("Best", "You are not allowed to book this room")
        self.engine._visible_room_permission_refusal.return_value = denied
        self.assertIs(prepare_seed(self.engine, self.page, self.desired), denied)
        self.save.click.assert_not_called()
        self.record.assert_not_called()

    def test_seed_save_uses_one_exact_child_and_returns_verified_identity(self):
        self.assertEqual(save_seed(self.engine, self.prepared, self.parent),
                         Reservation(99, self.desired.day, "Best", 720, 750))
        self.record.assert_called_once_with(room="Best", booking_date="2026-09-21",
            start="12:00", end="12:30", parent_id="parent", transfer_role="seed")
        self.assertEqual(self.save.click.call_args_list,
                         [mock.call(trial=True, timeout=3000), mock.call(no_wait_after=True, timeout=5000)])
        self.engine.wait_for_created_booking_outcome.assert_called_once()
        self.mark.assert_called_once_with(self.parent, 'destination')

    def test_wrong_parent_role_or_desired_tuple_never_reaches_save(self):
        changes = [
            ({**self.parent, "kind": "create"}, self.prepared, "seed"),
            (self.parent, self.prepared, "restore:42"),
            (self.parent, self.prepared, "invalid"),
            (self.parent, PreparedSeed(self.page, Reservation(42, date(2026, 9, 22), "Best", 720, 750)), "seed"),
        ]
        for parent, prepared, role in changes:
            with self.subTest(role=role, day=prepared.desired.day), self.assertRaises(b.BookingVerificationError):
                save_seed(self.engine, prepared, parent, role=role)
        self.record.assert_not_called()
        self.save.click.assert_not_called()
        self.mark.assert_not_called()

    def test_existing_anchor_cannot_be_recreated_as_a_new_seed(self):
        self.parent["transfer"] = transfer_fixture("extending")
        self.prepared.desired = reservation(self.parent["transfer"]["replacement"])
        with self.assertRaises(b.BookingVerificationError):
            save_seed(self.engine, self.prepared, self.parent)
        self.record.assert_not_called()
        self.save.click.assert_not_called()

    def test_only_a_fully_retired_original_can_be_recreated_for_restoration(self):
        self.parent["transfer"] = transfer_fixture("complete")
        self.prepared.desired = reservation(self.parent["transfer"]["originals"][0])
        restored = save_seed(self.engine, self.prepared, self.parent, role="restore:42")
        self.assertEqual((restored.room, restored.start, restored.end), ("Fallback", 810, 840))
        self.assertEqual(self.record.call_args.kwargs["transfer_role"], "restore:42")

    def test_denied_boundary_validation_creates_no_child_and_does_not_retry(self):
        denied = RoomPermissionRefusal("Best", "You are not allowed to book this room")
        self.engine.refresh_new_booking_validation.return_value = (False, "permission")
        self.engine._visible_room_permission_refusal.return_value = denied
        self.assertIs(save_seed(self.engine, self.prepared, self.parent), denied)
        self.engine.refresh_new_booking_validation.assert_called_once()
        self.record.assert_not_called()
        self.save.click.assert_not_called()

    def test_network_permission_refusal_is_retained_before_dom_warning(self):
        self.engine.refresh_new_booking_validation.return_value = (
            False, "You are not allowed to book this room")
        outcome = save_seed(self.engine, self.prepared, self.parent)
        self.assertIsInstance(outcome, RoomPermissionRefusal)
        self.assertEqual(outcome.room, "Best")
        self.record.assert_not_called()
        self.save.click.assert_not_called()

    def test_form_drift_during_trial_stops_before_child_or_real_save(self):
        self.engine.booking_summary_matches.side_effect = [True, False]
        with self.assertRaisesRegex(b.BookingVerificationError, "changed before creation"):
            save_seed(self.engine, self.prepared, self.parent)
        self.record.assert_not_called()
        self.save.click.assert_called_once_with(trial=True, timeout=3000)

    def test_child_receipt_is_present_before_save_and_uncertain_click_never_retries(self):
        order = []
        self.record.side_effect = lambda **kwargs: order.append("record") or self.child
        def click(**kwargs):
            if not kwargs.get("trial"):
                order.append("save")
                raise TimeoutError("response lost")
        self.save.click.side_effect = click
        with self.assertRaisesRegex(b.BookingVerificationError, "uncertain"):
            save_seed(self.engine, self.prepared, self.parent)
        self.assertEqual(order, ["record", "save"])
        self.assertEqual(self.save.click.call_count, 2)
        self.engine.wait_for_created_booking_outcome.assert_not_called()

    def test_returned_rejection_does_not_retry_save(self):
        self.engine.wait_for_created_booking_outcome.return_value = False
        self.assertFalse(save_seed(self.engine, self.prepared, self.parent))
        self.record.assert_called_once()
        self.assertEqual(self.save.click.call_count, 2)


class ProgressiveDestinationPreflightTests(unittest.TestCase):
    def setUp(self):
        t = transfer_fixture()
        self.plan = SimpleNamespace(originals=tuple(map(reservation, t['originals'])),
            remaining=tuple(map(reservation, t['remaining'])), replacement=reservation(t['replacement']), seed=None)
        self.page = mock.MagicMock(url='https://rwcmd.asimut.net/event')
        self.prepared = PreparedSeed(self.page, self.plan.replacement)
        self.engine = mock.Mock(PEAK_START=9, PEAK_END=16, MAX_PEAK_HOURS=2)
        self.engine.require_live_room_policy.return_value = SimpleNamespace(all_room_location_ids={'Best': 1})
        self.engine.is_new_booking_form_url.return_value = True
        self.engine.booking_summary_matches.return_value = True
        self.listeners = {}
        self.page.on.side_effect = lambda name, callback: self.listeners.__setitem__(name, callback)
        self.page.remove_listener.side_effect = lambda name, callback: self.listeners.pop(name)
        # Sanitized observed NEW-create shape: POST, id 0, labelled location,
        # London timestamps and the unused single-booking weekday default.
        self.request = SimpleNamespace(method='POST',
            url='https://rwcmd.asimut.net/services/v2/event/type=check',
            post_data_json={'event': {'id': 0, 'st': '2026-09-21T12:00:00.000+01:00',
                'en': '2026-09-21T12:30:00.000+01:00', 'rs': [{'id': 1, 'dn': 'Best (Practice: Grand Piano)'}]},
                'booking_type': 'single', 'time_period_id': 0, 'weekdays': [1]})
        self.document = {'response': {'success': True, 'forms': [], 'event_ids': [0],
            'bookingrules': {'issues': [], 'clashing_person_ids': []}}}
        self.response = mock.Mock(request=self.request, ok=True)
        self.response.json.side_effect = lambda: copy.deepcopy(self.document)
        self.emit_request = True
        def refresh(*args):
            if self.emit_request:
                self.listeners['request'](self.request)
            self.listeners['response'](self.response)
            return True, 'fresh event validation completed'
        self.engine.refresh_new_booking_validation.side_effect = refresh

    def persons(self, interval='12:00 - 14:00'):
        return {'class': 'message-warning', 'type': 'persons', 'text':
            '<p class="message-event-headline">You have conflicting events:</p>'
            f'<div class="message-event-line"><p class="time-slot-p">{interval}</p>'
            '<p class="desc-p">Reservation</p></div><p class="message-event-footer">'
            'You must resolve the conflicts before saving the event.</p>'}

    def reject_with(self, *issues):
        self.document['response']['success'] = False
        self.document['response']['bookingrules']['issues'] = list(issues)
        self.document['messages'] = {'errors': ['Unable to modify event, see the booking rules']}

    def preflight(self):
        result = preflight_transfer_destination(self.engine, self.prepared, self.plan)
        self.assertEqual(self.listeners, {})
        self.page.mouse.click.assert_not_called()
        self.page.get_by_role.assert_not_called()
        return result

    def test_explicit_exact_approved_check_requires_no_save(self):
        self.assertIs(self.preflight(), True)
        self.engine.refresh_new_booking_validation.assert_called_once_with(self.page, '12:30')

    def test_only_known_removed_personal_overlap_and_general_info_are_allowed(self):
        self.reject_with(self.persons(), {'class': 'message-info', 'type': 'general',
            'text': 'Your booking will be provisional.<br />Reconfirm before it starts.'})
        self.assertIs(self.preflight(), True)

    def test_observed_peak_warning_is_allowed_only_when_sources_compensate_peak(self):
        self.reject_with(self.persons(), {'class': 'message-warning', 'type': 'category',
                                       'text': 'Requested booking exceeds your peak quota'})
        self.assertIs(self.preflight(), True)
        self.engine.MAX_PEAK_HOURS = 1
        self.assertFalse(self.preflight())

    def test_shifted_seed_cannot_excuse_uncompensated_peak_minutes(self):
        day = self.plan.replacement.day
        self.plan.originals = (Reservation(42, day, 'Fallback', 480, 600),)
        self.plan.remaining = (Reservation(42, day, 'Fallback', 510, 600),)
        self.reject_with({'class': 'message-warning', 'type': 'category',
                          'text': 'Requested booking exceeds your peak quota'})
        self.assertFalse(self.preflight())

    def test_mixed_room_horizon_quota_and_unknown_warnings_block_release(self):
        extras = [
            {'class': 'message-warning', 'type': 'horizon', 'text': 'Booking horizon exceeded'},
            {'class': 'message-warning', 'type': 'category', 'text': 'Weekly quota exceeded'},
            {'class': 'message-warning', 'type': 'location', 'text': 'Room closed'},
            {'class': 'message-warning', 'type': 'other', 'text': 'Additional conflict'},
        ]
        for issue in extras:
            with self.subTest(issue=issue):
                self.reject_with(self.persons(), issue)
                self.assertFalse(self.preflight())

    def test_room_permission_refusal_remains_a_typed_skip(self):
        self.reject_with(self.persons(), {'class': 'message-warning', 'type': 'location',
                                        'text': 'You are not allowed to book this room'})
        self.assertIsInstance(self.preflight(), RoomPermissionRefusal)

    def test_unexplained_error_cannot_hide_behind_personal_overlap(self):
        self.reject_with(self.persons())
        self.document['messages']['errors'].append('Service unavailable')
        self.assertFalse(self.preflight())

    def test_unknown_or_unremoved_conflict_interval_blocks_release(self):
        for interval in ('12:15 - 13:00', '11:00 - 12:30', 'invalid', '25:00 - 26:00'):
            with self.subTest(interval=interval):
                self.reject_with(self.persons(interval))
                self.assertFalse(self.preflight())
        self.reject_with(self.persons())
        self.plan.remaining = self.plan.originals
        self.assertFalse(self.preflight())

    def test_unknown_error_cannot_be_appended_inside_a_persons_message(self):
        issue = self.persons()
        issue['text'] += '<p>This room is closed.</p>'
        self.reject_with(issue)
        self.assertFalse(self.preflight())

    def test_stale_unmatched_and_wrong_http_responses_do_not_authorize_trim(self):
        self.emit_request = False
        self.assertFalse(self.preflight())
        self.emit_request = True
        self.response.ok = False
        self.assertFalse(self.preflight())

    def test_exact_new_request_identity_and_local_offset_are_required(self):
        baseline = copy.deepcopy(self.request.post_data_json)
        for key, value in [('id', 42), ('id', True), ('en', '2026-09-21T12:45:00+01:00'),
                           ('st', '2026-09-21T12:00:00Z'), ('rs', [{'id': 9}]),
                           ('rs', [{'id': 1}, {'id': 2}])]:
            with self.subTest(key=key, value=value):
                self.request.post_data_json = copy.deepcopy(baseline)
                self.request.post_data_json['event'][key] = value
                self.assertFalse(self.preflight())
        self.request.post_data_json = baseline
        self.request.post_data_json['booking_type'] = 'weekly'
        self.assertFalse(self.preflight())

    def test_incomplete_or_contradictory_response_blocks_release(self):
        baseline = copy.deepcopy(self.document)
        for key, value in [('success', 1), ('forms', [{'id': 1}]), ('event_ids', [42])]:
            with self.subTest(key=key):
                self.document = copy.deepcopy(baseline)
                self.document['response'][key] = value
                self.assertFalse(self.preflight())
        self.document = baseline
        self.document['response']['bookingrules']['clashing_person_ids'] = [123]
        self.assertFalse(self.preflight())

    def test_existing_seed_has_no_new_create_preflight(self):
        self.plan.seed = Reservation(99, self.plan.replacement.day, 'Best', 720, 750)
        self.assertFalse(self.preflight())
        self.engine.refresh_new_booking_validation.assert_not_called()

    def use_existing_seed(self):
        t = transfer_fixture('extending')
        self.plan = SimpleNamespace(originals=tuple(map(reservation, t['originals'])),
            remaining=tuple(map(reservation, t['remaining'])), replacement=reservation(t['replacement']),
            seed=reservation(t['seed_before']))
        self.prepared = PreparedSeed(self.page, self.plan.replacement, self.plan.seed)
        self.page.url = 'https://rwcmd.asimut.net/event?eventId=99'
        self.engine.parse_event_editor_id.return_value = 99
        self.request.method = 'PATCH'
        self.request.url = 'https://rwcmd.asimut.net/services/v2/event/event_id=99;type=check'
        self.request.post_data_json['event'].update(id=99, en='2026-09-21T12:45:00.000+01:00')
        self.document['response']['event_ids'] = [99]
        self.engine.refresh_extension_validation.side_effect = self.engine.refresh_new_booking_validation.side_effect

    def test_existing_seed_exact_patch_preflight_never_uses_new_create_check(self):
        self.use_existing_seed()
        self.assertIs(preflight_transfer_destination(self.engine, self.prepared, self.plan), True)
        self.engine.refresh_new_booking_validation.assert_not_called()
        self.engine.refresh_extension_validation.assert_called_once()
        self.page.mouse.click.assert_not_called()
        self.assertEqual(self.listeners, {})

    def test_existing_seed_compensated_peak_includes_already_booked_seed(self):
        self.use_existing_seed()
        self.reject_with(self.persons('12:30 - 14:00'), {'class': 'message-warning', 'type': 'category',
            'text': 'Requested booking exceeds your peak quota'})
        # The ordinary extension helper rejects this warning; the captured,
        # exact network response is required to establish compensation.
        emit = self.engine.refresh_extension_validation.side_effect
        def reject(*args):
            emit(*args)
            return False, 'Asimut did not approve the exact extension validation'
        self.engine.refresh_extension_validation.side_effect = reject
        self.assertIs(preflight_transfer_destination(self.engine, self.prepared, self.plan), True)
        self.request.post_data_json['event']['id'] = 42
        self.assertFalse(preflight_transfer_destination(self.engine, self.prepared, self.plan))

    def test_existing_seed_itself_is_not_a_removable_personal_conflict(self):
        self.use_existing_seed()
        self.reject_with(self.persons('12:00 - 12:30'))
        self.assertFalse(preflight_transfer_destination(self.engine, self.prepared, self.plan))


class ProgressiveCancelBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.parent = {'id': 'parent', 'kind': 'transfer', 'transfer': transfer_fixture('complete')}
        self.original = reservation(self.parent['transfer']['originals'][0])
        self.page, self.cancel = mock.MagicMock(), mock.Mock()
        self.order = []
        self.prove = self.stack.enter_context(mock.patch.object(b, 'verify_persisted_booking_page',
                                                               side_effect=lambda *args: self.order.append('proof')))
        self.mark = self.stack.enter_context(mock.patch.object(receipts, 'mark_transfer_step',
                                                              side_effect=lambda *args: self.order.append('marker')))
        self.cancel.click.side_effect = lambda **kwargs: self.order.append('click')
        @contextlib.contextmanager
        def guard():
            self.order.append('guard')
            yield
        self.stack.enter_context(mock.patch.object(b, 'booking_save_boundary', guard))
        for name in ('safe_goto', 'remove_extendable_booking_by_event_id', 'clear_booking_plan'):
            self.stack.enter_context(mock.patch.object(b, name))
        self.stack.enter_context(mock.patch.object(b, '_find_exact_cancellation_card', return_value=mock.Mock()))
        self.stack.enter_context(mock.patch.object(b, '_unique_visible_control', return_value=mock.Mock()))
        self.stack.enter_context(mock.patch.object(b, '_cancel_menu_option', return_value=self.cancel))
        self.stack.enter_context(mock.patch.object(b, '_optional_cancel_confirmation', return_value=None))
        self.stack.enter_context(mock.patch.object(b, 'list_pending_mutation_receipts', return_value=[self.parent]))
        def scan(_page, tracker, *_args, **_kwargs):
            tracker.agenda_events = []
            return 0, []
        self.stack.enter_context(mock.patch.object(b, 'scan_agenda', side_effect=scan))

    def cancel_original(self):
        r = self.original
        return b.cancel_reservation_exact(self.page, [r.as_booking()], event_id=r.event_id,
            room=r.room, date_str=r.day.isoformat(), start_time=r.as_booking()['startTime'],
            end_time=r.as_booking()['endTime'], today=date(2026, 9, 16), live_dates=(r.day,),
            ignored_events=set(), transfer_receipt=self.parent, transfer_revalidate=lambda: True)

    def test_marker_is_after_proof_and_settings_guard_immediately_before_click(self):
        self.assertTrue(self.cancel_original()[0])
        self.assertEqual(self.order, ['proof', 'guard', 'marker', 'click'])
        self.mark.assert_called_once_with(self.parent, 'source:42')

    def test_manual_deletion_before_persisted_proof_does_not_record_attempt(self):
        self.prove.side_effect = ValueError('Reservation was manually deleted')
        with self.assertRaises(b.BookingCancellationError):
            self.cancel_original()
        self.mark.assert_not_called()
        self.cancel.click.assert_not_called()

    def test_changed_preferences_at_guard_do_not_record_attempt(self):
        @contextlib.contextmanager
        def changed_preferences():
            raise b.BookingPreferencesChanged('Date disabled during preparation')
            yield
        with mock.patch.object(b, 'booking_save_boundary', changed_preferences):
            with self.assertRaises(b.BookingVerificationError):
                self.cancel_original()
        self.mark.assert_not_called()
        self.cancel.click.assert_not_called()


class ProgressiveTransferEditorTests(unittest.TestCase):
    setUpClass = classmethod(editor_fixture.UpgradeEditorTests.setUpClass.__func__)
    tearDownClass = classmethod(editor_fixture.UpgradeEditorTests.tearDownClass.__func__)
    run_edit = editor_fixture.UpgradeEditorTests.run_edit

    def setUp(self):
        editor_fixture.UpgradeEditorTests.setUp(self)
        self.check_document = None
        original_mark = receipts.mark_transfer_step
        patch = mock.patch.object(receipts, 'mark_transfer_step',
                                  side_effect=lambda parent, step: original_mark(parent, step, path=self.path))
        self.mark = patch.start()
        self.addCleanup(patch.stop)

    def site(self, route):
        if ';type=check' in route.request.url and self.check_document is not None:
            route.fulfill(json=self.check_document)
        elif "/event?eventId=42" in route.request.url and route.request.method == "GET":
            html = editor_fixture.EDITOR.replace("SAVE_DRIFT", "")
            html = html.replace('value="Fallback"', f'value="{self.original.room}"')
            html = html.replace('value="12:00"', f'value="{self.original.as_booking()["startTime"]}"')
            html = html.replace('value="14:00"', f'value="{self.original.as_booking()["endTime"]}"')
            if self.original.room == "Best":
                html = html.replace("let locationId=2;", "let locationId=1;")
            route.fulfill(content_type="text/html", body=html)
        else:
            editor_fixture.UpgradeEditorTests.site(self, route)

    def parent(self, phase):
        payload = transfer_fixture(phase, original_id=42 if phase == "initial" else 43, seed_id=42)
        self.original = reservation(payload["originals"][0] if phase == "initial" else payload["seed_before"])
        self.new = reservation(payload["remaining"][0] if phase == "initial" else payload["replacement"])
        self.persisted = self.original
        self.upgrade = TransferEdit(self.original, self.new)
        policy = mock.patch.object(b, "ACTIVE_ROOM_POLICY", SimpleNamespace(all_room_location_ids={"Best": 1, "Fallback": 2}))
        policy.start(); self.addCleanup(policy.stop)
        return receipts.record_pending("transfer", room=payload["replacement"]["room"],
            booking_date=payload["replacement"]["date"], start=payload["replacement"]["start"],
            end=payload["replacement"]["end"], transfer=payload, path=self.path)

    def test_same_room_fallback_shrink_uses_exact_parent_and_single_save(self):
        parent = self.parent("initial")
        self.assertTrue(self.run_edit(transaction_receipt=parent))
        self.assertEqual(self.persisted, self.new)
        self.assertEqual(len(self.save_calls), 1)
        self.assertIn("T12:30:00", self.save_calls[0]["event"]["st"])
        self.assertIn("T14:00:00", self.save_calls[0]["event"]["en"])
        self.assertEqual(receipts.list_pending(self.path), [parent])
        self.assertFalse(self.other_mutations)
        self.assertEqual(parent['transfer']['started_steps'], ['source:42'])

    def test_existing_seed_extends_without_creating_a_second_receipt(self):
        parent = self.parent("extending")
        self.assertTrue(self.run_edit(transaction_receipt=parent))
        self.assertEqual(self.persisted, self.new)
        self.assertEqual(len(self.save_calls), 1)
        self.assertIn("T12:45:00", self.save_calls[0]["event"]["en"])
        self.assertEqual(receipts.list_pending(self.path), [parent])
        self.assertEqual(len(receipts.load_journal(self.path)["receipts"]), 1)
        self.assertEqual(parent['transfer']['started_steps'], ['destination'])

    def test_changed_preferences_after_preparation_leave_no_attempt_marker(self):
        parent = self.parent('initial')
        @contextlib.contextmanager
        def changed_preferences():
            self.assertEqual(parent['transfer']['started_steps'], [])
            self.assertEqual(self.page.get_by_role('textbox', name='Start time', exact=True).input_value(), '12:30')
            raise b.BookingPreferencesChanged('Preferences changed during preparation')
            yield
        with mock.patch.object(b, 'booking_save_boundary', changed_preferences):
            with self.assertRaises(b.BookingPreferencesChanged):
                self.run_edit(transaction_receipt=parent)
        self.mark.assert_not_called()
        self.assertFalse(self.save_calls)
        self.assertEqual(self.persisted, self.original)
        self.assertEqual(parent['transfer']['started_steps'], [])

    def test_unrecorded_duration_cannot_use_valid_parent(self):
        parent = self.parent("initial")
        self.upgrade = TransferEdit(self.original, Reservation(42, self.original.day, "Fallback", 765, 840))
        with self.assertRaises(b.BookingVerificationError):
            self.run_edit(transaction_receipt=parent)
        self.assertFalse(self.save_calls)
        self.assertEqual(self.persisted, self.original)

    def test_rejected_transfer_edit_retains_original_and_pending_parent(self):
        parent = self.parent("initial")
        self.mode = "save_rejected"
        self.assertFalse(self.run_edit(transaction_receipt=parent))
        self.assertEqual(self.persisted, self.original)
        self.assertEqual(len(self.save_calls), 1)
        self.assertEqual(receipts.list_pending(self.path), [parent])

    def test_uncertain_transfer_edit_retains_parent_and_never_repeats(self):
        parent = self.parent("initial")
        self.mode = "lost_response"
        with self.assertRaises(b.BookingVerificationError):
            self.run_edit(transaction_receipt=parent)
        self.assertEqual(self.persisted, self.new)
        self.assertEqual(len(self.save_calls), 1)
        self.assertEqual(receipts.list_pending(self.path), [parent])

    def existing_plan(self, parent):
        t = parent['transfer']
        return SimpleNamespace(originals=tuple(map(reservation, t['originals'])),
            remaining=tuple(map(reservation, t['remaining'])), replacement=reservation(t['replacement']),
            seed=reservation(t['seed_before']))

    def test_actual_existing_editor_prepares_and_rechecks_without_save(self):
        parent = self.parent('extending')
        plan = self.existing_plan(parent)
        prepared = prepare_transfer_destination(b, self.page, plan)
        self.assertIsInstance(prepared, PreparedSeed)
        self.assertEqual(prepared.existing_seed, self.original)
        self.assertTrue(preflight_transfer_destination(b, prepared, plan))
        self.assertEqual(self.page.get_by_role('textbox', name='End time', exact=True).input_value(), '12:45')
        self.assertEqual(self.persisted, self.original)
        self.assertFalse(self.save_calls)
        self.assertFalse(self.other_mutations)
        self.assertEqual(receipts.list_pending(self.path), [parent])

    def test_actual_existing_editor_rechecks_denial_before_any_source_change(self):
        parent = self.parent('extending')
        plan = self.existing_plan(parent)
        prepared = prepare_transfer_destination(b, self.page, plan)
        self.mode = 'check_permission'
        self.assertIsInstance(preflight_transfer_destination(b, prepared, plan), RoomPermissionRefusal)
        self.assertEqual(self.persisted, self.original)
        self.assertFalse(self.save_calls)
        self.assertFalse(self.other_mutations)

    def test_actual_existing_editor_allows_only_compensated_personal_warning(self):
        parent = self.parent('extending')
        plan = self.existing_plan(parent)
        prepared = prepare_transfer_destination(b, self.page, plan)
        self.check_document = editor_fixture.result(False)
        self.check_document['response']['bookingrules']['issues'] = [
            ProgressiveDestinationPreflightTests.persons(self, '12:30 - 14:00'),
            {'class': 'message-warning', 'type': 'category', 'text': 'Requested booking exceeds your peak quota'}]
        self.assertTrue(preflight_transfer_destination(b, prepared, plan))
        self.check_document['response']['bookingrules']['issues'].append(
            {'class': 'message-warning', 'type': 'horizon', 'text': 'Booking horizon exceeded'})
        self.assertFalse(preflight_transfer_destination(b, prepared, plan))
        self.assertEqual(self.persisted, self.original)
        self.assertFalse(self.save_calls)
        self.assertFalse(self.other_mutations)
