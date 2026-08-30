import io
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from runtime_guard import (
    SingleInstanceAlreadyRunning,
    SingleInstanceLock,
    booking_identity_matches,
    booking_summary_matches,
    booking_times_match,
    configure_utf8_stdio,
    hhmm_values_match,
    is_confirmed_post_save_url,
    is_new_booking_form_url,
    normalize_date,
    parse_confirmed_event_id,
    parse_event_editor_id,
)


class Utf8StdioTests(unittest.TestCase):
    def test_redirected_cp1252_streams_are_reconfigured_to_utf8(self):
        stdout_bytes = io.BytesIO()
        stderr_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="cp1252")
        stderr = io.TextIOWrapper(stderr_bytes, encoding="cp1252")

        try:
            configured = configure_utf8_stdio(stdout=stdout, stderr=stderr)
            self.assertEqual(configured, (True, True))
            self.assertEqual(stdout.encoding.lower().replace("_", "-"), "utf-8")
            self.assertEqual(stderr.encoding.lower().replace("_", "-"), "utf-8")

            stdout.write("ready \u2713")
            stderr.write("safe \u2713")
            stdout.flush()
            stderr.flush()
            self.assertEqual(stdout_bytes.getvalue().decode("utf-8"), "ready \u2713")
            self.assertEqual(stderr_bytes.getvalue().decode("utf-8"), "safe \u2713")
        finally:
            stdout.detach()
            stderr.detach()

    def test_unicode_native_capture_stream_is_left_intact(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        self.assertEqual(
            configure_utf8_stdio(stdout=stdout, stderr=stderr), (False, False)
        )
        stdout.write("\u2713")
        self.assertEqual(stdout.getvalue(), "\u2713")


class SingleInstanceLockTests(unittest.TestCase):
    def test_lock_is_nonblocking_and_released_by_context_manager(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime.lock"
            first = SingleInstanceLock(path)
            self.assertTrue(first.acquire())
            self.assertTrue(first.acquired)
            self.assertTrue(first.acquire(), "acquire should be idempotent for its owner")

            second = SingleInstanceLock(path)
            self.assertFalse(second.acquire())
            with self.assertRaises(SingleInstanceAlreadyRunning):
                with second:
                    self.fail("contended context manager must not enter")

            first.release()
            self.assertFalse(first.acquired)
            with second:
                self.assertTrue(second.acquired)
            self.assertFalse(second.acquired)
            self.assertTrue(path.exists())

    def test_lock_excludes_a_separate_python_process(self):
        project_root = Path(__file__).resolve().parents[1]
        child_script = """
import sys
from runtime_guard import SingleInstanceLock

lock = SingleInstanceLock(sys.argv[1])
acquired = lock.acquire()
if acquired:
    lock.release()
sys.exit(10 if acquired else 0)
"""

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cross-process.lock"
            parent_lock = SingleInstanceLock(path)
            self.assertTrue(parent_lock.acquire())
            try:
                blocked = subprocess.run(
                    [sys.executable, "-c", child_script, str(path)],
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(blocked.returncode, 0, blocked.stderr)
            finally:
                parent_lock.release()

            available = subprocess.run(
                [sys.executable, "-c", child_script, str(path)],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(
                available.returncode,
                10,
                "child should acquire the lock after the parent releases it",
            )


class DateNormalizationTests(unittest.TestCase):
    def test_date_datetime_and_iso_string_normalize_to_date(self):
        expected = date(2026, 8, 30)
        self.assertIs(normalize_date(expected), expected)
        self.assertEqual(normalize_date(datetime(2026, 8, 30, 23, 59)), expected)
        self.assertEqual(normalize_date("2026-08-30"), expected)

    def test_invalid_date_values_are_rejected(self):
        for value in ("30/08/2026", "2026-02-30", "20260830"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_date(value)
        with self.assertRaises(TypeError):
            normalize_date(20260830)  # type: ignore[arg-type]


class PostSaveUrlTests(unittest.TestCase):
    def test_positive_arrangement_event_id_is_confirmed(self):
        urls = (
            "/arrangement?eventId=1",
            "https://rwcmd.asimut.net/arrangement?eventId=42",
        )
        expected_ids = (1, 42)
        for url, expected in zip(urls, expected_ids):
            with self.subTest(url=url):
                self.assertEqual(parse_confirmed_event_id(url), expected)
                self.assertTrue(is_confirmed_post_save_url(url))

    def test_pre_save_and_noncanonical_urls_are_rejected(self):
        rejected = (
            "/event?eventId=0",
            "/arrangement?eventId=0",
            "/arrangement?eventId=-1",
            "/arrangement?eventId=abc",
            "/arrangement?eventId=01",
            "/arrangement?eventId=1&other=value",
            "/arrangement?other=value&eventId=1",
            "/arrangement?eventId=1&eventId=2",
            "/arrangement/?eventId=1",
            "/arrangement?eventId=1#details",
            "https://rwcmd.asimut.net/event?eventId=1",
            "//rwcmd.asimut.net/arrangement?eventId=1",
            "https:///arrangement?eventId=1",
            "http://rwcmd.asimut.net/arrangement?eventId=1",
            "http://localhost/arrangement?eventId=1",
            "https://evil.example/arrangement?eventId=1",
            "https://rwcmd.asimut.net:443/arrangement?eventId=1",
            "https://user@rwcmd.asimut.net/arrangement?eventId=1",
            "ftp://rwcmd.asimut.net/arrangement?eventId=1",
            " /arrangement?eventId=1",
            "",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertIsNone(parse_confirmed_event_id(url))
                self.assertFalse(is_confirmed_post_save_url(url))

    def test_existing_event_editor_requires_exact_positive_event_route(self):
        self.assertEqual(parse_event_editor_id("/event?eventId=1"), 1)
        self.assertEqual(
            parse_event_editor_id(
                "https://rwcmd.asimut.net/event?eventId=3580122"
            ),
            3580122,
        )
        for url in (
            "/event?eventId=0",
            "/event?eventId=01",
            "/event?eventId=-1",
            "/event?eventId=1&other=value",
            "/event?other=value&eventId=1",
            "/event?eventId=1&eventId=2",
            "/event/?eventId=1",
            "/event?eventId=1#edit",
            "/arrangement?eventId=1",
            "https://evil.example/event?eventId=1",
            "http://rwcmd.asimut.net/event?eventId=1",
            "https://rwcmd.asimut.net:443/event?eventId=1",
            " /event?eventId=1",
            "",
        ):
            with self.subTest(url=url):
                self.assertIsNone(parse_event_editor_id(url))

    def test_new_booking_form_requires_relative_or_canonical_asimut_origin(self):
        self.assertTrue(is_new_booking_form_url("/event?eventId=0"))
        self.assertTrue(
            is_new_booking_form_url("https://rwcmd.asimut.net/event?eventId=0")
        )
        current_prefill = (
            "https://rwcmd.asimut.net/event?eventId=0&prefillTime=true&"
            "start=2026-09-01T09%3A45%3A00.000%2B01%3A00&"
            "categoryId=56&locationId=85"
        )
        self.assertTrue(is_new_booking_form_url(current_prefill))
        self.assertTrue(
            is_new_booking_form_url(
                "https://rwcmd.asimut.net/event?locationId=85&categoryId=56&"
                "start=2026-09-01T09%3A45%3A00.000%2B01%3A00&"
                "prefillTime=true&eventId=0"
            ),
            "the known prefill tuple is order-independent",
        )
        for url in (
            "http://rwcmd.asimut.net/event?eventId=0",
            "https://evil.example/event?eventId=0",
            "//rwcmd.asimut.net/event?eventId=0",
            "https://rwcmd.asimut.net:443/event?eventId=0",
            "https://rwcmd.asimut.net/event?eventId=0#form",
            "https://rwcmd.asimut.net/event?eventId=1",
            current_prefill + "&unexpected=true",
            current_prefill.replace("eventId=0", "eventId=0&eventId=0"),
            current_prefill.replace("prefillTime=true&", ""),
            current_prefill.replace("prefillTime=true", "prefillTime=false"),
            current_prefill.replace("categoryId=56", "categoryId=0"),
            current_prefill.replace("locationId=85", "locationId=-85"),
            current_prefill.replace(
                "2026-09-01T09%3A45%3A00.000%2B01%3A00",
                "2026-09-01T09%3A45%3A00",
            ),
        ):
            with self.subTest(url=url):
                self.assertFalse(is_new_booking_form_url(url))


class TimeVerificationTests(unittest.TestCase):
    def test_canonical_hhmm_values_must_match_exactly(self):
        self.assertTrue(hhmm_values_match("09:30", "09:30"))
        self.assertTrue(hhmm_values_match("23:59", "23:59"))
        self.assertFalse(hhmm_values_match("09:30", "09:45"))
        self.assertFalse(hhmm_values_match("9:30", "09:30"))
        self.assertFalse(hhmm_values_match("24:00", "24:00"))
        self.assertFalse(hhmm_values_match("09:60", "09:60"))
        self.assertFalse(hhmm_values_match(" 09:30", "09:30"))

    def test_start_and_end_are_both_required_to_match(self):
        self.assertTrue(booking_times_match("09:30", "11:00", "09:30", "11:00"))
        self.assertFalse(booking_times_match("09:30", "11:00", "09:30", "10:45"))
        self.assertFalse(booking_times_match("09:30", "11:00", "09:45", "11:00"))


class StructuredBookingVerificationTests(unittest.TestCase):
    def test_unrelated_global_text_cannot_confirm_wrong_structured_fields(self):
        snapshot = {
            "room": "B1.09",
            "date": "2026-09-01",
            "start": "10:00",
            "end": "10:30",
            "global_body_text": (
                "Reservation B0.29 Monday 31 August 2026 09:30-11:00"
            ),
        }

        self.assertFalse(
            booking_identity_matches(snapshot, "B0.29", date(2026, 8, 31))
        )
        self.assertFalse(
            booking_summary_matches(
                snapshot,
                "B0.29",
                date(2026, 8, 31),
                "09:30",
                "11:00",
            )
        )
        self.assertFalse(
            booking_summary_matches(
                "Reservation B0.29 Monday 31 August 2026 09:30-11:00",  # type: ignore[arg-type]
                "B0.29",
                date(2026, 8, 31),
                "09:30",
                "11:00",
            ),
            "legacy whole-page text must never be accepted as a booking record",
        )

    def test_complete_structured_snapshot_must_match_every_field_exactly(self):
        snapshot = {
            "room": "B0.29",
            "date": "2026-08-31",
            "start": "09:30",
            "end": "11:00",
        }
        self.assertTrue(
            booking_summary_matches(
                snapshot,
                "B0.29",
                date(2026, 8, 31),
                "09:30",
                "11:00",
            )
        )
        for field, wrong_value in (
            ("room", "B1.09"),
            ("date", "2026-09-01"),
            ("start", "09:45"),
            ("end", "10:45"),
        ):
            with self.subTest(field=field):
                wrong = dict(snapshot)
                wrong[field] = wrong_value
                self.assertFalse(
                    booking_summary_matches(
                        wrong,
                        "B0.29",
                        date(2026, 8, 31),
                        "09:30",
                        "11:00",
                    )
                )


if __name__ == "__main__":
    unittest.main()
