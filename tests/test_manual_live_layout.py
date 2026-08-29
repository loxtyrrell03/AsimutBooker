"""Safety checks that keep real-site diagnostics outside test discovery."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MANUAL_LIVE = ROOT / "tools" / "manual_live"
EXPECTED_MANUAL_SCRIPTS = {
    "book_feb2_live.py",
    "book_feb6_live.py",
    "edit_reservation_live.py",
    "extension_live.py",
    "panel_matching_live.py",
}


class ManualLiveLayoutTests(unittest.TestCase):
    def test_live_scripts_are_not_root_test_modules(self):
        self.assertEqual([], sorted(path.name for path in ROOT.glob("test_*.py")))

    def test_live_scripts_use_non_test_names_in_manual_directory(self):
        present = {path.name for path in MANUAL_LIVE.glob("*.py")}
        self.assertTrue(EXPECTED_MANUAL_SCRIPTS.issubset(present))
        self.assertFalse(any(name.startswith("test_") for name in present))

    def test_pytest_discovery_is_restricted_to_tests(self):
        config = (ROOT / "pytest.ini").read_text(encoding="utf-8")
        self.assertIn("testpaths = tests", config)
        self.assertIn("norecursedirs = tools/manual_live", config)

    def test_manual_readme_has_real_booking_warning(self):
        readme = (MANUAL_LIVE / "README.md").read_text(encoding="utf-8")
        self.assertIn("can create or", readme)
        self.assertIn("Dangerous real-booking or real-edit modes", readme)


if __name__ == "__main__":
    unittest.main()
