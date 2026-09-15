import copy
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from booking_strategy import DailyPlanningPreferences
from progressive_planner import TransferPlan, ordered_adjustments, plan_progressive_transfer
from room_upgrades import (Reservation, RoomConsolidation, RoomUpgrade,
                          UpgradeOpportunity, local_instant)


class ProgressivePlannerTests(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 9, 21)
        self.originals = (Reservation(42, self.day, "Fallback", 720, 840),)
        self.target = Reservation(42, self.day, "Best", 720, 840)
        self.now = self.edge(750)
        self.policy = SimpleNamespace(
            room_order=("Best", "Better", "Fallback", "Other fallback"),
            minimum_block_minutes=30, site_maximum_booking_minutes=120,
            booking_horizon=self.now + timedelta(days=7), site_clock_offset_bounds=(-1, 1),
            horizon_minutes_for=lambda room: (5 if room == "Best" else 7) * 1440,
            booking_dates=lambda today: tuple(today + timedelta(days=i) for i in range(8)))
        self.gaps = [{"room": "Best", "slots": [{"startHour": 12, "endHour": 14}]}]
        self.prefs = {"enabled": True, "strict_mode": True, "start_hour": 12, "end_hour": 16}
        self.planning = DailyPlanningPreferences()
        self.others = []

    def edge(self, end, seconds=0):
        return local_instant(self.day, end) - timedelta(days=5) + timedelta(seconds=1 + seconds)

    def args(self, **overrides):
        events = [{**r.as_booking(), "isReservation": True} for r in self.originals] + self.others
        values = dict(events=events, available_data=self.gaps, policy=self.policy, now=self.now,
                      time_preferences=self.prefs, planning=self.planning)
        values.update(overrides)
        return values

    def opportunity(self):
        change = (RoomUpgrade(self.originals[0], self.target) if len(self.originals) == 1
                  else RoomConsolidation(self.originals, self.target))
        return UpgradeOpportunity(change, self.edge(self.target.end))

    def plan(self, **overrides):
        return plan_progressive_transfer(self.opportunity(), **self.args(**overrides))

    def test_new_seed_opens_at_minimum_boundary_and_keeps_placeholder_separate(self):
        candidate = self.plan()
        self.assertEqual(candidate.replacement, replace(self.target, end=750))
        self.assertEqual(candidate.remaining, (replace(self.originals[0], start=750),))
        self.assertIsNone(candidate.seed_event_id)
        # Same numeric placeholder does not remove the source reservation.
        self.assertEqual(candidate.replacement.event_id, candidate.remaining[0].event_id)
        self.assertEqual(candidate.opens_at, self.edge(750))
        self.assertEqual(candidate.prepare_at, candidate.opens_at - timedelta(seconds=180))
        self.assertEqual(candidate.transferred_minutes, 30)
        self.assertEqual(candidate.action_count, 2)

    def test_preparation_never_moves_opening_earlier_than_proved_site_clock(self):
        self.now = self.edge(750, seconds=-120)
        candidate = self.plan()
        self.assertGreater(candidate.opens_at, self.now)
        self.assertLess(candidate.prepare_at, self.now)
        self.assertEqual(candidate.replacement.duration, 30)
        self.now = self.edge(750, seconds=-1)
        self.assertGreater(self.plan().opens_at, self.now)

    def test_late_run_catches_largest_legal_open_prefix(self):
        self.now = self.edge(795)
        candidate = self.plan()
        self.assertEqual(candidate.replacement.duration, 75)
        self.assertEqual(candidate.remaining[0].duration, 45)

    def test_new_transfer_does_not_replace_the_existing_whole_session_upgrade_path(self):
        self.now = self.edge(840)
        self.assertIsNone(self.plan())

    def test_seed_extension_advances_fifteen_minutes(self):
        seed = Reservation(99, self.day, "Best", 720, 750)
        self.originals = (seed, replace(self.originals[0], start=750))
        self.target = replace(self.target, event_id=99)
        self.gaps[0]["slots"] = [{"startHour": 12.5, "endHour": 14}]
        self.now = self.edge(765)
        candidate = self.plan(seed=seed)
        self.assertEqual(candidate.seed_event_id, 99)
        self.assertEqual(candidate.originals, (self.originals[1],))
        self.assertEqual(candidate.replacement.end, 765)
        self.assertEqual(candidate.remaining[0].start, 765)
        self.assertEqual(candidate.transferred_minutes, 15)

    def test_final_thirty_minutes_transfer_together_without_illegal_remainder(self):
        seed = Reservation(99, self.day, "Best", 720, 810)
        self.originals = (seed, replace(self.originals[0], start=810))
        self.target = replace(self.target, event_id=99)
        self.gaps[0]["slots"] = [{"startHour": 13.5, "endHour": 14}]
        self.now = self.edge(825)
        candidate = self.plan(seed=seed)
        self.assertEqual(candidate.opens_at, self.edge(840))
        self.assertGreater(candidate.opens_at, self.now)
        self.assertEqual(candidate.remaining, ())
        self.assertEqual(candidate.transferred_minutes, 30)
        self.assertEqual(candidate.adjustments, ((self.originals[1], None),))
        self.now = self.edge(840)
        self.assertLessEqual(self.plan(seed=seed).opens_at, self.now)

    def test_catch_up_does_not_leave_a_fifteen_minute_fallback(self):
        self.now = self.edge(825)
        candidate = self.plan()
        self.assertEqual(candidate.replacement.end, 810)
        self.assertEqual(candidate.remaining[0].duration, 30)

    def test_earlier_shift_trims_the_other_end_to_keep_a_contiguous_day(self):
        self.originals = (replace(self.originals[0], start=750, end=870),)
        candidate = self.plan()
        self.assertEqual(candidate.replacement, replace(self.target, end=750))
        self.assertEqual(candidate.remaining, (replace(self.originals[0], end=840),))

    def test_later_shift_uses_proved_adjoining_fallback_gap(self):
        self.target = replace(self.target, start=750, end=870)
        self.gaps[0]["slots"] = [{"startHour": 12.5, "endHour": 14.5}]
        self.gaps.append({"room": "Fallback", "slots": [{"startHour": 14, "endHour": 14.5}]})
        self.now = self.edge(780)
        candidate = self.plan()
        self.assertEqual(candidate.replacement.start, 750)
        self.assertEqual(candidate.remaining, (replace(self.originals[0], start=780, end=870),))

    def test_later_shift_cannot_invent_a_gap_or_split_one_identity(self):
        self.target = replace(self.target, start=750, end=870)
        self.gaps[0]["slots"] = [{"startHour": 12.5, "endHour": 14.5}]
        self.now = self.edge(780)
        candidate = self.plan()
        self.assertEqual(candidate.opens_at, self.edge(840))
        self.assertEqual(candidate.remaining, (replace(self.originals[0], end=750),))
        self.assertEqual(candidate.replacement.duration, 90)

    def test_three_fragments_transfer_without_downgrading_or_losing_minutes(self):
        self.originals = (Reservation(42, self.day, "Fallback", 720, 750),
                          Reservation(43, self.day, "Better", 750, 780),
                          Reservation(44, self.day, "Other fallback", 780, 840))
        candidate = self.plan()
        self.assertEqual(candidate.remaining, self.originals[1:])
        self.assertEqual(candidate.adjustments, ((self.originals[0], None),))
        self.assertEqual(sum(r.duration for r in candidate.remaining) + candidate.replacement.duration, 120)
        seed = replace(candidate.replacement, event_id=99)
        self.originals = (seed, *candidate.remaining)
        self.target = replace(self.target, event_id=99)
        self.gaps[0]["slots"] = [{"startHour": 12.5, "endHour": 14}]
        self.now = self.edge(780)
        extended = self.plan(seed=seed)
        self.assertEqual(extended.remaining, (self.originals[-1],))
        self.assertEqual(extended.replacement.end, 780)

    def test_stale_missing_duplicate_or_changed_source_identity_fails_closed(self):
        events = self.args()["events"]
        for changed in ([], events * 2, [{**events[0], "endTime": "13:00"}],
                        [{**events[0], "isReservation": False}]):
            with self.subTest(events=changed):
                self.assertIsNone(self.plan(events=changed))

    def test_teacher_booking_after_free_prefix_does_not_prevent_useful_first_step(self):
        self.gaps[0]["slots"] = [{"startHour": 12, "endHour": 13}]
        candidate = self.plan()
        self.assertEqual(candidate.replacement.end, 750)
        self.assertEqual(candidate.remaining[0].start, 750)
        self.now = self.edge(795)
        candidate = self.plan()
        self.assertEqual(candidate.replacement.end, 780)
        self.assertEqual(candidate.remaining[0].start, 780)

    def test_initial_partly_available_room_can_upgrade_even_after_full_time_horizon_opens(self):
        self.gaps[0]["slots"] = [{"startHour": 12, "endHour": 13}]
        self.now = self.edge(840)
        candidate = self.plan()
        self.assertEqual(candidate.replacement.end, 780)
        self.assertEqual(candidate.remaining[0].start, 780)

    def test_layout_acceptance_searches_other_prefixes_instead_of_stopping_at_first_rejection(self):
        self.now = self.edge(780)
        candidate = self.plan(acceptable_layout=lambda candidate: candidate.replacement.end == 765)
        self.assertEqual(candidate.replacement.end, 765)

    def test_expensive_layout_acceptance_has_a_shared_conservative_budget(self):
        calls = []
        self.gaps.append({"room": "Fallback", "slots": [{"startHour": 14, "endHour": 16}]})
        def reject(candidate):
            calls.append(candidate)
            return False
        with patch("progressive_planner.MAX_LAYOUT_ACCEPTANCE_CHECKS", 2):
            self.assertIsNone(self.plan(acceptable_layout=reject))
        self.assertEqual(len(calls), 2)

    def test_teacher_booking_in_proposed_prefix_still_blocks_that_step(self):
        self.gaps[0]["slots"] = [{"startHour": 12.25, "endHour": 14}]
        self.assertIsNone(self.plan())
        self.gaps[0]["slots"] = [{"startHour": 12, "endHour": 14}]
        self.others = [{"eventId": 50, "date": str(self.day), "room": "Lesson", "startTime": "12:30",
                        "endTime": "13:00", "isReservation": False}]
        self.assertIsNone(self.plan())

    def test_existing_seed_grows_into_free_prefix_when_teacher_takes_the_tail(self):
        seed = Reservation(99, self.day, "Best", 720, 750)
        self.originals = (seed, replace(self.originals[0], start=750))
        self.target = replace(self.target, event_id=99)
        self.gaps[0]["slots"] = [{"startHour": 12.5, "endHour": 13}]
        self.now = self.edge(795)
        candidate = self.plan(seed=seed)
        self.assertEqual(candidate.replacement.end, 780)
        self.assertEqual(candidate.remaining, (replace(self.originals[1], start=780),))
        self.assertEqual(candidate.target.end, 840)
        # Once that free half-hour is secured, no further step can pretend the
        # teacher's occupied13:00-14:00 interval is available.
        seed = candidate.replacement
        self.originals = (seed, *candidate.remaining)
        self.gaps[0]["slots"] = []
        self.assertIsNone(self.plan(seed=seed))

    def test_excluded_room_date_clock_horizon_and_rank_are_preserved(self):
        for patch_name, value in (("room_order", ("Fallback", "Best")),
                                  ("room_order", ("Fallback",)),
                                  ("booking_dates", lambda today: ()),
                                  ("site_clock_offset_bounds", None),
                                  ("booking_horizon", local_instant(self.day, 810))):
            original = getattr(self.policy, patch_name)
            with self.subTest(field=patch_name):
                setattr(self.policy, patch_name, value)
                self.assertIsNone(self.plan())
                setattr(self.policy, patch_name, original)

    def test_blackout_ignored_source_and_foreign_extension_remain_protected(self):
        self.assertIsNone(self.plan(blocked_intervals=((735, 745),)))
        self.assertIsNone(self.plan(ignored_event_ids=(42,)))
        extension = {**self.originals[0].as_booking(), "target_end": "14:30"}
        self.assertIsNone(self.plan(protected_extensions=(extension,)))
        extension = {**extension, "eventId": 88, "room": "Other", "startTime": "13:00", "target_end": "15:00"}
        self.assertIsNone(self.plan(protected_extensions=(extension,)))

    def test_strict_hours_and_time_quality_survive_partial_arrangements(self):
        self.prefs["end_hour"] = 13
        self.assertIsNone(self.plan())
        self.prefs.update(enabled=False, strict_mode=False)
        self.target = replace(self.target, start=1080, end=1200)
        self.gaps[0]["slots"] = [{"startHour": 18, "endHour": 20}]
        self.assertIsNone(self.plan())  # weekday preferred peak time cannot disappear

    def test_peak_quota_counts_ignored_other_reservations(self):
        self.others = [{"eventId": 50, "date": str(self.day), "room": "Other", "startTime": "10:00",
                        "endTime": "10:30", "isReservation": True, "blocksConflict": False}]
        self.assertIsNone(self.plan())

    def test_freeze_and_protected_seed_prevent_takeover(self):
        self.assertIsNone(self.plan(freeze_minutes=7 * 1440))
        seed = Reservation(99, self.day, "Best", 720, 750)
        self.originals = (seed, replace(self.originals[0], start=750))
        self.target = replace(self.target, event_id=99)
        self.gaps[0]["slots"] = [{"startHour": 12.5, "endHour": 14}]
        extension = {**seed.as_booking(), "target_end": "14:00"}
        self.assertIsNone(self.plan(seed=seed, protected_extensions=(extension,)))
        self.assertIsNone(self.plan(seed=replace(seed, event_id=100)))

    def test_search_limit_is_conservative_and_does_not_mutate_inputs(self):
        args = self.args()
        before = copy.deepcopy(args)
        with patch("progressive_planner.MAX_REMAINDER_SEARCH_NODES", 0):
            self.assertIsNone(plan_progressive_transfer(self.opportunity(), **args))
        self.assertEqual(args, before)
        self.assertIsNotNone(plan_progressive_transfer(self.opportunity(), **args))
        self.assertEqual(args, before)

    def test_invalid_current_time_or_preparation_configuration_rejected(self):
        with self.assertRaises(ValueError):
            self.plan(now=datetime(2026, 9, 16, 12, 30))
        for seconds in (-1, 0.5, True):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                self.plan(preparation_seconds=seconds)

    def test_shifted_crowded_windows_keep_coverage_across_every_open_edge(self):
        self.prefs.update(start_hour=11, end_hour=16)
        self.planning = replace(self.planning, enabled=False)
        for shift in range(-60, 61, 15):
            self.originals = (Reservation(42, self.day, "Fallback", 720 + shift, 840 + shift),)
            # The room's observed gaps deliberately omit its occupied source.
            self.gaps = [{"room": "Best", "slots": [{"startHour": 12, "endHour": 14}]},
                         {"room": "Fallback", "slots": [
                             {"startHour": 11, "endHour": self.originals[0].start / 60},
                             {"startHour": self.originals[0].end / 60, "endHour": 16}]}]
            self.gaps[1]["slots"] = [gap for gap in self.gaps[1]["slots"] if gap["startHour"] < gap["endHour"]]
            for end in range(750, 826, 15):
                self.now = self.edge(end)
                with self.subTest(shift=shift, open_end=end):
                    candidate = self.plan()
                    self.assertIsNotNone(candidate)
                    self.assertEqual(candidate.replacement.duration + sum(r.duration for r in candidate.remaining), 120)
                    self.assertLessEqual(candidate.replacement.end, end)
                    self.assertTrue(all(r.duration >= 30 for r in candidate.remaining))
                    self.assertLessEqual(len(candidate.remaining), len(candidate.originals))
                    self.assertIsNotNone(ordered_adjustments(candidate))

    def test_site_minimum_is_used_for_seed_and_last_transfer(self):
        self.policy.minimum_block_minutes = 45
        self.now = self.edge(750)
        candidate = self.plan()
        self.assertEqual(candidate.opens_at, self.edge(765))
        self.assertEqual(candidate.replacement.duration, 45)
        seed = Reservation(99, self.day, "Best", 720, 795)
        self.originals = (seed, replace(self.originals[0], start=795))
        self.target = replace(self.target, event_id=99)
        self.gaps[0]["slots"] = [{"startHour": 13.25, "endHour": 14}]
        self.now = self.edge(825)
        candidate = self.plan(seed=seed)
        self.assertEqual(candidate.transferred_minutes, 45)
        self.assertEqual(candidate.opens_at, self.edge(840))
        self.assertEqual(candidate.remaining, ())


class TransferOrderingTests(unittest.TestCase):
    def plan(self, originals, remaining, *, seed=None, target_start=720, target_end=840, end=750):
        day = originals[0].day
        target = Reservation(seed.event_id if seed else originals[0].event_id, day, "Best", target_start, target_end)
        return TransferPlan(originals, target, replace(target, end=end), remaining,
                            datetime(2026, 9, 16, 12, 30, tzinfo=timezone.utc),
                            seed_event_id=seed.event_id if seed else None, seed=seed)

    def test_dependent_move_waits_for_other_original_to_release_its_old_interval(self):
        day = date(2026, 9, 21)
        a = Reservation(1, day, "Fallback", 720, 780)
        b = Reservation(2, day, "Other fallback", 780, 840)
        new_a, new_b = replace(a, start=780, end=810), replace(b, start=810)
        plan = self.plan((a, b), (new_a, new_b), end=780)
        self.assertEqual(ordered_adjustments(plan), ((b, new_b), (a, new_a)))

    def test_cyclic_moves_are_not_silently_executed(self):
        day = date(2026, 9, 21)
        a = Reservation(1, day, "Fallback", 780, 840)
        b = Reservation(2, day, "Other fallback", 840, 900)
        new_a, new_b = replace(a, start=840, end=870), replace(b, start=780, end=810)
        plan = self.plan((a, b), (new_a, new_b), end=780)
        self.assertIsNone(ordered_adjustments(plan))

    def test_peak_reducing_adjustment_precedes_peak_increasing_move(self):
        day = date(2026, 9, 21)
        a = Reservation(1, day, "Fallback", 960, 1020)
        b = Reservation(2, day, "Other fallback", 780, 840)
        new_a, new_b = replace(a, start=840, end=870), replace(b, start=1020, end=1080)
        plan = self.plan((a, b), (new_a, new_b), target_start=1080, target_end=1200, end=1110)
        self.assertEqual(ordered_adjustments(plan), ((b, new_b), (a, new_a)))

    def test_dataclass_rejects_unaccounted_minutes_and_duplicate_fallbacks(self):
        day = date(2026, 9, 21)
        a = Reservation(1, day, "Fallback", 720, 840)
        for remaining in ((), (replace(a, start=750), replace(a, start=750))):
            with self.subTest(remaining=remaining), self.assertRaises(ValueError):
                self.plan((a,), remaining)


if __name__ == "__main__":
    unittest.main()
