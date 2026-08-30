"""Crash-safe journal for booking mutations that still need reconciliation.

The journal is deliberately independent from the GUI and booking engine.  A
caller records its intent before touching Asimut, then marks that receipt as
verified or resolved only after it has reconciled the remote state.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, TypeVar
from uuid import UUID, uuid4

from app_settings import InterProcessFileLock, SettingsError, atomic_write_json


APP_DIR = Path(__file__).resolve().parent
RECEIPTS_FILE = APP_DIR / "data" / "mutation_receipts.json"
SCHEMA_VERSION = 1

ReceiptKind = Literal["create", "extension", "uncertain"]
ReceiptStatus = Literal["pending", "verified", "resolved"]

_KINDS = {"create", "extension", "uncertain"}
_STATUSES = {"pending", "verified", "resolved"}
_DOCUMENT_KEYS = {"schema_version", "receipts"}
_REQUIRED_RECEIPT_KEYS = {
    "id",
    "kind",
    "status",
    "created_at",
    "updated_at",
    "room",
    "date",
    "start",
    "end",
}
_OPTIONAL_RECEIPT_KEYS = {
    "event_url",
    "verified_at",
    "resolved_at",
    "resolution",
}


class MutationReceiptError(SettingsError):
    """Raised when the receipt journal cannot be trusted or safely updated."""


T = TypeVar("T")


def _empty_document() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "receipts": {}}


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise MutationReceiptError(f"Receipt {field} must be a non-empty UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MutationReceiptError(f"Receipt {field} is not a valid timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MutationReceiptError(f"Receipt {field} must include a timezone: {value!r}")
    return parsed


def _validate_uuid(value: Any) -> None:
    if not isinstance(value, str):
        raise MutationReceiptError("Receipt id must be a UUID string")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise MutationReceiptError(f"Receipt id is not a valid UUID: {value!r}") from exc
    if str(parsed) != value.lower():
        raise MutationReceiptError(f"Receipt id is not canonical: {value!r}")


def _validate_date(value: Any) -> None:
    if not isinstance(value, str):
        raise MutationReceiptError("Receipt date must use YYYY-MM-DD")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise MutationReceiptError(f"Receipt date must use YYYY-MM-DD: {value!r}") from exc
    if parsed.strftime("%Y-%m-%d") != value:
        raise MutationReceiptError(f"Receipt date must use YYYY-MM-DD: {value!r}")


def _time_minutes(value: Any, field: str) -> int:
    if not isinstance(value, str):
        raise MutationReceiptError(f"Receipt {field} must use HH:MM")
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise MutationReceiptError(f"Receipt {field} must use HH:MM: {value!r}") from exc
    if parsed.strftime("%H:%M") != value:
        raise MutationReceiptError(f"Receipt {field} must use HH:MM: {value!r}")
    return parsed.hour * 60 + parsed.minute


def _validate_optional_text(receipt: dict[str, Any], field: str) -> None:
    if field in receipt and (not isinstance(receipt[field], str) or not receipt[field].strip()):
        raise MutationReceiptError(f"Receipt {field} must be a non-empty string")


def _validate_receipt(receipt: Any, key: str) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        raise MutationReceiptError(f"Receipt {key!r} must be a JSON object")

    keys = set(receipt)
    missing = _REQUIRED_RECEIPT_KEYS - keys
    unknown = keys - _REQUIRED_RECEIPT_KEYS - _OPTIONAL_RECEIPT_KEYS
    if missing:
        raise MutationReceiptError(f"Receipt {key!r} is missing fields: {sorted(missing)}")
    if unknown:
        raise MutationReceiptError(f"Receipt {key!r} has unknown fields: {sorted(unknown)}")

    _validate_uuid(receipt["id"])
    if receipt["id"] != key:
        raise MutationReceiptError(f"Receipt map key {key!r} does not match its id")
    if receipt["kind"] not in _KINDS:
        raise MutationReceiptError(f"Receipt {key!r} has invalid kind: {receipt['kind']!r}")
    if receipt["status"] not in _STATUSES:
        raise MutationReceiptError(f"Receipt {key!r} has invalid status: {receipt['status']!r}")
    if not isinstance(receipt["room"], str) or not receipt["room"].strip():
        raise MutationReceiptError(f"Receipt {key!r} room must be a non-empty string")

    _validate_date(receipt["date"])
    start_minutes = _time_minutes(receipt["start"], "start")
    end_minutes = _time_minutes(receipt["end"], "end")
    if end_minutes <= start_minutes:
        raise MutationReceiptError(f"Receipt {key!r} end time must be after its start time")

    created_at = _parse_timestamp(receipt["created_at"], "created_at")
    updated_at = _parse_timestamp(receipt["updated_at"], "updated_at")
    if updated_at < created_at:
        raise MutationReceiptError(f"Receipt {key!r} updated_at precedes created_at")

    for field in ("event_url", "resolution"):
        _validate_optional_text(receipt, field)

    status = receipt["status"]
    if status == "pending" and any(
        field in receipt for field in ("verified_at", "resolved_at", "resolution")
    ):
        raise MutationReceiptError(f"Pending receipt {key!r} contains terminal state fields")
    if status == "verified":
        if "verified_at" not in receipt:
            raise MutationReceiptError(f"Verified receipt {key!r} is missing verified_at")
        if "resolved_at" in receipt or "resolution" in receipt:
            raise MutationReceiptError(f"Verified receipt {key!r} contains resolved state fields")
    if status == "resolved" and "resolved_at" not in receipt:
        raise MutationReceiptError(f"Resolved receipt {key!r} is missing resolved_at")

    for field in ("verified_at", "resolved_at"):
        if field in receipt:
            terminal_at = _parse_timestamp(receipt[field], field)
            if terminal_at < created_at or terminal_at > updated_at:
                raise MutationReceiptError(
                    f"Receipt {key!r} {field} must fall between created_at and updated_at"
                )
    return receipt


def _validate_document(document: Any, path: Path) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise MutationReceiptError(f"Receipt journal must contain a JSON object: {path}")
    unknown = set(document) - _DOCUMENT_KEYS
    missing = _DOCUMENT_KEYS - set(document)
    if missing:
        raise MutationReceiptError(f"Receipt journal is missing fields: {sorted(missing)}")
    if unknown:
        raise MutationReceiptError(f"Receipt journal has unknown fields: {sorted(unknown)}")
    if document["schema_version"] != SCHEMA_VERSION:
        raise MutationReceiptError(
            f"Unsupported receipt journal schema version: {document['schema_version']!r}"
        )
    receipts = document["receipts"]
    if not isinstance(receipts, dict):
        raise MutationReceiptError("Receipt journal receipts must be a JSON object")
    for key, receipt in receipts.items():
        if not isinstance(key, str):
            raise MutationReceiptError("Receipt journal keys must be strings")
        _validate_receipt(receipt, key)
    return document


def load_journal(path: Path = RECEIPTS_FILE) -> dict[str, Any]:
    """Load and strictly validate the whole journal without mutating it."""

    path = Path(path)
    if not path.exists():
        return _empty_document()
    try:
        with path.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MutationReceiptError(
            f"Mutation receipt journal is unreadable; autonomous mutation must stop: {exc}"
        ) from exc
    return copy.deepcopy(_validate_document(document, path))


def _update_journal(mutator: Callable[[dict[str, Any]], T], path: Path) -> T:
    path = Path(path)
    lock_path = path.with_suffix(path.suffix + ".lock")
    try:
        with InterProcessFileLock(lock_path):
            document = load_journal(path)
            result = mutator(document)
            _validate_document(document, path)
            atomic_write_json(path, document, backup=True)
            return copy.deepcopy(result)
    except MutationReceiptError:
        raise
    except SettingsError as exc:
        raise MutationReceiptError(str(exc)) from exc


def record_pending(
    kind: ReceiptKind,
    *,
    room: str,
    booking_date: str,
    start: str,
    end: str,
    event_url: str | None = None,
    path: Path = RECEIPTS_FILE,
) -> dict[str, Any]:
    """Atomically append a pending create, extension, or uncertain receipt."""

    timestamp = _utc_timestamp()

    def append(document: dict[str, Any]) -> dict[str, Any]:
        receipt_id = str(uuid4())
        while receipt_id in document["receipts"]:
            receipt_id = str(uuid4())
        receipt: dict[str, Any] = {
            "id": receipt_id,
            "kind": kind,
            "status": "pending",
            "created_at": timestamp,
            "updated_at": timestamp,
            "room": room,
            "date": booking_date,
            "start": start,
            "end": end,
        }
        if event_url is not None:
            receipt["event_url"] = event_url
        _validate_receipt(receipt, receipt_id)
        document["receipts"][receipt_id] = receipt
        return receipt

    return _update_journal(append, Path(path))


def record_pending_create(**kwargs: Any) -> dict[str, Any]:
    """Convenience wrapper for a pending reservation creation."""

    return record_pending("create", **kwargs)


def record_pending_extension(**kwargs: Any) -> dict[str, Any]:
    """Convenience wrapper for a pending reservation extension."""

    return record_pending("extension", **kwargs)


def record_uncertain(**kwargs: Any) -> dict[str, Any]:
    """Record an unresolved mutation whose exact operation outcome is unknown."""

    return record_pending("uncertain", **kwargs)


def _get_receipt(document: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    _validate_uuid(receipt_id)
    try:
        return document["receipts"][receipt_id]
    except KeyError as exc:
        raise MutationReceiptError(f"Unknown mutation receipt: {receipt_id}") from exc


def attach_event_url(
    receipt_id: str,
    event_url: str,
    *,
    path: Path = RECEIPTS_FILE,
) -> dict[str, Any]:
    """Persist an observed event URL while keeping the receipt pending."""

    if not isinstance(event_url, str) or not event_url.strip():
        raise MutationReceiptError("event_url must be a non-empty string")

    def attach(document: dict[str, Any]) -> dict[str, Any]:
        receipt = _get_receipt(document, receipt_id)
        if receipt["status"] != "pending":
            raise MutationReceiptError(
                f"Only a pending receipt can receive an event URL: {receipt_id}"
            )
        receipt["event_url"] = event_url
        receipt["updated_at"] = _utc_timestamp()
        return receipt

    return _update_journal(attach, Path(path))


def mark_verified(
    receipt_id: str,
    *,
    event_url: str | None = None,
    path: Path = RECEIPTS_FILE,
) -> dict[str, Any]:
    """Mark a pending receipt verified after confirming the remote mutation."""

    def verify(document: dict[str, Any]) -> dict[str, Any]:
        receipt = _get_receipt(document, receipt_id)
        if receipt["status"] == "resolved":
            raise MutationReceiptError(f"Resolved receipt cannot be marked verified: {receipt_id}")
        if receipt["status"] == "pending":
            timestamp = _utc_timestamp()
            receipt["status"] = "verified"
            receipt["verified_at"] = timestamp
            receipt["updated_at"] = timestamp
        if event_url is not None:
            receipt["event_url"] = event_url
        return receipt

    return _update_journal(verify, Path(path))


def mark_resolved(
    receipt_id: str,
    *,
    resolution: str = "reconciled",
    event_url: str | None = None,
    path: Path = RECEIPTS_FILE,
) -> dict[str, Any]:
    """Close a pending or verified receipt after explicit reconciliation."""

    def resolve(document: dict[str, Any]) -> dict[str, Any]:
        receipt = _get_receipt(document, receipt_id)
        if receipt["status"] == "resolved":
            return receipt
        timestamp = _utc_timestamp()
        receipt["status"] = "resolved"
        receipt["resolved_at"] = timestamp
        receipt["updated_at"] = timestamp
        receipt["resolution"] = resolution
        if event_url is not None:
            receipt["event_url"] = event_url
        return receipt

    return _update_journal(resolve, Path(path))


def list_pending(path: Path = RECEIPTS_FILE) -> list[dict[str, Any]]:
    """Return isolated copies of all unresolved pending receipts, oldest first."""

    document = load_journal(Path(path))
    pending = [
        copy.deepcopy(receipt)
        for receipt in document["receipts"].values()
        if receipt["status"] == "pending"
    ]
    return sorted(pending, key=lambda receipt: (receipt["created_at"], receipt["id"]))
