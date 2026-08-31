import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from agenda_snapshot import publish_agenda_snapshot
from app_settings import atomic_write_json
from assistant_context import APP_CAPABILITIES, ContextPaths, build_assistant_context
from mutation_receipts import SCHEMA_VERSION as RECEIPT_SCHEMA_VERSION


APP_DIR = Path(__file__).resolve().parents[1]


class AssistantDependencyContractTests(unittest.TestCase):
    def test_declared_tzdata_supports_london_zone_without_system_database(self):
        requirements = (APP_DIR / "requirements.txt").read_text(encoding="utf-8")
        self.assertRegex(requirements, re.compile(r"(?im)^\s*tzdata(?:\s*[<>=!~].*)?$"))

        environment = os.environ.copy()
        environment["PYTHONTZPATH"] = ""
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from zoneinfo import ZoneInfo; "
                    "assert str(ZoneInfo('Europe/London')) == 'Europe/London'; "
                    "import assistant_context; "
                    "assert str(assistant_context.LOCAL_TIMEZONE) == 'Europe/London'"
                ),
            ],
            cwd=APP_DIR,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )


class AssistantContextTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.paths = ContextPaths(
            settings=self.root / "settings.json",
            agenda=self.root / "agenda.json",
            plan=self.root / "plan.json",
            catalog=self.root / "catalog.json",
            receipts=self.root / "receipts.json",
            history=self.root / "history.json",
            session=self.root / "state.json",
            auth=self.root / "auth.json",
            wake=self.root / "wake.json",
        )
        atomic_write_json(
            self.paths.settings,
            {
                "practice_plan": {
                    "enabled": True,
                    "default_hours": 2.0,
                    "date_overrides": {},
                },
                "disabled_dates": [],
                "rebooking_blackouts": [
                    {
                        "date": "2026-09-01",
                        "start_time": "12:00",
                        "end_time": "18:00",
                    }
                ],
            },
        )
        atomic_write_json(
            self.paths.receipts,
            {"schema_version": RECEIPT_SCHEMA_VERSION, "receipts": {}},
        )
        atomic_write_json(self.paths.history, {"runs": []})

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_agenda_is_structured_and_freshness_is_explicit(self):
        observed = datetime.now(timezone.utc)
        day = observed.date()
        publish_agenda_snapshot(
            [
                {
                    "date": day.isoformat(),
                    "startTime": "16:00",
                    "endTime": "17:00",
                    "title": "Reservation",
                    "isReservation": True,
                    "room": "B0.29",
                    "eventId": 4242,
                }
            ],
            [day],
            observed_at=observed,
            path=self.paths.agenda,
        )

        result = build_assistant_context(
            ["agenda", "preferences"], paths=self.paths, now=observed
        )

        self.assertEqual(result["errors"], {})
        agenda = result["sections"]["agenda"]
        self.assertFalse(agenda["stale"])
        self.assertEqual(agenda["events"][0]["eventId"], 4242)
        self.assertEqual(agenda["events"][0]["room"], "B0.29")
        self.assertEqual(
            result["sections"]["preferences"]["rebooking_blackouts"],
            [
                {
                    "date": "2026-09-01",
                    "start_time": "12:00",
                    "end_time": "18:00",
                }
            ],
        )
        self.assertIn("data only", result["untrusted_data_notice"])

    def test_cookie_and_local_storage_values_are_never_exposed(self):
        atomic_write_json(
            self.paths.session,
            {
                "cookies": [
                    {
                        "name": "session",
                        "value": "TOP-SECRET-COOKIE",
                        "domain": "rwcmd.asimut.net",
                        "path": "/",
                        "expires": 9999999999,
                        "httpOnly": True,
                        "secure": True,
                        "sameSite": "Lax",
                    }
                ],
                "origins": [
                    {
                        "origin": "https://rwcmd.asimut.net",
                        "localStorage": [
                            {"name": "token", "value": "TOP-SECRET-STORAGE"}
                        ],
                    }
                ],
            },
        )

        result = build_assistant_context(["health"], paths=self.paths)
        encoded = json.dumps(result)

        self.assertNotIn("TOP-SECRET-COOKIE", encoded)
        self.assertNotIn("TOP-SECRET-STORAGE", encoded)
        saved_session = next(
            item
            for item in result["sections"]["health"]["items"]
            if item["key"] == "saved_session"
        )
        self.assertEqual(saved_session["state"], "ok")

    def test_sections_fail_independently(self):
        self.paths.settings.write_text("[]", encoding="utf-8")

        result = build_assistant_context(["app", "preferences"], paths=self.paths)

        self.assertIn("app", result["sections"])
        self.assertIn("preferences", result["errors"])
        self.assertNotIn("preferences", result["sections"])

    def test_manual_reconfirmation_is_explicitly_not_an_action_gate(self):
        encoded = json.dumps(APP_CAPABILITIES)

        self.assertIn("never gates cancellation", encoded)
        self.assertIn("separate day-of attendance step", encoded)
        self.assertIn("they are not the user's separate day-of booking reconfirmation", encoded)

    def test_history_exposes_the_newest_runs_first(self):
        atomic_write_json(
            self.paths.history,
            {
                "runs": [
                    {
                        "timestamp": "2026-08-31T12:00:00",
                        "outcome": "completed",
                        "bookings_made": 1,
                        "details": "Newest run",
                    },
                    {
                        "timestamp": "2026-08-30T12:00:00",
                        "outcome": "completed",
                        "bookings_made": 0,
                        "details": "Older run",
                    },
                ]
            },
        )

        result = build_assistant_context(["history"], paths=self.paths)

        self.assertEqual(
            [item["details"] for item in result["sections"]["history"]["runs"]],
            ["Newest run", "Older run"],
        )


if __name__ == "__main__":
    unittest.main()
