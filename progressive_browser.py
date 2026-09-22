"""Prepare exact transfer destinations early; Save only under their parent."""
from booking_quotas import QuotaWait
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

from progressive_transactions import reservation, validate_transfer, TransferEdit
from room_upgrades import Reservation, time_text, local_instant
from upgrade_validation import (RoomPermissionRefusal, room_permission_refusal_text,
                                response_room_permission_refusal, upgrade_request_matches)


@dataclass
class PreparedSeed:
    page: object
    desired: Reservation
    existing_seed: Reservation | None = None


def _new_seed_check_matches(request, desired, location_id):
    """Bind a newly issued check to the exact single reservation being prepared."""
    try:
        url = urlsplit(request.url)
        if (url.scheme != 'https' or url.netloc != 'rwcmd.asimut.net'
                or url.path != '/services/v2/event/type=check' or url.query or url.fragment
                or request.method != 'POST'):
            return False
        payload = request.post_data_json
        event = payload['event']
        if type(event.get('id')) is not int or event['id'] != 0:
            return False
        if (not isinstance(event['rs'], list) or len(event['rs']) != 1
                or event['rs'][0]['id'] != location_id or type(event['rs'][0]['id']) is not int):
            return False
        if payload.get('booking_type') != 'single' or type(payload.get('time_period_id')) is not int or payload['time_period_id'] != 0:
            return False
        weekdays = payload.get('weekdays')
        if (not isinstance(weekdays, list) or len(weekdays) != 1
                or type(weekdays[0]) is not int or not 0 <= weekdays[0] <= 6):
            return False
        for key, minutes in (('st', desired.start), ('en', desired.end)):
            stamp = datetime.fromisoformat(event[key])
            if (stamp.tzinfo is None or stamp.second or stamp.microsecond
                    or stamp.date() != desired.day or stamp.hour * 60 + stamp.minute != minutes
                    or stamp.astimezone(local_instant(desired.day, minutes).tzinfo) != local_instant(desired.day, minutes)):
                return False
        return True
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


class _PersonalConflictMessage(HTMLParser):
    """Read only the structured personal-clash interval and fixed framing."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = {'message-event-headline': [], 'time-slot-p': [],
                      'message-event-footer': [], 'desc-p': []}
        self.active = None
        self.text = []
        self.unclassified_text = False

    def handle_starttag(self, tag, attrs):
        if tag == 'p':
            classes = dict(attrs).get('class', '').split()
            self.active = next((name for name in self.parts if name in classes), None)
            self.text = []

    def handle_data(self, data):
        if self.active is not None:
            self.text.append(data)
        elif data.strip():
            self.unclassified_text = True

    def handle_endtag(self, tag):
        if tag == 'p' and self.active is not None:
            self.parts[self.active].append(' '.join(''.join(self.text).split()))
            self.active = None


def _only_removed_personal_conflicts(document, plan, *, peak_start, peak_end, peak_limit):
    """Accept approved checks or only independently compensated source conflicts."""
    try:
        response = document['response']
        expected_id = plan.seed.event_id if plan.seed is not None else 0
        if (not isinstance(response, dict) or response.get('forms') != []
                or response.get('event_ids') != [expected_id]
                or type(response['event_ids'][0]) is not int):
            return False
        errors = document.get('messages', {}).get('errors', [])
        if (not isinstance(errors, list) or any(error != 'Unable to modify event, see the booking rules'
                                               for error in errors)):
            return False
        issues = response['bookingrules']['issues']
        if not isinstance(issues, list):
            return False
        warnings = []
        peak_warning = False
        for issue in issues:
            if not isinstance(issue, dict):
                return False
            if issue.get('class') == 'message-info' and issue.get('type') == 'general':
                # Permission text cannot be disguised as a harmless info row.
                if room_permission_refusal_text(issue.get('text')):
                    return False
                continue
            if (issue.get('class') == 'message-warning' and issue.get('type') == 'category'
                    and issue.get('text') == 'Requested booking exceeds your peak quota'):
                peak_warning = True
                continue
            if issue.get('class') != 'message-warning' or issue.get('type') != 'persons':
                return False
            warnings.append(issue)
        if response.get('success') is True:
            return not errors and not warnings and not peak_warning and not response['bookingrules'].get('clashing_person_ids')
        if response.get('success') is not False or not (warnings or peak_warning):
            return False
        desired = plan.replacement
        if peak_warning:
            peak = lambda r: max(0, min(r.end, peak_end) - max(r.start, peak_start))
            before_peak = sum(peak(r) for r in plan.originals) + (peak(plan.seed) if plan.seed is not None else 0)
            after_peak = sum(peak(r) for r in plan.remaining) + peak(desired)
            # The runtime separately verifies all other reservations. Here the
            # exact planned source reductions must free every newly added peak
            # minute; a better room never excuses an uncompensated quota error.
            if after_peak > before_peak or after_peak > peak_limit:
                return False
        retained = {r.event_id: r for r in plan.remaining}
        overlaps = lambda r: r.start < desired.end and r.end > desired.start
        expected = {(r.start, r.end) for r in plan.originals if overlaps(r)
                    and (r.event_id not in retained or not overlaps(retained[r.event_id]))}
        if warnings and not expected:
            return False
        reported = []
        for issue in warnings:
            if not isinstance(issue.get('text'), str) or room_permission_refusal_text(issue['text']):
                return False
            parsed = _PersonalConflictMessage()
            parsed.feed(issue['text'])
            if (parsed.unclassified_text
                    or parsed.parts['message-event-headline'] != ['You have conflicting events:']
                    or parsed.parts['message-event-footer'] != ['You must resolve the conflicts before saving the event.']):
                return False
            if not parsed.parts['time-slot-p']:
                return False
            for interval in parsed.parts['time-slot-p']:
                match = re.fullmatch(r'([0-2][0-9]):([0-5][0-9])\s*[-–]\s*([0-2][0-9]):([0-5][0-9])', interval)
                if not match:
                    return False
                start_h, start_m, end_h, end_m = map(int, match.groups())
                if not 0 <= start_h < 24 or not 0 <= end_h < 24:
                    return False
                reported.append((start_h * 60 + start_m, end_h * 60 + end_m))
        return len(reported) == len(set(reported)) and set(reported) == expected
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def preflight_transfer_destination(engine, prepared, plan):
    """Prove the destination accepts everything except compensated source conflicts.

    Call at the actual boundary before releasing coverage. This performs only a
    fresh check, never Save, against the exact new or existing destination form.
    A successful result is still not authority for the later post-trim Save.
    """
    if (not isinstance(prepared, PreparedSeed) or prepared.existing_seed != getattr(plan, 'seed', None)
            or prepared.desired != plan.replacement):
        return False
    page, desired = prepared.page, prepared.desired
    location_id = engine.require_live_room_policy().all_room_location_ids.get(desired.room)
    if type(location_id) is not int or location_id <= 0:
        return False
    def correct_url():
        return (engine.parse_event_editor_id(page.url) == prepared.existing_seed.event_id
                if prepared.existing_seed is not None else engine.is_new_booking_form_url(page.url))
    def check_matches(request):
        return (upgrade_request_matches(request, TransferEdit(prepared.existing_seed, desired), location_id)
                if prepared.existing_seed is not None else _new_seed_check_matches(request, desired, location_id))
    if (not correct_url()
            or not engine.booking_summary_matches(engine.page_booking_snapshot(page), desired.room,
                desired.day, time_text(desired.start), time_text(desired.end))):
        return False
    fresh_requests, responses = [], []
    def request_seen(request):
        if check_matches(request):
            fresh_requests.append(request)
    def response_seen(response):
        if response.request in fresh_requests:
            responses.append(response)
    page.on('request', request_seen)
    page.on('response', response_seen)
    try:
        if prepared.existing_seed is None:
            ok, detail = engine.refresh_new_booking_validation(page, time_text(desired.end))
        else:
            end = page.get_by_role('textbox', name='End time', exact=True)
            # An unchanged Angular value may suppress validation. Reset only
            # the unsaved end field, then commit the intended end afresh.
            end.fill(time_text(prepared.existing_seed.end))
            end.press('Tab')
            engine.dismiss_reservation_time_picker(page)
            ok, detail = engine.refresh_extension_validation(page, end,
                prepared.existing_seed.as_booking(), time_text(desired.end))
    except QuotaWait as exc:
        # This no-Save preflight independently validates compensated warnings
        # below. The eventual post-transfer Save must still pass normal checks.
        ok, detail = False, str(exc)
    finally:
        page.remove_listener('request', request_seen)
        page.remove_listener('response', response_seen)
    if not ok:
        refusal = room_permission_refusal_text(detail, desired.room)
        if refusal:
            return RoomPermissionRefusal(desired.room, refusal)
        # The ordinary edit helper rejects personal overlap/peak quota. Only
        # the exact captured response may establish those compensated cases.
        if not responses:
            return False
    if (not responses or not correct_url()
            or not engine.booking_summary_matches(engine.page_booking_snapshot(page), desired.room,
                desired.day, time_text(desired.start), time_text(desired.end))):
        return False
    try:
        response = responses[-1]
        if response.ok is not True or not check_matches(response.request):
            return False
        document = response.json()
        refusal = response_room_permission_refusal(document, desired.room)
        if refusal:
            return RoomPermissionRefusal(desired.room, refusal)
        return _only_removed_personal_conflicts(document, plan,
            peak_start=int(engine.PEAK_START * 60), peak_end=int(engine.PEAK_END * 60),
            peak_limit=int(engine.MAX_PEAK_HOURS * 60))
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def prepare_existing_destination(engine, page, plan):
    """Prepare the exact anchor's unsaved extension on an owned separate page."""
    seed, desired = getattr(plan, 'seed', None), plan.replacement
    if (not isinstance(seed, Reservation) or seed.event_id != desired.event_id
            or seed.day != desired.day or seed.room != desired.room
            or seed.start != desired.start or seed.end >= desired.end):
        return False
    engine.safe_goto(page, seed.event_url)
    engine.verify_persisted_booking_page(page, seed.room, seed.day,
                                         time_text(seed.start), time_text(seed.end))
    engine.safe_goto(page, f'{engine.ASIMUT_BASE_URL}/event?eventId={seed.event_id}')
    end = page.get_by_role('textbox', name='End time', exact=True)
    end.wait_for(state='visible', timeout=10000)
    if (engine.parse_event_editor_id(page.url) != seed.event_id
            or not engine.booking_summary_matches(engine.page_booking_snapshot(page), seed.room,
                seed.day, time_text(seed.start), time_text(seed.end))):
        return False
    ok, detail = engine.refresh_extension_validation(page, end, seed.as_booking(), time_text(desired.end))
    refusal = engine._visible_room_permission_refusal(page, seed.room)
    if refusal is not None:
        return refusal
    reason = room_permission_refusal_text(detail, seed.room) if not ok else None
    if reason:
        return RoomPermissionRefusal(seed.room, reason)
    engine.dismiss_reservation_time_picker(page)
    if not engine.booking_summary_matches(engine.page_booking_snapshot(page), desired.room,
            desired.day, time_text(desired.start), time_text(desired.end)):
        return False
    return PreparedSeed(page, desired, existing_seed=seed)


def prepare_transfer_destination(engine, page, plan):
    """Prepare either destination form without releasing any source coverage."""
    return (prepare_existing_destination(engine, page, plan) if plan.seed is not None
            else prepare_seed(engine, page, plan.replacement))


def prepare_seed(engine, page, desired):
    today = datetime.now().date()
    engine.open_practice_room_overview(page, today)
    if desired.day != today:
        engine.navigate_to_day(page, (desired.day - today).days, 0, base_date=today)
    engine.wait_for_practice_room_grid(page, desired.day)
    coords = engine.get_room_slot_coordinates(page, desired.room, desired.start / 60, desired.end / 60)
    if not coords:
        return False
    page.mouse.click(coords['x'], coords['y'])
    if not engine.enter_new_booking_form(page):
        return False
    for label, value in (('Start time', desired.start), ('End time', desired.end)):
        control = page.get_by_role('textbox', name=label, exact=True)
        control.fill(time_text(value))
        control.press('Tab')
    engine.dismiss_reservation_time_picker(page)
    if not engine.booking_summary_matches(engine.page_booking_snapshot(page), desired.room,
            desired.day, time_text(desired.start), time_text(desired.end)):
        return False
    refusal = engine._visible_room_permission_refusal(page, desired.room)
    if refusal is not None:
        return refusal
    # A personal overlap/horizon warning can be expected before the transfer;
    # it is never Save authority. The exact form is checked again after trimming.
    return PreparedSeed(page, desired)


def save_seed(engine, prepared, receipt, *, role='seed'):
    from mutation_receipts import record_pending_create, mark_transfer_step
    page, desired = prepared.page, prepared.desired
    if prepared.existing_seed is not None:
        raise engine.BookingVerificationError('An existing destination must be edited, never created again')
    if receipt.get('kind') != 'transfer':
        raise engine.BookingVerificationError('New reservation requires its exact transfer parent')
    t = validate_transfer(receipt['transfer'])
    retained_ids = {r['event_id'] for r in t['remaining']}
    allowed = (reservation(t['replacement']) if role == 'seed' and t['seed_before'] is None else next(
        (reservation(r) for r in t['originals']
         if role == f"restore:{r['event_id']}" and r['event_id'] not in retained_ids), None))
    if desired != allowed or engine.list_pending_mutation_receipts() != [receipt]:
        raise engine.BookingVerificationError('New transfer reservation lost its exact parent scope')
    if not engine.is_new_booking_form_url(page.url):
        raise engine.BookingVerificationError('Prepared transfer editor changed identity')
    try:
        ok, detail = engine.refresh_new_booking_validation(page, time_text(desired.end))
    except QuotaWait as exc:
        # Known pre-Save rejection must reach the parent's compensation path.
        print(f'TRANSFER NOT SAVED: {exc}')
        return False
    if not ok:
        refusal = engine._visible_room_permission_refusal(page, desired.room)
        if refusal is None and room_permission_refusal_text(detail, desired.room):
            refusal = RoomPermissionRefusal(desired.room, detail)
        print(f'TRANSFER NOT SAVED: {detail}')
        return refusal if refusal is not None else False
    if not engine.booking_summary_matches(engine.page_booking_snapshot(page), desired.room,
            desired.day, time_text(desired.start), time_text(desired.end)):
        raise engine.BookingVerificationError('Transfer form no longer matches the exact interval')
    rejection = engine._visible_save_rejection(page)
    if rejection:
        refusal = engine._visible_room_permission_refusal(page, desired.room)
        return refusal if refusal is not None else False
    save = page.get_by_role('button', name='Save event', exact=True)
    if save.count() != 1 or not save.is_enabled():
        return False
    engine.dismiss_reservation_time_picker(page)
    save.click(trial=True, timeout=3000)
    with engine.booking_save_boundary():
        if (engine.list_pending_mutation_receipts() != [receipt]
                or not engine.is_new_booking_form_url(page.url)
                or not engine.booking_summary_matches(engine.page_booking_snapshot(page), desired.room,
                    desired.day, time_text(desired.start), time_text(desired.end))
                or save.count() != 1 or not save.is_enabled()):
            raise engine.BookingVerificationError('Transfer parent, exact editor or Save changed before creation')
        mark_transfer_step(receipt, 'destination' if role == 'seed' else role)
        child = record_pending_create(room=desired.room, booking_date=desired.day.isoformat(),
            start=time_text(desired.start), end=time_text(desired.end),
            parent_id=receipt['id'], transfer_role=role)
        try:
            save.click(no_wait_after=True, timeout=5000)
        except Exception as exc:
            raise engine.BookingVerificationError('Transfer creation outcome is uncertain; parent retained') from exc
    if not engine.wait_for_created_booking_outcome(page, child, desired.room, desired.day,
            time_text(desired.start), time_text(desired.end)):
        return False
    event_id = engine.parse_confirmed_event_id(page.url)
    if not event_id:
        raise engine.BookingVerificationError('Verified transfer creation has no exact event identity')
    return Reservation(event_id, desired.day, desired.room, desired.start, desired.end)
