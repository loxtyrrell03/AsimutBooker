"""Calendar facts and a narrow contradiction veto for destructive selections.

This is not an intent parser: it never authorizes a cancellation or chooses an
alternative booking. It only rejects dates outside explicitly named weekdays.
"""
from datetime import date
import re

WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
_DAY = r'(?:mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:rs(?:day)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)'
_MENTION = re.compile(r'\b' + _DAY + r'\b', re.I)
_INDEX = {name[:3].lower(): index for index, name in enumerate(WEEKDAYS)}


def calendar_day(value):
    day = value if isinstance(value, date) else date.fromisoformat(value)
    return {'date': day.isoformat(), 'weekday': WEEKDAYS[day.weekday()]}


def _named_days(text):
    days = {_INDEX[match.group()[:3].lower()] for match in _MENTION.finditer(text)}
    # Inclusive named ranges, including a range crossing the weekend.
    for match in re.finditer(r'\b(' + _DAY + r')\s*(?:to|through|until|[-–—])\s*(' + _DAY + r')\b', text, re.I):
        start, end = (_INDEX[value[:3].lower()] for value in match.groups())
        days.update((start + offset) % 7 for offset in range((end - start) % 7 + 1))
    return days


def validate_cancellation_weekdays(request, targets):
    """Veto a contradiction with named days; leave unrestricted ranges to the model.

    Exact dates, time windows, identity, freshness and receipts have independent
    validators. A weekday match here is never sufficient authorization.
    """
    if not _MENTION.search(request):
        return
    # A weekday used as a range boundary does not restrict every target's day.
    if re.search(r'\b(?:from|since|starting|after|before|until|through)\s+(?:this\s+|next\s+)?' + _DAY + r'\b', request, re.I):
        return
    if re.search(r'\b(?:week|fortnight|month)\s+(?:of|beginning|starting)\b', request, re.I):
        return
    parts = re.split(r'\b(?:except|excluding|but not|not on)\b', request, maxsplit=1, flags=re.I)
    included = _named_days(parts[0])
    excluded = _named_days(parts[1]) if len(parts) == 2 else set()
    allowed = (included or set(range(7))) - excluded
    for target in targets:
        actual = calendar_day(target['date'])
        if date.fromisoformat(actual['date']).weekday() not in allowed:
            expected = ', '.join(WEEKDAYS[index] for index in sorted(allowed)) or 'no named day'
            raise ValueError(
                f"Cancellation blocked: {actual['date']} is {actual['weekday']}, "
                f"but the request names {expected}. Nothing was cancelled. "
                "Look up the requested day; never substitute another date. "
                "If it has no matching booking, ask before using an alternative."
            )
