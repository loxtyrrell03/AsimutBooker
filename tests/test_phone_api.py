import unittest
from unittest import mock

import phone_api


def context_fixture(*, stale=False, pending=False):
    return {
        "local_now": "2026-08-31T11:30:00+01:00",
        "timezone": "Europe/London",
        "sections": {
            "preferences": {
                "practice_plan": {
                    "enabled": True,
                    "default_hours": 2.0,
                    "date_overrides": {"2026-09-06": 4.0},
                },
                "time_preferences": {
                    "enabled": True,
                    "start_time": "12:30",
                    "end_time": "21:00",
                    "strict_mode": True,
                },
                "future_practice_intentions": [
                    {
                        "title": "Busy week",
                        "intent_summary": "Two hours weekdays, more at the weekend",
                        "start_date": "2026-08-31",
                        "end_date": "2026-09-06",
                        "daily_targets": {"private": "not for phone"},
                    }
                ],
                "rebooking_blackouts": [
                    {
                        "date": "2026-09-01",
                        "start_time": "12:00",
                        "end_time": "18:00",
                        "internal": "not for phone",
                    }
                ],
            },
            "agenda": {
                "available": True,
                "stale": stale,
                "observed_at": "2026-08-31T10:43:30Z",
                "freshness_reason": "needs refresh" if stale else "",
                "events": [
                    {
                        "date": "2026-09-01",
                        "startTime": "11:00",
                        "endTime": "11:30",
                        "title": "Reservation",
                        "isReservation": True,
                        "room": "B1.09",
                        "eventId": 999,
                        "match_token": "sha256:secret",
                    }
                ],
            },
            "plan": {
                "available": True,
                "stale": stale,
                "generated_at": "2026-08-31T10:45:00Z",
                "summary": "Waiting for the best room",
                "days": [
                    {
                        "date": "2026-09-07",
                        "status": "waiting",
                        "target_minutes": 120,
                        "existing_minutes": 0,
                        "reason": "Waiting",
                        "primary": {
                            "room": "Weston Gallery",
                            "date": "2026-09-07",
                            "start_time": "12:30",
                            "end_time": "14:30",
                            "state": "waiting",
                            "reason": "Best visible opportunity",
                            "confirmed_minutes": 0,
                            "potential_minutes": 120,
                            "unlock_at": "2026-08-31T12:00:00Z",
                            "internal": "not exposed",
                        },
                        "additional": [],
                        "backups": [],
                    }
                ],
            },
            "health": {
                "collected_at": "2026-08-31T11:30:00+01:00",
                "items": [
                    {
                        "key": "last_success",
                        "label": "Last successful run",
                        "state": "ok",
                        "headline": "Run completed",
                        "detail": "Verified history exists",
                        "observed_at": "2026-08-30T17:05:00+01:00",
                    }
                ],
            },
            "mutations": {
                "pending": ([{"receipt": "must-not-leak"}] if pending else [])
            },
        },
        "errors": {"rooms": "C:\\private\\path\\room_catalog.json"},
    }


class PhoneSnapshotTests(unittest.TestCase):
    def test_snapshot_is_small_and_omits_mutation_authority(self):
        with mock.patch.object(
            phone_api, "build_assistant_context", return_value=context_fixture()
        ):
            snapshot = phone_api.build_phone_snapshot()

        encoded = str(snapshot)
        self.assertNotIn("eventId", encoded)
        self.assertNotIn("match_token", encoded)
        self.assertNotIn("sha256:secret", encoded)
        self.assertNotIn("must-not-leak", encoded)
        self.assertNotIn("private\\path", encoded)
        self.assertEqual(snapshot["unavailable_sections"], ["rooms"])
        self.assertEqual(snapshot["agenda"]["next_event"]["room"], "B1.09")
        self.assertEqual(snapshot["plan"]["days"][0]["primary"]["room"], "Weston Gallery")
        self.assertEqual(snapshot["preferences"]["future_intentions"][0]["title"], "Busy week")
        self.assertEqual(
            snapshot["preferences"]["rebooking_blackouts"],
            [
                {
                    "date": "2026-09-01",
                    "start_time": "12:00",
                    "end_time": "18:00",
                }
            ],
        )

    def test_pending_receipt_blocks_phone_status_without_leaking_receipt(self):
        with mock.patch.object(
            phone_api,
            "build_assistant_context",
            return_value=context_fixture(pending=True),
        ):
            snapshot = phone_api.build_phone_snapshot()

        self.assertEqual(snapshot["status"]["state"], "blocked")
        self.assertEqual(snapshot["status"]["pending_mutations"], 1)
        self.assertNotIn("receipt", str(snapshot))

    def test_stale_agenda_is_labelled(self):
        with mock.patch.object(
            phone_api,
            "build_assistant_context",
            return_value=context_fixture(stale=True),
        ):
            snapshot = phone_api.build_phone_snapshot()

        self.assertEqual(snapshot["status"]["state"], "stale")
        self.assertTrue(snapshot["agenda"]["stale"])

    def test_next_event_uses_event_date_timezone_across_dst_change(self):
        events = [
            {
                "date": "2026-10-25",
                "start_time": "09:00",
                "end_time": "10:00",
                "room": "B0.29",
            }
        ]
        result = phone_api._next_event(
            events,
            "2026-10-24T23:30:00+01:00",
            "Europe/London",
        )
        self.assertEqual(result, events[0])

    def test_next_event_rejects_unknown_timezone(self):
        self.assertIsNone(
            phone_api._next_event([], "2026-08-31T11:30:00+01:00", "Mars/Olympus")
        )

    def test_next_event_skips_ambiguous_and_nonexistent_dst_times(self):
        for now, event_date in (
            ("2026-10-25T00:30:00+01:00", "2026-10-25"),
            ("2026-03-29T00:30:00+00:00", "2026-03-29"),
        ):
            with self.subTest(event_date=event_date):
                self.assertIsNone(
                    phone_api._next_event(
                        [
                            {
                                "date": event_date,
                                "start_time": "01:30",
                                "end_time": "02:00",
                                "room": "B0.29",
                            }
                        ],
                        now,
                        "Europe/London",
                    )
                )

    def test_independent_plan_failure_is_attention_not_ready(self):
        context = context_fixture()
        context["sections"].pop("plan")
        context["errors"] = {"plan": "C:\\private\\plan failure"}
        with mock.patch.object(phone_api, "build_assistant_context", return_value=context):
            snapshot = phone_api.build_phone_snapshot()

        self.assertEqual(snapshot["status"]["state"], "attention")
        self.assertEqual(snapshot["unavailable_sections"], ["plan"])
        self.assertNotIn("private", str(snapshot))

    def test_missing_current_plan_keeps_booker_ready_with_update_label(self):
        context = context_fixture()
        context["sections"]["plan"] = {"available": False, "stale": True, "days": []}
        context["errors"] = {}
        with mock.patch.object(phone_api, "build_assistant_context", return_value=context):
            snapshot = phone_api.build_phone_snapshot()

        self.assertEqual(snapshot["status"]["state"], "ready")
        self.assertEqual(
            snapshot["status"]["label"],
            "Booker ready · plan update available",
        )


if __name__ == "__main__":
    unittest.main()
