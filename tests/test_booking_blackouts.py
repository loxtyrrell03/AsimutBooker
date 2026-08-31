import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import book_week
from app_settings import SettingsError, load_settings, save_settings
from booking_blackouts import (
    NATURAL_TIME_WINDOWS,
    RebookingBlackout,
    add_rebooking_blackout,
    add_rebooking_blackouts,
    blackout_conflict_ranges,
    interval_overlaps_blackout,
    load_rebooking_blackouts,
    make_rebooking_blackout,
    merge_rebooking_blackouts,
    subtract_rebooking_blackout,
)


class RebookingBlackoutTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "settings.json"

    def tearDown(self):
        self.temporary.cleanup()

    def test_missing_section_is_empty(self):
        self.assertEqual(load_rebooking_blackouts({}), ())
        self.assertEqual(NATURAL_TIME_WINDOWS["afternoon"], ("12:00", "18:00"))

    def test_verified_intervals_persist_and_merge_without_losing_settings(self):
        save_settings({"practice_plan": {"enabled": True, "default_hours": 3.0}}, self.path)

        add_rebooking_blackout("2026-09-01", "14:00", "15:00", path=self.path)
        add_rebooking_blackout("2026-09-01", "13:00", "14:00", path=self.path)
        result = add_rebooking_blackout(
            "2026-09-01", "14:45", "16:00", path=self.path
        )

        self.assertEqual(
            result,
            (RebookingBlackout(date(2026, 9, 1), 13 * 60, 16 * 60),),
        )
        saved = load_settings(self.path)
        self.assertEqual(saved["practice_plan"]["default_hours"], 3.0)
        self.assertEqual(
            saved["rebooking_blackouts"],
            [
                {
                    "date": "2026-09-01",
                    "start_time": "13:00",
                    "end_time": "16:00",
                }
            ],
        )

    def test_broad_scope_can_be_added_atomically_after_a_verified_batch(self):
        save_settings({}, self.path)

        result = add_rebooking_blackouts(
            (
                ("2026-09-01", "13:00", "13:30"),
                ("2026-09-01", "12:00", "18:00"),
            ),
            path=self.path,
        )

        self.assertEqual(
            [item.to_dict() for item in result],
            [
                {
                    "date": "2026-09-01",
                    "start_time": "12:00",
                    "end_time": "18:00",
                }
            ],
        )

        before = load_settings(self.path)
        with self.assertRaises(SettingsError):
            add_rebooking_blackouts(
                (("2026-09-01", "12:05", "18:00"),),
                path=self.path,
            )
        with self.assertRaises(SettingsError):
            add_rebooking_blackouts(
                (("2026-09-01", "12:00"),),
                path=self.path,
            )
        self.assertEqual(load_settings(self.path), before)

    def test_explicit_reopen_can_split_a_merged_window(self):
        save_settings(
            {
                "rebooking_blackouts": [
                    {
                        "date": "2026-09-01",
                        "start_time": "13:00",
                        "end_time": "17:00",
                    }
                ]
            },
            self.path,
        )

        result = subtract_rebooking_blackout(
            "2026-09-01", "14:00", "16:00", path=self.path
        )

        self.assertEqual(
            [item.to_dict() for item in result],
            [
                {
                    "date": "2026-09-01",
                    "start_time": "13:00",
                    "end_time": "14:00",
                },
                {
                    "date": "2026-09-01",
                    "start_time": "16:00",
                    "end_time": "17:00",
                },
            ],
        )

    def test_malformed_or_noncanonical_state_fails_closed(self):
        invalid_sections = (
            {},
            ["2026-09-01 13:00-14:00"],
            [
                {
                    "date": "2026-09-01",
                    "start_time": "13:05",
                    "end_time": "14:00",
                }
            ],
            [
                {
                    "date": "2026-09-01",
                    "start_time": "14:00",
                    "end_time": "13:00",
                }
            ],
            [
                {
                    "date": "2026-09-01",
                    "start_time": "14:00",
                    "end_time": "15:00",
                },
                {
                    "date": "2026-09-01",
                    "start_time": "13:00",
                    "end_time": "14:00",
                },
            ],
        )
        for section in invalid_sections:
            with self.subTest(section=section), self.assertRaises(SettingsError):
                load_rebooking_blackouts({"rebooking_blackouts": section})

    def test_conflict_projection_is_global_and_half_open(self):
        values = (
            make_rebooking_blackout("2026-09-01", "13:00", "15:00"),
            make_rebooking_blackout("2026-09-02", "10:00", "11:00"),
        )

        self.assertEqual(
            blackout_conflict_ranges(values)["2026-09-01"],
            ((13.0, 15.0),),
        )
        self.assertTrue(
            interval_overlaps_blackout(values, "2026-09-01", 14 * 60, 16 * 60)
        )
        self.assertFalse(
            interval_overlaps_blackout(values, "2026-09-01", 15 * 60, 16 * 60)
        )

    def test_runtime_tracker_rejects_any_room_that_overlaps_blackout(self):
        tracker = book_week.BookingTracker()
        values = (
            make_rebooking_blackout("2026-09-01", "12:00", "18:00"),
        )

        applied = book_week.apply_rebooking_blackouts(tracker, values)

        self.assertEqual(applied, 1)
        for room in ("B0.29", "B1.09"):
            allowed, reason = tracker.can_book(
                room,
                date(2026, 9, 1),
                15.0,
                60,
            )
            self.assertFalse(allowed)
            self.assertIn("Overlaps", reason)
        allowed, _reason = tracker.can_book(
            "B0.29",
            date(2026, 9, 1),
            18.0,
            60,
        )
        self.assertTrue(allowed)

    def test_planner_splits_a_free_gap_and_keeps_legal_adjacent_sessions(self):
        target_date = date(2026, 9, 1)
        tracker = book_week.BookingTracker()
        book_week.apply_rebooking_blackouts(
            tracker,
            (make_rebooking_blackout(target_date, "15:00", "17:00"),),
        )

        with (
            mock.patch.object(book_week, "PRIORITY_ROOMS", ["B0.29"]),
            mock.patch.object(
                book_week,
                "room_horizon_minutes",
                return_value=24 * 60,
            ),
        ):
            opportunities = book_week.build_day_booking_opportunities(
                [
                    {
                        "room": "B0.29",
                        "slots": [{"startHour": 12.0, "endHour": 20.0}],
                    }
                ],
                target_date,
                tracker,
                {
                    "enabled": False,
                    "strict_mode": False,
                    "start_hour": 0.0,
                    "end_hour": 24.0,
                },
                book_week.DailyPlanningPreferences(),
                now=datetime(2026, 8, 31, 12, 0),
                remaining_daily_hours=4.0,
            )

        self.assertTrue(opportunities)
        self.assertTrue(
            any(item.end_minutes == 15 * 60 for item in opportunities)
        )
        self.assertTrue(
            any(item.start_minutes == 17 * 60 for item in opportunities)
        )
        self.assertTrue(
            all(
                item.end_minutes <= 15 * 60
                or item.start_minutes >= 17 * 60
                for item in opportunities
            )
        )

    def test_merge_rejects_unvalidated_values(self):
        with self.assertRaises(SettingsError):
            merge_rebooking_blackouts([object()])


if __name__ == "__main__":
    unittest.main()
