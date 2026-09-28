"""Synthetic coordination contracts; these tests never contact ASIMUT."""
import contextlib
import copy
from datetime import date, datetime, timedelta, timezone
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest import mock

from app_settings import atomic_write_json
import book_week as booker
from booking_preferences_guard import booking_preference_run, booking_save_boundary, BookingPreferencesChanged
import tempo_coordination as tempo
from room_upgrade_runtime import tracker_for_events, planning_events
from booking_plan import booking_plan_fingerprint


NOW = datetime(2026, 9, 28, 8, tzinfo=timezone.utc)
DAY = date(2026, 9, 29)


def document(**changes):
    value = dict(version=1, instance_id="test-tempo", revision=1, enabled=True,
        generated_at=NOW.isoformat(), valid_until=(NOW + timedelta(hours=24)).isoformat(),
        dates=[DAY.isoformat()], busy=[dict(date=str(DAY), start="11:00", end="11:30")],
        windows=[dict(date=str(DAY), start="09:00", end="12:00", rooms=["Practice A"],
                      task_id="practice", block_id="practice-tuesday")], protected_event_ids=[42])
    value.update(changes)
    return value


class TempoCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        folder = self.stack.enter_context(TemporaryDirectory())
        self.path = Path(folder) / "tempo" / "coordination.json"
        self.settings = Path(folder) / "settings.json"
        self.stack.enter_context(mock.patch.object(tempo, "SNAPSHOT_PATH", self.path))
        self.stack.enter_context(mock.patch.object(tempo, "RUNTIME_LOCK_PATH", Path(folder) / "runtime.lock"))
        self.stack.enter_context(mock.patch.object(tempo, "_now", side_effect=lambda value=None: value or NOW))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def publish(self, value=None, **kwargs):
        return tempo.publish_snapshot(value or document(), path=self.path, now=NOW, **kwargs)

    def test_absent_integration_is_fileless_and_standalone(self):
        snapshot = tempo.read_snapshot(now=NOW)
        self.assertFalse(snapshot.enabled)
        self.assertTrue(snapshot.permits(DAY, 10, 12, "Anywhere"))
        with booking_save_boundary(day=DAY, start="10:00", end="12:00", room="Anywhere"):
            pass
        self.assertFalse(self.path.parent.exists())

    def test_missing_practice_window_explains_why_booking_is_blocked(self):
        self.publish(document(windows=[]))
        snapshot = tempo.read_snapshot(now=NOW)
        self.assertIn("no accepted practice window", snapshot.planning_blocker(DAY))
        self.assertEqual(snapshot.planning_blocker(DAY + timedelta(days=1)), "")
        self.assertFalse(snapshot.permits(DAY, 13, 14.5, "Practice A"))
        with tempo.coordination_run(self.path, now=NOW):
            plan = booker.build_display_day_plan(DAY, [], booker.BookingTracker(),
                booker.DailyPlanningPreferences(), now=NOW.replace(tzinfo=None),
                target_minutes=240, free_horizon_only=True)
        self.assertIn("no accepted practice window", plan.reason)
        import advance_runtime
        from advance_planner import AdvanceDay, AdvanceAllocation
        day = AdvanceDay(DAY, 240, 0, 0, (), 60, 0, 0, (), 360)
        allocation = AdvanceAllocation(DAY, ())
        with tempo.coordination_run(self.path, now=NOW), mock.patch.object(booker, 'publish_booking_plan') as publish:
            advance_runtime.publish(booker, [day], [allocation], {}, mock.Mock(), {},
                booker.BookingTracker(), now=NOW.replace(tzinfo=None))
        self.assertIn("no accepted practice window", publish.call_args.args[0][0].reason)

    def test_opportunity_time_keeps_an_unobserved_date_bookable_without_a_selected_session(self):
        self.publish(document(windows=[], opportunity_windows=[
            dict(date=str(DAY), start="09:00", end="18:00", rooms=[])]))
        snapshot = tempo.read_snapshot(now=NOW)
        self.assertEqual(snapshot.planning_blocker(DAY), "")
        self.assertTrue(snapshot.permits(DAY, 13, 15, "Practice B"))
        self.assertFalse(snapshot.permits(DAY, 10.5, 11.5, "Practice B"))
        self.assertFalse(snapshot.permits(DAY, 18, 19, "Practice B"))
        with tempo.coordination_run(now=NOW):
            tracker = booker.BookingTracker()
            self.assertTrue(tracker.can_book("Practice B", DAY, 13, 60)[0])
            self.assertFalse(tracker.can_book("Practice B", DAY, 11, 30)[0])
            self.assertEqual(tracker.get_total_booking_hours(), 0)
            with booking_save_boundary(day=DAY, start="13:00", end="14:00", room="Practice B"):
                pass

    def test_opportunities_do_not_replace_selected_task_room_or_merge_adjacent_tasks(self):
        self.publish(document(busy=[], windows=[
            dict(date=str(DAY), start="10:00", end="11:00", rooms=["Practice A"]),
            dict(date=str(DAY), start="11:00", end="12:00", rooms=["Practice A"])],
            opportunity_windows=[dict(date=str(DAY), start="09:00", end="15:00", rooms=[])]))
        snapshot = tempo.read_snapshot(now=NOW)
        self.assertTrue(snapshot.permits(DAY, 9, 10, "Practice B"))
        self.assertTrue(snapshot.permits(DAY, 12, 14, "Practice B"))
        self.assertTrue(snapshot.permits(DAY, 10, 11, "Practice A"))
        for start, end, room in ((10, 11, "Practice B"), (10, 12, "Practice A"),
                                 (9, 11, "Practice A"), (11, 13, "Practice A")):
            self.assertFalse(snapshot.permits(DAY, start, end, room))
        self.assertEqual(snapshot.allowed_end(DAY, 9, 12, "Practice A"), 10)
        self.assertEqual(snapshot.allowed_end(DAY, 10, 12, "Practice A"), 11)

    def test_opportunity_room_scope_upgrade_gaps_and_whole_extension_stay_safe(self):
        self.publish(document(windows=[], opportunity_windows=[
            dict(date=str(DAY), start="09:00", end="15:00", rooms=["Practice B"])]))
        snapshot = tempo.read_snapshot(now=NOW)
        raw = [{"room": room, "slots": [{"startHour": 8, "endHour": 18}]}
               for room in ("Practice A", "Practice B")]
        values = snapshot.constrain_availability(raw, DAY)
        self.assertEqual(values[0]["slots"], [])
        self.assertEqual(values[1]["slots"], [{"startHour": 9, "endHour": 11},
                                              {"startHour": 11.5, "endHour": 15}])
        self.assertEqual(snapshot.allowed_end(DAY, 9, 16, "Practice B"), 11)
        self.assertEqual(snapshot.allowed_end(DAY, 11.5, 16, "Practice B"), 15)
        self.assertEqual(snapshot.allowed_end(DAY, 8.5, 10, "Practice B"), 8.5)
        with tempo.coordination_run(now=NOW):
            for start, end, room in (("08:30", "10:00", "Practice B"),
                    ("10:30", "11:45", "Practice B"), ("14:30", "15:30", "Practice B"),
                    ("13:00", "14:00", "Practice A")):
                with self.subTest(start=start, end=end, room=room), self.assertRaises(BookingPreferencesChanged), \
                        booking_save_boundary(day=DAY, start=start, end=end, room=room):
                    self.fail("Alternatives cannot bypass complete-interval or room checks")

    def test_same_day_opportunity_advances_with_free_horizon_without_bypassing_quota(self):
        today = NOW.date()
        self.publish(document(dates=[str(today)], busy=[], windows=[], protected_event_ids=[],
            opportunity_windows=[dict(date=str(today), start="12:00", end="16:00", rooms=[])]))
        with tempo.coordination_run(now=NOW), mock.patch.multiple(booker,
                FREE_HORIZON_MINUTES=300, FREE_HORIZON_OVERRIDES_PEAK=True):
            tracker = booker.BookingTracker()
            tracker.live_quota_minutes = 0
            tracker.quota_observed_hours = 0
            clock = NOW.replace(tzinfo=None)
            self.assertFalse(tracker.can_book("Practice A", today, 12.5, 30,
                                             now=clock - timedelta(seconds=1))[0])
            self.assertTrue(tracker.can_book("Practice A", today, 12.5, 30, now=clock)[0])
            self.assertFalse(tracker.can_book("Practice A", today, 12.5, 45, now=clock)[0])
            self.assertTrue(tracker.can_book("Practice A", today, 12.5, 45,
                                            now=clock + timedelta(minutes=15))[0])
            self.assertFalse(tracker.can_book("Practice A", today, 15.5, 60,
                                             now=clock + timedelta(hours=4))[0])

    def test_fresh_room_gaps_can_produce_a_ready_plan_even_without_selected_windows(self):
        today = NOW.date()
        self.publish(document(dates=[str(today)], busy=[
            dict(date=str(today), start="11:00", end="12:00")], windows=[],
            opportunity_windows=[dict(date=str(today), start="09:00", end="18:00", rooms=[])]))
        with tempo.coordination_run(now=NOW), mock.patch.multiple(booker,
                FREE_HORIZON_MINUTES=300, FREE_HORIZON_OVERRIDES_PEAK=True,
                PRIORITY_ROOMS=["Practice A"], ALLOW_FRAGMENTED_SESSIONS=True), \
                mock.patch.object(booker, "room_horizon_minutes", return_value=7200):
            tracker = booker.BookingTracker()
            tracker.live_quota_minutes = 0
            tracker.quota_observed_hours = 0
            planning = booker.DailyPlanningPreferences()
            gaps = [{"room": "Practice A", "slots": [{"startHour": 9, "endHour": 12}]}]
            opportunities = booker.build_day_booking_opportunities(gaps, today, tracker,
                {"enabled": False}, planning, now=NOW.replace(tzinfo=None),
                remaining_daily_hours=2, free_horizon_only=True, include_free_horizon_intent=True)
            plan = booker.build_display_day_plan(today, opportunities, tracker, planning,
                now=NOW.replace(tzinfo=None), target_minutes=120, free_horizon_only=True)
        self.assertIsNotNone(plan.primary)
        self.assertEqual((plan.primary.start_time, plan.primary.end_time), ("09:00", "11:00"))
        self.assertEqual(plan.primary.state, "ready")
        self.assertTrue(all(item.end_minutes <= 11 * 60 for item in opportunities))

    def test_opportunities_never_add_hours_to_the_saved_practice_goal(self):
        self.publish(document(windows=[], busy=[], opportunity_windows=[
            dict(date=str(DAY), start="00:00", end="24:00", rooms=[])]))
        snapshot = tempo.read_snapshot(now=NOW)
        plan = booker.PracticePlan(enabled=True, default_hours=3)
        self.assertEqual(snapshot.practice_plan_overlay(plan, {}).target_for(DAY), 3)
        self.assertEqual(snapshot.practice_plan_overlay(plan, {str(DAY): [(9, 10, "Practice A")]}).target_for(DAY), 3)
        self.assertIsNone(plan.date_overrides)
        disabled = booker.PracticePlan(enabled=False, default_hours=3)
        self.assertIs(snapshot.practice_plan_overlay(disabled, {}), disabled)

    def test_opportunity_validation_is_bounded_and_old_documents_unchanged(self):
        self.assertNotIn("opportunity_windows", tempo.validate_snapshot(document()))
        valid = dict(date=str(DAY), start="09:00", end="12:00", rooms=[])
        for alternative in (None, "all day", [valid] * 501,
                [{**valid, "date": "2026-09-30"}], [{**valid, "start": "12:00"}],
                [{**valid, "rooms": "Practice A"}], [{**valid, "rooms": [None]}]):
            with self.subTest(alternative=alternative), self.assertRaises(tempo.CoordinationError):
                tempo.validate_snapshot(document(opportunity_windows=alternative))

    def test_opportunity_revision_and_preferences_change_still_prevent_prepared_save(self):
        value = document(windows=[], opportunity_windows=[
            dict(date=str(DAY), start="13:00", end="15:00", rooms=[])])
        atomic_write_json(self.settings, {"practice_plan": {"default_hours": 3}})
        self.publish(value)
        with booking_preference_run(self.settings, {"practice_plan": {"default_hours": 3}}):
            atomic_write_json(self.settings, {"practice_plan": {"default_hours": 2}})
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary(
                    day=DAY, start="13:00", end="14:00", room="Practice B"):
                self.fail("A changed goal must invalidate an already prepared form")
        with tempo.coordination_run(now=NOW):
            self.publish({**value, "revision": 2, "opportunity_windows": []})
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary(
                    day=DAY, start="13:00", end="14:00", room="Practice B"):
                self.fail("A changed alternative must invalidate an already prepared form")

    def test_opportunities_do_not_bypass_stale_scope_or_protected_reservations(self):
        self.publish(document(windows=[], opportunity_windows=[
            dict(date=str(DAY), start="09:00", end="18:00", rooms=[])]))
        with tempo.coordination_run(now=NOW):
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary(
                    day=DAY, start="13:00", end="14:00", room="Practice A", event_ids=[42]):
                self.fail("Protected reservations stay fixed")
            self.path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary(
                    day=DAY, start="13:00", end="14:00", room="Practice A"):
                self.fail("Corrupt snapshots cannot fall back to spare time")
        snapshot = tempo.read_snapshot(now=NOW)
        self.assertTrue(snapshot.problem)
        self.assertEqual(snapshot.blocked_ranges(DAY), [(0, 24)])
        # Repair only this synthetic publication before exercising real expiry.
        self.publish(document(windows=[], opportunity_windows=[
            dict(date=str(DAY), start="09:00", end="18:00", rooms=[])]))
        expired = tempo.read_snapshot(now=NOW + timedelta(days=2))
        self.assertIn("expired", expired.planning_blocker(DAY))
        self.assertFalse(expired.permits(DAY, 13, 14, "Practice A"))

    def test_released_opportunities_restore_the_same_standalone_behavior(self):
        self.publish(document(opportunity_windows=[
            dict(date=str(DAY), start="13:00", end="15:00", rooms=[])]))
        self.publish(document(revision=2, enabled=False, dates=[], busy=[], windows=[],
                              opportunity_windows=[], protected_event_ids=[]))
        with tempo.coordination_run(now=NOW):
            snapshot = tempo.current_snapshot()
            self.assertFalse(snapshot.enabled)
            self.assertTrue(snapshot.permits(DAY, 8, 10, "Practice B"))
            self.assertEqual(snapshot.blocked_ranges(DAY), [])
            with booking_save_boundary(day=DAY, start="08:00", end="10:00", room="Practice B"):
                pass

    def test_alternative_booking_does_not_double_the_target_on_the_next_run(self):
        self.publish(document(busy=[], windows=[
            dict(date=str(DAY), start="16:00", end="18:00", rooms=["Practice A"])],
            opportunity_windows=[dict(date=str(DAY), start="09:00", end="16:00", rooms=[])],
            practice_targets=[dict(date=str(DAY), minutes=120)]))
        snapshot = tempo.read_snapshot(now=NOW)
        saved = booker.PracticePlan(enabled=True, default_hours=2)
        before = snapshot.practice_plan_overlay(saved, {})
        after = snapshot.practice_plan_overlay(saved, {str(DAY): [(10, 12, "Practice B")]})
        self.assertEqual((before.target_for(DAY), after.target_for(DAY)), (2, 2))
        self.assertIsNone(saved.date_overrides)
        # More demand from a named task can exceed the saved default without
        # subsequent confirmed prefixes continually increasing the total.
        self.publish({**snapshot.document, "revision": 2,
                      "practice_targets": [dict(date=str(DAY), minutes=240)]})
        snapshot = tempo.read_snapshot(now=NOW)
        for reservations in ({}, {str(DAY): [(10, 12, "Practice B")]},
                             {str(DAY): [(10, 12, "Practice B"), (16, 18, "Practice A")]}):
            self.assertEqual(snapshot.practice_plan_overlay(saved, reservations).target_for(DAY), 4)

    def test_explicit_targets_keep_goal_off_higher_saved_goal_and_legacy_days(self):
        self.publish(document(practice_targets=[dict(date=str(DAY), minutes=0)]))
        snapshot = tempo.read_snapshot(now=NOW)
        saved = booker.PracticePlan(enabled=True, default_hours=6)
        self.assertEqual(snapshot.practice_plan_overlay(saved, {}).target_for(DAY), 6)
        disabled = booker.PracticePlan(enabled=False, default_hours=6)
        self.assertIs(snapshot.practice_plan_overlay(disabled, {}), disabled)
        # Missing targets retain the exact older contract for older publishers.
        self.publish(document(revision=2, practice_targets=[]))
        low_goal = booker.PracticePlan(enabled=True, default_hours=2)
        self.assertEqual(tempo.read_snapshot(now=NOW).practice_plan_overlay(low_goal, {}).target_for(DAY), 3)

    def test_explicit_targets_validate_scope_unique_dates_integer_minutes_and_bound(self):
        self.assertNotIn("practice_targets", tempo.validate_snapshot(document()))
        valid = dict(date=str(DAY), minutes=120)
        for targets in (None, {str(DAY): 120}, [valid, valid], [valid] * 36,
                [{**valid, "date": "2026-09-30"}], [{**valid, "minutes": True}],
                [{**valid, "minutes": -1}], [{**valid, "minutes": 1441}],
                [{**valid, "minutes": 120.5}], [{**valid, "minutes": float('inf')}],
                [{**valid, "minutes": "120"}], [None]):
            with self.subTest(targets=targets), self.assertRaises(tempo.CoordinationError):
                tempo.validate_snapshot(document(practice_targets=targets))

    def test_completed_practice_credit_reduces_room_target_once_across_successive_passes(self):
        self.publish(document(practice_targets=[dict(date=str(DAY), minutes=240, completed_minutes=60)]))
        snapshot = tempo.read_snapshot(now=NOW)
        saved = booker.PracticePlan(enabled=True, default_hours=4)
        for booked, expected_remaining in ((2.5, .5), (3, 0), (3, 0)):
            reservations = {str(DAY): [(9, 9 + booked, "Practice A")]}
            overlaid = snapshot.practice_plan_overlay(saved, reservations)
            self.assertEqual(overlaid.target_for(DAY), 3)
            self.assertEqual(booker.remaining_target_hours(overlaid, DAY, booked), expected_remaining)
        self.assertEqual(saved.target_for(DAY), 4)
        self.assertIsNone(saved.date_overrides)

    def test_completed_practice_credit_keeps_named_demand_and_validates_bounds(self):
        valid = dict(date=str(DAY), minutes=300, completed_minutes=60)
        self.publish(document(practice_targets=[valid]))
        snapshot = tempo.read_snapshot(now=NOW)
        saved = booker.PracticePlan(enabled=True, default_hours=4)
        self.assertEqual(snapshot.practice_plan_overlay(saved, {}).target_for(DAY), 4)
        for credit in (True, -1, 301, 60.5, "60", None, float('inf')):
            with self.subTest(credit=credit), self.assertRaises(tempo.CoordinationError):
                tempo.validate_snapshot(document(practice_targets=[valid | dict(completed_minutes=credit)]))
        self.publish(document(revision=2, practice_targets=[dict(date=str(DAY), minutes=240, completed_minutes=240)]))
        self.assertEqual(tempo.read_snapshot(now=NOW).practice_plan_overlay(saved, {}).target_for(DAY), 0)

    def room_upgrade_document(self, **changes):
        value = document(busy=[], windows=[], room_upgrades=[
            dict(event_id=42, date=str(DAY), start="13:00", end="15:00",
                 from_room="Practice A", rooms=["Practice B"])])
        value.update(changes)
        return value

    def room_upgrade_original(self, **changes):
        return dict(eventId=42, date=str(DAY), startTime="13:00", endTime="15:00", room="Practice A") | changes

    def test_exact_room_upgrade_permission_does_not_open_general_booking_or_extension(self):
        self.publish(self.room_upgrade_document())
        snapshot = tempo.read_snapshot(now=NOW)
        original = self.room_upgrade_original()
        replacement = original | dict(room="Practice B")
        self.assertTrue(snapshot.allows_room_upgrade(original, replacement))
        self.assertEqual(snapshot.protected_ids(), {42})
        self.assertFalse(snapshot.permits(DAY, 13, 15, "Practice B"))
        self.assertEqual(snapshot.allowed_end(DAY, 13, 16, "Practice A"), 13)
        view = snapshot.room_upgrade_view(original)
        self.assertTrue(view.permits(DAY, 13, 15, "Practice B"))
        self.assertFalse(view.permits(DAY, 13, 15, "Practice C"))
        self.assertEqual(view.protected_ids(), set())
        self.assertEqual(snapshot.protected_ids(), {42})
        with tempo.coordination_run(now=NOW):
            with booking_save_boundary(day=DAY, start="13:00", end="15:00", room="Practice B",
                    event_ids=[42], room_upgrade_original=original):
                pass
            for args in (dict(day=DAY, start="13:00", end="15:00", room="Practice B"),
                         dict(event_ids=[42]),
                         dict(day=DAY, start="13:00", end="15:00", room="Practice B", event_ids=[42])):
                with self.subTest(args=args), self.assertRaises(BookingPreferencesChanged), booking_save_boundary(**args):
                    self.fail("Upgrade permission cannot authorize create, cancel or an unspecified edit")

    def test_room_upgrade_rejects_shift_extension_foreign_identity_room_and_day(self):
        self.publish(self.room_upgrade_document())
        original = self.room_upgrade_original()
        snapshot = tempo.read_snapshot(now=NOW)
        replacements = [dict(startTime="13:15", endTime="15:15"), dict(endTime="16:00"),
                        dict(eventId=43), dict(room="Practice C"), dict(room="Practice A"),
                        dict(date=str(DAY + timedelta(days=1)))]
        with tempo.coordination_run(now=NOW):
            for change in replacements:
                replacement = original | dict(room="Practice B") | change
                self.assertFalse(snapshot.allows_room_upgrade(original, replacement))
                with self.subTest(change=change), self.assertRaises(BookingPreferencesChanged), booking_save_boundary(
                        day=replacement['date'], start=replacement['startTime'], end=replacement['endTime'],
                        room=replacement['room'], event_ids=[replacement['eventId']], room_upgrade_original=original):
                    self.fail("Only the exact accepted fixed-time room improvement is allowed")
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary(day=DAY,
                    start="13:00", end="15:00", room="Practice B", event_ids=[42, 43], room_upgrade_original=original):
                self.fail("One-room permission cannot authorize consolidation")
        for change in (dict(room="Other"), dict(startTime="12:00"), dict(eventId=43)):
            wrong_original = original | change
            self.assertIs(snapshot.room_upgrade_view(wrong_original), snapshot)
            self.assertFalse(snapshot.allows_room_upgrade(wrong_original, original | dict(room="Practice B")))

    def test_room_upgrade_permission_obeys_busy_expiry_and_revision(self):
        original = self.room_upgrade_original()
        self.publish(self.room_upgrade_document(busy=[dict(date=str(DAY), start="14:00", end="15:00")]))
        snapshot = tempo.read_snapshot(now=NOW)
        self.assertFalse(snapshot.allows_room_upgrade(original, original | dict(room="Practice B")))
        self.publish(self.room_upgrade_document(revision=2))
        with tempo.coordination_run(now=NOW):
            self.publish(self.room_upgrade_document(revision=3, room_upgrades=[]))
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary(day=DAY,
                    start="13:00", end="15:00", room="Practice B", event_ids=[42], room_upgrade_original=original):
                self.fail("Changed room authority must stop a prepared edit")
        self.publish(self.room_upgrade_document(revision=4))
        stale = tempo.read_snapshot(now=NOW + timedelta(days=2))
        self.assertFalse(stale.allows_room_upgrade(original, original | dict(room="Practice B")))
        self.assertIs(stale.room_upgrade_view(original), stale)

    def test_room_upgrade_permission_is_explicit_bounded_and_old_publishers_stay_protected(self):
        self.assertNotIn("room_upgrades", tempo.validate_snapshot(document()))
        self.publish(document())
        snapshot = tempo.read_snapshot(now=NOW)
        original = self.room_upgrade_original()
        self.assertFalse(snapshot.allows_room_upgrade(original, original | dict(room="Practice B")))
        valid = self.room_upgrade_document()['room_upgrades'][0]
        for permissions in (None, {}, [None], [valid, valid], [valid] * 2001,
                [valid | dict(event_id=True)], [valid | dict(event_id=43)],
                [valid | dict(date=str(DAY + timedelta(days=1)))], [valid | dict(start="15:00")],
                [valid | dict(end="24:00")], [valid | dict(from_room="")], [valid | dict(rooms=[])],
                [valid | dict(rooms="Practice B")], [valid | dict(rooms=[None])]):
            with self.subTest(permissions=permissions), self.assertRaises(tempo.CoordinationError):
                tempo.validate_snapshot(document(room_upgrades=permissions))

    def room_extension_document(self, **changes):
        value = document(busy=[], windows=[], room_extensions=[
            dict(event_id=42, date=str(DAY), start="13:00", end="13:30",
                 room="Practice A", target_end="15:00")])
        value.update(changes)
        return value

    def room_extension_original(self, **changes):
        return dict(eventId=42, date=str(DAY), startTime="13:00", endTime="13:30",
                    room="Practice A", target_end="15:00") | changes

    def test_exact_prefix_permission_allows_successive_steps_but_not_general_booking(self):
        self.publish(self.room_extension_document())
        snapshot = tempo.read_snapshot(now=NOW)
        original = self.room_extension_original()
        self.assertFalse(snapshot.permits(DAY, 13, 15, "Practice A"))
        self.assertEqual(snapshot.protected_ids(), {42})
        self.assertEqual(snapshot.extension_end(original, 16), 15)
        with tempo.coordination_run(now=NOW):
            for old, new in (("13:30", "13:45"), ("13:45", "14:00"), ("14:00", "15:00")):
                current = original | dict(endTime=old)
                with booking_save_boundary(day=DAY, start="13:00", end=new, room="Practice A",
                        event_ids=[42], room_extension_original=current):
                    pass
            for args in (dict(event_ids=[42]), dict(day=DAY, start="13:00", end="14:00", room="Practice A"),
                         dict(day=DAY, start="13:00", end="14:00", room="Practice A", event_ids=[42])):
                with self.subTest(args=args), self.assertRaises(BookingPreferencesChanged), booking_save_boundary(**args):
                    self.fail("Extension authority cannot authorize create/cancel or an unspecified edit")

    def test_prefix_permission_never_changes_start_room_date_or_saved_target(self):
        self.publish(self.room_extension_document())
        original = self.room_extension_original()
        with tempo.coordination_run(now=NOW):
            for change in (dict(start="12:45"), dict(end="15:15"), dict(end="13:30"),
                           dict(room="Practice B"), dict(day=DAY + timedelta(days=1)), dict(event_ids=[43])):
                args = dict(day=DAY, start="13:00", end="14:00", room="Practice A", event_ids=[42]) | change
                with self.subTest(change=change), self.assertRaises(BookingPreferencesChanged), booking_save_boundary(
                        **args, room_extension_original=original):
                    self.fail("Only growth of the exact original's end is authorized")
            for change in (dict(endTime="13:15"), dict(startTime="12:45"), dict(room="Practice B"),
                           dict(target_end="13:45")):
                with self.subTest(change=change), self.assertRaises(BookingPreferencesChanged), booking_save_boundary(
                        day=DAY, start="13:00", end="14:00", room="Practice A", event_ids=[42],
                        room_extension_original=original | change):
                    self.fail("A changed original or shorter saved target must remain authoritative")

    def test_prefix_view_replaces_only_installed_tempo_conflicts_and_keeps_actual_events(self):
        self.publish(self.room_extension_document(busy=[dict(date=str(DAY), start="14:30", end="15:30")]))
        snapshot = tempo.read_snapshot(now=NOW)
        original = self.room_extension_original()
        base = snapshot.blocked_ranges(DAY)
        actual = [(13, 13.5), (14, 14.25), (0, 24)]
        adjusted = snapshot.extension_conflicts(original, [*base, *actual])
        self.assertEqual(adjusted[-len(actual):], actual)
        self.assertEqual(snapshot.extension_end(original, 16), 14.5)
        self.assertFalse(snapshot.allows_room_extension(original, original | dict(endTime="15:00")))
        self.assertEqual(snapshot.extension_conflicts(original, actual), actual)

    def test_prefix_extension_survives_full_quota_as_each_free_horizon_step_opens(self):
        today = NOW.date()
        value = self.room_extension_document(dates=[str(today)])
        value['room_extensions'][0]['date'] = str(today)
        self.publish(value)
        booking = self.room_extension_original(date=str(today), created_at=NOW.isoformat(),
                    event_url='https://rwcmd.asimut.net/arrangement?eventId=42')
        with tempo.coordination_run(now=NOW), mock.patch.multiple(booker,
                FREE_HORIZON_MINUTES=300, FREE_HORIZON_OVERRIDES_PEAK=True,
                PRIORITY_ROOMS=['Practice A'], BOOKING_WINDOW_DATES=(today,)), \
                mock.patch.object(booker, 'room_horizon_minutes', return_value=7200), \
                mock.patch.object(booker, 'refresh_quota_balances'), \
                mock.patch.object(booker, 'update_extendable_booking_end_time'), \
                mock.patch.object(booker, 'edit_reservation_end_time', return_value=True) as edit:
            for old, hour, minute, expected in (("13:30", 9, 0, "14:00"), ("14:00", 9, 15, "14:15")):
                current = booking | dict(endTime=old)
                tracker = booker.BookingTracker()
                tracker.add_existing_event(today, 13, tempo._minutes(old) / 60, is_reservation=True, room='Practice A')
                tracker.live_quota_minutes = 0
                tracker.quota_observed_hours = tracker.get_total_booking_hours()
                holds, _, held = booker.calculate_extension_capacity_holds([current], tracker,
                    booker.PracticePlan(enabled=True, default_hours=3), set(), time_prefs={'enabled': False},
                    now=NOW.replace(tzinfo=None, hour=hour, minute=minute))
                self.assertEqual(holds[str(today)], 15 * 60 - tempo._minutes(old))
                self.assertEqual(held[0]['target_end'], '15:00')
                success, end, reason = booker.try_extend_booking(object(), current, tracker,
                    remaining_daily_hours=2, time_prefs={'enabled': False},
                    now=NOW.replace(tzinfo=None, hour=hour, minute=minute))
                self.assertTrue(success, reason)
                self.assertEqual(end, expected)
            self.assertEqual(edit.call_count, 2)

    def test_prefix_permission_validation_expiry_and_changed_revision_fail_closed(self):
        original = self.room_extension_original()
        valid = self.room_extension_document()['room_extensions'][0]
        self.assertNotIn('room_extensions', tempo.validate_snapshot(document()))
        for changes in (dict(event_id=True), dict(event_id=43), dict(date='2026-09-30'),
                        dict(start='13:30'), dict(target_end='13:30'), dict(target_end='24:00'), dict(room='')):
            with self.subTest(changes=changes), self.assertRaises(tempo.CoordinationError):
                tempo.validate_snapshot(document(room_extensions=[valid | changes]))
        for entries in (None, {}, [None], [valid, valid], [valid] * 2001):
            with self.subTest(entries=entries), self.assertRaises(tempo.CoordinationError):
                tempo.validate_snapshot(document(room_extensions=entries))
        self.publish(self.room_extension_document())
        with tempo.coordination_run(now=NOW):
            self.publish(self.room_extension_document(revision=2, room_extensions=[]))
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary(day=DAY,
                    start='13:00', end='14:00', room='Practice A', event_ids=[42], room_extension_original=original):
                self.fail('A prepared extension cannot outlive its accepted authority')
        self.publish(self.room_extension_document(revision=3))
        expired = tempo.read_snapshot(now=NOW + timedelta(days=2))
        self.assertEqual(expired.extension_end(original, 15), 13.5)
        self.assertFalse(expired.allows_room_extension(original, original | dict(endTime='14:00')))

    def test_prefix_extension_cannot_cross_a_selected_named_session(self):
        self.publish(self.room_extension_document(windows=[
            dict(date=str(DAY), start='14:00', end='15:00', rooms=['Practice B'], task_id='lesson')]))
        snapshot = tempo.read_snapshot(now=NOW)
        original = self.room_extension_original()
        self.assertEqual(snapshot.extension_end(original, 15), 14)
        self.assertTrue(snapshot.allows_room_extension(original, original | dict(endTime='14:00')))
        self.assertFalse(snapshot.allows_room_extension(original, original | dict(endTime='14:15')))

    def test_noop_retry_and_compare_and_swap(self):
        self.assertEqual(self.publish(expected_revision=0)["revision"], 1)
        self.assertTrue(self.publish(expected_revision=0)["unchanged"])
        with self.assertRaises(tempo.CoordinationError):
            self.publish(document(revision=2), expected_revision=0)
        self.assertEqual(self.publish(document(revision=2), expected_revision=1)["revision"], 2)
        with self.assertRaises(tempo.CoordinationError):
            self.publish(document(revision=4), expected_revision=2)
        with self.assertRaises(tempo.CoordinationError):
            self.publish(document(revision=3, instance_id="another-installation"), expected_revision=2)

    def test_expired_or_future_publication_rejected(self):
        for value in (document(valid_until=NOW.isoformat()),
                      document(generated_at=(NOW + timedelta(minutes=3)).isoformat())):
            with self.subTest(value=value), self.assertRaises(tempo.CoordinationError):
                self.publish(value)
        self.assertFalse(self.path.exists())

    def test_invalid_scope_or_unbounded_schema_rejected(self):
        variants = [document(dates=[]), document(dates=[str(DAY), str(DAY)]),
                    document(dates=["2028-01-01"]), document(revision=True), document(enabled="yes"),
                    document(valid_until=(NOW + timedelta(hours=25)).isoformat()),
                    document(busy=[dict(date="2026-09-30", start="09:00", end="10:00")]),
                    document(windows=[dict(date=str(DAY), start="12:00", end="09:00")]),
                    document(protected_event_ids=[True])]
        for value in variants:
            with self.subTest(value=value), self.assertRaises(tempo.CoordinationError):
                self.publish(value)

    def test_missing_corrupt_or_stale_keeps_only_durable_scope_blocked(self):
        for fault in ("missing", "corrupt", "expired", "rollback", "same-revision-edited"):
            with self.subTest(fault=fault):
                self.publish()
                if fault == "missing":
                    self.path.unlink()
                elif fault == "corrupt":
                    self.path.write_text("{", encoding="utf-8")
                elif fault == "rollback":
                    self.publish(document(revision=2))
                    atomic_write_json(self.path, document())
                elif fault == "same-revision-edited":
                    atomic_write_json(self.path, document(windows=[]))
                snapshot = tempo.read_snapshot(now=NOW + timedelta(days=2) if fault == "expired" else NOW)
                self.assertTrue(snapshot.problem)
                self.assertFalse(snapshot.permits(DAY, 9, 10, "Practice A"))
                self.assertEqual(snapshot.blocked_ranges(DAY), [(0, 24)])
                self.assertTrue(snapshot.permits(DAY + timedelta(days=1), 9, 10, "Anywhere"))
                # Reset only this fixture's two exact files between cases.
                self.path.unlink(missing_ok=True)
                tempo._accepted_path(self.path).unlink(missing_ok=True)

    def test_interrupted_publish_keeps_new_scope_blocked(self):
        self.publish()
        next_doc = document(revision=2, dates=[str(DAY), "2026-09-30"])
        original = tempo.atomic_write_json
        def write(path, value):
            if path == self.path:
                raise OSError("synthetic interruption")
            original(path, value)
        with mock.patch.object(tempo, "atomic_write_json", side_effect=write), self.assertRaises(OSError):
            self.publish(next_doc)
        snapshot = tempo.read_snapshot(now=NOW)
        self.assertFalse(snapshot.permits("2026-09-30", 9, 10, "Anywhere"))
        self.assertTrue(snapshot.problem)
        self.assertTrue(self.publish(next_doc)["unchanged"])
        self.assertFalse(tempo.read_snapshot(now=NOW).problem)

    def test_explicit_disable_releases_scope_and_does_not_edit_settings(self):
        atomic_write_json(self.settings, {"practice_plan": {"target": "untouched"}})
        before = self.settings.read_bytes()
        self.publish()
        self.publish(document(revision=2, enabled=False, dates=[], busy=[], windows=[]))
        snapshot = tempo.read_snapshot(now=NOW)
        self.assertFalse(snapshot.enabled)
        self.assertTrue(snapshot.permits(DAY, 9, 10, "Anywhere"))
        self.assertEqual(self.settings.read_bytes(), before)

    def test_released_integration_preserves_standalone_planning_and_save_paths(self):
        baseline = booking_plan_fingerprint({}, config_path=self.settings)
        self.publish()
        self.publish(document(revision=2, enabled=False, dates=[], busy=[], windows=[], protected_event_ids=[]))
        plan = booker.PracticePlan(enabled=True, default_hours=4)
        gaps = [{'room':'Practice B','slots':[{'startHour':8,'endHour':20}]}]
        for state in ('released', 'expired', 'missing', 'corrupt'):
            with self.subTest(state=state):
                if state == 'missing':
                    self.path.unlink()
                elif state == 'corrupt':
                    self.path.write_text('{invalid')
                now = NOW + timedelta(days=2) if state == 'expired' else NOW
                with mock.patch.object(tempo, '_now', side_effect=lambda value=None: value or now), tempo.coordination_run(now=now):
                    snapshot = tempo.current_snapshot()
                    self.assertFalse(snapshot.enabled)
                    self.assertEqual(snapshot.blocked_ranges(DAY), [])
                    self.assertEqual(snapshot.protected_ids(), set())
                    self.assertEqual(snapshot.planning_blocker(DAY), '')
                    self.assertIs(snapshot.constrain_availability(gaps, DAY), gaps)
                    self.assertIs(snapshot.practice_plan_overlay(plan, {}), plan)
                    self.assertEqual(snapshot.allowed_end(DAY, 13, 15, 'Practice B'), 15)
                    self.assertEqual(booking_plan_fingerprint({}, config_path=self.settings), baseline)
                    tracker = booker.BookingTracker()
                    self.assertFalse(tracker.overlaps_conflict(DAY, 8, 20))
                    self.assertEqual(tracker.get_hours_for_day(DAY), 0)
                    # Create/edit/upgrade boundaries no longer inherit old
                    # room restrictions, busy tasks or protected event IDs.
                    with booking_save_boundary(day=DAY, start='13:00', end='14:00', room='Practice B', event_ids=(42,)):
                        pass
                    # Normal agenda conflicts still remain authoritative.
                    tracker.add_existing_event(DAY, 13, 14, is_reservation=True, room='Practice B')
                    self.assertTrue(tracker.overlaps_conflict(DAY, 13, 14))

    def test_full_interval_and_exact_room_required(self):
        self.publish()
        snapshot = tempo.read_snapshot(now=NOW)
        self.assertTrue(snapshot.permits(DAY, 9, 11, "Practice A"))
        self.assertTrue(snapshot.permits(DAY, 11.5, 12, "Practice A"))
        for start, end, room in ((8.5, 10, "Practice A"), (10.5, 11.5, "Practice A"),
                                 (11.5, 12.5, "Practice A"), (9, 10, "Practice B")):
            self.assertFalse(snapshot.permits(DAY, start, end, room))

    def test_adjacent_tasks_cannot_be_combined_into_one_booking(self):
        windows = [dict(date=str(DAY), start="09:00", end="10:00", rooms=[]),
                   dict(date=str(DAY), start="10:00", end="11:00", rooms=[])]
        self.publish(document(windows=windows, busy=[]))
        snapshot = tempo.read_snapshot(now=NOW)
        self.assertFalse(snapshot.permits(DAY, 9, 11, "Practice A"))
        self.assertTrue(snapshot.permits(DAY, 9, 10, "Practice B"))

    def test_tracker_rebuilds_keep_constraints_without_quota_or_fake_events(self):
        self.publish()
        with tempo.coordination_run(now=NOW):
            first = booker.BookingTracker()
            rebuilt = tracker_for_events(booker, [], ())
        for tracker in (first, rebuilt):
            self.assertTrue(tracker.overlaps_conflict(DAY, 8, 9.25))
            self.assertTrue(tracker.overlaps_conflict(DAY, 10.5, 11.25))
            self.assertFalse(tracker.overlaps_conflict(DAY, 9, 11))
            self.assertEqual(tracker.get_total_booking_hours(), 0)
            self.assertEqual(tracker.reservation_ranges, {})
            self.assertEqual(tracker.agenda_events, [])
            self.assertFalse(tracker.can_book("Practice B", DAY, 9, 60)[0])
            self.assertTrue(tracker.can_book("Practice A", DAY, 9, 60)[0])

    def test_upgrade_room_availability_respects_windows_and_task_conflicts(self):
        self.publish()
        raw = [{"room": name, "slots": [{"startHour": 8, "endHour": 18}]}
               for name in ("Practice A", "Practice B")]
        old = copy.deepcopy(raw)
        values = tempo.read_snapshot(now=NOW).constrain_availability(raw, DAY)
        self.assertEqual(values[0]["slots"], [{"startHour": 9, "endHour": 11},
                                             {"startHour": 11.5, "endHour": 12}])
        self.assertEqual(values[1]["slots"], [])
        self.assertEqual(raw, old)

    def test_protected_bookings_stay_real_conflicts(self):
        self.publish()
        event = dict(eventId=42, date=str(DAY), startTime="10:00", endTime="11:00",
                     room="Practice A", isReservation=True, title="Reservation")
        with tempo.coordination_run(now=NOW):
            tracker = tracker_for_events(booker, [event], ())
            events, ignored_ids = planning_events(booker, tracker, ())
        self.assertIn(42, ignored_ids)
        self.assertNotEqual(events[0].get("blocksConflict"), False)
        self.assertEqual(tracker.get_hours_for_day(DAY), 1)

    def test_extension_is_capped_by_whole_window_and_task(self):
        self.publish()
        snapshot = tempo.read_snapshot(now=NOW)
        self.assertEqual(snapshot.allowed_end(DAY, 9, 13, "Practice A"), 11)
        self.assertEqual(snapshot.allowed_end(DAY, 11.5, 13, "Practice A"), 12)
        self.assertEqual(snapshot.allowed_end(DAY, 8.5, 10, "Practice A"), 8.5)
        self.assertEqual(snapshot.allowed_end(DAY, 9, 11, "Practice B"), 9)

    def test_changed_revision_after_form_preparation_prevents_save(self):
        self.publish()
        saved = mock.Mock()
        with tempo.coordination_run(now=NOW):
            self.publish(document(revision=2, busy=[]))
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary(
                    day=DAY, start="09:00", end="10:00", room="Practice A"):
                saved()
        saved.assert_not_called()

    def test_activation_mid_run_stops_old_plan_save(self):
        with tempo.coordination_run(now=NOW):
            self.publish()
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary(
                    day=DAY, start="09:00", end="10:00", room="Practice A"):
                self.fail("An old standalone plan cannot cross activation")

    def test_stale_scope_blocks_save_but_other_date_remains_standalone(self):
        self.publish()
        with tempo.coordination_run(now=NOW):
            self.path.unlink()
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary(
                    day=DAY, start="09:00", end="10:00", room="Practice A"):
                self.fail("Missing coordination must not silently release its dates")
            with booking_save_boundary(day=DAY + timedelta(days=1), start="09:00", end="10:00", room="Anywhere"):
                pass

    def test_protected_automatic_edit_rejected_manual_control_retained(self):
        self.publish()
        with tempo.coordination_run(now=NOW):
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary(event_ids=[42]):
                self.fail("Automatic cancellation is not allowed for a protected booking")
            with booking_save_boundary(event_ids=[42], automatic=False):
                pass

    def test_final_guard_checks_start_and_complete_extension_not_only_tail(self):
        self.publish()
        with tempo.coordination_run(now=NOW):
            for start, end in (("08:30", "10:00"), ("10:30", "11:45"), ("11:45", "12:15")):
                with self.subTest(start=start, end=end), self.assertRaises(BookingPreferencesChanged), \
                        booking_save_boundary(day=DAY, start=start, end=end, room="Practice A"):
                    self.fail("Whole interval must be accepted")

    def test_publisher_waits_for_save_to_finish(self):
        self.publish()
        began, done = Event(), Event()
        failures = []
        def publish():
            began.set()
            try:
                self.publish(document(revision=2, busy=[]))
            except Exception as exc:
                failures.append(exc)
            finally:
                done.set()
        with tempo.coordination_run(now=NOW), booking_save_boundary(
                day=DAY, start="09:00", end="10:00", room="Practice A"):
            writer = Thread(target=publish)
            writer.start()
            self.assertTrue(began.wait(1))
            self.assertFalse(done.wait(.1))
        writer.join(3)
        self.assertTrue(done.is_set())
        self.assertEqual(failures, [])
        self.assertEqual(tempo.read_public_status(now=NOW)["revision"], 2)

    def test_preference_run_captures_coordination_revision_too(self):
        atomic_write_json(self.settings, {})
        self.publish()
        with booking_preference_run(self.settings, {}):
            self.publish(document(revision=2))
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary(
                    day=DAY, start="09:00", end="10:00", room="Practice A"):
                self.fail("Normal production runs pin both preference and Tempo revisions")

    def test_display_plan_fingerprint_tracks_coordination_and_retains_run_revision(self):
        standalone = booking_plan_fingerprint({}, config_path=self.settings)
        self.publish()
        with tempo.coordination_run(now=NOW):
            accepted = booking_plan_fingerprint({}, config_path=self.settings)
            self.publish(document(revision=2, windows=[]))
            self.assertEqual(booking_plan_fingerprint({}, config_path=self.settings), accepted)
        self.assertNotEqual(standalone, accepted)
        self.assertNotEqual(booking_plan_fingerprint({}, config_path=self.settings), accepted)
        self.publish(document(revision=3, enabled=False, dates=[], busy=[], windows=[]))
        self.assertEqual(booking_plan_fingerprint({}, config_path=self.settings), standalone)

    def test_first_enable_requires_idle_runtime_without_starting_or_stopping_it(self):
        with tempo.SingleInstanceLock(tempo.RUNTIME_LOCK_PATH):
            with self.assertRaisesRegex(tempo.CoordinationError, "busy"):
                self.publish()
        self.assertFalse(self.path.exists())
        self.assertFalse(tempo._accepted_path(self.path).exists())
        self.publish()
        # Once the hook is active, changing a revision is serialized with Save,
        # not blocked by a long-running room scan that owns the normal runtime.
        with tempo.SingleInstanceLock(tempo.RUNTIME_LOCK_PATH):
            self.publish(document(revision=2))
        self.assertEqual(tempo.read_public_status(now=NOW)["revision"], 2)

    def test_reenable_after_explicit_disable_requires_idle_runtime(self):
        self.publish(document(enabled=False, dates=[], busy=[], windows=[]))
        with tempo.SingleInstanceLock(tempo.RUNTIME_LOCK_PATH), self.assertRaisesRegex(tempo.CoordinationError, "busy"):
            self.publish(document(revision=2))
        self.assertFalse(tempo.read_public_status(now=NOW)["enabled"])

    def test_practice_demand_overlay_counts_confirmed_prefix_once_without_changing_saved_goal(self):
        self.publish(document(busy=[]))
        snapshot = tempo.read_snapshot(now=NOW)
        plan = booker.PracticePlan(enabled=True, default_hours=2, date_overrides={"2026-09-30": 4})
        reservations = {str(DAY): [(9, 9.5, "Practice A"), (13, 14, "Practice B")]}
        overlaid = snapshot.practice_plan_overlay(plan, reservations)
        self.assertEqual(overlaid.target_for(DAY), 4)  # 09–12 accepted + 13–14 booked.
        self.assertEqual(overlaid.target_for(date(2026, 9, 30)), 4)
        self.assertEqual(plan.target_for(DAY), 2)
        self.assertEqual(plan.date_overrides, {"2026-09-30": 4})
        # Filling the accepted window does not grow this run's fixed goal.
        reservations[str(DAY)].append((9.5, 12, "Practice A"))
        self.assertEqual(overlaid.target_for(DAY), 4)

    def test_practice_demand_preserves_disabled_mode_higher_saved_goal_and_stale_state(self):
        self.publish()
        snapshot = tempo.read_snapshot(now=NOW)
        disabled = booker.PracticePlan(enabled=False)
        self.assertIs(snapshot.practice_plan_overlay(disabled, {}), disabled)
        high_goal = booker.PracticePlan(enabled=True, default_hours=6)
        self.assertEqual(snapshot.practice_plan_overlay(high_goal, {}).target_for(DAY), 6)
        stale = tempo.read_snapshot(now=NOW + timedelta(days=2))
        self.assertIs(stale.practice_plan_overlay(high_goal, {}), high_goal)


if __name__ == "__main__":
    unittest.main()
