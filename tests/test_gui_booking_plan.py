import unittest
from datetime import datetime, timedelta, timezone

from booking_plan import (
    BookingPlanReadResult,
    BookingPlanSnapshot,
    DayPlan,
    PlanCandidate,
)
from gui import (
    calendar_timeline_geometry,
    next_selected_plan_candidate,
    plan_candidate_confirmed_minutes,
    plan_candidate_matches_event,
    timeline_plan_candidates,
    timeline_plan_segments,
    visible_plan_candidates,
)


NOW = datetime(2026, 8, 30, 11, 0, tzinfo=timezone.utc)


def candidate(
    *,
    room="B0.29",
    start="12:00",
    end="14:00",
    confirmed=0,
    booking_date="2026-09-04",
    unlock_at=None,
    state="waiting",
):
    start_hour, start_minute = map(int, start.split(":"))
    end_hour, end_minute = map(int, end.split(":"))
    potential = (end_hour * 60 + end_minute) - (start_hour * 60 + start_minute)
    return PlanCandidate(
        room=room,
        date=booking_date,
        start_time=start,
        end_time=end,
        unlock_at=unlock_at or NOW + timedelta(hours=1),
        initial_minutes=min(30, potential),
        potential_minutes=potential,
        confirmed_minutes=confirmed,
        state=state,
        reason="Preferred continuous afternoon session.",
    )


def result(*, stale=False):
    primary = candidate()
    backup = candidate(room="B1.09", start="13:00", end="15:00")
    day = DayPlan(
        date="2026-09-04",
        target_minutes=180,
        existing_minutes=0,
        peak_used_minutes=0,
        peak_limit_minutes=120,
        status="waiting",
        primary=primary,
        additional=(),
        backups=(backup,),
        held_peak_minutes=120,
        reason="Holding peak allowance for the afternoon.",
    )
    snapshot = BookingPlanSnapshot(
        version=1,
        generated_at=NOW,
        valid_until=NOW + timedelta(minutes=3),
        policy_observed_at=NOW,
        strategy_fingerprint="sha256:test",
        status="active",
        summary="One selected potential and one alternative.",
        days=(day,),
    )
    return BookingPlanReadResult(snapshot, stale, "stale" if stale else "current")


class GuiBookingPlanHelpersTests(unittest.TestCase):
    def test_current_plan_exposes_primary_then_alternative(self):
        overlays = visible_plan_candidates(result(), "2026-09-04")

        self.assertEqual(
            [(item.room, primary) for item, primary in overlays],
            [("B0.29", True), ("B1.09", False)],
        )

    def test_stale_or_wrong_date_plan_never_draws(self):
        self.assertEqual(visible_plan_candidates(result(stale=True), "2026-09-04"), ())
        self.assertEqual(visible_plan_candidates(result(), "2026-09-05"), ())

    def test_exact_confirmed_reservation_suppresses_only_its_overlay(self):
        confirmed = {
            "date": "2026-09-04",
            "startTime": "12:00",
            "endTime": "14:00",
            "room": "B0.29",
            "isReservation": True,
        }
        self.assertTrue(plan_candidate_matches_event(candidate(), confirmed))

        overlays = visible_plan_candidates(result(), "2026-09-04", [confirmed])
        self.assertEqual([(item.room, primary) for item, primary in overlays], [("B1.09", False)])

    def test_shorter_exact_start_reservation_reports_extension_progress(self):
        partial = {
            "date": "2026-09-04",
            "startTime": "12:00",
            "endTime": "12:30",
            "room": "B0.29",
            "isReservation": True,
        }

        self.assertEqual(plan_candidate_confirmed_minutes(candidate(), [partial]), 30)
        self.assertFalse(plan_candidate_matches_event(candidate(), partial))
        self.assertEqual(len(visible_plan_candidates(result(), "2026-09-04", [partial])), 2)

    def test_local_verified_progress_is_visible_before_the_next_agenda_scan(self):
        self.assertEqual(
            plan_candidate_confirmed_minutes(candidate(confirmed=30), []),
            30,
        )

    def test_class_or_tuple_mismatch_does_not_hide_potential(self):
        event = {
            "date": "2026-09-04",
            "startTime": "12:00",
            "endTime": "14:00",
            "room": "B0.29",
            "isReservation": False,
        }
        self.assertFalse(plan_candidate_matches_event(candidate(), event))
        self.assertEqual(len(visible_plan_candidates(result(), "2026-09-04", [event])), 2)

    def test_timeline_geometry_is_proportional_and_clipped(self):
        self.assertEqual(calendar_timeline_geometry("07:00", "07:15", 930), (0, 15))
        self.assertEqual(calendar_timeline_geometry("12:00", "14:00", 930), (300, 420))
        self.assertEqual(calendar_timeline_geometry("06:00", "08:00", 930), (0, 60))

    def test_timeline_geometry_rejects_reversed_or_empty_ranges(self):
        for start, end in (("12:00", "12:00"), ("14:00", "12:00")):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                calendar_timeline_geometry(start, end, 930)

    def test_next_action_prefers_ready_additional_over_future_primary(self):
        primary = candidate(unlock_at=NOW + timedelta(hours=3))
        ready = candidate(
            room="B1.09",
            start="16:00",
            end="17:00",
            unlock_at=NOW - timedelta(minutes=15),
            state="ready",
        )
        day = DayPlan(
            date="2026-09-04",
            target_minutes=180,
            existing_minutes=0,
            peak_used_minutes=0,
            peak_limit_minutes=120,
            status="planned",
            primary=primary,
            additional=(ready,),
            backups=(),
            held_peak_minutes=0,
            reason="Two selected sessions.",
        )
        snapshot = BookingPlanSnapshot(
            version=1,
            generated_at=NOW,
            valid_until=NOW + timedelta(minutes=20),
            policy_observed_at=NOW,
            strategy_fingerprint="sha256:test",
            status="active",
            summary="Selected sessions.",
            days=(day,),
        )

        selected_day, selected = next_selected_plan_candidate(
            snapshot,
            today=datetime(2026, 9, 1).date(),
            now=NOW,
        )

        self.assertIs(selected_day, day)
        self.assertEqual(selected.room, "B1.09")

    def test_next_action_skips_a_primary_fully_confirmed_in_cached_agenda(self):
        primary = candidate(unlock_at=NOW - timedelta(minutes=30), state="ready")
        ready = candidate(
            room="B1.09",
            start="16:00",
            end="17:00",
            unlock_at=NOW - timedelta(minutes=15),
            state="ready",
        )
        day = DayPlan(
            date="2026-09-04",
            target_minutes=180,
            existing_minutes=0,
            peak_used_minutes=0,
            peak_limit_minutes=120,
            status="planned",
            primary=primary,
            additional=(ready,),
            backups=(),
            held_peak_minutes=0,
            reason="Two selected sessions.",
        )
        snapshot = BookingPlanSnapshot(
            version=1,
            generated_at=NOW,
            valid_until=NOW + timedelta(minutes=20),
            policy_observed_at=NOW,
            strategy_fingerprint="sha256:test",
            status="active",
            summary="Selected sessions.",
            days=(day,),
        )
        confirmed = {
            "date": "2026-09-04",
            "startTime": "12:00",
            "endTime": "14:00",
            "room": "B0.29",
            "isReservation": True,
        }

        _, selected = next_selected_plan_candidate(
            snapshot,
            today=datetime(2026, 9, 1).date(),
            now=NOW,
            events=(confirmed,),
        )

        self.assertEqual(selected.room, "B1.09")

    def test_timeline_extension_splits_solid_progress_from_hatched_remainder(self):
        confirmed, solid, potential = timeline_plan_segments(
            candidate(confirmed=30),
            (),
            930,
        )

        self.assertEqual(confirmed, 30)
        self.assertEqual(solid, (300, 330))
        self.assertEqual(potential, (330, 420))

    def test_timeline_keeps_all_selected_and_caps_only_alternatives(self):
        selected = tuple(
            candidate(
                room=f"Selected {index}",
                start=f"{8 + index:02d}:00",
                end=f"{9 + index:02d}:00",
            )
            for index in range(5)
        )
        backups = tuple(
            candidate(
                room=f"Backup {index}",
                start=f"{15 + index:02d}:00",
                end=f"{16 + index:02d}:00",
            )
            for index in range(5)
        )
        day = DayPlan(
            date="2026-09-04",
            target_minutes=300,
            existing_minutes=0,
            peak_used_minutes=0,
            peak_limit_minutes=120,
            status="planned",
            primary=selected[0],
            additional=selected[1:],
            backups=backups,
            held_peak_minutes=0,
            reason="Several fragmented sessions.",
        )
        snapshot = BookingPlanSnapshot(
            version=1,
            generated_at=NOW,
            valid_until=NOW + timedelta(minutes=20),
            policy_observed_at=NOW,
            strategy_fingerprint="sha256:test",
            status="active",
            summary="Several sessions.",
            days=(day,),
        )
        overlays = timeline_plan_candidates(
            BookingPlanReadResult(snapshot, False, "current"),
            "2026-09-04",
        )

        self.assertEqual(sum(1 for _, is_selected in overlays if is_selected), 5)
        self.assertEqual(sum(1 for _, is_selected in overlays if not is_selected), 3)


if __name__ == "__main__":
    unittest.main()
