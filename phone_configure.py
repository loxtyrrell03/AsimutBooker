"""Write the private phone service's strict, no-secret runtime configuration."""

from __future__ import annotations

import argparse
from pathlib import Path

from app_settings import InterProcessFileLock, atomic_write_json
from phone_server import CONFIG_FILE, DEFAULT_HOST, DEFAULT_PORT, load_phone_server_config


def write_phone_config(
    *,
    allowed_login: str,
    public_origin: str,
    codex_executable: str,
    path: Path = CONFIG_FILE,
) -> None:
    target = Path(path)
    document = {
        "version": 2,
        "allowed_login": allowed_login.strip().casefold(),
        "public_origin": public_origin.rstrip("/"),
        "host": DEFAULT_HOST,
        "port": DEFAULT_PORT,
        "codex_executable": str(Path(codex_executable).resolve()),
    }
    # Validate the exact schema before replacing the active configuration.
    probe = target.with_name(f".{target.name}.validation")
    try:
        atomic_write_json(probe, document)
        load_phone_server_config(probe)
    finally:
        probe.unlink(missing_ok=True)
    with InterProcessFileLock(target.with_suffix(target.suffix + ".lock")):
        atomic_write_json(target, document, backup=True)
    load_phone_server_config(target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configure the private Asimut phone service")
    parser.add_argument("--allowed-login", required=True)
    parser.add_argument("--public-origin", required=True)
    parser.add_argument("--codex-executable", required=True)
    parser.add_argument("--path", type=Path, default=CONFIG_FILE)
    args = parser.parse_args(argv)
    write_phone_config(
        allowed_login=args.allowed_login,
        public_origin=args.public_origin,
        codex_executable=args.codex_executable,
        path=args.path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
