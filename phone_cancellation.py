"""Deterministic single-reservation cancellation for authenticated phone clicks."""

from datetime import date, time
from typing import Any

from assistant_tools import AssistantToolError, BookerToolSurface


class CancellationNotStarted(AssistantToolError):
    """Fresh validation failed before any cancellation was dispatched."""


def validate_target(target: Any) -> dict:
    if not isinstance(target, dict) or set(target) != {
        "event_id", "date", "room", "start_time", "end_time"
    }:
        raise ValueError("Choose one exact reservation to cancel.")
    if type(target["event_id"]) is not int or target["event_id"] <= 0:
        raise ValueError("Refresh the schedule to load this reservation's identity.")
    if not isinstance(target["room"], str) or not target["room"].strip() or len(target["room"]) > 200:
        raise ValueError("The reservation room is invalid.")
    try:
        if date.fromisoformat(target["date"]).isoformat() != target["date"]:
            raise ValueError()
        for key in ("start_time", "end_time"):
            if time.fromisoformat(target[key]).strftime("%H:%M") != target[key]:
                raise ValueError()
        if target["start_time"] >= target["end_time"]:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError("The reservation date or times are invalid.") from None
    return dict(target)


def cancel_phone_reservation(target: dict, *, surface=None, progress=None) -> dict:
    target = validate_target(target)
    surface = surface or BookerToolSurface()
    report = progress or (lambda _text: None)
    def activity(title, detail):
        stages = {
            "Cancellation progress: opening booking": "Opening your booking…",
            "Cancellation progress: cancelling booking": "Cancelling your booking…",
            "Cancellation progress: verifying removal": "Checking that the booking was removed…",
        }
        if detail in stages:
            report(stages[detail])
        elif title == "Matching reservations":
            report("Finding your exact booking…")
    report("Connecting to Asimut and checking your bookings…")
    try:
        surface.dispatch("refresh_booker_data", {"scope": "agenda"}, progress=activity)
        selection = surface.dispatch("find_reservations", {
            key: target[key] for key in ("date", "room", "start_time", "end_time")
        }, progress=activity)
    except AssistantToolError as exc:
        raise CancellationNotStarted("The live booking check failed. Refresh the schedule before trying again.") from exc
    matches = selection.get("matches", [])
    if (not selection.get("fresh") or len(matches) != 1
            or any(matches[0].get(key) != value for key, value in target.items())
            or not selection.get("selection_id")):
        raise CancellationNotStarted("This reservation changed or is no longer present. Refresh the schedule.")
    # This fixed request describes the authenticated button action. No model or
    # natural-language interpretation is involved; the issued exact selection
    # retains the shared receipt, identity, verification and blackout guards.
    request = "Cancel this exact reservation."
    report("Checking the booking in Asimut before cancellation…")
    result = surface.dispatch("cancel_reservations", {
        "selection_id": selection["selection_id"], "request_quote": request,
    }, user_request=request, progress=activity)
    cancelled = result.get("cancelled_count") == 1
    protected = result.get("protection_persisted") is True
    return {
        "cancelled": cancelled,
        "reconciliation_required": bool(result.get("reconciliation_required")),
        "message": (
            "Booking cancelled. This time will stay free."
            if cancelled and protected else
            "Booking cancelled, but keeping this time free could not be saved. Check Settings."
            if cancelled else
            "Cancellation was not confirmed. Refresh the schedule and check the booking before trying again."
        ),
    }
