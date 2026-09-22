"""One bounded, create-only room request, shared by phone and desktop."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil, floor
from pathlib import Path
from zoneinfo import ZoneInfo

from app_settings import SettingsError, atomic_write_json
from operation_control import operation_stage, operation_verification

LONDON = ZoneInfo('Europe/London')
MODES = ('preferred', 'longest')
DURATIONS = tuple(range(30, 121, 15))
MAX_AGE_SECONDS = 300


def validate_choices(args):
    if not isinstance(args, dict) or set(args) != {'mode', 'minutes'}:
        raise ValueError('Choose a duration mode and duration.')
    if args['mode'] not in MODES or type(args['minutes']) is not int or args['minutes'] not in DURATIONS:
        raise ValueError('Choose 30–120 minutes in 15-minute steps.')
    return args


def local_now():
    return datetime.now(LONDON)


@dataclass(frozen=True)
class RoomNowRequest:
    mode: str
    minutes: int
    requested_at: datetime

    def __post_init__(self):
        validate_choices({'mode': self.mode, 'minutes': self.minutes})
        if not isinstance(self.requested_at, datetime) or self.requested_at.utcoffset() is None:
            raise ValueError('The request needs an exact time.')

    def check_current(self, now):
        age = (now - self.requested_at).total_seconds()
        if not 0 <= age <= MAX_AGE_SECONDS or now.astimezone(LONDON).date() != self.requested_at.astimezone(LONDON).date():
            raise SettingsError('This room request expired. Press Find me a room now for a fresh check.')


def request_from_args(args):
    return RoomNowRequest(args.room_now_mode, args.room_now_minutes,
                          datetime.fromisoformat(args.room_now_requested_at))


def cli_flags(choices, requested_at, output):
    validate_choices(choices)
    return ['--headless', '--room-now-mode', choices['mode'], '--room-now-minutes',
            str(choices['minutes']), '--room-now-requested-at', requested_at,
            '--room-now-output', str(output)]


def validate_cli(parser, args):
    fields = ('room_now_mode', 'room_now_minutes', 'room_now_requested_at', 'room_now_output')
    if not any(getattr(args, name, None) is not None for name in fields):
        return
    if not all(getattr(args, name, None) is not None for name in fields):
        parser.error('Room now requires its mode, duration, request time and result path.')
    try:
        request_from_args(args).check_current(local_now())
    except (ValueError, TypeError, SettingsError) as exc:
        parser.error(str(exc))
    allowed = {'headless', *fields}
    if any(value for name, value in vars(args).items() if name not in allowed):
        parser.error('Room now is create-only and cannot be combined with other operation modes.')


def candidates(engine, rooms, tracker, request, time_prefs, daily_hours, *, now, reserved_peak=0):
    """Enumerate exact legal intervals; earliest start, then duration, then room."""
    request.check_current(now)
    day = now.astimezone(LONDON).date()
    naive_now = now.astimezone(LONDON).replace(tzinfo=None)
    minimum = engine.MINIMUM_BLOCK_MINUTES
    ceiling = min(request.minutes, engine.MAX_BOOKING_HOURS * 60)
    if daily_hours is not None:
        ceiling = min(ceiling, floor(daily_hours * 4 + 1e-8) * 15)
    first = (naive_now.hour * 60 + naive_now.minute) // 15 * 15 + 15
    rows, seen = [], set()
    for room in rooms:
        name = room.get('room')
        if name not in engine.PRIORITY_ROOMS:
            continue
        rank = engine.PRIORITY_ROOMS.index(name)
        horizon = naive_now + timedelta(minutes=engine.room_horizon_minutes(name))
        for gap in room.get('slots', ()):
            start = max(first, ceil(gap['startHour'] * 4 - 1e-8) * 15)
            last = floor(gap['endHour'] * 4 + 1e-8) * 15
            for begin in range(start, last - minimum + 1, 15):
                for length in range(int(ceiling), minimum - 1, -15):
                    end = begin + length
                    if end > last or end >= 1440:
                        continue
                    if datetime.combine(day, datetime.min.time()) + timedelta(minutes=end) > horizon:
                        continue
                    if not engine.interval_is_strictly_preferred(begin / 60, end / 60, time_prefs):
                        continue
                    if day.weekday() < 5 and not engine.peak_quota_exempt(day, begin / 60, end / 60, now=naive_now):
                        peak = max(0, min(end, engine.PEAK_END * 60) - max(begin, engine.PEAK_START * 60))
                        if peak > max(0, tracker.get_remaining_peak_minutes(day) - reserved_peak):
                            continue
                    okay, _ = tracker.can_book(name, day, begin / 60, length, now=naive_now)
                    key = (name, begin, end)
                    if okay and key not in seen:
                        seen.add(key)
                        rows.append({'room': name, 'start_hour': begin / 60,
                                     'end_hour': end / 60, 'minutes': length, 'rank': rank})
    return sorted(rows, key=lambda c: (c['start_hour'], -c['minutes'], c['rank']))


def confirmed_booking(saved, receipt, events):
    """A Save result alone is insufficient: bind receipt and complete agenda."""
    if not receipt or receipt.get('status') != 'verified' or receipt.get('kind') != 'create':
        raise ValueError('The booking receipt is not verified.')
    keys = ('room', 'date', 'start', 'end')
    if any(saved.get(k) != receipt.get(k) for k in keys):
        raise ValueError('The booking receipt does not match the request result.')
    from urllib.parse import urlparse, parse_qs
    values = parse_qs(urlparse(receipt.get('event_url') or '').query).get('eventId', [])
    if len(values) != 1 or not values[0].isdigit():
        raise ValueError('The confirmed booking has no exact event identity.')
    event_id = int(values[0])
    matches = [e for e in events if e.get('eventId') == event_id and e.get('isReservation') is True
               and e.get('room') == saved['room'] and e.get('date') == saved['date']
               and e.get('startTime') == saved['start'] and e.get('endTime') == saved['end']]
    if len(matches) != 1:
        raise ValueError('The new booking needs a complete agenda check before it is confirmed.')
    return {**{k: saved[k] for k in keys}, 'event_id': event_id,
            'duration_minutes': saved['duration_minutes'], 'receipt_id': saved['receipt_id']}


def run(engine, page, args, settings, practice_plan, tracker, *, today, live_dates):
    request = request_from_args(args)
    output = Path(args.room_now_output)

    def finish(state, message, **extra):
        atomic_write_json(output, {'state': state, 'message': message, **extra})
        return 0

    request.check_current(local_now())
    if engine.list_pending_mutation_receipts():
        return finish('uncertain', 'An earlier booking result needs checking before another booking.')
    if engine.is_date_disabled(today, engine.load_disabled_dates(settings)):
        return finish('blocked', 'Today is switched off in Practice dates. No room was booked.')
    if not engine.fragmentation_allows_new_booking(tracker, today)[0]:
        return finish('blocked', 'Your session limit for today is reached. No room was booked.')
    prefs = engine.resolve_time_preferences(engine.load_time_preferences(settings), today)
    # Freeze today's resolved window so downstream date resolution cannot restore
    # a soft override after this explicit soonest-start request disables it.
    prefs = {k: v for k, v in prefs.items() if k not in {'date_overrides', '_default_preferences'}}
    # An explicit earliest-start request supersedes only soft timing preferences.
    # Strict windows and the saved preference snapshot remain authoritative.
    if not prefs.get('strict_mode', False):
        prefs = {**prefs, 'enabled': False}
    daily = engine.remaining_run_daily_budget(practice_plan, today, tracker)
    if daily is not None and daily * 60 < engine.MINIMUM_BLOCK_MINUTES:
        return finish('blocked', 'Your daily practice target is already met. No room was booked.')
    if request.minutes < engine.MINIMUM_BLOCK_MINUTES:
        return finish('blocked', f'Your minimum session is {engine.MINIMUM_BLOCK_MINUTES} minutes. Choose a longer duration or review Rooms.')
    planning = {'held_peak_by_date': {}, 'held_target_by_date': {},
                'extension_peak_by_date': {}, 'extension_target_by_date': {},
                'extension_bookings': (), 'display_days': {}}
    engine.refresh_extension_capacity_holds(planning, tracker, practice_plan,
                                           engine.load_disabled_dates(settings), time_prefs=prefs, settings=settings)
    held = planning['extension_target_by_date'].get(today.isoformat(), 0)
    if daily is not None:
        daily = max(0, daily - held / 60)
    refused = set()
    for _ in range(8):
        operation_stage('Checking available rooms for the earliest start…')
        request.check_current(local_now())
        engine.refresh_practice_room_overview(page, today)
        engine.refresh_quota_balances(page, tracker, (today,))
        options = candidates(engine, engine.get_available_slots(page), tracker, request, prefs, daily, now=local_now(),
                             reserved_peak=planning['extension_peak_by_date'].get(today.isoformat(), 0))
        options = [c for c in options if (c['room'], c['start_hour'], c['minutes']) not in refused]
        if not options:
            return finish('empty', 'No eligible room left today for this duration and your current booking rules. No room was booked.')
        candidate = options[0]
        start = round(candidate['start_hour'] * 60)
        operation_stage(f"Trying {candidate['room']} at {start // 60:02d}:{start % 60:02d} for {candidate['minutes']} minutes…")

        def before_save(actual):
            now = local_now()
            request.check_current(now)
            starts = datetime.combine(today, datetime.min.time(), tzinfo=LONDON) + timedelta(minutes=start)
            if starts <= now:
                raise SettingsError('The start time passed while checking. Press Find me a room now to check again.')
            okay, reason = tracker.can_book(candidate['room'], today, candidate['start_hour'], actual['duration_minutes'], now=now.replace(tzinfo=None))
            if not okay:
                raise SettingsError(reason)
            finish('uncertain', 'Checking the booking result. Do not book again yet.',
                   attempted={**actual, 'requested_at': request.requested_at.isoformat()},
                   requested_minutes=request.minutes, mode=request.mode)

        saved = engine.try_book_slot(page, candidate, today, tracker, 0,
                                    remaining_daily_hours=daily,
                                    max_action_minutes=candidate['minutes'],
                                    time_prefs=prefs, before_save=before_save)
        if not isinstance(saved, dict) or not saved.get('receipt_id'):
            if engine.list_pending_mutation_receipts():
                # Keep the durable pre-Save identity for read-only recovery.
                if output.exists():
                    return 0
                return finish('uncertain', 'The booking result needs checking. Check booking status before trying again.')
            refused.add((candidate['room'], candidate['start_hour'], candidate['minutes']))
            continue
        # Persist the exact Save identity before any fallible post-Save scan.
        finish('uncertain', 'The room was saved; checking its current agenda entry.', saved=saved,
               requested_minutes=request.minutes, mode=request.mode)
        try:
            with operation_verification():
                operation_stage('Confirming your booking in the agenda…')
                proof_tracker = engine.BookingTracker()
                _, events = engine.scan_agenda(page, proof_tracker, today,
                    ignored_events=engine.load_ignored_events(settings), window_dates=live_dates,
                    snapshot_path=engine.AGENDA_SNAPSHOT_FILE)
            from mutation_receipts import load_journal
            journal = load_journal()
            booking = confirmed_booking(saved, journal['receipts'].get(saved['receipt_id']), events)
        except Exception:
            return finish('uncertain', 'The room was saved, but its agenda entry still needs checking. Do not book again yet.', saved=saved,
                          requested_minutes=request.minutes, mode=request.mode)
        engine.save_history(1, len(proof_tracker.agenda_events), list(engine._run_verified_details.get() or ()))
        return finish('completed', f"Booked {booking['room']} · {booking['start']}–{booking['end']} · {booking['duration_minutes']} minutes.",
                      booking=booking, requested_minutes=request.minutes, mode=request.mode)
    return finish('empty', 'The checked rooms were no longer available. No room was booked; try a fresh check.')


def review_result(directory, root):
    """Called only after a successful fresh read-only agenda/reconciliation run."""
    import json
    from agenda_snapshot import read_agenda_snapshot
    from mutation_receipts import load_journal
    path = Path(directory) / 'room-now.json'
    if not path.exists():
        return {'state': 'stopped', 'message': 'This request did not reach Save. You can make a fresh request.'}
    result = json.loads(path.read_text(encoding='utf-8'))
    if result.get('state') != 'uncertain':
        return result
    saved = result.get('saved')
    receipts = load_journal(Path(root) / 'data/mutation_receipts.json')['receipts']
    if saved:
        receipt = receipts.get(saved['receipt_id'])
    else:
        attempt = result.get('attempted')
        if not attempt:
            raise ValueError('The request outcome needs manual review.')
        matches = [r for r in receipts.values() if r['kind'] == 'create'
                   and all(r[k] == attempt[k] for k in ('room', 'date', 'start', 'end'))
                   # Receipt timestamps have second precision; submission keeps microseconds.
                   and datetime.fromisoformat(r['created_at']) >= datetime.fromisoformat(attempt['requested_at']).replace(microsecond=0)]
        if not matches:
            return {'state': 'stopped', 'message': 'This request stopped before Save. You can make a fresh request.'}
        if len(matches) != 1:
            raise ValueError('More than one receipt matches; review the booking outcome.')
        receipt = matches[0]
        saved = {**attempt, 'receipt_id': receipt['id']}
    if receipt and receipt.get('status') == 'resolved' and receipt.get('resolution', '').startswith('Asimut rejected Save:'):
        return {'state': 'blocked', 'message': 'Asimut rejected this booking. No room was saved.'}
    agenda = read_agenda_snapshot(Path(root) / 'data/agenda_snapshot.json')
    if agenda.stale or not agenda.snapshot:
        raise ValueError('The latest agenda is unavailable.')
    booking = confirmed_booking(saved, receipt, agenda.snapshot.event_dicts())
    if agenda.snapshot.observed_at < datetime.fromisoformat(receipt['verified_at']):
        raise ValueError('The agenda must be newer than the booking receipt.')
    return {'state': 'completed', 'booking': booking,
            'message': f"Booked {booking['room']} · {booking['start']}–{booking['end']} · {booking['duration_minutes']} minutes.",
            'requested_minutes': result.get('requested_minutes', saved['duration_minutes'])}
