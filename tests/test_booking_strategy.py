import json
import unittest
from dataclasses import FrozenInstanceError

from booking_strategy import (
    BookingStrategyError,
    BookingStrategyPreferences,
    DailyPlanningPreferences,
    apply_booking_strategy_update,
    booking_strategy_to_dict,
    format_clock_minutes,
    load_booking_strategy,
    parse_clock_minutes,
)


class BookingStrategyValidationTests(unittest.TestCase):
    def test_missing_strategy_uses_safe_daily_planning_defaults(self):
        strategy = load_booking_strategy({})

        self.assertEqual(
            strategy,
            BookingStrategyPreferences(
                reverse_date_order=False,
                daily_planning=DailyPlanningPreferences(),
            ),
        )
        self.assertTrue(strategy.daily_planning.enabled)
        self.assertEqual(strategy.daily_planning.preferred_peak_start, "12:00")
        self.assertEqual(strategy.daily_planning.preferred_peak_end, "16:00")
        self.assertEqual(strategy.daily_planning.desired_peak_block_minutes, 120)
        self.assertTrue(strategy.daily_planning.hold_early_peak_edges)
        self.assertEqual(strategy.daily_planning.foresight_minutes, 420)
        self.assertEqual(strategy.daily_planning.minimum_later_options, 2)
        self.assertEqual(strategy.daily_planning.fallback_lead_minutes, 120)
        self.assertEqual(strategy.daily_planning.after_peak_mode, "longest_first")
        self.assertEqual(strategy.daily_planning.priority_mode, "time_first")

    def test_legacy_reverse_date_order_is_preserved_with_new_defaults(self):
        strategy = load_booking_strategy(
            {"booking_strategy": {"reverse_date_order": True}}
        )

        self.assertTrue(strategy.reverse_date_order)
        self.assertEqual(strategy.daily_planning, DailyPlanningPreferences())

    def test_complete_strategy_round_trips_as_plain_json(self):
        raw = {
            "reverse_date_order": True,
            "daily_planning": {
                "enabled": False,
                "preferred_peak_start": "09:15",
                "preferred_peak_end": "15:45",
                "desired_peak_block_minutes": 75,
                "hold_early_peak_edges": False,
                "foresight_minutes": 180,
                "minimum_later_options": 4,
                "fallback_lead_minutes": 45,
                "after_peak_mode": "earliest_first",
                "priority_mode": "room_first",
            },
        }

        strategy = load_booking_strategy({"booking_strategy": raw})
        serialized = booking_strategy_to_dict(strategy)

        self.assertEqual(json.loads(json.dumps(serialized)), raw)
        self.assertEqual(
            load_booking_strategy({"booking_strategy": serialized}), strategy
        )

    def test_preference_objects_are_immutable(self):
        strategy = load_booking_strategy({})
        with self.assertRaises(FrozenInstanceError):
            strategy.reverse_date_order = True
        with self.assertRaises(FrozenInstanceError):
            strategy.daily_planning.enabled = False

    def test_non_objects_non_string_keys_and_unknown_fields_fail_closed(self):
        for value in (None, [], "strategy", 1, False):
            with self.subTest(strategy=value):
                with self.assertRaises(BookingStrategyError):
                    load_booking_strategy({"booking_strategy": value})
        with self.assertRaises(BookingStrategyError):
            load_booking_strategy([])
        with self.assertRaises(BookingStrategyError):
            load_booking_strategy({"booking_strategy": {1: False}})
        with self.assertRaises(BookingStrategyError):
            load_booking_strategy({"booking_strategy": {"date_order": "reverse"}})
        with self.assertRaises(BookingStrategyError):
            load_booking_strategy(
                {"booking_strategy": {"daily_planning": {"prefer_lunch": True}}}
            )

    def test_boolean_fields_reject_coercions(self):
        for field in ("enabled", "hold_early_peak_edges"):
            for value in (0, 1, "true", None):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(BookingStrategyError):
                        load_booking_strategy(
                            {
                                "booking_strategy": {
                                    "daily_planning": {field: value}
                                }
                            }
                        )
        for value in (0, 1, "false", None):
            with self.subTest(reverse_date_order=value):
                with self.assertRaises(BookingStrategyError):
                    load_booking_strategy(
                        {"booking_strategy": {"reverse_date_order": value}}
                    )

    def test_peak_times_require_canonical_quarter_hours_and_order(self):
        invalid_pairs = (
            ("9:00", "16:00"),
            ("09:05", "16:00"),
            ("24:00", "16:00"),
            ("16:00", "16:00"),
            ("15:00", "12:00"),
            (12, "16:00"),
        )
        for start, end in invalid_pairs:
            with self.subTest(start=start, end=end):
                with self.assertRaises(BookingStrategyError):
                    load_booking_strategy(
                        {
                            "booking_strategy": {
                                "daily_planning": {
                                    "preferred_peak_start": start,
                                    "preferred_peak_end": end,
                                }
                            }
                        }
                    )

    def test_preferred_window_can_follow_an_advanced_peak_rule(self):
        strategy = load_booking_strategy(
            {
                "booking_strategy": {
                    "daily_planning": {
                        "preferred_peak_start": "08:45",
                        "preferred_peak_end": "16:15",
                    }
                }
            }
        )

        self.assertEqual(strategy.daily_planning.preferred_peak_start, "08:45")
        self.assertEqual(strategy.daily_planning.preferred_peak_end, "16:15")

    def test_integer_ranges_steps_and_bool_as_int_fail_closed(self):
        invalid = {
            "desired_peak_block_minutes": (True, 29, 31, 121, 60.0, "60"),
            "foresight_minutes": (True, -15, 1, 1455, 60.0, "60"),
            "minimum_later_options": (True, 0, 6, 2.0, "2"),
            "fallback_lead_minutes": (True, -15, 1, 255, 60.0, "60"),
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(BookingStrategyError):
                        load_booking_strategy(
                            {
                                "booking_strategy": {
                                    "daily_planning": {field: value}
                                }
                            }
                        )

    def test_enum_fields_are_exact(self):
        invalid = {
            "after_peak_mode": ("latest_first", "Longest_First", 1, None),
            "priority_mode": ("balanced", "TIME_FIRST", 1, None),
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(BookingStrategyError):
                        load_booking_strategy(
                            {
                                "booking_strategy": {
                                    "daily_planning": {field: value}
                                }
                            }
                        )

    def test_disabled_planning_still_validates_every_dormant_value(self):
        with self.assertRaises(BookingStrategyError):
            load_booking_strategy(
                {
                    "booking_strategy": {
                        "daily_planning": {
                            "enabled": False,
                            "foresight_minutes": "420",
                        }
                    }
                }
            )


class BookingStrategyUpdateTests(unittest.TestCase):
    def test_scoped_nested_update_preserves_unrelated_and_unedited_fields(self):
        settings = {
            "cached_events": [{"id": 1}],
            "booking_strategy": {
                "reverse_date_order": True,
                "daily_planning": {
                    "preferred_peak_start": "11:00",
                    "foresight_minutes": 300,
                    "priority_mode": "room_first",
                },
            },
        }

        merged = apply_booking_strategy_update(
            settings,
            {
                "daily_planning": {
                    "preferred_peak_end": "15:30",
                    "minimum_later_options": 3,
                }
            },
        )

        self.assertEqual(settings["cached_events"], [{"id": 1}])
        self.assertTrue(merged.reverse_date_order)
        self.assertEqual(merged.daily_planning.preferred_peak_start, "11:00")
        self.assertEqual(merged.daily_planning.preferred_peak_end, "15:30")
        self.assertEqual(merged.daily_planning.foresight_minutes, 300)
        self.assertEqual(merged.daily_planning.minimum_later_options, 3)
        self.assertEqual(merged.daily_planning.priority_mode, "room_first")
        self.assertEqual(
            settings["booking_strategy"], booking_strategy_to_dict(merged)
        )

    def test_reverse_order_can_be_updated_without_losing_daily_planning(self):
        settings = {
            "booking_strategy": {
                "daily_planning": {"preferred_peak_start": "13:00"}
            }
        }

        merged = apply_booking_strategy_update(
            settings, {"reverse_date_order": True}
        )

        self.assertTrue(merged.reverse_date_order)
        self.assertEqual(merged.daily_planning.preferred_peak_start, "13:00")

    def test_failed_update_never_partially_mutates_document(self):
        settings = {"other": "preserve"}
        before = dict(settings)
        with self.assertRaises(BookingStrategyError):
            apply_booking_strategy_update(
                settings,
                {"daily_planning": {"fallback_lead_minutes": 17}},
            )
        self.assertEqual(settings, before)

    def test_malformed_existing_dormant_value_blocks_scoped_update(self):
        settings = {
            "booking_strategy": {
                "daily_planning": {
                    "enabled": False,
                    "desired_peak_block_minutes": "120",
                }
            }
        }
        with self.assertRaises(BookingStrategyError):
            apply_booking_strategy_update(settings, {"reverse_date_order": True})

    def test_update_rejects_unknown_and_non_object_nested_patches(self):
        for patch in (
            {"date_order": "reverse"},
            {"daily_planning": []},
            {"daily_planning": {"prefer_lunch": True}},
            {1: True},
        ):
            with self.subTest(patch=patch):
                with self.assertRaises(BookingStrategyError):
                    apply_booking_strategy_update({}, patch)


class ClockHelperTests(unittest.TestCase):
    def test_clock_helpers_round_trip_quarter_hours(self):
        for value in (0, 9 * 60, 12 * 60 + 15, 23 * 60 + 45):
            self.assertEqual(parse_clock_minutes(format_clock_minutes(value)), value)

    def test_clock_helpers_reject_noncanonical_values(self):
        for value in (True, 1.0, -15, 1, 24 * 60, 61):
            with self.subTest(format_value=value):
                with self.assertRaises(BookingStrategyError):
                    format_clock_minutes(value)
        for value in ("9:00", "24:00", "12:01", 720, None):
            with self.subTest(parse_value=value):
                with self.assertRaises(BookingStrategyError):
                    parse_clock_minutes(value)


if __name__ == "__main__":
    unittest.main()
