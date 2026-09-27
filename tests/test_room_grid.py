import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app_settings import atomic_write_json
from room_grid import normalize_day, publish_day, read_grid, room_revision, event_display, enrich_grid
from operation_control import observe_room_grid, report_room_grid
from phone_operation_worker import execute
from tests import test_phone_server as http_fixture


def fixture():
    return {'renderer': 'svg', 'rooms': [
        {'room': 'B0.13', 'blockedRanges': [
            {'startHour': 7, 'endHour': 8, 'closed': True},
            {'startHour': 9, 'endHour': 10.25, 'closed': False, 'label': 'Alex Morgan', 'eventId': '123'},
            {'startHour': 22.25, 'endHour': 23, 'closed': True}]},
        {'room': 'B1.15', 'blockedRanges': [{'startHour': 7, 'endHour': 23, 'closed': True}]},
        {'room': 'Excluded room', 'blockedRanges': [{'startHour': 9, 'endHour': 12, 'closed': False, 'label': 'Do not expose'}]}]}


class RoomGridTests(unittest.TestCase):
    def test_person_names_require_exact_identity_room_date_and_times(self):
        from datetime import date
        event = {'id':123,'rs':[{'id':74}], 'st':'2026-09-27T09:00:00+01:00', 'en':'2026-09-27T10:15:00+01:00',
                 'ar':'Reservation','ps':[{'bo':[{'fn':'Alex','ln':'Morgan','un':'Never retain'}]}]}
        block = {'startHour':9, 'endHour':10.25}
        result=event_display(event,'123','74',date(2026,9,27),block)
        self.assertEqual(result['label'],'Alex Morgan')
        self.assertNotIn('Never retain',json.dumps(result))
        for changes in ({'id':456},{'rs':[{'id':75}]},{'st':'2026-09-28T09:00:00+01:00'},{'en':'2026-09-27T11:00:00+01:00'}):
            self.assertIsNone(event_display({**event,**changes},'123','74',date(2026,9,27),block))

    def test_live_closures_replace_duplicate_overlays_and_failed_names_stay_busy(self):
        from datetime import date
        from unittest.mock import MagicMock
        source=fixture();source['rooms'][0]['locationId']='74'
        source['rooms'][0]['blockedRanges'].append({'startHour':22.25,'endHour':23,'closed':False,'eventId':'999'})
        page=MagicMock();page.request.get.return_value.ok=True
        page.request.get.return_value.json.return_value={'response':{'locations':[{'id':74,'closed_hours':[
            {'st':'2026-09-27T00:00:00+01:00','en':'2026-09-27T09:30:00+01:00'},
            {'st':'2026-09-27T16:15:00+01:00','en':'2026-09-27T23:59:59+01:00'}]}]}}
        page.evaluate.return_value=[None]
        enriched=enrich_grid(page,date(2026,9,27),source,['B0.13'])
        normalized=normalize_day('2026-09-27',enriched,['B0.13'])['rooms'][0]
        self.assertEqual(normalized['closes'],'16:15')
        self.assertEqual(len([b for b in normalized['intervals'] if b['kind']=='booked']),1)
        self.assertEqual(page.evaluate.call_args.args[1],['123'])

    def test_only_eligible_rooms_and_explicit_closing_times(self):
        result = normalize_day('2026-09-27', fixture(), ['B1.15', 'B0.13'])
        self.assertEqual([r['name'] for r in result['rooms']], ['B1.15', 'B0.13'])
        self.assertTrue(result['rooms'][0]['closed_all_day'])
        self.assertEqual(result['rooms'][1]['closes'], '22:15')
        self.assertEqual(result['rooms'][1]['intervals'][1], dict(start=540, end=615, kind='booked', label='Alex Morgan', title='', event_id='123'))
        self.assertNotIn('Do not expose', json.dumps(result))

    def test_missing_names_remain_busy_and_unknown_close_is_not_invented(self):
        source = fixture(); source['rooms'][0]['blockedRanges'] = [{'startHour': 7, 'endHour': 23, 'closed': False}]
        room = normalize_day('2026-09-27', source, ['B0.13'])['rooms'][0]
        self.assertEqual(room['intervals'][0]['label'], 'Booked · name not shown')
        self.assertIsNone(room['closes']); self.assertFalse(room['closed_all_day'])

    def test_bad_or_incomplete_grids_never_become_free_space(self):
        for transform in [lambda s: s['rooms'].pop(0), lambda s: s['rooms'].append(copy.deepcopy(s['rooms'][0])),
                          lambda s: s['rooms'][0]['blockedRanges'][0].update(startHour=float('nan'))]:
            source = fixture(); transform(source)
            with self.assertRaises(ValueError): normalize_day('2026-09-27', source, ['B0.13'])

    def test_day_merge_staleness_and_room_preference_invalidation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); settings = root/'data/settings.json'
            atomic_write_json(settings, {})
            day = datetime.now(timezone.utc).date().isoformat()
            publish_day(day, fixture(), ['B0.13'], room_revision({}), root=root)
            fresh = read_grid(root=root)
            self.assertFalse(fresh['days'][day]['stale'])
            self.assertTrue(read_grid(root=root, now=datetime.now(timezone.utc)+timedelta(minutes=6))['days'][day]['stale'])
            atomic_write_json(settings, {'room_preferences': {'excluded_rooms': ['B0.13']}})
            self.assertEqual(read_grid(root=root)['days'], {})
            self.assertNotIn('Alex', json.dumps(read_grid(root=root)))
            self.assertNotEqual(read_grid(root=root)['revision'], fresh['revision'])

    def test_unreadable_settings_explain_failure_without_authorizing_auto_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'data/settings.json'
            path.parent.mkdir(); path.write_text('{broken')
            result = read_grid(root=tmp)
            self.assertEqual(result['days'], {})
            self.assertIsNone(result['revision'])
            self.assertIn('settings could not be read', result['message'])

    def test_hook_scoped_and_worker_only_requests_check_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); directory=root/'job'; directory.mkdir()
            atomic_write_json(root/'data/settings.json', {})
            day=datetime.now(timezone.utc).date().isoformat()
            def main(flags):
                self.assertEqual(flags, ['--headless','--check-only','--check-dates',day])
                report_room_grid(datetime.fromisoformat(day).date(), fixture(), ['B0.13'], {})
                return 0
            result=execute('scan', {'dates':[day]}, directory, root=root, booker_main=main)
            self.assertEqual(result['state'], 'completed')
            self.assertEqual(len(read_grid(root=root)['days'][day]['rooms']), 1)
            with patch('room_grid.publish_day') as publish:
                report_room_grid(datetime.fromisoformat(day).date(), fixture(), ['B0.13'], {})
                publish.assert_not_called()


class RoomGridHTTPTests(unittest.TestCase):
    setUp = http_fixture.HTTPBoundaryTests.setUp
    tearDown = http_fixture.HTTPBoundaryTests.tearDown
    request = http_fixture.HTTPBoundaryTests.request
    open_session = http_fixture.HTTPBoundaryTests.open_session
    def test_grid_requires_session_and_remains_private(self):
        with patch('room_grid.read_grid', return_value={'days':{},'message':''}) as read:
            status, _, _ = self.request('GET','/api/v1/room-grid')
            self.assertEqual(status,401); read.assert_not_called()
            cookie, _ = self.open_session()
            status, headers, body = self.request('GET','/api/v1/room-grid', headers={'Cookie':cookie})
            self.assertEqual(status,200)
            self.assertIn('no-store',headers['Cache-Control'])
            self.assertEqual(json.loads(body)['days'],{})


class RoomGridLayoutTests(unittest.TestCase):
    def test_real_navigation_recovers_missing_and_invalidated_cache_without_retrying_stop(self):
        from tests.test_desktop_settings import DesktopSettingsTests
        from room_catalog import SITE_TIMEZONE
        fixture_app = DesktopSettingsTests(); fixture_app.setUp()
        self.addCleanup(fixture_app.doCleanups)
        app, root = fixture_app.app, fixture_app.root
        root.attributes('-alpha', 0); root.deiconify(); root.geometry('1200x850')
        panel = app.room_availability
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp)
            settings = store/'data/settings.json'
            atomic_write_json(settings, {})
            panel.reader = lambda: read_grid(root=store)
            class Scanner:
                active = False
                state = None
                text = ''
                calls = []
                def start(self, dates):
                    self.calls.append(dates); self.active = True
                    self.state, self.text = 'running', 'Reading rooms…'
                def stop(self):
                    self.active = False
                    self.state, self.text = 'stopped', 'Stopped. Select Refresh to try again.'
            panel.scanner = Scanner()
            busy = [True]
            panel.other_busy = lambda: busy[0]
            app._select_quiet_page('rooms'); root.update()
            self.assertEqual(panel.scanner.calls, [])
            self.assertEqual(panel.empty_title.cget('text'), 'Waiting for the Booker…')
            def poll():
                if panel.after_id: panel.after_cancel(panel.after_id)
                panel.poll(); root.update()
            busy[0] = False
            poll()
            self.assertEqual(len(panel.scanner.calls), 1)
            self.assertEqual(panel.empty_title.cget('text'), 'Loading room availability…')
            self.assertTrue(panel.empty.winfo_ismapped())
            today = datetime.now(SITE_TIMEZONE).date().isoformat()
            for day in panel.scanner.calls[0]:
                publish_day(day, fixture(), ['B0.13','B1.15'], room_revision({}), root=store)
            panel.scanner.active = False; panel.scanner.state = 'completed'
            app._select_quiet_page('rooms'); root.update()
            self.assertTrue(panel.booking_widgets)
            self.assertFalse(panel.empty.winfo_ismapped())
            self.assertEqual(len(panel.scanner.calls), 1)
            # Reproduce the reported failure with actual settings/cache reads.
            changed = {'room_preferences': {'excluded_rooms': ['B0.13']}}
            atomic_write_json(settings, changed)
            poll()
            self.assertEqual(len(panel.scanner.calls), 2)
            self.assertFalse(panel.booking_widgets, 'Old excluded room remained visible')
            self.assertIn('Loading', panel.empty_title.cget('text'))
            panel.scanner.stop()
            app._select_quiet_page('rooms'); root.update()
            self.assertEqual(len(panel.scanner.calls), 2, 'Stop must not silently restart')
            self.assertIn('Stopped', panel.empty_message.cget('text'))
            panel.refresh_button.invoke(); root.update()
            self.assertEqual(len(panel.scanner.calls), 3, 'Explicit retry must still work')
            publish_day(today, fixture(), ['B1.15'], room_revision(changed), root=store)
            panel.scanner.active = False; panel.scanner.state = 'completed'
            app._select_quiet_page('rooms'); root.update()
            self.assertEqual([r['name'] for r in panel.document['days'][today]['rooms']], ['B1.15'])
            self.assertFalse(panel.empty.winfo_ismapped(), 'A fully closed room must still be displayed')
            app._select_quiet_page('settings'); root.update()
            self.assertFalse(panel.active)
            self.assertIsNone(panel.after_id)

    def test_resize_fills_timeline_and_centres_controls_at_ultrawide_and_small_sizes(self):
        from tests.test_desktop_settings import DesktopSettingsTests
        from room_catalog import SITE_TIMEZONE
        fixture_app = DesktopSettingsTests(); fixture_app.setUp()
        self.addCleanup(fixture_app.doCleanups)
        app, root = fixture_app.app, fixture_app.root
        root.maxsize(5000,3000)
        root.attributes('-alpha',0);root.deiconify()
        day=datetime.now(SITE_TIMEZONE).date().isoformat()
        doc=normalize_day(day,fixture(),['B0.13','B1.15']);doc['stale']=False
        panel=app.room_availability
        panel.reader=lambda:{'days':{day:doc},'message':''}
        app._select_quiet_page('rooms')
        for width in (760,1040,1920,3440,1040):
            with self.subTest(width=width):
                root.geometry(f'{width}x900');root.update()
                self.assertEqual(root.winfo_width(),width)
                self.assertEqual(panel.rendered_width,max(1280,panel.track.winfo_width()))
                centre=panel.winfo_rootx()+panel.winfo_width()/2
                self.assertLess(abs(panel.body.winfo_rootx()+panel.body.winfo_width()/2-centre),2)
                self.assertLess(abs(panel.date_controls.winfo_rootx()+panel.date_controls.winfo_width()/2-centre),2)
                self.assertLessEqual(panel.date_controls.winfo_width(),1000)
                self.assertEqual(panel.booking_font.cget('size'),-16)
                panel.booking_widgets[0].invoke();root.update()
                self.assertTrue(panel.details.winfo_ismapped())
                self.assertLessEqual(panel.legend.winfo_rooty()+panel.legend.winfo_height(),panel.winfo_rooty()+panel.winfo_height())
                panel.details.pack_forget()
