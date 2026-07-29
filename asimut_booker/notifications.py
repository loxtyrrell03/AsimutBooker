"""Optional result notifications.

Notifications are intentionally best-effort and never determine whether a
remote booking is considered successful.  The topic is validated as a
non-guessable secret by the configuration layer.
"""

from __future__ import annotations

import json
from urllib.request import Request, urlopen

from .config import NotificationConfig
from .coordinator import RunSummary


def send_run_notification(
    config: NotificationConfig,
    summary: RunSummary,
    *,
    timeout_seconds: float = 5.0,
) -> None:
    if not config.enabled:
        return
    if config.provider != "ntfy" or not config.topic:
        raise ValueError("notification configuration is incomplete")
    title = (
        f"AsimutBooker: {summary.bookings_created} booked, {summary.extensions_completed} extended"
    )
    body = {
        "topic": config.topic,
        "title": title,
        "message": _message(summary),
        "priority": config.priority,
        "tags": ["white_check_mark" if summary.status != "failed" else "warning"],
    }
    request = Request(
        config.base_url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"notification service returned HTTP {response.status}")


def _message(summary: RunSummary) -> str:
    parts = [
        f"Run {summary.status}.",
        f"Bookings: {summary.bookings_created}.",
        f"Extensions: {summary.extensions_completed}.",
    ]
    if summary.booked:
        rendered = ", ".join(
            f"{item['date']} {item['room']} {_time(int(item['start']))}-{_time(int(item['end']))}"
            for item in summary.booked
        )
        parts.append(f"Created: {rendered}.")
    if summary.rejected:
        parts.append(f"Rejected attempts: {len(summary.rejected)}.")
    return " ".join(parts)


def _time(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"
