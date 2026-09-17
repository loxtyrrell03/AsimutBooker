import copy
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from booking_strategy import DailyPlanningPreferences
from mutation_receipts import (MutationReceiptError, load_journal, record_pending_upgrade,
                               attach_event_url)
from room_upgrades import (Reservation, RoomUpgrade, classify_upgrade_outcome,
                           find_room_upgrades, local_instant)


class RoomUpgradePlannerTests(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 9, 21)
        self.now = datetime(2026, 9, 18, 16, tzinfo=timezone.utc)
        self.original = Reservation(42, self.day, "Fallback", 720, 840)
        self.events = [{**self.original.as_booking(), "isReservation": True}]
        self.policy = SimpleNamespace(
            room_order=("Best", "Better", "Fallback"),
            minimum_block_minutes=30, site_maximum_booking_minutes=120,
            booking_horizon=self.now + timedelta(days=7), site_clock_offset_bounds=(-1, 1),
            horizon_minutes_for=lambda room: 3 * 1440,
            booking_dates=lambda today: tuple(today + timedelta(days=i) for i in range(8)))
        self.gaps = [{"room": "Best", "slots": [{"startHour": 14, "endHour": 16}]}]
        self.prefs = {"enabled": True, "strict_mode": False, "start_hour": 12, "end_hour": 16}
        self.planning = DailyPlanningPreferences()

    def plan(self, **kwargs):
        values = dict(events=self.events, available_data=self.gaps, policy=self.policy,
                      now=self.now, time_preferences=self.prefs, planning=self.planning)
        values.update(kwargs)
        return find_room_upgrades(self.original, **values)

    def add_event(self, start, end, room="Other", reservation=False, event_id=43, **extra):
        self.events.append({"eventId": event_id, "date": self.day.isoformat(),
                            "room": room, "startTime": start, "endTime": end,
                            "isReservation": reservation, **extra})

    def test_moves_the_full_session_to_a_better_room_elsewhere_in_preferred_window(self):
        before = copy.deepcopy(self.events)
        candidate, = self.plan()
        self.assertEqual(candidate.replacement, Reservation(42, self.day, "Best", 840, 960))
        self.assertEqual(self.events, before)

    def test_same_slot_upgrade_does_not_conflict_with_itself(self):
        self.gaps[0]["slots"] = [{"startHour": 12, "endHour": 14}]
        self.assertEqual(len(self.plan()), 1)

    def test_shorter_gap_never_shrinks_booked_hours(self):
        self.gaps[0]["slots"] = [{"startHour": 13, "endHour": 14}]
        self.assertEqual(self.plan(), ())

    def test_horizon_must_cover_end_not_only_start_or_seed_block(self):
        for minutes_before in (1, 30, 119):
            now = local_instant(self.day, 960) - timedelta(days=3, minutes=minutes_before)
            with self.subTest(minutes=minutes_before):
                self.assertEqual(self.plan(now=now), ())
        boundary = local_instant(self.day, 960) - timedelta(days=3)
        self.assertEqual(self.plan(now=boundary), ())  # site clock may be one second behind
        self.assertEqual(len(self.plan(now=boundary + timedelta(seconds=1))), 1)

    def test_global_cutoff_and_unknown_site_clock_fail_closed(self):
        self.policy.booking_horizon = local_instant(self.day, 945)
        self.assertEqual(self.plan(), ())
        self.policy.booking_horizon = self.now + timedelta(days=7)
        self.policy.site_clock_offset_bounds = None
        self.assertEqual(self.plan(), ())

    def test_arbitrary_room_horizons_are_used(self):
        self.policy.horizon_minutes_for = lambda room: 2 * 1440 + 15
        self.assertEqual(self.plan(), ())
        self.policy.horizon_minutes_for = lambda room: 5 * 1440
        self.assertEqual(len(self.plan()), 1)

    def test_conflicts_and_same_room_spacing_use_other_reservations(self):
        self.add_event("15:00", "16:00")
        self.assertEqual(self.plan(), ())
        self.events.pop()
        self.add_event("16:30", "17:30", "Best", True)
        self.assertEqual(self.plan(), ())
        self.events[-1].update(startTime="17:00", endTime="18:00")
        self.assertEqual(len(self.plan()), 1)

    def test_ignored_class_can_be_overlapped_but_ignored_reservation_still_uses_quota(self):
        self.add_event("14:00", "16:00", blocksConflict=False)
        self.assertEqual(len(self.plan()), 1)
        self.events[-1]["isReservation"] = True
        self.assertEqual(self.plan(), ())

    def test_ignored_original_missing_duplicate_or_changed_identity_is_not_moved(self):
        self.assertEqual(self.plan(ignored_event_ids={42}), ())
        self.assertEqual(self.plan(events=[]), ())
        self.assertEqual(self.plan(events=self.events * 2), ())
        self.events[0]["endTime"] = "13:00"
        self.assertEqual(self.plan(), ())

    def test_peak_allowance_is_recomputed_after_removing_exact_original(self):
        self.assertEqual(len(self.plan()), 1)  # full 120 minutes already used by original
        self.add_event("10:00", "10:30", "Other", True)
        self.assertEqual(self.plan(), ())

    def test_one_hour_policy_preserves_upgrade_without_increasing_peak_usage(self):
        self.original = replace(self.original, end=780)
        self.events = [{**self.original.as_booking(), "isReservation": True}]
        candidates = self.plan(peak_limit=60)
        self.assertTrue(candidates)
        self.assertTrue(all(c.replacement.duration == 60 for c in candidates))
        self.add_event("10:00", "10:30", "Other", True)
        self.assertEqual(self.plan(peak_limit=60), ())

    def test_existing_legacy_peak_booking_can_improve_without_adding_minutes(self):
        candidates = self.plan(peak_limit=60)
        self.assertTrue(candidates)
        self.assertTrue(all(c.replacement.duration == self.original.duration for c in candidates))

    def test_outside_preference_never_wins_just_for_room_quality(self):
        self.gaps[0]["slots"] = [{"startHour": 18, "endHour": 20}]
        self.assertEqual(self.plan(), ())
        self.prefs["strict_mode"] = True
        self.assertEqual(self.plan(), ())

    def test_soft_preference_allows_a_better_time_without_worsening_room(self):
        self.original = Reservation(42, self.day, "Fallback", 660, 780)
        self.events = [{**self.original.as_booking(), "isReservation": True}]
        self.assertEqual(len(self.plan()), 1)

    def test_daily_peak_preference_is_retained_when_general_time_preference_disabled(self):
        self.prefs["enabled"] = False
        self.gaps[0]["slots"] = [{"startHour": 18, "endHour": 20}]
        self.assertEqual(self.plan(), ())

    def test_weekend_does_not_get_weekday_peak_limit(self):
        self.day = date(2026, 9, 20)
        self.original = replace(self.original, day=self.day)
        self.events = [{**self.original.as_booking(), "isReservation": True}]
        self.add_event("10:00", "12:00", "Other", True)
        self.assertEqual(len(self.plan()), 1)

    def test_original_and_destination_must_both_be_outside_settled_window(self):
        self.assertEqual(self.plan(now=local_instant(self.day, 720) - timedelta(hours=24), freeze_minutes=1440), ())
        self.original = replace(self.original, start=840, end=960)
        self.events = [{**self.original.as_booking(), "isReservation": True}]
        self.gaps[0]["slots"] = [{"startHour": 12, "endHour": 14}]
        self.assertEqual(self.plan(now=local_instant(self.day, 720) - timedelta(hours=24), freeze_minutes=1440), ())

    def test_pending_extension_preserves_target_and_other_intended_sessions(self):
        self.original = replace(self.original, end=750)
        self.events = [{**self.original.as_booking(), "isReservation": True}]
        extension = {**self.original.as_booking(), "target_end": "14:00"}
        self.assertEqual(self.plan(protected_extensions=[extension]), ())
        extension.update(eventId=43, startTime="14:00", target_end="16:00")
        self.assertEqual(self.plan(protected_extensions=[extension]), ())

    def test_blackouts_and_excluded_rooms_are_preserved(self):
        self.assertEqual(self.plan(blocked_intervals=[(850, 900)]), ())
        self.policy.room_order = ("Fallback", "Best")
        self.assertEqual(self.plan(), ())
        self.policy.room_order = ("Fallback",)
        self.assertEqual(self.plan(), ())

    def test_duplicates_are_removed_and_room_rank_beats_small_time_shift(self):
        self.gaps *= 2
        self.gaps.append({"room": "Better", "slots": [{"startHour": 12, "endHour": 14}]})
        candidates = self.plan()
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].replacement.room, "Best")

    def test_gap_boundaries_round_inwards(self):
        self.gaps[0]["slots"] = [{"startHour": 13 + 1/60, "endHour": 15.25}]
        candidate, = self.plan()
        self.assertEqual(candidate.replacement.start, 795)
        self.assertEqual(candidate.replacement.end, 915)

    def test_dst_ambiguous_and_nonexistent_times_are_rejected(self):
        for day in (date(2026, 3, 29), date(2026, 10, 25)):
            with self.assertRaises(ValueError):
                local_instant(day, 90)
        self.assertEqual(local_instant(date(2026, 10, 25), 150).hour, 2)

    def test_upgrades_cannot_change_date_identity_or_duration(self):
        for destination in (replace(self.original, event_id=43),
                            replace(self.original, day=date(2026, 9, 22)),
                            replace(self.original, end=780), self.original):
            with self.assertRaises(ValueError):
                RoomUpgrade(self.original, destination)


class UpgradeReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "receipts.json"
        self.details = dict(room="Best", booking_date="2026-09-21", start="14:00", end="16:00",
                            event_url="https://rwcmd.asimut.net/arrangement?eventId=42", path=self.path,
                            original={"event_id": 42, "room": "Fallback", "date": "2026-09-21",
                                      "start": "12:00", "end": "14:00"})

    def test_persists_both_states_atomically(self):
        receipt = record_pending_upgrade(**self.details)
        self.assertEqual(load_journal(self.path)["receipts"][receipt["id"]], receipt)
        self.details["original"]["room"] = "changed"
        self.assertEqual(receipt["original"]["room"], "Fallback")

    def test_invalid_replacements_never_change_the_journal(self):
        for patch in ({"original": None}, {"end": "15:00"}, {"booking_date": "2026-09-22"},
                      {"room": "Fallback"}, {"event_url": "https://rwcmd.asimut.net/arrangement?eventId=43"}):
            with self.subTest(patch=patch), self.assertRaises(MutationReceiptError):
                record_pending_upgrade(**{**self.details, **patch})
            self.assertFalse(self.path.exists())

    def test_event_identity_cannot_change_when_attaching_a_url(self):
        receipt = record_pending_upgrade(**self.details)
        with self.assertRaises(MutationReceiptError):
            attach_event_url(receipt["id"], "https://rwcmd.asimut.net/arrangement?eventId=43", path=self.path)
        self.assertEqual(load_journal(self.path)["receipts"][receipt["id"]], receipt)

    def test_reconciliation_distinguishes_original_new_and_uncertain_state(self):
        receipt = record_pending_upgrade(**self.details)
        event = dict(eventId=42, room="Fallback", date="2026-09-21", startTime="12:00",
                     endTime="14:00", isReservation=True)
        self.assertEqual(classify_upgrade_outcome([event], receipt), "not_applied")
        event.update(room="Best", startTime="14:00", endTime="16:00")
        self.assertEqual(classify_upgrade_outcome([event], receipt), "applied")
        for events in ([], [event, event], [{**event, "eventId": 43}],
                       [{**event, "endTime": "15:00"}], [{**event, "isReservation": False}]):
            self.assertEqual(classify_upgrade_outcome(events, receipt), "uncertain")
