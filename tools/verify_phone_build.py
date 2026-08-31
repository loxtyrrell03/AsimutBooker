"""Validate that a built phone PWA can render from its first offline shell cache."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Iterable


class PhoneBuildError(RuntimeError):
    pass


class _Assets(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.required: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and values.get("src", "").startswith("/"):
            self.required.add(values["src"] or "")
        if (
            tag == "link"
            and values.get("rel") == "stylesheet"
            and values.get("href", "").startswith("/")
        ):
            self.required.add(values["href"] or "")


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PhoneBuildError(f"Unreadable phone build JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise PhoneBuildError(f"Phone build JSON is not an object: {path.name}")
    return value


def validate_phone_build(dist: Path, *, expected_version: str | None = None) -> None:
    root = Path(dist).resolve()
    index = root / "index.html"
    worker_path = root / "sw.js"
    if not index.is_file() or not worker_path.is_file():
        raise PhoneBuildError("Phone build is missing index.html or sw.js")

    build = _read_json(root / "build-info.json")
    version = build.get("version")
    if not isinstance(version, str) or not version:
        raise PhoneBuildError("Phone build version is missing")
    if expected_version is not None and version != expected_version:
        raise PhoneBuildError("Phone build version does not match expected source")

    manifest = _read_json(root / "manifest.webmanifest")
    if manifest.get("display") != "standalone" or manifest.get("start_url") != "/":
        raise PhoneBuildError("Phone manifest is not standalone at the origin root")
    icons = manifest.get("icons")
    if not isinstance(icons, list) or len(icons) < 3:
        raise PhoneBuildError("Phone manifest icons are incomplete")
    for icon in icons:
        source = icon.get("src") if isinstance(icon, dict) else None
        if not isinstance(source, str) or not source.startswith("/") or not (root / source[1:]).is_file():
            raise PhoneBuildError("Phone manifest references a missing icon")

    parser = _Assets()
    parser.feed(index.read_text(encoding="utf-8"))
    worker = worker_path.read_text(encoding="utf-8")
    cache_match = re.search(r"const CACHE_VERSION = (['\"])(.*?)\1;", worker)
    shell_match = re.search(r"const SHELL_FILES = (\[[\s\S]*?\]);", worker)
    if cache_match is None or cache_match.group(2) != f"asimut-phone-{version}":
        raise PhoneBuildError("Service-worker cache version is not synchronized")
    if shell_match is None:
        raise PhoneBuildError("Service-worker shell list is missing")
    try:
        shell = set(json.loads(shell_match.group(1)))
    except json.JSONDecodeError as exc:
        raise PhoneBuildError("Service-worker shell list is invalid") from exc
    missing = ({"/"} | parser.required) - shell
    if missing:
        raise PhoneBuildError("Offline shell omits required assets: " + ", ".join(sorted(missing)))
    if "request.method !== 'GET'" not in worker or "url.pathname.startsWith('/api/')" not in worker:
        raise PhoneBuildError("Service worker does not explicitly exclude mutations and APIs")
    if list(root.rglob("*.map")):
        raise PhoneBuildError("Phone build unexpectedly contains source maps")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the built Asimut phone PWA")
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--expected-version")
    args = parser.parse_args(list(argv) if argv is not None else None)
    validate_phone_build(args.dist, expected_version=args.expected_version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
