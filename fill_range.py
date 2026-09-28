"""Explicit, bounded gap filling through the normal exact-create engine.

The requested window replaces scheduling defaults in this operation only.
Room eligibility, actual agenda conflicts, site rules and receipts still apply.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import json
import re

from app_settings import SettingsError, atomic_write_json
from operation_control import operation_stage, operation_verification, check_operation_stop, OperationStopped
from room_now import LONDON, local_now, confirmed_booking
from booking_quotas import QuotaWait

MAX_AGE_SECONDS = 900
MAX_ATTEMPTS = 32


def minutes(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
        raise ValueError('Choose valid clock times.')
    h, m = map(int, value.split(':'))
    return h * 60 + m


def clock(value):
    return f'{value // 60:02d}:{value % 60:02d}'


def validate_choices(choices):
    if not isinstance(choices, dict) or set(choices) != {'date', 'start_time', 'end_time'}:
        raise ValueError('Choose a date, start time and end time.')
    if not isinstance(choices['date'], str) or date.fromisoformat(choices['date']).isoformat() != choices['date']:
        raise ValueError('Choose a valid date.')
    if minutes(choices['end_time']) <= minutes(choices['start_time']):
        raise ValueError('Until must be later than From on the same date.')
    if any(minutes(choices[k]) % 15 for k in ('start_time', 'end_time')):
        raise ValueError('Choose times in 15-minute steps.')
    return choices


@dataclass(frozen=True)
class FillRequest:
    day: date
    start: int
    end: int
    requested_at: datetime

    @classmethod
    def create(cls, choices, requested_at):
        validate_choices(choices)
        instant = datetime.fromisoformat(requested_at)
        if instant.utcoffset() is None:
            raise ValueError('The request time must include its timezone.')
        return cls(date.fromisoformat(choices['date']), minutes(choices['start_time']), minutes(choices['end_time']), instant)

    @property
    def choices(self):
        return {'date': self.day.isoformat(), 'start_time': clock(self.start), 'end_time': clock(self.end)}

    def check_current(self, now):
        if not 0 <= (now-self.requested_at).total_seconds() <= MAX_AGE_SECONDS:
            raise SettingsError('This fill request expired. Try the remaining gaps again.')
        if self.day < now.astimezone(LONDON).date():
            raise SettingsError('Choose today or a future date.')


def request_from_args(args):
    return FillRequest.create({'date': args.fill_date, 'start_time': args.fill_start,
                               'end_time': args.fill_end}, args.fill_requested_at)


def cli_flags(choices, requested_at, output):
    validate_choices(choices)
    return ['--headless', '--fill-date', choices['date'], '--fill-start', choices['start_time'],
            '--fill-end', choices['end_time'], '--fill-requested-at', requested_at, '--fill-output', str(output)]


def validate_cli(parser, args):
    if getattr(args, 'fill_review_output', None):
        if any(v for k,v in vars(args).items() if k not in {'headless','fill_review_output'}):
            parser.error('Fill result review cannot be combined with booking actions.')
        return
    fields = {'fill_date', 'fill_start', 'fill_end', 'fill_requested_at', 'fill_output'}
    if not any(getattr(args, k, None) is not None for k in fields):
        return
    if not all(getattr(args, k, None) is not None for k in fields):
        parser.error('Fill requires the date, both times, request time and result path.')
    try:
        request_from_args(args).check_current(local_now())
    except (ValueError, TypeError, SettingsError) as exc:
        parser.error(str(exc))
    if any(v for k, v in vars(args).items() if k not in fields | {'headless'}):
        parser.error('Fill is isolated from automatic booking, editing and other operation modes.')


def uncovered(request, events):
    """Complement of the union, clipped to the exact half-open requested range."""
    intervals = sorted((max(request.start, minutes(e['startTime'])), min(request.end, minutes(e['endTime'])))
                       for e in events if e.get('isReservation') is True and e.get('date') == str(request.day)
                       and minutes(e['startTime']) < request.end and minutes(e['endTime']) > request.start)
    gaps, edge = [], request.start
    for start, end in intervals:
        if start > edge:
            gaps.append((edge, start))
        edge = max(edge, end)
    if edge < request.end:
        gaps.append((edge, request.end))
    return gaps


def scoped_tracker(engine, previous, request, settings):
    """Rebuild from facts, reopening only the requested cancelled-time portion."""
    tracker = engine.BookingTracker()
    tracker.agenda_events = list(previous.agenda_events)
    tracker.agenda_active_event_ids = list(previous.agenda_active_event_ids)
    for event in tracker.agenda_events:
        tracker.add_existing_event(date.fromisoformat(event['date']), minutes(event['startTime'])/60,
            minutes(event['endTime'])/60, is_reservation=event.get('isReservation') is True,
            room=event.get('room'))
    for blackout in engine.load_rebooking_blackouts(settings):
        if blackout.date != request.day:
            tracker.add_conflict(blackout.date, blackout.start_minutes/60, blackout.end_minutes/60)
        else:
            for start, end in ((blackout.start_minutes, min(blackout.end_minutes, request.start)),
                               (max(blackout.start_minutes, request.end), blackout.end_minutes)):
                if start < end:
                    tracker.add_conflict(blackout.date, start/60, end/60)
    return tracker


def candidates(engine, rooms, tracker, request, now):
    request.check_current(now)
    gaps = uncovered(request, tracker.agenda_events)
    naive = now.astimezone(LONDON).replace(tzinfo=None)
    first = ((naive.hour*60+naive.minute)//15+1)*15 if request.day == naive.date() else 0
    options = []
    reasons = set()
    for rank, name in enumerate(engine.PRIORITY_ROOMS):
        horizon = naive + timedelta(minutes=engine.room_horizon_minutes(name))
        for row in rooms:
            if row.get('room') != name:
                continue
            for slot in row.get('slots', ()):
                for gap_start, gap_end in gaps:
                    start = max(first, gap_start, int(round(slot['startHour']*60)))
                    end = min(gap_end, int(round(slot['endHour']*60)))
                    start = (start+14)//15*15
                    for begin in range(start, end-engine.MIN_BOOKING_MINUTES+1, 15):
                        for duration in range(min(int(engine.MAX_BOOKING_HOURS*60), (end-begin)//15*15), engine.MIN_BOOKING_MINUTES-1, -15):
                            finish = begin+duration
                            if datetime.combine(request.day, datetime.min.time())+timedelta(minutes=finish) > horizon:
                                reasons.add('Outside the room booking horizon')
                                continue
                            allowed, reason = tracker.can_book(name, request.day, begin/60, duration, now=naive)
                            if allowed:
                                options.append({'room': name, 'start_hour': begin/60, 'end_hour': finish/60,
                                                'minutes': duration, 'rank': rank})
                            else:
                                reasons.add(reason)
    return sorted(options, key=lambda c: (c['start_hour'], -c['minutes'], c['rank'])), sorted(reasons)


def choose_candidate(engine, options, tracker, request, now):
    """Bounded interval planning avoids spending the only room needed later.

    Keep up to 48 distinct paths per quarter-hour. Prefer total covered minutes,
    then fewer reservations, then room rank; every Save is still revalidated.
    """
    by_start = {}
    for c in options:
        by_start.setdefault(round(c['start_hour']*60), []).append(c)
    # State: added minutes, charged peak minutes, room-rank cost, selected path.
    states = {request.start: [(0, 0, 0, ())]}
    naive = now.astimezone(LONDON).replace(tzinfo=None)
    peak_left = tracker.get_remaining_peak_minutes(request.day)
    best = (0, 0, 0, ())
    def score(s):
        return s[0], -len(s[3]), -s[2]
    for tick in range(request.start, request.end+1, 15):
        pool = states.pop(tick, [])
        if not pool:
            continue
        unique = {}
        for s in sorted(pool,key=score,reverse=True):
            recent = tuple(sorted((c['room'],c['end_hour']) for c in s[3]
                                 if c['end_hour']*60+engine.SAME_ROOM_GAP_MINUTES>tick))
            unique.setdefault((s[0],s[1],recent),s)
        beam=list(unique.values())[:48]
        best=max([best,*beam],key=score)
        if tick<request.end:
            states.setdefault(tick+15,[]).extend(beam)
        for c in by_start.get(tick,[]):
            length=c['minutes']
            exempt=engine.peak_quota_exempt(request.day,c['start_hour'],c['end_hour'],now=naive)
            peak=0 if exempt or request.day.weekday()>=5 else max(0,min(c['end_hour'],engine.PEAK_END)-max(c['start_hour'],engine.PEAK_START))*60
            free=engine.free_horizon_hours(request.day,c['start_hour'],now=naive,horizon_minutes=engine.FREE_HORIZON_MINUTES)*60
            for s in beam:
                if any(p['room']==c['room'] and (c['start_hour']-p['end_hour'])*60<engine.SAME_ROOM_GAP_MINUTES for p in s[3]):
                    continue
                if length>free+1e-8 and s[0]+length>tracker.get_remaining_quota_hours()*60+1e-8:
                    continue
                if peak and s[1]+peak>peak_left+1e-8:
                    continue
                next_state=(s[0]+length,s[1]+peak,s[2]+c['rank'],s[3]+(c,))
                states.setdefault(round(c['end_hour']*60),[]).append(next_state)
    return best[3][0] if best[3] else options[0]


def result_document(request, events, bookings, state, message, **extra):
    gaps = uncovered(request, events)
    missing = sum(b-a for a, b in gaps)
    return {'state': state, 'message': message, 'range': request.choices, 'bookings': bookings,
            'requested_minutes': request.end-request.start, 'covered_minutes': request.end-request.start-missing,
            'remaining': [{'start': clock(a), 'end': clock(b), 'minutes': b-a} for a,b in gaps], **extra}


def pin_bookings(bookings, path):
    from app_settings import update_settings
    from manual_booking_overrides import pin_in_settings
    if not bookings:
        return
    def mutate(settings):
        for b in bookings:
            pin_in_settings(settings,b['event_id'],{'date':b['date'],'room':b['room'],
                'startTime':b['start'],'endTime':b['end']})
    update_settings(mutate,path)


def run(engine, page, args, settings, tracker, *, today, live_dates):
    request = request_from_args(args)
    output = Path(args.fill_output)
    bookings, refused = [], set()
    events = tracker.agenda_events

    def finish(state, message, **extra):
        atomic_write_json(output, result_document(request, events, bookings, state, message, **extra))
        return 0

    request.check_current(local_now())
    if request.day not in live_dates:
        return finish('blocked', 'This date is outside the current ASIMUT booking window.')
    if engine.list_pending_mutation_receipts():
        return finish('uncertain', 'An earlier booking result needs checking before filling this range.')
    finish('running', 'Checking your existing practice and the remaining gaps…')
    try:
        for _ in range(MAX_ATTEMPTS):
            check_operation_stop()
            request.check_current(local_now())
            if not uncovered(request, events):
                return finish('completed', 'The whole time range is covered by confirmed practice.')
            tracker = scoped_tracker(engine, tracker, request, settings)
            engine.refresh_quota_balances(page, tracker, (request.day,))
            operation_stage('Checking rooms for the remaining practice gaps…')
            engine.open_practice_room_overview(page, today)
            if request.day != today:
                engine.navigate_to_day(page, (request.day-today).days, 0, base_date=today)
            options, reasons = candidates(engine, engine.get_available_slots(page), tracker, request, local_now())
            options = [c for c in options if (c['room'], c['start_hour'], c['minutes']) not in refused]
            if not options:
                return finish('partial' if bookings else 'empty', 'Some of this range could not be booked. Review the remaining gaps.',
                              reasons=reasons or ['No available eligible room, or the gap is shorter than the site minimum.'],
                              advance_minutes=tracker.live_quota_minutes,
                              peak_minutes=tracker.live_peak_minutes.get(str(request.day)))
            candidate = choose_candidate(engine, options, tracker, request, local_now())
            start = round(candidate['start_hour']*60)
            end = round(candidate['end_hour']*60)
            operation_stage(f"Filling {clock(start)}–{clock(end)} in {candidate['room']}…")

            def before_save(actual):
                now = local_now()
                request.check_current(now)
                if actual != {'room': candidate['room'], 'date': str(request.day), 'start': clock(start),
                              'end': clock(end), 'duration_minutes': end-start}:
                    raise SettingsError('The final booking no longer matches the selected gap. No Save was attempted.')
                if datetime.combine(request.day, datetime.min.time(), tzinfo=LONDON)+timedelta(minutes=start) <= now:
                    raise SettingsError('The selected start time has passed.')
                allowed, reason = tracker.can_book(candidate['room'], request.day, start/60, end-start, now=now.replace(tzinfo=None))
                if not allowed:
                    raise SettingsError(reason)
                finish('uncertain', 'Checking the last booking result. Do not submit another fill yet.',
                       attempted={**actual, 'requested_at': request.requested_at.isoformat()})

            saved = engine.try_book_slot(page, candidate, request.day, tracker, (request.day-today).days,
                remaining_daily_hours=None, max_action_minutes=candidate['minutes'],
                time_prefs={'enabled': False, 'strict_mode': False}, before_save=before_save)
            if not isinstance(saved, dict) or not saved.get('receipt_id'):
                if engine.list_pending_mutation_receipts():
                    return 0  # Preserve the durable attempted identity, never retry an uncertain Save.
                refused.add((candidate['room'], candidate['start_hour'], candidate['minutes']))
                continue
            finish('uncertain', 'The room was saved; checking its complete agenda entry.', saved=saved)
            try:
                with operation_verification():
                    proof_tracker = engine.BookingTracker()
                    engine.scan_agenda(page, proof_tracker, today,
                        ignored_events=engine.load_ignored_events(settings), window_dates=live_dates,
                        snapshot_path=engine.AGENDA_SNAPSHOT_FILE)
                    proof_events = proof_tracker.agenda_events
                    from mutation_receipts import load_journal
                    booking = confirmed_booking(saved, load_journal()['receipts'].get(saved['receipt_id']), proof_events)
                bookings.append(booking)
                tracker, events = proof_tracker, proof_events
                engine.save_history(len(bookings), len(events), list(engine._run_verified_details.get() or ()))
                finish('running', 'Booking confirmed; checking the remaining gaps…')
            except Exception:
                return 0  # The saved identity remains recoverable on disk.
        return finish('partial', 'The bounded fill attempt finished. Review or retry the remaining gaps.')
    except OperationStopped:
        return finish('stopped', 'Stopped. Any confirmed practice remains booked.')
    except QuotaWait as exc:
        return finish('partial' if bookings else 'blocked', 'ASIMUT refused further booking because of its quota.', reasons=[str(exc)])
    except SettingsError as exc:
        if output.exists() and json.loads(output.read_text(encoding='utf-8')).get('state') == 'uncertain':
            return 0
        return finish('partial' if bookings else 'blocked', str(exc))
    finally:
        if bookings:
            try:
                pin_bookings(bookings, engine.settings_file)
            except Exception:
                finish('uncertain', 'Practice was booked, but its protection from automatic changes needs checking.')
                raise


def review_result(directory, root, *, filename='fill-range.json'):
    """Read-only reconciliation; never resumes the old fill request."""
    from agenda_snapshot import read_agenda_snapshot
    from mutation_receipts import load_journal
    path = Path(directory)/filename
    if not path.exists():
        return {'state': 'stopped', 'message': 'This fill did not record a booking attempt. You can make a fresh request.'}
    old = json.loads(path.read_text(encoding='utf-8'))
    agenda = read_agenda_snapshot(Path(root)/'data/agenda_snapshot.json')
    if agenda.stale or not agenda.snapshot:
        raise ValueError('A fresh complete agenda is required.')
    events = agenda.snapshot.event_dicts()
    receipts = load_journal(Path(root)/'data/mutation_receipts.json')['receipts']
    saved = old.get('saved')
    attempt = old.get('attempted')
    receipt = receipts.get(saved['receipt_id']) if saved else None
    if not saved and attempt:
        matches = [r for r in receipts.values() if r['kind']=='create'
                   and all(r[k]==attempt[k] for k in ('room','date','start','end'))
                   and datetime.fromisoformat(r['created_at']) >= datetime.fromisoformat(attempt['requested_at']).replace(microsecond=0)]
        if len(matches)>1:
            raise ValueError('Several receipts match; review the booking outcome.')
        if matches:
            receipt=matches[0]
            saved={**attempt,'receipt_id':receipt['id']}
    bookings = old.get('bookings', [])
    if saved:
        if not receipt or receipt['status']=='pending':
            raise ValueError('The last booking still needs reconciliation.')
        if receipt['status']=='verified':
            booking=confirmed_booking(saved,receipt,events)
            if agenda.snapshot.observed_at < datetime.fromisoformat(receipt['verified_at']):
                raise ValueError('The agenda must be newer than the booking receipt.')
            if booking['receipt_id'] not in {b['receipt_id'] for b in bookings}:
                bookings.append(booking)
        elif not receipt.get('resolution','').startswith('Asimut rejected Save:'):
            raise ValueError('Review the resolved booking receipt before another fill.')
    request=FillRequest.create(old['range'], local_now().isoformat())
    complete=not uncovered(request,events)
    result=result_document(request,events,bookings,'completed' if complete else 'partial',
        'The whole range is covered.' if complete else 'Booking status checked. You can try the remaining gaps with a new request.')
    for b in bookings:
        if not any(e.get('eventId')==b['event_id'] and e.get('isReservation') is True
                   and (e['date'],e.get('room'),e['startTime'],e['endTime'])==(b['date'],b['room'],b['start'],b['end']) for e in events):
            raise ValueError('A fill booking changed after saving. Review it before another fill.')
    pin_bookings(bookings, Path(root)/'data/settings.json')
    atomic_write_json(path,result)
    return result
