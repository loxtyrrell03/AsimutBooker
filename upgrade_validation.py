"""Exact network contract for one room/time edit; no network or file effects."""

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from urllib.parse import urlsplit

from room_upgrades import local_instant


@dataclass(frozen=True)
class RoomPermissionRefusal:
    """A definite room-specific refusal, never an uncertain mutation result.

    False-valued so existing callers continue to their next candidate. Callers
    may remember the room for a bounded retry delay, without changing its saved
    ranking or treating this observation as a permanent policy.
    """

    room: str
    reason: str

    def __bool__(self):
        return False


_SESSION_OR_SERVICE_ERROR = re.compile(
    r"\b(?:log\s*in|sign\s*in|log(?:ged)?\s*out|session|authenticat\w*|"
    r"csrf|network|server\s+error|service\s+unavailable|timed?\s*out|timeout)\b",
    re.IGNORECASE,
)
_ROOM_TARGET = r"(?:this|the(?:\s+selected)?|that|selected)\s+(?:room|location)"
_BOOK_ROOM_ACTION = r"(?:(?:book|reserve|use)(?:\s+in)?|make\s+(?:a\s+)?(?:booking|reservation)\s+(?:in|for))"
_ROOM_PERMISSION = re.compile(
    r"\b(?:you\s+(?:are\s+not|aren['’]t|(?:do\s+not|don['’]t)\s+have\s+permission)"
    rf"(?:\s+(?:allowed|permitted|authori[sz]ed))?\s+to\s+{_BOOK_ROOM_ACTION}\s+{_ROOM_TARGET}"
    rf"|you['’]re\s+not\s+(?:allowed|permitted|authori[sz]ed)\s+to\s+{_BOOK_ROOM_ACTION}\s+{_ROOM_TARGET}"
    rf"|(?:booking|reservation)(?:s)?\s+(?:in|of|for)\s+{_ROOM_TARGET}\s+(?:is|are)\s+not\s+(?:allowed|permitted)"
    rf"|{_ROOM_TARGET}\s+(?:cannot|can['’]t)"
    r"\s+be\s+(?:booked|reserved)\s+by\s+you)\b",
    re.IGNORECASE,
)


def room_permission_refusal_text(value):
    """Recognize explicit room permissions, excluding login/service failures.

    A generic 403, 'access denied', quota limit or 'not allowed' does not prove
    a room permission. The caller must bind the text to its exact editor/check.
    This classifier alone is never evidence that a Save did not take effect.
    """
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or _SESSION_OR_SERVICE_ERROR.search(text) or not _ROOM_PERMISSION.search(text):
        return None
    return text[:500]


def session_or_service_error_text(value):
    """Recognize diagnostics that cannot establish a safe booking-rule rejection."""
    return isinstance(value, str) and bool(_SESSION_OR_SERVICE_ERROR.search(value))


def response_room_permission_refusal(document):
    """Return the reason from a rejected exact check, not from HTTP status.

    Inspect only known message fields. Malformed or mixed service-error
    evidence remains unclassified; callers retain their ordinary failure guard.
    """
    if not isinstance(document, dict):
        return None
    response = document.get("response")
    if not isinstance(response, dict) or response.get("success") is not False:
        return None
    messages = []

    def collect(value):
        if isinstance(value, str):
            messages.append(value)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            for key in ("message", "text", "description", "title", "reason"):
                if key in value:
                    collect(value[key])

    rules = response.get("bookingrules", {})
    if not isinstance(rules, dict):
        return None
    collect(rules.get("issues", []))
    collect(response.get("message"))
    top_messages = document.get("messages", {})
    if not isinstance(top_messages, dict):
        return None
    collect(top_messages.get("errors", []))
    if any(_SESSION_OR_SERVICE_ERROR.search(text) for text in messages):
        return None
    return next((reason for text in messages if (reason := room_permission_refusal_text(text))), None)


def check_response_has_explicit_success(document):
    """A stale enabled Save cannot override a rejected or malformed check."""
    try:
        response = document["response"]
        issues = response.get("bookingrules", {}).get("issues", [])
        return (response["success"] is True
                and not document.get("messages", {}).get("errors")
                and not response.get("forms")
                and isinstance(issues, list)
                and all(isinstance(issue, dict) and issue.get("class") == "message-info" for issue in issues))
    except (TypeError, KeyError, AttributeError):
        return False


def upgrade_request_matches(request, upgrade, location_id, *, operation="check"):
    try:
        expected = upgrade.replacement
        parsed = urlsplit(request.url)
        if (parsed.scheme != "https" or parsed.netloc != "rwcmd.asimut.net"
                or parsed.path != f"/services/v2/event/event_id={expected.event_id};type={operation}"
                or parsed.query or parsed.fragment or request.method != "PATCH"):
            return False
        data = request.post_data_json
        if data.get("booking_type") != "single" or type(data.get("time_period_id")) is not int or data["time_period_id"] != 0:
            return False
        weekdays = data.get("weekdays")
        # Asimut retains its recurrence editor's default [1] even for a
        # Thursday single booking. The single mode and exact event timestamps
        # establish scope; this inactive field is preserved through Save.
        if (not isinstance(weekdays, list) or not weekdays
                or any(type(day) is not int or not 0 <= day <= 6 for day in weekdays)
                or len(set(weekdays)) != len(weekdays)):
            return False
        event = data["event"]
        if type(event["id"]) is not int or event["id"] != expected.event_id:
            return False
        if (type(location_id) is not int or location_id <= 0
                or not isinstance(event["rs"], list) or len(event["rs"]) != 1
                or type(event["rs"][0]["id"]) is not int or event["rs"][0]["id"] != location_id):
            return False
        for key, minutes in (("st", expected.start), ("en", expected.end)):
            stamp = datetime.fromisoformat(event[key])
            if (stamp.tzinfo is None or stamp.second or stamp.microsecond
                    or stamp.astimezone(timezone.utc) != local_instant(expected.day, minutes)):
                return False
        return True
    except (TypeError, KeyError, ValueError, AttributeError, OverflowError):
        return False


def upgrade_response_success(document, event_id):
    """HTTP success and editor enablement alone do not establish eligibility."""
    try:
        response = document["response"]
        ids = response["event_ids"]
        if (type(event_id) is not int or event_id <= 0 or response["success"] is not True
                or not isinstance(ids, list) or len(ids) != 1
                or type(ids[0]) is not int or ids != [event_id]):
            return False
        if document.get("messages", {}).get("errors") or response.get("forms") != []:
            return False
        issues = response["bookingrules"]["issues"]
        if not isinstance(issues, list) or any(issue.get("class") != "message-info" for issue in issues):
            return False
        resolution = response["save_resolution"]
        params = resolution["query_params"]
        return (resolution["uri"] == "/arrangement"
                and params == [{"key": "eventId", "value": event_id}]
                and type(params[0]["value"]) is int)
    except (TypeError, KeyError, AttributeError):
        return False


def upgrade_save_acknowledgement_consistent(document, event_id):
    """Reject contradictory Save replies; persisted readback proves success.

    The observed Save reply omits the check response's empty forms list.
    All other exact success, event-ID, resolution and rule checks still apply,
    followed by independent persisted readback in the caller.
    """
    try:
        response = document['response']
        if not isinstance(response, dict) or document.get('messages', {}).get('errors'):
            return False
        if 'success' in response and response['success'] is not True:
            return False
        # Permit only the observed omission. Never invent success or identity.
        normalized = dict(response)
        normalized.setdefault('forms', [])
        return upgrade_response_success({**document, 'response': normalized}, event_id)
    except (TypeError, KeyError, AttributeError):
        return False
