import json
import unittest

from room_preferences import (
    DEFAULT_ORDERED_ROOMS,
    RoomPreferencesError,
    apply_room_preferences_update,
    effective_room_order,
    filter_effective_rooms,
    load_room_preferences,
    reconcile_room_order,
    reconcile_room_preferences,
    room_preferences_to_dict,
    summarize_room_preferences,
)


class RoomPreferencesValidationTests(unittest.TestCase):
    def test_missing_preferences_preserve_current_booking_behavior(self):
        preferences = load_room_preferences({})

        self.assertEqual(preferences.ordered_rooms, DEFAULT_ORDERED_ROOMS)
        self.assertEqual(preferences.excluded_rooms, ())
        self.assertEqual(preferences.acceptable_instrument_tags, ())
        self.assertEqual(preferences.acceptable_room_type_tags, ())
        self.assertEqual(preferences.required_feature_terms, ())
        self.assertEqual(preferences.minimum_block_minutes, 30)
        self.assertTrue(preferences.allow_fragmented_sessions)

    def test_complete_preferences_round_trip_as_json(self):
        settings = {
            "room_preferences": {
                "ordered_rooms": ["B0.29", "A3.39", "B1.09"],
                "excluded_rooms": ["B1.09"],
                "acceptable_instrument_tags": ["Grand piano", "Upright piano"],
                "acceptable_room_type_tags": ["Practice room"],
                "required_feature_terms": ["adjustable stool", "natural light"],
                "minimum_block_minutes": 75,
                "allow_fragmented_sessions": False,
            }
        }

        preferences = load_room_preferences(settings)
        serialized = room_preferences_to_dict(preferences)

        self.assertEqual(json.loads(json.dumps(serialized)), settings["room_preferences"])
        self.assertEqual(load_room_preferences({"room_preferences": serialized}), preferences)

    def test_invalid_room_lists_fail_closed(self):
        invalid_blocks = {
            "not a list": {"ordered_rooms": "B0.29"},
            "empty order": {"ordered_rooms": []},
            "untrimmed room": {"ordered_rooms": [" B0.29"]},
            "control in room": {"ordered_rooms": ["B0.29\n"]},
            "duplicate order": {"ordered_rooms": ["B0.29", "B0.29"]},
            "duplicate exclusion": {
                "ordered_rooms": ["B0.29"],
                "excluded_rooms": ["B0.29", "B0.29"],
            },
            "unknown exclusion": {
                "ordered_rooms": ["B0.29"],
                "excluded_rooms": ["A3.39"],
            },
        }
        for label, block in invalid_blocks.items():
            with self.subTest(label=label):
                with self.assertRaises(RoomPreferencesError):
                    load_room_preferences({"room_preferences": block})

    def test_invalid_tags_and_terms_fail_closed(self):
        invalid_values = [
            "Grand piano",
            [1],
            [""],
            [" Grand piano"],
            ["Grand piano\n"],
            ["Piano", "piano"],
        ]
        for field in (
            "acceptable_instrument_tags",
            "acceptable_room_type_tags",
            "required_feature_terms",
        ):
            for value in invalid_values:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(RoomPreferencesError):
                        load_room_preferences({"room_preferences": {field: value}})

    def test_invalid_duration_fragmentation_and_unknown_fields_fail_closed(self):
        for value in (True, 29, 31, 121, 45.0, "45"):
            with self.subTest(minimum=value):
                with self.assertRaises(RoomPreferencesError):
                    load_room_preferences(
                        {"room_preferences": {"minimum_block_minutes": value}}
                    )

        for value in (0, 1, "yes", None):
            with self.subTest(fragmented=value):
                with self.assertRaises(RoomPreferencesError):
                    load_room_preferences(
                        {"room_preferences": {"allow_fragmented_sessions": value}}
                    )

        with self.assertRaises(RoomPreferencesError):
            load_room_preferences({"room_preferences": {"minimum_block_length": 60}})

    def test_existing_non_object_block_and_non_object_settings_fail_closed(self):
        for value in (None, [], "preferences", 1, False):
            with self.subTest(value=value):
                with self.assertRaises(RoomPreferencesError):
                    load_room_preferences({"room_preferences": value})
        with self.assertRaises(RoomPreferencesError):
            load_room_preferences([])

    def test_scoped_merge_preserves_unrelated_and_unedited_values(self):
        settings = {
            "cached_events": [{"id": 1}],
            "room_preferences": {
                "ordered_rooms": ["B0.29", "A3.39"],
                "excluded_rooms": [],
                "acceptable_instrument_tags": ["Grand piano"],
                "acceptable_room_type_tags": ["Practice room"],
                "required_feature_terms": ["stool"],
                "minimum_block_minutes": 30,
                "allow_fragmented_sessions": True,
            },
        }

        merged = apply_room_preferences_update(
            settings,
            {
                "excluded_rooms": ["A3.39"],
                "minimum_block_minutes": 60,
            },
        )

        self.assertEqual(settings["cached_events"], [{"id": 1}])
        self.assertEqual(merged.acceptable_instrument_tags, ("Grand piano",))
        self.assertEqual(merged.acceptable_room_type_tags, ("Practice room",))
        self.assertEqual(merged.required_feature_terms, ("stool",))
        self.assertEqual(merged.excluded_rooms, ("A3.39",))
        self.assertEqual(merged.minimum_block_minutes, 60)

    def test_failed_scoped_merge_does_not_partially_mutate_settings(self):
        settings = {"other": "preserve"}
        before = dict(settings)
        with self.assertRaises(RoomPreferencesError):
            apply_room_preferences_update(settings, {"minimum_block_minutes": 44})
        self.assertEqual(settings, before)


class RoomCatalogReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.preferences = load_room_preferences(
            {
                "room_preferences": {
                    "ordered_rooms": ["B0.29", "A3.39", "B1.09"],
                    "excluded_rooms": ["B1.09"],
                }
            }
        )

    def test_reconciliation_keeps_missing_saved_rank_and_appends_new_rooms(self):
        reconciled = reconcile_room_order(
            self.preferences.ordered_rooms,
            ["B1.09", "B0.24", "B0.29"],
        )

        self.assertEqual(reconciled, ("B0.29", "A3.39", "B1.09", "B0.24"))
        updated = reconcile_room_preferences(
            self.preferences,
            ["B1.09", "B0.24", "B0.29"],
        )
        self.assertEqual(updated.ordered_rooms, reconciled)
        self.assertEqual(updated.excluded_rooms, ("B1.09",))

    def test_effective_order_omits_missing_and_excluded_without_losing_order(self):
        live = ["B1.09", "B0.24", "B0.29"]
        self.assertEqual(
            effective_room_order(self.preferences, live),
            ("B0.29", "B0.24"),
        )
        self.assertEqual(
            self.preferences.ordered_rooms,
            ("B0.29", "A3.39", "B1.09"),
        )

    def test_bad_live_catalog_fails_closed(self):
        for catalog in (
            [" B0.29"],
            ["B0.29", "B0.29"],
            "B0.29",
            {"B0.29"},
        ):
            with self.subTest(catalog=catalog):
                with self.assertRaises(RoomPreferencesError):
                    effective_room_order(self.preferences, catalog)

    def test_site_discovered_named_room_is_preserved_exactly(self):
        updated = reconcile_room_preferences(
            self.preferences,
            ["B0.29", "The Hopkins Studio"],
        )

        self.assertEqual(updated.ordered_rooms[-1], "The Hopkins Studio")
        self.assertEqual(
            effective_room_order(updated, ["B0.29", "The Hopkins Studio"]),
            ("B0.29", "The Hopkins Studio"),
        )


class RoomMetadataFilterTests(unittest.TestCase):
    def setUp(self):
        self.metadata = {
            "B0.29": {
                "instrument_tags": ["Grand Piano", "Keyboard"],
                "room_type_tags": ["Practice Room"],
                "features": ["Steinway grand piano", "Adjustable piano stool"],
            },
            "A3.39": {
                "instrument_tags": ["Upright Piano"],
                "room_type_tags": ["Teaching Room"],
                "features": ["Yamaha upright", "Natural light"],
            },
            "B0.24": {
                "instrument_tags": ["Percussion"],
                "room_type_tags": ["Practice Room"],
                "features": ["Music stands"],
            },
        }

    def _preferences(self, **updates):
        block = {
            "ordered_rooms": ["B0.29", "A3.39", "B1.09"],
            "excluded_rooms": [],
        }
        block.update(updates)
        return load_room_preferences({"room_preferences": block})

    def test_empty_metadata_requirements_do_not_filter(self):
        self.assertEqual(
            filter_effective_rooms(self._preferences(), self.metadata),
            ("B0.29", "A3.39", "B0.24"),
        )

    def test_instruments_and_types_are_or_within_each_category(self):
        preferences = self._preferences(
            acceptable_instrument_tags=["upright piano", "organ"],
            acceptable_room_type_tags=["teaching room", "ensemble room"],
        )
        self.assertEqual(filter_effective_rooms(preferences, self.metadata), ("A3.39",))

        preferences = self._preferences(
            acceptable_instrument_tags=["grand piano", "upright piano"],
            acceptable_room_type_tags=["practice room"],
        )
        self.assertEqual(filter_effective_rooms(preferences, self.metadata), ("B0.29",))

    def test_requirements_are_and_between_categories(self):
        preferences = self._preferences(
            acceptable_instrument_tags=["grand piano"],
            acceptable_room_type_tags=["teaching room"],
        )
        self.assertEqual(filter_effective_rooms(preferences, self.metadata), ())

    def test_all_feature_terms_are_required_and_match_descriptive_features(self):
        preferences = self._preferences(
            required_feature_terms=["steinway", "adjustable stool"],
        )
        self.assertEqual(filter_effective_rooms(preferences, self.metadata), ("B0.29",))

        preferences = self._preferences(
            required_feature_terms=["steinway", "natural light"],
        )
        self.assertEqual(filter_effective_rooms(preferences, self.metadata), ())

    def test_exclusion_wins_before_metadata_matching(self):
        preferences = self._preferences(excluded_rooms=["B0.29"])
        self.assertEqual(
            filter_effective_rooms(preferences, self.metadata),
            ("A3.39", "B0.24"),
        )

    def test_missing_metadata_category_does_not_satisfy_a_selected_filter(self):
        preferences = self._preferences(acceptable_instrument_tags=["Grand piano"])
        self.metadata["B0.29"] = {}
        self.assertEqual(filter_effective_rooms(preferences, self.metadata), ())

    def test_malformed_metadata_fails_closed(self):
        preferences = self._preferences()
        self.metadata["B0.29"]["features"] = "Steinway"
        with self.assertRaises(RoomPreferencesError):
            filter_effective_rooms(preferences, self.metadata)

    def test_summary_is_concise_and_reports_live_eligibility(self):
        preferences = self._preferences(
            excluded_rooms=["B1.09"],
            acceptable_instrument_tags=["Grand Piano"],
            required_feature_terms=["stool"],
            minimum_block_minutes=60,
            allow_fragmented_sessions=False,
        )
        summary = summarize_room_preferences(preferences, self.metadata)

        self.assertIn("1 live eligible room", summary)
        self.assertIn("1 excluded", summary)
        self.assertIn("Instrument: Grand Piano", summary)
        self.assertIn("Needs: stool", summary)
        self.assertIn("60+ min", summary)
        self.assertIn("single blocks only", summary)


if __name__ == "__main__":
    unittest.main()
