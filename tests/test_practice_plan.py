import math
import unittest
from datetime import date, datetime

from practice_plan import (
    PracticePlanError,
    as_date,
    cap_duration_minutes,
    load_practice_plan,
    max_bookings_for_budget,
    remaining_target_hours,
)


class PracticePlanTests(unittest.TestCase):
    def test_missing_plan_preserves_legacy_allocation(self):
        plan = load_practice_plan({})
        self.assertFalse(plan.enabled)
        self.assertIsNone(plan.target_for(date(2026, 9, 1)))
        self.assertIsNone(remaining_target_hours(plan, date(2026, 9, 1), 1.0))

    def test_default_and_date_override(self):
        plan = load_practice_plan(
            {
                "practice_plan": {
                    "enabled": True,
                    "default_hours": 2.0,
                    "date_overrides": {"2026-09-01": 1.0},
                }
            }
        )
        self.assertEqual(plan.target_for(date(2026, 9, 1)), 1.0)
        self.assertEqual(plan.target_for(date(2026, 9, 2)), 2.0)

    def test_existing_hours_reduce_budget(self):
        plan = load_practice_plan(
            {"practice_plan": {"enabled": True, "default_hours": 2.0}}
        )
        self.assertEqual(remaining_target_hours(plan, date(2026, 9, 1), 1.5), 0.5)
        self.assertEqual(remaining_target_hours(plan, date(2026, 9, 1), 2.5), 0.0)

    def test_long_slot_is_trimmed_to_daily_budget(self):
        self.assertEqual(cap_duration_minutes(120, 0.5, 10), 30)
        self.assertEqual(cap_duration_minutes(120, 1.5, 10), 90)
        self.assertEqual(cap_duration_minutes(120, None, 0.75), 45)
        self.assertEqual(cap_duration_minutes(120, 0.25, 10), 0)

    def test_budget_booking_count_is_a_ceiling(self):
        self.assertEqual(max_bookings_for_budget(0.5), 1)
        self.assertEqual(max_bookings_for_budget(2.0), 4)
        self.assertEqual(max_bookings_for_budget(2.5), 5)
        self.assertEqual(max_bookings_for_budget(0.0), 0)
        self.assertIsNone(max_bookings_for_budget(None))

    def test_date_normalization_accepts_date_and_datetime(self):
        expected = date(2026, 9, 1)
        self.assertEqual(as_date(expected), expected)
        self.assertEqual(as_date(datetime(2026, 9, 1, 12, 30)), expected)

    def test_invalid_values_fail_closed(self):
        invalid_values = [True, False, -1, 0, 0.25, 12.5, math.nan, math.inf, "2"]
        for invalid in invalid_values:
            with self.subTest(invalid=invalid):
                with self.assertRaises(PracticePlanError):
                    load_practice_plan(
                        {"practice_plan": {"enabled": True, "default_hours": invalid}}
                    )

        with self.assertRaises(PracticePlanError):
            load_practice_plan(
                {
                    "practice_plan": {
                        "enabled": True,
                        "default_hours": 2.0,
                        "date_overrides": {"01/09/2026": 1.0},
                    }
                }
            )


if __name__ == "__main__":
    unittest.main()
