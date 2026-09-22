"""Synthetic ranking and exact proof; never signs in or modifies live data."""
from contextlib import ExitStack
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

import book_week as engine
from app_settings import SettingsError, atomic_write_json
from desktop_room_now import DesktopRoomNow
from operation_control import owned_operation, operation_verification, check_operation_stop, OperationStopped
from room_now import LONDON, RoomNowRequest, candidates, confirmed_booking, cli_flags, run
from room_now import review_result

NOW = datetime(2030, 9, 28, 14, 1, tzinfo=LONDON)
PREFS = {'enabled': False, 'strict_mode': False, 'start_hour': 12, 'end_hour': 22}


def grid(*entries):
    return [{'room': room, 'slots': [{'startHour': start, 'endHour': end}]} for room, start, end in entries]


class RoomNowTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack();self.addCleanup(self.stack.close)
        for name,value in [('PRIORITY_ROOMS',['Top','Other']),('MINIMUM_BLOCK_MINUTES',30),
                           ('MIN_BOOKING_MINUTES',30),('MAX_BOOKING_HOURS',2),('SAME_ROOM_GAP_MINUTES',60),
                           ('FREE_HORIZON_MINUTES',300),('FREE_HORIZON_OVERRIDES_PEAK',True)]:
            self.stack.enter_context(patch.object(engine,name,value))
        self.stack.enter_context(patch.object(engine,'room_horizon_minutes',return_value=10080))
        self.tracker=engine.BookingTracker()
        self.request=RoomNowRequest('preferred',60,NOW)

    def choose(self,rooms,**kwargs):
        return candidates(engine,rooms,self.tracker,kwargs.get('request',self.request),
                          kwargs.get('prefs',PREFS),kwargs.get('daily'),now=kwargs.get('now',NOW))

    def test_shorter_now_wins_over_full_duration_later_in_better_room(self):
        rows=self.choose(grid(('Top',14.5,16),('Other',14.25,14.75)))
        self.assertEqual((rows[0]['room'],rows[0]['start_hour'],rows[0]['minutes']),('Other',14.25,30))

    def test_duration_then_room_ranking_at_same_start(self):
        rows=self.choose(grid(('Top',14.25,14.75),('Other',14.25,16)))
        self.assertEqual((rows[0]['room'],rows[0]['minutes']),('Other',60))
        rows=self.choose(grid(('Other',14.25,16),('Top',14.25,16)))
        self.assertEqual(rows[0]['room'],'Top')

    def test_longest_never_exceeds_maximum_or_books_past_time(self):
        rows=self.choose(grid(('Top',10,23)),request=RoomNowRequest('longest',120,NOW))
        self.assertEqual(rows[0]['minutes'],120)
        self.assertTrue(all(r['minutes']<=120 and r['start_hour']>14 for r in rows))

    def test_custom_ceiling_and_daily_target(self):
        rows=self.choose(grid(('Top',14,22)),request=RoomNowRequest('longest',75,NOW),daily=.75)
        self.assertEqual(rows[0]['minutes'],45)

    def test_conflicts_cancelled_intervals_and_same_room_gap(self):
        self.tracker.add_conflict(NOW.date(),14,15)
        self.tracker.add_conflict(NOW.date(),15,16)
        self.tracker.reservation_ranges[str(NOW.date())]=[(15,16,'Top')]
        rows=self.choose(grid(('Top',14,20),('Other',14,20)))
        self.assertEqual(rows[0]['start_hour'],16)
        self.assertEqual(rows[0]['room'],'Other')
        self.assertTrue(all(r['room']!='Top' or r['start_hour']>=17 for r in rows))

    def test_extension_holds_and_strict_window(self):
        self.tracker.extension_holds=({'date':str(NOW.date()),'room':'Top','startTime':'14:00','target_end':'16:00'},)
        prefs={**PREFS,'enabled':True,'strict_mode':True,'start_hour':15,'end_hour':17}
        rows=self.choose(grid(('Top',14,21),('Other',14,21)),prefs=prefs)
        self.assertEqual((rows[0]['room'],rows[0]['start_hour']),('Other',16))
        self.assertTrue(all(r['end_hour']<=17 for r in rows))

    def test_access_room_filter_and_complete_horizon(self):
        with patch.object(engine,'room_horizon_minutes',return_value=60):
            rows=self.choose(grid(('Excluded',14,21),('Top',14,21)))
        self.assertTrue(rows)
        self.assertTrue(all(r['room']=='Top' and r['end_hour']<=15 for r in rows))

    def test_zero_advance_credit_only_allows_complete_free_window(self):
        self.tracker.live_quota_minutes=0
        rows=self.choose(grid(('Top',14,23)),request=RoomNowRequest('longest',120,NOW))
        self.assertTrue(rows)
        self.assertTrue(all(r['end_hour']<=19 for r in rows))

    def test_peak_exception_off_keeps_daily_limit(self):
        now=NOW-timedelta(days=1)
        self.tracker.live_peak_minutes[str(now.date())]=0
        with patch.object(engine,'FREE_HORIZON_OVERRIDES_PEAK',False):
            rows=self.choose(grid(('Top',14,18)),now=now,request=RoomNowRequest('longest',120,now))
        self.assertEqual(rows[0]['start_hour'],16)

    def test_request_expiry_midnight_and_invalid_choices(self):
        for at in (NOW-timedelta(seconds=301),NOW+timedelta(seconds=1)):
            with self.assertRaises(SettingsError):RoomNowRequest('preferred',60,at).check_current(NOW)
        for mode,minutes in [('other',60),('preferred',True),('longest',180),('preferred',32)]:
            with self.assertRaises(ValueError):RoomNowRequest(mode,minutes,NOW)

    def test_cli_isolated_from_all_other_mutations(self):
        with patch('room_now.local_now',return_value=NOW):
            parser=engine.build_argument_parser()
            flags=cli_flags({'mode':'preferred','minutes':60},NOW.isoformat(),'result.json')
            engine._validate_cli_args(parser,parser.parse_args(flags))
            for extra in (['--upgrades-only'],['--scheduled'],['--only-date',str(NOW.date())],['--plan-only']):
                with self.assertRaises(SystemExit):engine._validate_cli_args(parser,parser.parse_args(flags+extra))

    def test_receipt_and_agenda_identity_both_required(self):
        saved={'receipt_id':'id','room':'Top','date':str(NOW.date()),'start':'14:15','end':'15:00','duration_minutes':45}
        receipt={**saved,'status':'verified','kind':'create','event_url':'https://rwcmd.asimut.net/arrangement?eventId=99'}
        event={'room':'Top','date':str(NOW.date()),'startTime':'14:15','endTime':'15:00','eventId':99,'isReservation':True}
        self.assertEqual(confirmed_booking(saved,receipt,[event])['event_id'],99)
        for r,events in [({**receipt,'status':'pending'},[event]),(receipt,[]),(receipt,[{**event,'eventId':98}]),(receipt,[event,event])]:
            with self.assertRaises(ValueError):confirmed_booking(saved,r,events)

    def test_stop_does_not_interrupt_read_only_post_save_proof(self):
        with tempfile.TemporaryDirectory() as folder:
            stop=Path(folder)/'stop'
            with owned_operation(stop,lambda _:None,lambda *_:None):
                stop.touch()
                with operation_verification():check_operation_stop()
                with self.assertRaises(OperationStopped):check_operation_stop()


class RoomNowRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)

    def test_disabled_day_exits_before_any_room_or_save_attempt(self):
        args=SimpleNamespace(room_now_mode='preferred',room_now_minutes=60,room_now_requested_at=NOW.isoformat(),room_now_output=str(self.root/'result.json'))
        fake=MagicMock();fake.list_pending_mutation_receipts.return_value=[];fake.is_date_disabled.return_value=True
        with patch('room_now.local_now',return_value=NOW):
            run(fake,None,args,{},None,None,today=NOW.date(),live_dates=[NOW.date()])
        fake.try_book_slot.assert_not_called();fake.refresh_practice_room_overview.assert_not_called()
        self.assertEqual(json.loads((self.root/'result.json').read_text())['state'],'blocked')

    def test_pc_duplicate_and_restart_uncertainty_do_not_replay(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        def runner(job,review):
            entered.set();release.wait(3);raise RuntimeError('Lost after Save')
        controller=DesktopRoomNow(self.root,runner)
        controller.start({'mode':'longest','minutes':120});self.assertTrue(entered.wait(2))
        with self.assertRaises(ValueError):controller.start({'mode':'longest','minutes':120})
        release.set();controller.thread.join(4)
        restarted=DesktopRoomNow(self.root,MagicMock())
        self.assertEqual(restarted.snapshot()['state'],'uncertain')
        with self.assertRaises(ValueError):restarted.start({'mode':'preferred','minutes':60})
        restarted.runner.assert_not_called()

    def runtime_fixture(self):
        fake=MagicMock()
        fake.MINIMUM_BLOCK_MINUTES=30
        fake.list_pending_mutation_receipts.return_value=[]
        fake.is_date_disabled.return_value=False
        fake.fragmentation_allows_new_booking.return_value=(True,'')
        fake.resolve_time_preferences.return_value=PREFS.copy()
        fake.remaining_run_daily_budget.return_value=2
        tracker=MagicMock();tracker.can_book.return_value=(True,'')
        args=SimpleNamespace(room_now_mode='longest',room_now_minutes=120,room_now_requested_at=NOW.isoformat(),room_now_output=str(self.root/'room-now.json'))
        candidate={'room':'Top','start_hour':14.25,'end_hour':16.25,'minutes':120,'rank':0}
        return fake,tracker,args,candidate

    def test_exact_saved_receipt_and_new_agenda_confirm_once(self):
        fake,tracker,args,candidate=self.runtime_fixture()
        saved={'receipt_id':'proof','room':'Top','date':str(NOW.date()),'start':'14:15','end':'16:15','duration_minutes':120}
        receipt={**saved,'status':'verified','kind':'create','event_url':'https://rwcmd.asimut.net/arrangement?eventId=99'}
        event={'room':'Top','date':str(NOW.date()),'startTime':'14:15','endTime':'16:15','isReservation':True,'eventId':99}
        fake.scan_agenda.return_value=(1,[event])
        fake.try_book_slot.side_effect=lambda *a,**k:(k['before_save'](saved),saved)[1]
        with patch('room_now.local_now',return_value=NOW),patch('room_now.candidates',return_value=[candidate]),patch('mutation_receipts.load_journal',return_value={'receipts':{'proof':receipt}}):
            run(fake,None,args,{},None,tracker,today=NOW.date(),live_dates=[NOW.date()])
        result=json.loads(Path(args.room_now_output).read_text())
        self.assertEqual(result['booking']['event_id'],99)
        self.assertEqual(result['state'],'completed')
        fake.try_book_slot.assert_called_once();fake.scan_agenda.assert_called_once()
        fake.process_pending_extensions.assert_not_called()

    def test_saved_but_missing_agenda_stops_without_second_save(self):
        fake,tracker,args,candidate=self.runtime_fixture()
        fake.try_book_slot.return_value={'receipt_id':'proof','room':'Top','date':str(NOW.date()),'start':'14:15','end':'16:15','duration_minutes':120}
        fake.scan_agenda.side_effect=RuntimeError('offline')
        with patch('room_now.local_now',return_value=NOW),patch('room_now.candidates',return_value=[candidate]):
            run(fake,None,args,{},None,tracker,today=NOW.date(),live_dates=[NOW.date()])
        self.assertEqual(json.loads(Path(args.room_now_output).read_text())['state'],'uncertain')
        fake.try_book_slot.assert_called_once()

    def test_dated_soft_window_cannot_be_restored_at_exact_save_check(self):
        from date_time_preferences import resolve_time_preferences, with_date_overrides
        for strict in (False, True):
            fake,tracker,args,candidate=self.runtime_fixture()
            prefs=with_date_overrides(PREFS.copy(), {'date_time_preferences': {str(NOW.date()):
                {'enabled':True,'strict_mode':strict,'start_time':'18:00','end_time':'20:00'}}})
            fake.resolve_time_preferences.return_value=resolve_time_preferences(prefs,NOW.date())
            fake.try_book_slot.return_value=False
            with patch('room_now.local_now',return_value=NOW),patch('room_now.candidates',return_value=[candidate]):
                run(fake,None,args,{},None,tracker,today=NOW.date(),live_dates=[NOW.date()])
            effective=resolve_time_preferences(fake.try_book_slot.call_args.kwargs['time_prefs'],NOW.date())
            self.assertEqual(effective['enabled'],strict)
            self.assertEqual(effective['start_hour'],18)
            self.assertEqual(effective['strict_mode'],strict)

    def test_pending_save_retains_identity_for_read_only_recovery(self):
        fake,tracker,args,candidate=self.runtime_fixture()
        attempted={'room':'Top','date':str(NOW.date()),'start':'14:15','end':'16:15','duration_minutes':120}
        fake.try_book_slot.side_effect=lambda *a,**k:(k['before_save'](attempted),False)[1]
        fake.list_pending_mutation_receipts.side_effect=[[],[{'id':'pending'}]]
        with patch('room_now.local_now',return_value=NOW),patch('room_now.candidates',return_value=[candidate]):
            run(fake,None,args,{},None,tracker,today=NOW.date(),live_dates=[NOW.date()])
        result=json.loads(Path(args.room_now_output).read_text())
        self.assertEqual(result['attempted']['start'],'14:15')
        self.assertEqual(result['state'],'uncertain')

    def test_expired_request_cannot_cross_final_save_boundary(self):
        fake,tracker,args,candidate=self.runtime_fixture()
        fake.try_book_slot.side_effect=lambda *a,**k:k['before_save']({'duration_minutes':120})
        # Entry, grid request, ranking, then final Save check.
        with patch('room_now.local_now',side_effect=[NOW,NOW,NOW,NOW+timedelta(seconds=301)]),patch('room_now.candidates',return_value=[candidate]):
            with self.assertRaises(SettingsError):run(fake,None,args,{},None,tracker,today=NOW.date(),live_dates=[NOW.date()])
        self.assertFalse(Path(args.room_now_output).exists())

    def test_refusals_are_bounded_and_pending_receipt_stops_fallback(self):
        fake,tracker,args,candidate=self.runtime_fixture()
        fake.try_book_slot.return_value=False
        fake.list_pending_mutation_receipts.side_effect=[[],[{'id':'pending'}]]
        with patch('room_now.local_now',return_value=NOW),patch('room_now.candidates',return_value=[candidate]):
            run(fake,None,args,{},None,tracker,today=NOW.date(),live_dates=[NOW.date()])
        fake.try_book_slot.assert_called_once()
        self.assertEqual(json.loads(Path(args.room_now_output).read_text())['state'],'uncertain')

    def test_recovery_binds_pre_save_identity_and_never_writes_remote(self):
        from mutation_receipts import record_pending_create, mark_verified
        from agenda_snapshot import publish_agenda_snapshot
        receipt_path=self.root/'data/mutation_receipts.json'
        observed=datetime.now(LONDON)
        day=observed.date().isoformat()
        attempt={'room':'Example','date':day,'start':'20:00','end':'21:00','duration_minutes':60,'requested_at':observed.isoformat()}
        atomic_write_json(self.root/'room-now.json',{'state':'uncertain','attempted':attempt,'requested_minutes':120})
        receipt=record_pending_create(room='Example',booking_date=day,start='20:00',end='21:00',path=receipt_path)
        mark_verified(receipt['id'],event_url='https://rwcmd.asimut.net/arrangement?eventId=99',path=receipt_path)
        event={'room':'Example','date':day,'startTime':'20:00','endTime':'21:00','title':'Reservation','isReservation':True,'eventId':99}
        publish_agenda_snapshot([event],[observed.date()],observed_at=datetime.now(LONDON),path=self.root/'data/agenda_snapshot.json')
        result=review_result(self.root,self.root)
        self.assertEqual(result['state'],'completed');self.assertEqual(result['booking']['event_id'],99)

    def test_no_save_record_recovery_allows_fresh_request(self):
        self.assertEqual(review_result(self.root,self.root)['state'],'stopped')


if __name__=='__main__':unittest.main()
