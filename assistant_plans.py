"""Deterministic future-practice intentions created through the assistant.

The natural-language model supplies a fully resolved date range and target for
every day.  This module validates and persists that explanation record while
also writing the ordinary ``practice_plan`` and ``disabled_dates`` fields that
the autonomous Booker already treats as authority.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, MutableMapping, Sequence
from uuid import UUID, uuid4

from practice_plan import PracticePlanError, load_practice_plan


SETTINGS_KEY = "assistant_practice_intentions"
MAX_PLAN_DAYS = 92
MAX_STORED_PLANS = 64
_RECORD_KEYS = {
    "id",
    "created_at",
    "title",
    "intent_summary",
    "start_date",
    "end_date",
    "resolved_targets",
}


class AssistantPlanError(ValueError):
    """Raised when a future-plan instruction is incomplete or ambiguous."""


def _canonical_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise AssistantPlanError(f"{label} must use YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise AssistantPlanError(f"{label} must use YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise AssistantPlanError(f"{label} must use canonical YYYY-MM-DD")
    return parsed


def _text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise AssistantPlanError(f"{label} must be text")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise AssistantPlanError(f"{label} must contain 1-{maximum} characters")
    if any(ord(character) < 32 for character in normalized):
        raise AssistantPlanError(f"{label} contains control characters")
    return normalized


def _target_hours(value: Any, label: str) -> float:
    if value == 0 and not isinstance(value, bool):
        return 0.0
    try:
        plan = load_practice_plan(
            {"practice_plan": {"enabled": True, "default_hours": value}}
        )
    except PracticePlanError as exc:
        raise AssistantPlanError(str(exc).replace("practice_plan.default_hours", label)) from exc
    return plan.default_hours


def _parse_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise AssistantPlanError("Future-plan created_at must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AssistantPlanError("Future-plan created_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AssistantPlanError("Future-plan created_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _record(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _RECORD_KEYS:
        raise AssistantPlanError(f"{label} has unexpected or missing fields")
    record_id = value["id"]
    if not isinstance(record_id, str):
        raise AssistantPlanError(f"{label}.id must be a UUID")
    try:
        parsed_id = UUID(record_id)
    except (ValueError, AttributeError) as exc:
        raise AssistantPlanError(f"{label}.id must be a UUID") from exc
    if str(parsed_id) != record_id:
        raise AssistantPlanError(f"{label}.id must be canonical")
    start = _canonical_date(value["start_date"], f"{label}.start_date")
    end = _canonical_date(value["end_date"], f"{label}.end_date")
    if end < start or (end - start).days + 1 > MAX_PLAN_DAYS:
        raise AssistantPlanError(f"{label} must cover 1-{MAX_PLAN_DAYS} days")
    targets = value["resolved_targets"]
    if not isinstance(targets, Mapping) or any(not isinstance(key, str) for key in targets):
        raise AssistantPlanError(f"{label}.resolved_targets must be an object")
    expected = {
        (start + timedelta(days=offset)).isoformat()
        for offset in range((end - start).days + 1)
    }
    if set(targets) != expected:
        raise AssistantPlanError(
            f"{label}.resolved_targets must contain every date in the range exactly once"
        )
    normalized_targets = {
        key: _target_hours(targets[key], f"{label}.resolved_targets.{key}")
        for key in sorted(targets)
    }
    return {
        "id": record_id,
        "created_at": _parse_timestamp(value["created_at"]),
        "title": _text(value["title"], f"{label}.title", 120),
        "intent_summary": _text(
            value["intent_summary"], f"{label}.intent_summary", 500
        ),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "resolved_targets": normalized_targets,
    }


def load_assistant_plans(settings: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Strictly load explanation records; they are never booking authority."""

    raw = settings.get(SETTINGS_KEY, [])
    if not isinstance(raw, list):
        raise AssistantPlanError(f"{SETTINGS_KEY} must be a list")
    records = tuple(_record(item, f"{SETTINGS_KEY}[{index}]") for index, item in enumerate(raw))
    if len(records) > MAX_STORED_PLANS:
        raise AssistantPlanError(
            f"At most {MAX_STORED_PLANS} future-practice plans can be stored"
        )
    ids = [item["id"] for item in records]
    if len(ids) != len(set(ids)):
        raise AssistantPlanError("Future-practice plan IDs must be unique")
    sort_keys = [(item["start_date"], item["created_at"], item["id"]) for item in records]
    if sort_keys != sorted(sort_keys):
        raise AssistantPlanError("Future-practice plans must be canonically sorted")
    for previous, current in zip(records, records[1:]):
        if previous["end_date"] >= current["start_date"]:
            raise AssistantPlanError("Future-practice plans must not overlap")
    return records


def apply_future_practice_plan(
    settings: MutableMapping[str, Any],
    *,
    title: str,
    intent_summary: str,
    start_date: str,
    end_date: str,
    daily_targets: Sequence[Mapping[str, Any]],
    replace_overlapping: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Resolve one complete dated plan into the existing autonomous settings."""

    if not isinstance(settings, MutableMapping):
        raise AssistantPlanError("Settings must be a mutable object")
    if type(replace_overlapping) is not bool:
        raise AssistantPlanError("replace_overlapping must be Boolean")
    start = _canonical_date(start_date, "start_date")
    end = _canonical_date(end_date, "end_date")
    if end < start or (end - start).days + 1 > MAX_PLAN_DAYS:
        raise AssistantPlanError(f"Future plans must cover 1-{MAX_PLAN_DAYS} days")
    if not isinstance(daily_targets, Sequence) or isinstance(daily_targets, (str, bytes)):
        raise AssistantPlanError("daily_targets must be a list")
    targets: dict[str, float] = {}
    for index, item in enumerate(daily_targets):
        if not isinstance(item, Mapping) or set(item) != {"date", "hours"}:
            raise AssistantPlanError(
                f"daily_targets[{index}] must contain exactly date and hours"
            )
        key = _canonical_date(item["date"], f"daily_targets[{index}].date").isoformat()
        if key in targets:
            raise AssistantPlanError(f"Duplicate daily target: {key}")
        targets[key] = _target_hours(item["hours"], f"daily_targets[{index}].hours")
    expected = {
        (start + timedelta(days=offset)).isoformat()
        for offset in range((end - start).days + 1)
    }
    if set(targets) != expected:
        missing = sorted(expected - set(targets))
        extra = sorted(set(targets) - expected)
        raise AssistantPlanError(
            "daily_targets must resolve every date in the range exactly once"
            + (f"; missing {missing}" if missing else "")
            + (f"; outside range {extra}" if extra else "")
        )

    existing = list(load_assistant_plans(settings))
    overlaps = [
        record
        for record in existing
        if record["start_date"] <= end.isoformat()
        and record["end_date"] >= start.isoformat()
    ]
    if overlaps and not replace_overlapping:
        raise AssistantPlanError(
            "The requested range overlaps an existing future plan; explicitly replace it or revise the dates."
        )
    if replace_overlapping:
        if any(
            record["start_date"] != start.isoformat()
            or record["end_date"] != end.isoformat()
            for record in overlaps
        ):
            raise AssistantPlanError(
                "Replacing a future plan requires the complete existing date range; "
                "resolve every date in that range so no old target is orphaned."
            )
        overlap_ids = {item["id"] for item in overlaps}
        existing = [item for item in existing if item["id"] not in overlap_ids]
    if len(existing) >= MAX_STORED_PLANS:
        raise AssistantPlanError(
            f"At most {MAX_STORED_PLANS} future-practice plans can be stored"
        )

    current_plan = load_practice_plan(settings)
    overrides = dict(current_plan.date_overrides or {})
    disabled_raw = settings.get("disabled_dates", [])
    if not isinstance(disabled_raw, list) or not all(isinstance(item, str) for item in disabled_raw):
        raise AssistantPlanError("disabled_dates must be a list of ISO dates")
    disabled = {_canonical_date(item, "disabled date").isoformat() for item in disabled_raw}
    for key, hours in targets.items():
        if hours == 0:
            overrides.pop(key, None)
            disabled.add(key)
        else:
            overrides[key] = hours
            disabled.discard(key)
    candidate_plan = {
        "enabled": True,
        "default_hours": current_plan.default_hours,
        "date_overrides": overrides,
    }
    validated_plan = load_practice_plan({"practice_plan": candidate_plan})

    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise AssistantPlanError("Future-plan clock must include a timezone")
    record = _record(
        {
            "id": str(uuid4()),
            "created_at": timestamp.astimezone(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "title": title,
            "intent_summary": intent_summary,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "resolved_targets": targets,
        },
        "new future plan",
    )
    existing.append(record)
    existing.sort(key=lambda item: (item["start_date"], item["created_at"], item["id"]))

    settings["practice_plan"] = {
        "enabled": validated_plan.enabled,
        "default_hours": validated_plan.default_hours,
        "date_overrides": dict(validated_plan.date_overrides or {}),
    }
    settings["disabled_dates"] = sorted(disabled)
    settings[SETTINGS_KEY] = existing
    return record


__all__ = [
    "AssistantPlanError",
    "MAX_PLAN_DAYS",
    "MAX_STORED_PLANS",
    "SETTINGS_KEY",
    "apply_future_practice_plan",
    "load_assistant_plans",
]
