import json
import tempfile
import unittest
from pathlib import Path

from app_settings import SettingsError, load_settings, save_settings, update_settings


class SettingsStoreTests(unittest.TestCase):
    def test_invalid_existing_json_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(SettingsError):
                load_settings(path)

    def test_atomic_update_preserves_unrelated_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            save_settings({"cached_events": [1], "ignored_events": ["x"]}, path)

            def mutate(settings):
                settings["practice_plan"] = {"enabled": True, "default_hours": 2.0}

            update_settings(mutate, path)
            saved = load_settings(path)
            self.assertEqual(saved["cached_events"], [1])
            self.assertEqual(saved["ignored_events"], ["x"])
            self.assertTrue(saved["practice_plan"]["enabled"])
            self.assertTrue(path.with_suffix(".json.bak").exists())

    def test_non_finite_json_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            with self.assertRaises(SettingsError):
                save_settings({"bad": float("nan")}, path)


if __name__ == "__main__":
    unittest.main()
