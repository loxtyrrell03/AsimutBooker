import json
from pathlib import Path
import tempfile
import unittest

from tools.verify_phone_build import PhoneBuildError, validate_phone_build


class PhoneBuildVerificationTests(unittest.TestCase):
    def make_build(self, directory: str) -> Path:
        root = Path(directory)
        (root / "assets").mkdir()
        (root / "assets" / "app.js").write_text("console.log('phone')", encoding="utf-8")
        (root / "assets" / "app.css").write_text("body{}", encoding="utf-8")
        (root / "index.html").write_text(
            '<!doctype html><link rel="stylesheet" href="/assets/app.css">'
            '<script type="module" src="/assets/app.js"></script>',
            encoding="utf-8",
        )
        icons = [
            {"src": "/icon-192.png"},
            {"src": "/icon-512.png"},
            {"src": "/icon-maskable-512.png"},
        ]
        (root / "manifest.webmanifest").write_text(
            json.dumps({"display": "standalone", "start_url": "/", "icons": icons}),
            encoding="utf-8",
        )
        for icon in icons:
            (root / icon["src"][1:]).write_bytes(b"png")
        (root / "build-info.json").write_text('{"version":"test-sha"}', encoding="utf-8")
        (root / "sw.js").write_text(
            "const CACHE_VERSION = 'asimut-phone-test-sha';\n"
            "const SHELL_FILES = [\"/\",\"/assets/app.js\",\"/assets/app.css\"];\n"
            "if (request.method !== 'GET' || url.pathname.startsWith('/api/')) return;\n",
            encoding="utf-8",
        )
        return root

    def test_complete_first_install_offline_shell_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_build(directory)
            validate_phone_build(root, expected_version="test-sha")

    def test_missing_hashed_asset_from_precache_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_build(directory)
            worker = (root / "sw.js").read_text(encoding="utf-8")
            (root / "sw.js").write_text(
                worker.replace(',\"/assets/app.js\"', ""),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(PhoneBuildError, "omits required assets"):
                validate_phone_build(root)

    def test_cache_version_mismatch_and_source_map_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_build(directory)
            (root / "assets" / "app.js.map").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(PhoneBuildError, "source maps"):
                validate_phone_build(root)


if __name__ == "__main__":
    unittest.main()
