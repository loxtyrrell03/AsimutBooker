"""Exact network contract for one room/time edit; no network or file effects."""

from datetime import datetime, timezone
from urllib.parse import urlsplit

from room_upgrades import local_instant


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

    Save need not repeat the check response's booking rules, forms or success
    marker. Missing acknowledgement fields alone do not override an exact,
    independently reloaded reservation. Explicit errors or a different event
    still require reconciliation, even if HTTP succeeded.
    """
    try:
        response = document['response']
        if not isinstance(response, dict) or document.get('messages', {}).get('errors'):
            return False
        if 'success' in response and response['success'] is not True:
            return False
        # Reuse the strict check validator after supplying only omitted fields.
        # Present contradictory or malformed values are never replaced.
        normalized = dict(response)
        normalized.setdefault('success', True)
        normalized.setdefault('event_ids', [event_id])
        normalized.setdefault('forms', [])
        normalized.setdefault('bookingrules', {'issues': []})
        normalized.setdefault('save_resolution', {'uri': '/arrangement',
            'query_params': [{'key': 'eventId', 'value': event_id}]})
        return upgrade_response_success({**document, 'response': normalized}, event_id)
    except (TypeError, KeyError, AttributeError):
        return False
