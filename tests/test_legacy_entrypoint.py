import contextlib
import io
import unittest
from unittest import mock

import book_week
from src import booker as legacy_booker
from src import config as legacy_config
from src import main as legacy_main
from src import scheduler as legacy_scheduler


class LegacyEntrypointTests(unittest.TestCase):
    def test_dry_run_maps_to_canonical_read_only_live_policy_path(self):
        with (
            mock.patch.object(book_week, "main", return_value=0) as canonical,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = legacy_main.main(["--once", "--dry-run"])

        self.assertEqual(result, 0)
        canonical.assert_called_once_with(["--headless", "--check-only"])

    def test_setup_maps_to_canonical_visible_login(self):
        with (
            mock.patch.object(book_week, "main", return_value=0) as canonical,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = legacy_main.main(["--setup", "--headed"])

        self.assertEqual(result, 0)
        canonical.assert_called_once_with(["--setup-login"])

    def test_legacy_scheduler_and_custom_config_fail_closed(self):
        for arguments in (
            ["--schedule"],
            ["--once", "--config", "elsewhere.yaml"],
        ):
            with self.subTest(arguments=arguments):
                with (
                    mock.patch.object(book_week, "main") as canonical,
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    result = legacy_main.main(arguments)
                self.assertEqual(result, 2)
                canonical.assert_not_called()

    def test_direct_legacy_policy_surfaces_are_retired(self):
        with self.assertRaisesRegex(RuntimeError, "room horizons"):
            legacy_config.Config()
        with self.assertRaisesRegex(RuntimeError, "live room policy"):
            legacy_booker.AsimutBooker(None, None)

    def test_direct_legacy_scheduler_construction_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "src.scheduler is retired"):
            legacy_scheduler.BookingScheduler(lambda: None)

    def test_direct_legacy_scheduler_start_fails_closed_when_construction_is_bypassed(self):
        scheduler = object.__new__(legacy_scheduler.BookingScheduler)

        with self.assertRaisesRegex(RuntimeError, "src.scheduler is retired"):
            scheduler.start()


if __name__ == "__main__":
    unittest.main()
