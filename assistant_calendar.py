"""Calendar facts and a narrow contradiction veto for destructive selections.

This is not an intent parser: it never authorizes a cancellation or chooses an
alternative booking. It only rejects dates outside explicitly named weekdays.
"""
from datetime import date
import re

WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
_DAY = r'(?:mon(?:days?)?|tue(?:s(?:days?)?)?|wed(?:nesdays?)?|thu(?:rs(?:days?)?)?|fri(?:days?)?|sat(?:urdays?)?|sun(?:days?)?)'
_MENTION = re.compile(r'\b' + _DAY + r'\b', re.I)
_INDEX = {name[:3].lower(): index for index, name in enumerate(WEEKDAYS)}
_UNIT = re.compile(r'\b(' + _DAY + r')\b(?:\s*(?:to|through|until|[-–—])\s*(' + _DAY + r')\b)?', re.I)
_CUE = re.compile(r"\b(?:(?:don't|don’t|do not|never)(?:\s+(?:cancel|remove|delete|touch))?|not|except|excluding|keep|leave|preserve|cancel|remove|delete)\b", re.I)


def calendar_day(value):
    day = value if isinstance(value, date) else date.fromisoformat(value)
    return {'date': day.isoformat(), 'weekday': WEEKDAYS[day.weekday()]}


def _weekday_constraints(text):
    # Quoted examples/titles cannot introduce an action or weekday constraint.
    text = re.sub(r'"[^"\n]*"|“[^”\n]*”|`[^`\n]*`', ' ', text)
    included, excluded = set(), set()
    negative, broad, previous_end = False, False, 0
    units = list(_UNIT.finditer(text))
    for index, match in enumerate(units):
        prefix = text[previous_end:match.start()]
        suffix = text[match.end():units[index + 1].start() if index + 1 < len(units) else len(text)]
        cues = list(_CUE.finditer(prefix))
        if cues:
            negative = cues[-1].group().lower() not in {'cancel', 'remove', 'delete'}
        start = _INDEX[match[1][:3].lower()]
        end = _INDEX[match[2][:3].lower()] if match[2] else start
        days = {(start + offset) % 7 for offset in range((end - start) % 7 + 1)}
        if negative:
            # A particular week's day or one session is not a whole-weekday
            # exclusion. Keep that finer date/time interpretation with the model
            # and exact reservation selector instead of blocking other sessions.
            qualified = re.search(r'\b(?:this|next|following|last|previous)\s*$', prefix, re.I)
            qualified = qualified or re.match(
                r'\s*(?:\d|at\b|from\b|morning\b|afternoon\b|evening\b|noon\b|midnight\b|this\b|next\b)', suffix, re.I
            )
            if not qualified:
                excluded.update(days)
        elif not match[2] and re.search(
            r'\b(?:(?:from|since|starting|after|before|until|through)(?:\s+this|\s+next)?|'
            r'(?:week|fortnight|month)\s+(?:of|beginning|starting))\s*$', prefix, re.I
        ):
            # An open-ended boundary does not restrict the intervening weekdays.
            # Still enforce any exclusions elsewhere in the request.
            broad = True
        else:
            included.update(days)
        previous_end = match.end()
    return (set(range(7)) if broad or not included else included) - excluded


def validate_cancellation_weekdays(request, targets):
    """Veto a contradiction with named days; leave unrestricted ranges to the model.

    Exact dates, time windows, identity, freshness and receipts have independent
    validators. A weekday match here is never sufficient authorization.
    """
    if not _MENTION.search(request):
        return
    allowed = _weekday_constraints(request)
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
