import unittest
from dataclasses import dataclass
from datetime import date, datetime, timezone

from live_room_policy import (
    LiveRoomPolicyError,
    build_live_room_policy,
    canonical_overview_url,
    format_horizon_minutes,
)
from room_preferences import load_room_preferences


@dataclass(frozen=True)
class FakeRoom:
    name: str
    horizon_minutes: int
    location_id: int


class FakeCatalog:
    def __init__(self, *, fresh=True, complete=True):
        self.fresh = fresh
        self.complete = complete
        self.observed_at = datetime(2026, 8, 30, 6, 19, tzinfo=timezone.utc)
        self.booking_horizon = datetime(2026, 9, 6, 6, 19, tzinfo=timezone.utc)
        self.global_horizon_minutes = 7 * 24 * 60
        self.minimum_booking_minutes = 30
        self.maximum_booking_minutes = 120
        self.minimum_booking_gap_minutes = 60
        self.rooms = (
            FakeRoom("B0.29", 5 * 24 * 60, 96),
            FakeRoom("B1.09", 3 * 24 * 60, 87),
            FakeRoom("B0.11", 7 * 24 * 60, 75),
            FakeRoom("B0.24", 4 * 24 * 60 + 30, 92),
            FakeRoom("The Hopkins Studio", 3 * 24 * 60, 111),
        )

    def require_fresh(self):
        if not self.fresh:
            raise ValueError("stale")

    def as_live_metadata(self):
        return {
            "B0.29": {
                "instrument_tags": ["Grand Piano"],
                "room_type_tags": ["Practice Room"],
                "features": ["Steinway grand piano", "Adjustable stool"],
            },
            "B1.09": {
                "instrument_tags": ["Upright Piano"],
                "room_type_tags": ["Practice Room"],
                "features": ["Yamaha upright"],
            },
            "B0.11": {
                "instrument_tags": ["Upright Piano"],
                "room_type_tags": ["Practice Room"],
                "features": ["Kawai upright"],
            },
            "B0.24": {
                "instrument_tags": ["Percussion"],
                "room_type_tags": ["Practice Room"],
                "features": ["Music stands"],
            },
            "The Hopkins Studio": {
                "instrument_tags": [],
                "room_type_tags": ["Studio"],
                "features": ["Large rehearsal space"],
            },
        }


class LiveRoomPolicyTests(unittest.TestCase):
    def preferences(self, **updates):
        block = {
            "ordered_rooms": ["B0.29", "B1.09", "B0.11"],
            "excluded_rooms": [],
        }
        block.update(updates)
        return load_room_preferences({"room_preferences": block})

    def test_builds_only_from_fresh_complete_catalog(self):
        for catalog in (FakeCatalog(fresh=False), FakeCatalog(complete=False)):
            with self.subTest(fresh=catalog.fresh, complete=catalog.complete):
                with self.assertRaises(LiveRoomPolicyError):
                    build_live_room_policy(catalog, self.preferences())

    def test_applies_order_exclusions_metadata_and_arbitrary_horizons(self):
        policy = build_live_room_policy(
            FakeCatalog(),
            self.preferences(
                ordered_rooms=["B0.24", "B0.29", "B1.09", "B0.11"],
                excluded_rooms=["B1.09"],
                acceptable_room_type_tags=["Practice Room"],
                required_feature_terms=["music stands"],
                minimum_block_minutes=75,
                allow_fragmented_sessions=False,
            ),
        )

        self.assertEqual(policy.room_order, ("B0.24",))
        self.assertEqual(policy.horizon_minutes_for("B0.24"), 4 * 24 * 60 + 30)
        self.assertEqual(policy.minimum_block_minutes, 75)
        self.assertFalse(policy.allow_fragmented_sessions)
        with self.assertRaises(LiveRoomPolicyError):
            policy.horizon_minutes_for("B1.09")

    def test_new_live_rooms_append_after_saved_order(self):
        policy = build_live_room_policy(FakeCatalog(), self.preferences())
        self.assertEqual(
            policy.room_order,
            ("B0.29", "B1.09", "B0.11", "B0.24", "The Hopkins Studio"),
        )
        self.assertEqual(policy.all_room_names[-1], "The Hopkins Studio")

    def test_builds_exact_all_locations_url_from_fresh_catalog_order(self):
        policy = build_live_room_policy(FakeCatalog(), self.preferences())

        self.assertEqual(
            dict(policy.all_room_location_ids),
            {
                "B0.29": 96,
                "B1.09": 87,
                "B0.11": 75,
                "B0.24": 92,
                "The Hopkins Studio": 111,
            },
        )
        self.assertEqual(
            policy.overview_url,
            "https://rwcmd.asimut.net/overview?locationGroupId=0&"
            "locationIds=96,87,75,92,111",
        )

    def test_catalog_location_ids_are_strict_unique_positive_evidence(self):
        for invalid_id in (True, 0, -1, "96"):
            catalog = FakeCatalog()
            catalog.rooms = (
                FakeRoom("B0.29", 5 * 24 * 60, invalid_id),
            ) + catalog.rooms[1:]
            with self.subTest(invalid_id=invalid_id):
                with self.assertRaisesRegex(LiveRoomPolicyError, "location_id"):
                    build_live_room_policy(catalog, self.preferences())

        catalog = FakeCatalog()
        catalog.rooms = catalog.rooms[:-1] + (
            FakeRoom("The Hopkins Studio", 3 * 24 * 60, 96),
        )
        with self.assertRaisesRegex(LiveRoomPolicyError, "location_id"):
            build_live_room_policy(catalog, self.preferences())

    def test_canonical_overview_url_requires_exact_name_id_identity(self):
        with self.assertRaisesRegex(LiveRoomPolicyError, "match exactly"):
            canonical_overview_url(("B0.29", "B1.09"), {"B0.29": 96})

    def test_booking_dates_come_from_absolute_live_cutoff(self):
        policy = build_live_room_policy(FakeCatalog(), self.preferences())
        dates = policy.booking_dates(date(2026, 8, 30))

        self.assertEqual(dates[0], date(2026, 8, 30))
        self.assertEqual(dates[-1], date(2026, 9, 6))
        self.assertEqual(policy.day_offsets(date(2026, 8, 30)), tuple(range(8)))

    def test_minimum_block_must_fit_current_site_limits(self):
        catalog = FakeCatalog()
        catalog.maximum_booking_minutes = 60
        with self.assertRaisesRegex(LiveRoomPolicyError, "outside Asimut"):
            build_live_room_policy(
                catalog,
                self.preferences(minimum_block_minutes=75),
            )

    def test_empty_requirement_result_fails_closed(self):
        with self.assertRaisesRegex(LiveRoomPolicyError, "no eligible"):
            build_live_room_policy(
                FakeCatalog(),
                self.preferences(required_feature_terms=["pipe organ"]),
            )

    def test_arbitrary_horizon_formatting(self):
        self.assertEqual(format_horizon_minutes(3 * 24 * 60), "3d")
        self.assertEqual(format_horizon_minutes(4 * 24 * 60 + 75), "4d 1h 15m")


if __name__ == "__main__":
    unittest.main()
