"""Exact, room-bound permission refusals from authenticated ASIMUT checks."""

from html import unescape
import re


def named_room_refusal_text(value, room):
    if not isinstance(value, str) or not isinstance(room, str) or not room:
        return None
    text = " ".join(unescape(re.sub(r"<[^>]*>", " ", value)).split())
    expected = f"You are not allowed to create or modify bookings in {room}"
    # Full equality matters: a suffix restricting dates, horizons or duration
    # does not mean this account cannot use the room.
    return expected if text.rstrip(".") == expected else None


def response_named_room_refusal(document, room):
    """Keep an independent access refusal even alongside quota/horizon issues.

    The caller binds this response to the exact requested room. Only the
    site's explicit general warning counts; generic HTTP/auth errors do not.
    """
    if not isinstance(document, dict):
        return None
    response = document.get("response")
    if not isinstance(response, dict) or response.get("success") is not False:
        return None
    rules = response.get("bookingrules")
    if not isinstance(rules, dict) or not isinstance(rules.get("issues"), list):
        return None
    for issue in rules["issues"]:
        if (isinstance(issue, dict) and issue.get("type") == "general"
                and issue.get("class") == "message-warning"):
            reason = named_room_refusal_text(issue.get("text"), room)
            if reason:
                return reason
    return None
