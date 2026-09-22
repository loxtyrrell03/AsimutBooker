"""RWCMD advance quotas and the bounded last-minute booking exception.

All reservations count towards practice targets. The free horizon is permission
to exceed a depleted quota, never extra credit to spend on future reservations.
Live balances are seconds, not hours; unknown quota types fail closed.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from room_catalog import ASIMUT_ORIGIN, SITE_TIMEZONE, _api_json, _format_site_iso

ROLLING_QUOTA_HOURS = 6
PEAK_QUOTA_MINUTES = 60
FREE_HORIZON_MINUTES = 300
RULES_REVISION = '2026-09-22-preferred-free-readiness'


class QuotaPolicyError(RuntimeError):
    pass


class QuotaWait(RuntimeError):
    """An explicit server quota refusal ends the pass without trying more rooms."""


def check_quota_refusal(document):
    issues = document.get('response', {}).get('bookingrules', {}).get('issues', [])
    for issue in issues if isinstance(issues, list) else ():
        if isinstance(issue, dict) and issue.get('type') == 'category' and issue.get('class') == 'message-warning':
            text = issue.get('text', '')
            if text in ('Requested booking exceeds your quota', 'Requested booking exceeds your peak quota'):
                raise QuotaWait(text + '; waiting for quota or an eligible free-horizon interval')


@dataclass(frozen=True)
class QuotaBalance:
    rolling_minutes: float
    peak_minutes: float | None


def parse_quota_balance(document):
    try:
        response = document['response']
        if response['success'] is not True:
            raise ValueError('unsuccessful quota response')
        raw = response['quota_overview']
        required = {'booking_quota', 'booking_quota_w', 'booking_quota_d',
                    'booking_quota_t_dates', 'booking_quota_t', 'booking_quota_p'}
        if set(raw) != required:
            raise ValueError('unknown quota fields')
        if any(raw[k] is not None for k in required - {'booking_quota', 'booking_quota_p'}):
            raise ValueError('additional quota rules need review')
        def minutes(value, optional=False):
            if optional and value is None:
                return None
            if type(value) not in (int, float) or not 0 <= value <= 604800:
                raise ValueError('invalid quota balance')
            return value / 60
        return QuotaBalance(minutes(raw['booking_quota']), minutes(raw['booking_quota_p'], True))
    except (KeyError, TypeError, ValueError) as exc:
        raise QuotaPolicyError('ASIMUT quota rules could not be verified; booking is paused for this run') from exc


def read_quota_balance(page, day):
    instant = datetime.combine(day, time(), tzinfo=SITE_TIMEZONE)
    path = '/services/v2/quota/date=' + _format_site_iso(instant)
    try:
        response = page.request.get(ASIMUT_ORIGIN + path, timeout=15000)
        return parse_quota_balance(_api_json(response, path, 'quota'))
    except QuotaPolicyError:
        raise
    except Exception as exc:
        raise QuotaPolicyError('Could not refresh ASIMUT quota; booking is paused for this run') from exc


def refresh_quota_balances(page, tracker, days):
    """Install only a complete snapshot; refreshing never invents extra credit."""
    balances = {day.isoformat(): read_quota_balance(page, day) for day in days}
    if not balances:
        raise QuotaPolicyError('No dates supplied for quota verification')
    tracker.live_quota_minutes = min(b.rolling_minutes for b in balances.values())
    tracker.quota_observed_hours = tracker.get_total_booking_hours()
    tracker.live_peak_minutes = {day: b.peak_minutes for day, b in balances.items()}
    tracker.peak_observed_minutes = dict(tracker.peak_hours_by_day)
    from booking_run_report import observe
    observe(tracker)


def free_horizon_hours(day, start_hour, *, now, horizon_minutes=300):
    """Maximum whole-quarter duration whose BOTH endpoints fit the free window.

    Use elapsed time over clock changes and the conservative current minute,
    matching the site's minute-resolution horizon. An ambiguous wall clock is
    never a candidate for a new booking.
    """
    if isinstance(day, datetime):
        day = day.date()
    if not isinstance(day, date):
        day = date.fromisoformat(day)
    now = now.replace(tzinfo=SITE_TIMEZONE) if now.tzinfo is None else now.astimezone(SITE_TIMEZONE)
    start = datetime.combine(day, time(), tzinfo=SITE_TIMEZONE) + timedelta(minutes=round(start_hour * 60))
    if start.replace(fold=0).utcoffset() != start.replace(fold=1).utcoffset():
        return 0.0
    utc_start = start.astimezone(timezone.utc)
    utc_now = now.astimezone(timezone.utc)
    if utc_start <= utc_now:
        return 0.0
    cutoff = utc_now.replace(second=0, microsecond=0) + timedelta(minutes=horizon_minutes)
    return max(0, int((cutoff - utc_start).total_seconds() // 900)) / 4


def in_free_horizon(day, start, end, *, now, horizon_minutes=300):
    return end > start and end - start <= free_horizon_hours(
        day, start, now=now, horizon_minutes=horizon_minutes) + 1e-9


def peak_quota_exempt(day, start, end, *, now, free_horizon_overrides_peak=False,
                      free_horizon_minutes=300):
    """An opt-in local exception; the site's exact check still authorizes Save.

    Clock hours describe the COMPLETE new/edited reservation, never just its tail.
    """
    return free_horizon_overrides_peak is True and in_free_horizon(
        day, start, end, now=now, horizon_minutes=free_horizon_minutes)
