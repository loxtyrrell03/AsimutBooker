import unittest

from event_identity import (
    EventIdentityError,
    deduplicate_events,
    event_identity_v2,
    is_v2_event_identity_key,
    legacy_event_identity,
    resolve_ignored_event_keys,
)


class EventIdentityTests(unittest.TestCase):
    def setUp(self):
        self.event = {
            "date": "2026-08-31",
            "startTime": "09:00",
            "endTime": "10:00",
            "title": "Orchestra_rehearsal",
            "room": "B0.29",
            "isReservation": False,
        }

    def test_v2_identity_is_deterministic_delimiter_safe_and_covers_every_field(self):
        key = event_identity_v2(self.event)
        self.assertEqual(key, event_identity_v2(dict(reversed(list(self.event.items())))))
        self.assertTrue(is_v2_event_identity_key(key))
        self.assertFalse(is_v2_event_identity_key(legacy_event_identity(self.event)))

        changes = {
            "date": "2026-09-01",
            "startTime": "09:15",
            "endTime": "10:15",
            "title": "Orchestra|rehearsal",
            "room": "B1.09",
            "isReservation": True,
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                changed = dict(self.event)
                changed[field] = value
                self.assertNotEqual(event_identity_v2(changed), key)

    def test_deduplication_removes_exact_duplicate_not_same_time_neighbor(self):
        same_time_reservation = {
            **self.event,
            "title": "Reservation",
            "room": "B1.09",
            "isReservation": True,
        }
        unique = deduplicate_events(
            [self.event, dict(self.event), same_time_reservation]
        )
        self.assertEqual(unique, [self.event, same_time_reservation])

    def test_legacy_key_resolves_only_for_one_distinct_v2_identity(self):
        legacy_key = legacy_event_identity(self.event)
        unique_resolution = resolve_ignored_event_keys(
            [legacy_key],
            [self.event, dict(self.event)],
        )
        self.assertEqual(
            unique_resolution.ignored_v2_keys,
            frozenset({event_identity_v2(self.event)}),
        )
        self.assertEqual(unique_resolution.ambiguous_legacy_keys, frozenset())

        same_time_reservation = {
            **self.event,
            "title": "Reservation",
            "room": "B1.09",
            "isReservation": True,
        }
        ambiguous = resolve_ignored_event_keys(
            [legacy_key],
            [self.event, same_time_reservation],
        )
        self.assertEqual(ambiguous.ignored_v2_keys, frozenset())
        self.assertEqual(ambiguous.ambiguous_legacy_keys, frozenset({legacy_key}))

    def test_direct_v2_key_selects_only_its_exact_event(self):
        same_time_reservation = {
            **self.event,
            "title": "Reservation",
            "room": "B1.09",
            "isReservation": True,
        }
        key = event_identity_v2(same_time_reservation)
        resolution = resolve_ignored_event_keys(
            [key],
            [self.event, same_time_reservation],
        )
        self.assertEqual(resolution.ignored_v2_keys, frozenset({key}))
        self.assertEqual(resolution.ambiguous_legacy_keys, frozenset())

    def test_invalid_event_fields_fail_closed(self):
        for field, value in (
            ("date", "31/08/2026"),
            ("startTime", "9:00"),
            ("title", None),
            ("isReservation", "false"),
        ):
            with self.subTest(field=field):
                event = dict(self.event)
                event[field] = value
                with self.assertRaises(EventIdentityError):
                    event_identity_v2(event)


if __name__ == "__main__":
    unittest.main()
