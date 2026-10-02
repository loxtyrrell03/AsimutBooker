import unittest

from event_identity import (
    EventIdentityError,
    deduplicate_events,
    event_choice_eligibility,
    event_identity,
    event_identity_v2,
    event_identity_v3,
    event_respect_key,
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

    def test_remote_event_ids_preserve_distinct_same_identity_events(self):
        first = {**self.event, "eventId": 3580086}
        repeated_dom_copy = dict(first)
        separate_remote_event = {**self.event, "eventId": 3580087}

        unique = deduplicate_events(
            [first, repeated_dom_copy, separate_remote_event]
        )

        self.assertEqual(unique, [first, separate_remote_event])

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

    def test_v3_binds_positive_remote_id_date_and_every_reviewed_field(self):
        event = self.event | {'eventId': 100}
        key = event_identity_v3(event)
        self.assertEqual(event_identity(event), key)
        self.assertEqual(event_identity_v3(event | {'eventId': '100'}), key)
        for field, value in {'eventId': 101, 'date': '2026-09-01', 'startTime': '09:15',
                             'endTime': '10:15', 'title': 'Another class', 'room': 'B1.15',
                             'isReservation': True}.items():
            with self.subTest(field=field):
                self.assertNotEqual(event_identity_v3(event | {field: value}), key)
                self.assertFalse(resolve_ignored_event_keys([key], [event | {field: value}]).ignored_event_keys)

    def test_missing_or_invalid_remote_identity_cannot_be_new_exact_permission(self):
        for identifier in (None, 0, -1, True, '', '0', '001', '1.0', 1.5):
            event = self.event | {'eventId': identifier}
            with self.subTest(identifier=identifier), self.assertRaises(EventIdentityError):
                event_identity_v3(event)
            self.assertFalse(event_choice_eligibility(event, [event])[0])

    def test_same_tuple_distinct_remote_events_have_independent_exact_permissions(self):
        first, second = (self.event | {'eventId': identifier} for identifier in (100, 101))
        key = event_identity_v3(first)
        resolution = resolve_ignored_event_keys([key], [first, second])
        self.assertEqual(resolution.ignored_event_keys, {key})
        self.assertFalse(resolution.ignored_v2_keys, 'An old consumer must never broaden the exact choice')
        for old in (legacy_event_identity(first), event_identity_v2(first)):
            ambiguous = resolve_ignored_event_keys([old], [first, second])
            self.assertFalse(ambiguous.ignored_event_keys)
            self.assertIn(old, ambiguous.ambiguous_legacy_keys | ambiguous.ambiguous_v2_keys)

    def test_existing_unique_old_choices_remain_effective_without_storage_migration(self):
        event = self.event | {'eventId': 100}
        for old in (legacy_event_identity(event), event_identity_v2(event)):
            stored = [old, 'out-of-view']
            resolution = resolve_ignored_event_keys(stored, [event, dict(event)])
            self.assertEqual(resolution.ignored_event_keys, {event_identity_v3(event)})
            self.assertEqual(stored, [old, 'out-of-view'])
            # Historical storage has no remote ID: its known limitation stays
            # explicit until the owner reviews and rebinds that dated choice.
            replacement = event | {'eventId': 101}
            self.assertEqual(resolve_ignored_event_keys(stored, [replacement]).ignored_event_keys,
                             {event_identity_v3(replacement)})

    def test_conflicting_same_remote_dated_copies_are_preserved_and_authorize_nothing(self):
        first = self.event | {'eventId': 100}
        conflict = first | {'title': 'Changed detail'}
        self.assertEqual(deduplicate_events([first, dict(first), conflict]), [first, conflict])
        for key in (event_identity_v3(first), event_identity_v2(first), legacy_event_identity(first)):
            result = resolve_ignored_event_keys([key], [first, conflict])
            self.assertFalse(result.ignored_event_keys)
            self.assertEqual(result.ambiguous_event_keys, {event_identity(first), event_identity(conflict)})
        self.assertFalse(event_choice_eligibility(first, [first, conflict])[0])

    def test_same_remote_id_on_different_dates_remains_individually_dated(self):
        first = self.event | {'eventId': 100}
        next_day = first | {'date': '2026-09-01'}
        self.assertEqual(deduplicate_events([first, next_day]), [first, next_day])
        self.assertTrue(event_choice_eligibility(first, [first, next_day])[0])
        self.assertEqual(resolve_ignored_event_keys([event_identity_v3(first)], [first, next_day]).ignored_event_keys,
                         {event_identity_v3(first)})

    def test_existing_reservation_permission_is_preserved_but_new_control_is_ineligible(self):
        event = self.event | {'eventId': 100, 'isReservation': True}
        self.assertEqual(resolve_ignored_event_keys([event_identity_v2(event)], [event]).ignored_event_keys,
                         {event_identity_v3(event)})
        self.assertFalse(event_choice_eligibility(event, [event])[0])

    def test_exact_respect_wins_when_older_collision_disappears(self):
        first = self.event | {'eventId': 100}
        neighbor = self.event | {'eventId': 101}
        for old in (legacy_event_identity(first), event_identity_v2(first)):
            stored = [old, event_respect_key(first)]
            self.assertFalse(resolve_ignored_event_keys(stored, [first, neighbor]).ignored_event_keys)
            remaining = resolve_ignored_event_keys(stored, [first])
            self.assertFalse(remaining.ignored_event_keys)
            self.assertEqual(remaining.respected_event_keys, {event_identity(first)})
            # Respect belongs only to the reviewed remote event. It does not
            # silently retire a historical choice for an unrelated remaining row.
            self.assertEqual(resolve_ignored_event_keys(stored, [neighbor]).ignored_event_keys,
                             {event_identity(neighbor)})

    def test_exact_respect_overrides_conflicting_allow_but_not_changed_identity(self):
        event = self.event | {'eventId': 100}
        result = resolve_ignored_event_keys([event_identity(event), event_respect_key(event)], [event])
        self.assertFalse(result.ignored_event_keys)
        self.assertFalse(result.ignored_v2_keys)
        for field, value in {'eventId': 101, 'date': '2026-09-01', 'startTime': '09:15',
                             'endTime': '10:15', 'title': 'Other class', 'room': 'B1.15',
                             'isReservation': True}.items():
            changed = event | {field: value}
            result = resolve_ignored_event_keys([event_respect_key(event), event_identity(changed)], [changed])
            with self.subTest(field=field):
                self.assertEqual(result.ignored_event_keys, {event_identity(changed)})
                self.assertFalse(result.respected_event_keys)


if __name__ == "__main__":
    unittest.main()
