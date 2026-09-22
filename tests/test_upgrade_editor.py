"""Real Chromium editor flow against an entirely intercepted synthetic site."""
import copy
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from playwright.sync_api import sync_playwright

import book_week as b
import mutation_receipts as receipts
from room_upgrades import Reservation, RoomUpgrade, RoomConsolidation
from upgrade_validation import (upgrade_request_matches, upgrade_response_success,
                                upgrade_save_acknowledgement_consistent, RoomPermissionRefusal)
from booking_preferences_guard import booking_preference_run
from operation_control import OperationStopped
from progressive_transactions import TransferEdit, record


def result(success=True):
    return {"response": {"success": success, "event_ids": [42], "forms": [],
                         "bookingrules": {"issues": []},
                         "save_resolution": {"uri": "/arrangement",
                                             "query_params": [{"key": "eventId", "value": 42}]}}}


class UpgradeValidationTests(unittest.TestCase):
    def setUp(self):
        self.upgrade = RoomUpgrade(Reservation(42, date(2026, 9, 21), "Fallback", 720, 840),
                                   Reservation(42, date(2026, 9, 21), "Best", 840, 960))
        self.payload = {"event": {"id": 42, "st": "2026-09-21T14:00:00+01:00",
                                  "en": "2026-09-21T16:00:00+01:00", "rs": [{"id": 1}]},
                        "booking_type": "single", "time_period_id": 0, "weekdays": [1]}
        self.request = SimpleNamespace(url="https://rwcmd.asimut.net/services/v2/event/event_id=42;type=check",
                                       method="PATCH", post_data_json=self.payload)

    def test_only_exact_single_event_room_times_and_offset_are_accepted(self):
        self.assertTrue(upgrade_request_matches(self.request, self.upgrade, 1))
        for key, value in [("id", 43), ("id", True), ("st", "2026-09-21T14:00:00Z"),
                           ("en", "2026-09-21T15:00:00+01:00"), ("rs", [{"id": 2}]),
                           ("rs", [{"id": 1}, {"id": 2}])]:
            with self.subTest(key=key, value=value):
                data = copy.deepcopy(self.payload)
                data["event"][key] = value
                self.request.post_data_json = data
                self.assertFalse(upgrade_request_matches(self.request, self.upgrade, 1))
        for key, value in [("booking_type", "weekly"), ("time_period_id", 1),
                           ("time_period_id", False), ("weekdays", [1, 1]), ("weekdays", [True]),
                           ("weekdays", []), ("weekdays", [-1])]:
            self.request.post_data_json = {**self.payload, key: value}
            self.assertFalse(upgrade_request_matches(self.request, self.upgrade, 1))

    def test_single_thursday_edit_keeps_unused_monday_recurrence_default(self):
        thursday = date(2026, 9, 17)
        upgrade = RoomUpgrade(Reservation(42, thursday, 'Fallback', 720, 840),
                              Reservation(42, thursday, 'Best', 840, 960))
        self.request.post_data_json['event'].update(st='2026-09-17T14:00:00.000+01:00',
                                                     en='2026-09-17T16:00:00.000+01:00')
        self.assertTrue(upgrade_request_matches(self.request, upgrade, 1))
        self.request.post_data_json['booking_type'] = 'weekly'
        self.assertFalse(upgrade_request_matches(self.request, upgrade, 1))

    def test_http_success_without_exact_approved_result_is_not_enough(self):
        self.assertTrue(upgrade_response_success(result(), 42))
        for key, value in [("success", False), ("success", 1), ("event_ids", [43]),
                           ("event_ids", [42, 43]), ("forms", [{"id": 1}]),
                           ("bookingrules", {"issues": [{"class": "message-warning"}]})]:
            document = result()
            document["response"][key] = value
            self.assertFalse(upgrade_response_success(document, 42))
        self.assertFalse(upgrade_response_success({**result(), "messages": {"errors": ["conflict"]}}, 42))
        self.assertFalse(upgrade_response_success({}, 42))
        document = result()
        document["response"]["event_ids"] = [True]
        self.assertFalse(upgrade_response_success(document, 1))

    def test_save_can_omit_forms_but_must_retain_explicit_success_and_identity(self):
        document = result()
        document['response'].pop('forms')
        self.assertTrue(upgrade_save_acknowledgement_consistent(document, 42))
        for field in ('success', 'event_ids', 'save_resolution', 'bookingrules'):
            missing = copy.deepcopy(document)
            missing['response'].pop(field)
            self.assertFalse(upgrade_save_acknowledgement_consistent(missing, 42))


EDITOR = r'''<!doctype html><app-event-editor><form>
<input aria-label="Event date" value="2026-09-21">
<input id="startDate" aria-label="Start time" value="12:00">
<input id="endDate" aria-label="End time" value="14:00">
<input role="combobox" aria-label="Event location" value="Fallback" id="location">
<div id="options" role="listbox" hidden><div role="option"><span aria-hidden="true">place</span><span>Best (Practice: Grand Piano)</span></div></div>
<button type="button" aria-label="Cancel event" id="cancel">Cancel event</button>
<button type="button" aria-label="Save event" id="save">Save</button>
</form></app-event-editor><script>
const start=document.getElementById('startDate'), end=document.getElementById('endDate');
const loc=document.getElementById('location'), options=document.getElementById('options');
const save=document.getElementById('save'); let locationId=2;
const payload=()=>({event:{id:42,st:`2026-09-21T${start.value}:00+01:00`,
 en:`2026-09-21T${end.value}:00+01:00`,rs:[{id:locationId}],ca:56,pe:[{id:100,ro:1}]},
 booking_type:'single',time_period_id:0,weekdays:[1]});
async function check(){save.disabled=true;let r=await fetch('/services/v2/event/event_id=42;type=check',
 {method:'PATCH',body:JSON.stringify(payload()),headers:{'Content-Type':'application/json'}});
 let data=await r.json();save.disabled=!data.response.success;}
start.addEventListener('change',check);end.addEventListener('change',check);
loc.addEventListener('input',()=>options.hidden=false);
options.firstElementChild.addEventListener('click',()=>{locationId=1;loc.value='Best (Practice: Grand Piano)';options.hidden=true;check();});
save.addEventListener('click',async()=>{let body=payload(); SAVE_DRIFT
 let r=await fetch('/services/v2/event/event_id=42;type=save',{method:'PATCH',body:JSON.stringify(body),headers:{'Content-Type':'application/json'}});
 let data=await r.json();if(data.response.success)location.href='/arrangement?eventId=42';});
document.getElementById('cancel').onclick=()=>fetch('/services/v2/event/event_id=42;type=cancel',{method:'PATCH'});
</script>'''

# Asimut preserves the current duration when changing the start. It suppresses
# checks for unchanged committed values, even when an input is filled again.
AUTOFOLLOW_TIMES = r'''
const minutes=value=>Number(value.slice(0,2))*60+Number(value.slice(3));
const clock=value=>`${String(Math.floor(value/60)).padStart(2,'0')}:${String(value%60).padStart(2,'0')}`;
let committedStart=start.value, committedEnd=end.value;
start.addEventListener('change',()=>{
 if(start.value===committedStart)return;
 end.value=clock(minutes(end.value)+minutes(start.value)-minutes(committedStart));
 committedStart=start.value;committedEnd=end.value;check();
});
end.addEventListener('change',()=>{
 if(end.value===committedEnd)return;
 committedEnd=end.value;check();
});'''


class UpgradeEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        # Fixed-date intercepted fixtures need a fixed clock, too.
        clock_patch = mock.patch.object(b, "datetime", wraps=datetime)
        clock = clock_patch.start()
        self.addCleanup(clock_patch.stop)
        clock.now.return_value = datetime(2026, 9, 21, 8, tzinfo=timezone.utc)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "receipts.json"
        self.original = Reservation(42, date(2026, 9, 21), "Fallback", 720, 840)
        self.new = Reservation(42, date(2026, 9, 21), "Best", 840, 960)
        self.upgrade = RoomUpgrade(self.original, self.new)
        self.persisted = self.original
        self.mode = "success"
        self.save_calls = []
        self.other_mutations = []
        self.context = self.browser.new_context(service_workers="block")
        self.addCleanup(self.context.close)
        self.context.route("**/*", self.site)
        self.page = self.context.new_page()
        patches = [
            mock.patch.object(b, "settings_file", self.path.with_name("settings.json")),
            mock.patch.object(b, "ACTIVE_ROOM_POLICY", SimpleNamespace(all_room_location_ids={"Best": 1})),
            mock.patch.object(b, "LIVE_CATALOG_ROOM_NAMES", ("Fallback", "Best")),
            mock.patch.object(b, "safe_goto", side_effect=lambda page, url: page.goto(url)),
            mock.patch.object(b, "safe_reload", side_effect=lambda page: page.reload()),
            mock.patch.object(b, "record_pending_upgrade", side_effect=lambda **kw: receipts.record_pending_upgrade(path=self.path, **kw)),
            mock.patch.object(b, "list_pending_mutation_receipts", side_effect=lambda: receipts.list_pending(self.path)),
            mock.patch.object(b, "resolve_mutation_receipt", side_effect=lambda rid, **kw: receipts.mark_resolved(rid, path=self.path, **kw)),
            mock.patch.object(b, "verify_mutation_receipt", side_effect=lambda rid, **kw: receipts.mark_verified(rid, path=self.path, **kw)),
            mock.patch.object(b, "remove_extendable_booking_by_event_id"),
            mock.patch.object(b, "UPGRADE_SAVE_TIMEOUT_MS", 500),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def site(self, route):
        request = route.request
        if ";type=check" in request.url:
            payload = result(self.mode not in {"check_rejected", "check_permission", "check_quota"})
            if self.mode == "check_quota":
                payload['response']['bookingrules']['issues'] = [
                    {'type':'category','class':'message-warning','text':'Requested booking exceeds your quota'}]
            if self.mode == "check_permission":
                payload["response"]["bookingrules"]["issues"] = [
                    {"class": "message-warning", "message": "You do not have permission to book this room"}]
            route.fulfill(json=payload)
        elif ";type=save" in request.url:
            self.save_calls.append(request.post_data_json)
            if self.mode in {"save_rejected", "rejected_but_original_changed", "save_permission", "permission_but_changed"}:
                if self.mode in {"rejected_but_original_changed", "permission_but_changed"}:
                    self.persisted = self.new
                payload = result(False)
                if self.mode in {"save_permission", "permission_but_changed"}:
                    payload["response"]["bookingrules"]["issues"] = [
                        {"class": "message-warning", "message": "You are not allowed to book this room"}]
                route.fulfill(json=payload)
            elif self.mode == "lost_response":
                self.persisted = self.new
                route.abort()
            else:
                if self.mode != 'compact_reply_without_change':
                    self.persisted = self.new
                payload = result()
                if self.mode == "wrong_success_id":
                    payload["response"]["event_ids"] = [99]
                if self.mode in {'compact_reply', 'compact_reply_without_change'}:
                    payload['response'].pop('forms')
                route.fulfill(json=payload)
        elif request.method != "GET":
            self.other_mutations.append(request.url)
            route.abort()
        elif "/arrangement?eventId=42" in request.url:
            p = self.persisted.as_booking()
            route.fulfill(content_type="text/html", body=f'<div data-cy="event_42"><p>{p["date"]}</p><p>{p["room"]}</p><p>{p["startTime"]} - {p["endTime"]}</p></div>')
        elif "/event?eventId=42" in request.url:
            drift = "body.event.rs[0].id=9;" if self.mode == "save_drift" else ""
            html = EDITOR.replace("SAVE_DRIFT", drift)
            if self.mode in {'sticky_time_picker', 'unknown_overlay'}:
                component = ('<app-as-timepicker-body><div aria-label="Time picker container">minutes</div></app-as-timepicker-body>'
                             if self.mode == 'sticky_time_picker' else '<div role="dialog">Unknown confirmation</div>')
                html += '''<script>for(const field of [start,end]) field.addEventListener('focus',()=>{
                  if(document.getElementById('fixture-overlay'))return;
                  let overlay=document.createElement('div');overlay.id='fixture-overlay';
                  overlay.innerHTML=`<div class="cdk-overlay-backdrop cdk-overlay-dark-backdrop cdk-overlay-backdrop-showing" style="position:fixed;inset:0;background:#0008;z-index:50"></div>
                    <div style="position:fixed;left:400px;top:200px;z-index:51;background:white">COMPONENT</div>`;
                  document.body.append(overlay);overlay.firstElementChild.onclick=()=>overlay.remove();
                });</script>'''.replace('COMPONENT', component)
            route.fulfill(content_type="text/html", body=html)
        else:
            route.fulfill(status=404, body="Synthetic fixture: no external requests")

    def run_edit(self, **kwargs):
        return b.edit_reservation_room_time(self.page, self.upgrade,
                                            revalidate=lambda: True, **kwargs)

    def stage_parent(self):
        def record(r):
            return dict(event_id=r.event_id, room=r.room, date=str(r.day),
                        start=r.as_booking()['startTime'], end=r.as_booking()['endTime'])
        anchor = Reservation(43, self.original.day, 'Weston', 660, 720)
        return receipts.record_pending_consolidation(room='Weston', booking_date=str(anchor.day),
            start='11:00', end='14:00', event_url=anchor.event_url, original=record(anchor),
            originals=[record(anchor), record(self.original)],
            bridges=[dict(original=record(self.original), replacement=record(self.new))], path=self.path)

    def test_recorded_bridge_uses_parent_and_preserves_pending_transaction(self):
        parent = self.stage_parent()
        self.assertTrue(self.run_edit(transaction_receipt=parent))
        self.assertEqual(self.persisted, self.new)
        self.assertEqual(receipts.list_pending(self.path), [parent])
        self.assertEqual(len(self.save_calls), 1)
        self.assertFalse(self.other_mutations)

    def test_rejected_bridge_save_cannot_resolve_its_whole_parent(self):
        parent = self.stage_parent()
        self.mode = 'save_rejected'
        self.assertFalse(self.run_edit(transaction_receipt=parent))
        self.assertEqual(self.persisted, self.original)
        self.assertEqual(receipts.list_pending(self.path), [parent])

    def test_lost_bridge_save_keeps_exact_parent_and_never_repeats(self):
        parent = self.stage_parent()
        self.mode = 'lost_response'
        with self.assertRaises(b.BookingVerificationError):
            self.run_edit(transaction_receipt=parent)
        self.assertEqual(self.persisted, self.new)
        self.assertEqual(receipts.list_pending(self.path), [parent])
        self.assertEqual(len(self.save_calls), 1)

    def test_parent_cannot_authorize_different_bridge_time(self):
        parent = self.stage_parent()
        self.upgrade = RoomUpgrade(self.original, Reservation(42, self.original.day, 'Best', 900, 1020))
        with self.assertRaises(b.BookingVerificationError):
            self.run_edit(transaction_receipt=parent)
        self.assertFalse(self.save_calls)

    def test_real_fields_and_network_change_one_reservation_once(self):
        self.assertTrue(self.run_edit())
        self.assertEqual(self.persisted, self.new)
        self.assertEqual(len(self.save_calls), 1)
        self.assertFalse(self.other_mutations)
        receipt, = receipts.load_journal(self.path)["receipts"].values()
        self.assertEqual(receipt["status"], "verified")
        self.assertEqual(receipt["original"]["end"], "14:00")

    def test_sticky_asimut_time_picker_is_closed_without_changing_values(self):
        self.mode = 'sticky_time_picker'
        self.assertTrue(self.run_edit())
        self.assertEqual(self.persisted, self.new)
        self.assertEqual(len(self.save_calls), 1)
        self.assertFalse(self.other_mutations)

    def test_unknown_overlay_is_never_dismissed_or_clicked_through(self):
        self.mode = 'unknown_overlay'
        self.assertFalse(self.run_edit())
        self.assertFalse(self.save_calls)
        self.assertFalse(self.other_mutations)
        self.assertFalse(self.path.exists())

    def test_dry_run_exercises_editor_but_keeps_original_and_no_receipt(self):
        self.assertFalse(self.run_edit(dry_run=True))
        self.assertEqual(self.persisted, self.original)
        self.assertFalse(self.save_calls)
        self.assertFalse(self.path.exists())

    def test_same_time_room_change_preserves_both_editor_times(self):
        self.new = Reservation(42, self.original.day, 'Best', self.original.start, self.original.end)
        self.upgrade = RoomUpgrade(self.original, self.new)
        self.assertTrue(self.run_edit())
        self.assertEqual(self.persisted, self.new)
        self.assertEqual(len(self.save_calls), 1)
        self.assertIn('T12:00:00', self.save_calls[0]['event']['st'])
        self.assertIn('T14:00:00', self.save_calls[0]['event']['en'])

    def test_live_revalidation_failure_prevents_save(self):
        self.assertFalse(b.edit_reservation_room_time(self.page, self.upgrade, revalidate=lambda: False))
        self.assertEqual(self.persisted, self.original)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.save_calls)

    def test_manual_edit_pin_blocks_queued_upgrade_at_final_save(self):
        from app_settings import save_settings
        from manual_booking_overrides import KEY
        save_settings({KEY: {'42': {k: self.original.as_booking()[k]
            for k in ('date', 'room', 'startTime', 'endTime')}}}, b.settings_file)
        with self.assertRaises(b.BookingPreferencesChanged):
            self.run_edit()
        self.assertEqual(self.persisted, self.original)
        self.assertFalse(self.save_calls)
        self.assertFalse(self.path.exists())

    def test_long_revalidation_does_not_leave_a_dirty_editor_open(self):
        def revalidate():
            self.assertIn('/arrangement?eventId=42', self.page.url)
            self.assertEqual(self.page.get_by_role('textbox', name='Start time', exact=True).count(), 0)
            return True
        self.assertTrue(b.edit_reservation_room_time(self.page, self.upgrade, revalidate=revalidate))
        self.assertEqual(len(self.save_calls), 1)

    def test_rejected_check_prevents_receipt_and_save(self):
        self.mode = "check_rejected"
        self.assertFalse(self.run_edit())
        self.assertEqual(self.persisted, self.original)
        self.assertFalse(self.save_calls)
        self.assertFalse(self.path.exists())

    def test_quota_refusal_ends_pass_before_save_and_cleans_editor(self):
        self.mode = 'check_quota'
        with self.assertRaises(b.QuotaWait):
            self.run_edit()
        self.assertEqual(self.persisted,self.original)
        self.assertFalse(self.save_calls)
        self.assertFalse(self.path.exists())
        self.assertIn('/arrangement?eventId=42',self.page.url)

    def test_rejected_save_proves_original_before_resolving(self):
        self.mode = "save_rejected"
        self.assertFalse(self.run_edit())
        self.assertEqual(self.persisted, self.original)
        self.assertEqual(len(self.save_calls), 1)
        receipt, = receipts.load_journal(self.path)["receipts"].values()
        self.assertEqual(receipt["status"], "resolved")
        self.assertFalse(self.other_mutations)

    def test_permission_denied_check_retains_original_and_allows_later_candidate(self):
        self.mode = "check_permission"
        outcome = self.run_edit()
        self.assertIsInstance(outcome, RoomPermissionRefusal)
        self.assertFalse(outcome)
        self.assertEqual(outcome.room, "Best")
        self.assertEqual(self.persisted, self.original)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.save_calls)
        self.mode = "success"
        self.assertTrue(self.run_edit())
        self.assertEqual(len(self.save_calls), 1)

    def test_permission_denied_save_proves_original_before_safe_skip(self):
        self.mode = "save_permission"
        outcome = self.run_edit()
        self.assertIsInstance(outcome, RoomPermissionRefusal)
        self.assertEqual(self.persisted, self.original)
        self.assertEqual(len(self.save_calls), 1)
        self.assertFalse(receipts.list_pending(self.path))

    def test_permission_message_cannot_hide_a_changed_original_after_save(self):
        self.mode = "permission_but_changed"
        with self.assertRaises(b.BookingVerificationError):
            self.run_edit()
        self.assertEqual(len(self.save_calls), 1)
        self.assertEqual(len(receipts.list_pending(self.path)), 1)
        self.assertFalse(self.other_mutations)

    def test_lost_save_response_never_retries_or_cancels(self):
        self.mode = "lost_response"
        with self.assertRaises(b.BookingVerificationError):
            self.run_edit()
        self.assertEqual(self.persisted, self.new)
        self.assertEqual(len(self.save_calls), 1)
        self.assertEqual(len(receipts.list_pending(self.path)), 1)
        self.assertFalse(self.other_mutations)

    def test_outgoing_wrong_room_is_blocked_before_network(self):
        self.mode = "save_drift"
        with self.assertRaises(b.BookingVerificationError):
            self.run_edit()
        self.assertFalse(self.save_calls)
        self.assertEqual(self.persisted, self.original)
        self.assertEqual(len(receipts.list_pending(self.path)), 1)

    def test_wrong_success_identity_stays_pending_despite_http_200(self):
        self.mode = "wrong_success_id"
        with self.assertRaises(b.BookingVerificationError):
            self.run_edit()
        self.assertEqual(len(self.save_calls), 1)
        self.assertEqual(len(receipts.list_pending(self.path)), 1)

    def test_compact_save_reply_requires_independent_persisted_success(self):
        self.mode = 'compact_reply'
        self.assertTrue(self.run_edit())
        self.assertEqual(self.persisted, self.new)
        self.assertEqual(len(self.save_calls), 1)
        self.assertFalse(receipts.list_pending(self.path))

    def test_compact_save_reply_without_persisted_change_stays_pending(self):
        self.mode = 'compact_reply_without_change'
        with self.assertRaises(b.BookingVerificationError):
            self.run_edit()
        self.assertEqual(self.persisted, self.original)
        self.assertEqual(len(self.save_calls), 1)
        self.assertEqual(len(receipts.list_pending(self.path)), 1)

    def test_rejection_without_intact_original_is_not_declared_safe(self):
        self.mode = "rejected_but_original_changed"
        with self.assertRaises(b.BookingVerificationError):
            self.run_edit()
        self.assertEqual(len(self.save_calls), 1)
        self.assertEqual(len(receipts.list_pending(self.path)), 1)

    def test_changed_original_stops_before_opening_editor(self):
        self.persisted = self.new
        with self.assertRaises(b.BookingVerificationError):
            self.run_edit()
        self.assertFalse(self.save_calls)
        self.assertFalse(self.path.exists())

    def test_preference_change_at_save_boundary_keeps_original(self):
        settings = Path(self.tmp.name) / 'settings.json'
        settings.write_text('{}')
        def changed_preferences():
            settings.write_text(json.dumps({'booking_strategy': {'daily_planning': {'upgrade_rooms': False}}}))
            return True
        with booking_preference_run(settings, {}), self.assertRaises(b.BookingPreferencesChanged):
            b.edit_reservation_room_time(self.page, self.upgrade, revalidate=changed_preferences)
        self.assertEqual(self.persisted, self.original)
        self.assertFalse(self.save_calls)
        self.assertFalse(self.path.exists())

    def test_stop_before_save_preserves_original_without_receipt(self):
        with mock.patch('booking_preferences_guard.check_operation_stop', side_effect=OperationStopped('Stopped')):
            with self.assertRaises(OperationStopped):
                self.run_edit()
        self.assertEqual(self.persisted, self.original)
        self.assertFalse(self.save_calls)
        self.assertFalse(self.path.exists())


class SameRoomAutofollowEditorTests(unittest.TestCase):
    setUpClass = classmethod(UpgradeEditorTests.setUpClass.__func__)
    tearDownClass = classmethod(UpgradeEditorTests.tearDownClass.__func__)
    run_edit = UpgradeEditorTests.run_edit

    def setUp(self):
        UpgradeEditorTests.setUp(self)
        self.check_calls = []
        original_mark = receipts.mark_transfer_step
        patches = [
            mock.patch.object(receipts, 'mark_transfer_step',
                side_effect=lambda parent, step: original_mark(parent, step, path=self.path)),
            mock.patch.object(b, 'ACTIVE_ROOM_POLICY',
                SimpleNamespace(all_room_location_ids={'Best': 1, 'Fallback': 2})),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def site(self, route):
        if ';type=check' in route.request.url:
            self.check_calls.append(route.request.post_data_json)
        if '/event?eventId=42' in route.request.url:
            html = EDITOR.replace('SAVE_DRIFT', '').replace(
                "start.addEventListener('change',check);end.addEventListener('change',check);", AUTOFOLLOW_TIMES)
            html = html.replace('value="12:00"', f'value="{self.original.as_booking()["startTime"]}"')
            html = html.replace('value="14:00"', f'value="{self.original.as_booking()["endTime"]}"')
            route.fulfill(content_type='text/html', body=html)
        else:
            UpgradeEditorTests.site(self, route)

    def parent(self, *, shifted=False):
        if shifted:
            self.original = Reservation(42, self.original.day, 'Fallback', 720, 780)
            self.persisted = self.original
        self.new = Reservation(42, self.original.day, 'Fallback', 780, 840)
        self.upgrade = TransferEdit(self.original, self.new)
        originals = [self.original]
        if shifted:
            originals.append(Reservation(43, self.original.day, 'Other', 960, 1020))
        prefix = Reservation(42, self.original.day, 'Best', 720, 780)
        target = Reservation(42, self.original.day, 'Best', 720, 840)
        payload = dict(plan_id=str(uuid4()), originals=[record(r) for r in originals],
            seed_before=None, remaining=[record(self.new)], replacement=record(prefix),
            target=record(target), opens_at='2026-09-15T12:00:00+00:00',
            baseline_ids=[r.event_id for r in originals], minimum_minutes=30,
            adjustment_order=[r.event_id for r in originals], started_steps=[])
        return receipts.record_pending('transfer', room='Best', booking_date=str(self.original.day),
            start='12:00', end='13:00', transfer=payload, path=self.path)

    def test_trim_start_restores_requested_end_after_automatic_duration_preservation(self):
        parent = self.parent()
        self.assertTrue(self.run_edit(transaction_receipt=parent))
        times = [(p['event']['st'][11:16], p['event']['en'][11:16]) for p in self.check_calls]
        self.assertEqual(times, [('13:00', '15:00'), ('13:00', '14:00')])
        self.assertEqual(self.persisted, self.new)
        self.assertEqual(len(self.save_calls), 1)
        self.assertEqual(self.save_calls[0], self.check_calls[-1])
        self.assertEqual(parent['transfer']['started_steps'], ['source:42'])
        self.assertEqual(receipts.list_pending(self.path), [parent])
        self.assertFalse(self.other_mutations)

    def test_same_duration_shift_listens_before_start_automatically_sets_exact_end(self):
        parent = self.parent(shifted=True)
        self.assertTrue(self.run_edit(transaction_receipt=parent))
        times = [(p['event']['st'][11:16], p['event']['en'][11:16]) for p in self.check_calls]
        self.assertEqual(times, [('13:00', '14:00')])
        self.assertEqual(self.persisted, self.new)
        self.assertEqual(len(self.save_calls), 1)
        self.assertEqual(self.save_calls[0], self.check_calls[-1])
        self.assertEqual(parent['transfer']['started_steps'], ['source:42'])
        self.assertEqual(receipts.list_pending(self.path), [parent])
        self.assertFalse(self.other_mutations)


class ConsolidationEditorTests(unittest.TestCase):
    setUpClass = classmethod(UpgradeEditorTests.setUpClass.__func__)
    tearDownClass = classmethod(UpgradeEditorTests.tearDownClass.__func__)
    run_edit = UpgradeEditorTests.run_edit

    def setUp(self):
        UpgradeEditorTests.setUp(self)
        self.original = Reservation(42, date(2026, 9, 21), 'Fallback', 720, 750)
        self.new = Reservation(42, self.original.day, 'Best', 720, 840)
        self.donors = (Reservation(43, self.original.day, 'Fallback', 750, 810),
                       Reservation(44, self.original.day, 'Fallback', 810, 840))
        self.persisted = self.original
        self.upgrade = RoomConsolidation((self.original, *self.donors), self.new)
        patch = mock.patch.object(b, 'record_pending_consolidation',
                                 side_effect=lambda **kw: receipts.record_pending_consolidation(path=self.path, **kw))
        patch.start()
        self.addCleanup(patch.stop)

    def site(self, route):
        if '/event?eventId=42' in route.request.url:
            html = EDITOR.replace('SAVE_DRIFT', '')
            html = html.replace('value="12:00"', f'value="{self.original.as_booking()["startTime"]}"')
            html = html.replace('value="14:00"', f'value="{self.original.as_booking()["endTime"]}"')
            if self.original.room == 'Best':
                html = html.replace('value="Fallback"', 'value="Best"').replace('let locationId=2', 'let locationId=1')
            route.fulfill(content_type='text/html', body=html)
            return
        for donor in getattr(self, 'donors', ()):
            if f'/arrangement?eventId={donor.event_id}' in route.request.url:
                p = donor.as_booking()
                route.fulfill(content_type='text/html', body=f'<div data-cy="event_{donor.event_id}"><p>{p["date"]}</p><p>{p["room"]}</p><p>{p["startTime"]} - {p["endTime"]}</p></div>')
                return
        UpgradeEditorTests.site(self, route)

    def test_longer_anchor_is_secured_once_and_all_donors_remain_protected(self):
        self.assertTrue(self.run_edit())
        self.assertEqual(self.persisted, self.new)
        self.assertEqual(len(self.save_calls), 1)
        receipt, = receipts.list_pending(self.path)
        self.assertEqual(receipt['kind'], 'consolidation')
        self.assertEqual(len(receipt['originals']), 3)
        self.assertFalse(self.other_mutations)

    def test_same_room_expansion_triggers_fresh_check_after_revalidation(self):
        self.original = Reservation(42, self.original.day, 'Best', 720, 750)
        self.persisted = self.original
        self.upgrade = RoomConsolidation((self.original, *self.donors), self.new)
        self.assertTrue(self.run_edit())
        self.assertEqual(len(self.save_calls), 1)
        self.assertFalse(self.other_mutations)

    def test_same_room_earlier_start_with_unchanged_end_triggers_fresh_check(self):
        self.original = Reservation(42, self.original.day, 'Best', 780, 840)
        self.donors = (Reservation(43, self.original.day, 'Fallback', 720, 750),
                       Reservation(44, self.original.day, 'Fallback', 750, 780))
        self.persisted = self.original
        self.upgrade = RoomConsolidation((*self.donors, self.original), self.new)
        self.assertTrue(self.run_edit())
        self.assertEqual(len(self.save_calls), 1)

    def test_server_quota_rejection_keeps_all_originals(self):
        self.mode = 'check_rejected'
        self.assertFalse(self.run_edit())
        self.assertEqual(self.persisted, self.original)
        self.assertFalse(self.save_calls)
        self.assertFalse(self.other_mutations)
        self.assertFalse(self.path.exists())

    def test_lost_anchor_save_response_keeps_donors_and_blocks_further_mutation(self):
        self.mode = 'lost_response'
        with self.assertRaises(b.BookingVerificationError):
            self.run_edit()
        self.assertEqual(self.persisted, self.new)
        self.assertEqual(len(receipts.list_pending(self.path)), 1)
        self.assertFalse(self.other_mutations)

    def test_preview_preserves_anchor_donors_and_journal(self):
        self.assertFalse(self.run_edit(dry_run=True))
        self.assertEqual(self.persisted, self.original)
        self.assertFalse(self.save_calls)
        self.assertFalse(self.other_mutations)
        self.assertFalse(self.path.exists())
