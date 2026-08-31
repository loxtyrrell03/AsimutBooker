"""Sanitized read-only state for the private Asimut phone companion.

The phone surface deliberately receives much less than the Codex assistant.
It never receives event IDs, cancellation match tokens, receipt bodies, command
output, file paths, credentials, or browser state.  All state changes continue
to flow through :class:`assistant_runtime.AssistantRuntime` and its typed tools.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from assistant_context import ContextPaths, build_assistant_context


PHONE_SNAPSHOT_VERSION = 1
MAX_PHONE_EVENTS = 120
MAX_PHONE_PLAN_DAYS = 92
MAX_PHONE_TEXT = 500


def _text(value: Any, *, fallback: str = "") -> str:
    if not isinstance(value, str):
        return fallback
    return value.strip()[:MAX_PHONE_TEXT]


def _boolean(value: Any, *, fallback: bool = False) -> bool:
    return value if type(value) is bool else fallback


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, list) else ()


def _phone_event(raw: Any) -> dict[str, Any] | None:
    event = _mapping(raw)
    date = _text(event.get("date"))
    start = _text(event.get("startTime"))
    end = _text(event.get("endTime"))
    if not date or not start or not end:
        return None
    return {
        "date": date,
        "start_time": start,
        "end_time": end,
        "title": _text(event.get("title"), fallback="Agenda event"),
        "is_reservation": _boolean(event.get("isReservation")),
        "room": _text(event.get("room"), fallback="Room not shown"),
    }


def _phone_candidate(raw: Any) -> dict[str, Any] | None:
    candidate = _mapping(raw)
    required = ("room", "date", "start_time", "end_time")
    if any(not _text(candidate.get(key)) for key in required):
        return None
    return {
        "room": _text(candidate.get("room")),
        "date": _text(candidate.get("date")),
        "start_time": _text(candidate.get("start_time")),
        "end_time": _text(candidate.get("end_time")),
        "state": _text(candidate.get("state"), fallback="potential"),
        "reason": _text(candidate.get("reason")),
        "confirmed_minutes": _number(candidate.get("confirmed_minutes")) or 0,
        "potential_minutes": _number(candidate.get("potential_minutes")) or 0,
        "unlock_at": _text(candidate.get("unlock_at")),
    }


def _phone_plan_day(raw: Any) -> dict[str, Any] | None:
    day = _mapping(raw)
    date = _text(day.get("date"))
    if not date:
        return None
    primary = _phone_candidate(day.get("primary"))
    additional = [
        item
        for item in (_phone_candidate(value) for value in _sequence(day.get("additional")))
        if item is not None
    ]
    backups = [
        item
        for item in (_phone_candidate(value) for value in _sequence(day.get("backups")))
        if item is not None
    ]
    return {
        "date": date,
        "status": _text(day.get("status"), fallback="unplanned"),
        "target_minutes": _number(day.get("target_minutes")) or 0,
        "existing_minutes": _number(day.get("existing_minutes")) or 0,
        "reason": _text(day.get("reason")),
        "primary": primary,
        "additional": additional,
        "backups": backups,
    }


def _phone_health_item(raw: Any) -> dict[str, Any] | None:
    item = _mapping(raw)
    key = _text(item.get("key"))
    if not key:
        return None
    state = _text(item.get("state"), fallback="unknown")
    if state not in {"ok", "warning", "error", "unknown"}:
        state = "unknown"
    return {
        "key": key,
        "label": _text(item.get("label"), fallback=key.replace("_", " ").title()),
        "state": state,
        "headline": _text(item.get("headline"), fallback="No current evidence"),
        "detail": _text(item.get("detail")),
        "observed_at": _text(item.get("observed_at")),
    }


def _next_event(
    events: Sequence[Mapping[str, Any]],
    local_now: str,
    timezone_name: str,
) -> dict[str, Any] | None:
    try:
        now = datetime.fromisoformat(local_now)
        local_zone = ZoneInfo(timezone_name)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return None
    now = now.replace(tzinfo=local_zone) if now.tzinfo is None else now.astimezone(local_zone)
    candidates: list[tuple[datetime, Mapping[str, Any]]] = []
    for event in events:
        try:
            wall_time = datetime.fromisoformat(f"{event['date']}T{event['start_time']}:00")
        except (KeyError, TypeError, ValueError):
            continue
        first = wall_time.replace(tzinfo=local_zone, fold=0)
        second = wall_time.replace(tzinfo=local_zone, fold=1)
        first_valid = first.astimezone(timezone.utc).astimezone(local_zone).replace(
            tzinfo=None
        ) == wall_time
        second_valid = second.astimezone(timezone.utc).astimezone(local_zone).replace(
            tzinfo=None
        ) == wall_time
        # Fail closed for a nonexistent spring-forward time or an ambiguous
        # repeated autumn time. Booker agenda rows normally avoid both, and
        # guessing would make the phone's "next" label untrustworthy.
        if not first_valid or not second_valid or first.utcoffset() != second.utcoffset():
            continue
        starts = first
        if starts >= now:
            candidates.append((starts, event))
    if not candidates:
        return None
    return dict(min(candidates, key=lambda item: item[0])[1])


def build_phone_snapshot(*, paths: ContextPaths | None = None) -> dict[str, Any]:
    """Build the complete allow-listed phone snapshot from validated context."""

    context = build_assistant_context(
        ["preferences", "agenda", "plan", "health", "mutations"],
        paths=paths,
    )
    sections = _mapping(context.get("sections"))
    preferences = _mapping(sections.get("preferences"))
    agenda = _mapping(sections.get("agenda"))
    plan = _mapping(sections.get("plan"))
    health = _mapping(sections.get("health"))
    mutations = _mapping(sections.get("mutations"))

    events = [
        item
        for item in (_phone_event(value) for value in _sequence(agenda.get("events")))
        if item is not None
    ][:MAX_PHONE_EVENTS]
    days = [
        item
        for item in (_phone_plan_day(value) for value in _sequence(plan.get("days")))
        if item is not None
    ][:MAX_PHONE_PLAN_DAYS]
    health_items = [
        item
        for item in (
            _phone_health_item(value) for value in _sequence(health.get("items"))
        )
        if item is not None
    ]
    pending_count = len(_sequence(mutations.get("pending")))
    errors = _mapping(context.get("errors"))

    agenda_available = _boolean(agenda.get("available"))
    agenda_stale = _boolean(agenda.get("stale"), fallback=True)
    plan_available = _boolean(plan.get("available"))
    plan_stale = _boolean(plan.get("stale"), fallback=True)
    critical_errors = set(errors) & {
        "preferences",
        "agenda",
        "plan",
        "health",
        "mutations",
    }
    health_states = {item["state"] for item in health_items}
    if pending_count:
        overall_state = "blocked"
        overall_label = "A Booker action needs reconciliation"
    elif (
        critical_errors
        or "error" in health_states
        or not agenda_available
        or "preferences" not in sections
        or "health" not in sections
        or "mutations" not in sections
    ):
        overall_state = "attention"
        overall_label = "Booker needs attention"
    elif agenda_stale or plan_stale or not plan_available or "warning" in health_states:
        overall_state = "stale"
        overall_label = "Booker data needs a refresh"
    else:
        overall_state = "ready"
        overall_label = "Booker ready"

    practice = _mapping(preferences.get("practice_plan"))
    time_preferences = _mapping(preferences.get("time_preferences"))
    future_intentions = []
    for raw in _sequence(preferences.get("future_practice_intentions")):
        intention = _mapping(raw)
        start = _text(intention.get("start_date"))
        end = _text(intention.get("end_date"))
        if start and end:
            future_intentions.append(
                {
                    "title": _text(intention.get("title"), fallback="Future practice plan"),
                    "intent_summary": _text(intention.get("intent_summary")),
                    "start_date": start,
                    "end_date": end,
                }
            )

    local_now = _text(context.get("local_now"))
    timezone_name = _text(context.get("timezone"), fallback="Europe/London")
    return {
        "version": PHONE_SNAPSHOT_VERSION,
        "generated_at": local_now,
        "timezone": timezone_name,
        "status": {
            "state": overall_state,
            "label": overall_label,
            "pending_mutations": pending_count,
        },
        "agenda": {
            "available": agenda_available,
            "stale": agenda_stale,
            "observed_at": _text(agenda.get("observed_at")),
            "freshness_reason": _text(agenda.get("freshness_reason")),
            "events": events,
            "next_event": _next_event(events, local_now, timezone_name),
        },
        "plan": {
            "available": plan_available,
            "stale": plan_stale,
            "generated_at": _text(plan.get("generated_at")),
            "summary": _text(plan.get("summary"), fallback="No current booking plan"),
            "days": days,
        },
        "preferences": {
            "practice_plan": {
                "enabled": _boolean(practice.get("enabled")),
                "default_hours": _number(practice.get("default_hours")),
                "date_overrides": dict(_mapping(practice.get("date_overrides"))),
            },
            "time_preferences": {
                "enabled": _boolean(time_preferences.get("enabled")),
                "start_time": _text(time_preferences.get("start_time")),
                "end_time": _text(time_preferences.get("end_time")),
                "strict_mode": _boolean(time_preferences.get("strict_mode")),
            },
            "future_intentions": future_intentions,
        },
        "health": {
            "collected_at": _text(health.get("collected_at")),
            "items": health_items,
        },
        # Error values may contain local paths or raw parser details.  The phone
        # receives only which read-only sections were unavailable.
        "unavailable_sections": sorted(str(key)[:80] for key in errors),
    }


__all__ = ["PHONE_SNAPSHOT_VERSION", "build_phone_snapshot"]
