"""Gap filling and mutation isolation; entirely synthetic, no ASIMUT access."""
from contextlib import ExitStack
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import book_week as engine
from app_settings import SettingsError
from fill_range import FillRequest, candidates, uncovered, scoped_tracker, run, cli_flags, validate_choices, choose_candidate
from room_now import LONDON
from phone_system import validate_action

NOW=datetime(2030,9,30,8,0,tzinfo=LONDON)
CHOICES={'date':str(NOW.date()),'start_time':'11:00','end_time':'13:00'}


def event(start='12:00',end='12:30',room='Top',identity=9,**extra):
    return {'date':str(NOW.date()),'startTime':start,'endTime':end,'room':room,
            'eventId':identity,'isReservation':True,**extra}


class FillTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name,value in [('PRIORITY_ROOMS',['Top','Other']),('MIN_BOOKING_MINUTES',30),
                           ('MINIMUM_BLOCK_MINUTES',90),('MAX_BOOKING_HOURS',2),
                           ('SAME_ROOM_GAP_MINUTES',60),('FREE_HORIZON_MINUTES',300),
                           ('FREE_HORIZON_OVERRIDES_PEAK',True)]:
            self.stack.enter_context(patch.object(engine,name,value))
        self.stack.enter_context(patch.object(engine,'room_horizon_minutes',return_value=10080))
        self.stack.enter_context(patch('fill_range.local_now',return_value=NOW))
        self.request=FillRequest.create(CHOICES,NOW.isoformat())
        self.tracker=engine.BookingTracker()

    def test_clipped_union_avoids_double_counting_or_adjacent_boundary(self):
        values=[event('10:00','11:30'),event('11:00','12:00'),event('12:00','12:30'),event('13:00','14:00')]
        self.assertEqual(uncovered(self.request,values),[(750,780)])
        self.assertEqual(uncovered(self.request,[event(isReservation=False)]),[(660,780)])

    def test_one_off_blackout_override_keeps_real_events_and_other_times(self):
        settings={'rebooking_blackouts':[{'date':str(NOW.date()),'start_time':'10:00','end_time':'14:00'}]}
        self.tracker.agenda_events=[event(),event('11:15','11:45',isReservation=False)]
        before=json.dumps(settings)
        tracker=scoped_tracker(engine,self.tracker,self.request,settings)
        self.assertFalse(tracker.overlaps_conflict(NOW.date(),11,11.25))
        self.assertTrue(tracker.overlaps_conflict(NOW.date(),11.25,11.5))
        self.assertTrue(tracker.overlaps_conflict(NOW.date(),12,12.5))
        self.assertTrue(tracker.overlaps_conflict(NOW.date(),10.5,11))
        self.assertTrue(tracker.overlaps_conflict(NOW.date(),13,13.5))
        self.assertEqual(json.dumps(settings),before)

    def test_candidates_only_fill_gaps_in_eligible_rooms_with_site_minimum(self):
        self.tracker.agenda_events=[event()]
        tracker=scoped_tracker(engine,self.tracker,self.request,{})
        rows=[{'room':r,'slots':[{'startHour':10,'endHour':14}]} for r in ('Top','Other','Excluded')]
        options,_=candidates(engine,rows,tracker,self.request,NOW)
        self.assertTrue(options)
        self.assertEqual(options[0]['room'],'Other')
        self.assertEqual(options[0]['minutes'],60)
        self.assertTrue(all(11<=c['start_hour']<c['end_hour']<=13 for c in options))
        self.assertTrue(all(c['end_hour']<=12 or c['start_hour']>=12.5 for c in options))
        self.assertTrue(any(c['minutes']==30 for c in options))
        self.assertTrue(all(c['room']!='Excluded' for c in options))

    def test_zero_credit_does_not_escape_complete_five_hour_window(self):
        self.tracker.live_quota_minutes=0
        now=NOW-timedelta(hours=1)
        request=FillRequest.create(CHOICES,now.isoformat())
        options,reasons=candidates(engine,[{'room':'Top','slots':[{'startHour':11,'endHour':13}]}],self.tracker,request,now)
        self.assertTrue(options)
        self.assertTrue(all(c['end_hour']<=12 for c in options))
        self.assertTrue(any('quota' in r for r in reasons))

    def test_expiry_past_dates_time_validation_and_cli_isolation(self):
        with self.assertRaises(SettingsError):self.request.check_current(NOW+timedelta(seconds=901))
        for change in [{'start_time':'12:01'},{'end_time':'11:00'},{'date':'2030-9-30'},{'extra':1}]:
            with self.assertRaises(ValueError):validate_choices({**CHOICES,**change})
        validate_action('fill_range',CHOICES)
        parser=engine.build_argument_parser();flags=cli_flags(CHOICES,NOW.isoformat(),'result.json')
        engine._validate_cli_args(parser,parser.parse_args(flags))
        for extra in (['--scheduled'],['--only-date',str(NOW.date())],['--room-now-mode','preferred'],['--plan-only']):
            with self.assertRaises(SystemExit):engine._validate_cli_args(parser,parser.parse_args(flags+extra))

    def test_fill_cannot_acknowledge_unrelated_preference_dispatch(self):
        from preference_runs import complete_matching_run
        with patch('preference_runs.settle') as settle:
            complete_matching_run(Path('unused'),{'preference_run':{'id':'other','state':'pending'}},SimpleNamespace(fill_date=CHOICES['date']))
        settle.assert_not_called()

    def fixture(self, folder):
        fake=MagicMock();fake.list_pending_mutation_receipts.return_value=[]
        for name in ('SAME_ROOM_GAP_MINUTES','PEAK_START','PEAK_END','FREE_HORIZON_MINUTES','peak_quota_exempt','free_horizon_hours'):
            setattr(fake,name,getattr(engine,name))
        fake.BookingTracker.side_effect=engine.BookingTracker
        fake.load_rebooking_blackouts.return_value=()
        tracker=engine.BookingTracker();tracker.agenda_events=[event()]
        fake.settings_file=Path(folder)/'settings.json'
        fake.settings_file.write_text('{}')
        args=SimpleNamespace(fill_date=CHOICES['date'],fill_start='11:00',fill_end='13:00',
                             fill_requested_at=NOW.isoformat(),fill_output=str(Path(folder)/'fill-range.json'))
        return fake,tracker,args

    def test_already_covered_no_scan_or_save(self):
        with tempfile.TemporaryDirectory() as folder:
            fake,tracker,args=self.fixture(folder);tracker.agenda_events=[event('11:00','13:00')]
            run(fake,None,args,{},tracker,today=NOW.date(),live_dates=[NOW.date()])
            fake.try_book_slot.assert_not_called();fake.refresh_practice_room_overview.assert_not_called()
            self.assertEqual(json.loads(Path(args.fill_output).read_text())['covered_minutes'],120)

    def test_short_gaps_explain_no_new_booking_without_blaming_quota(self):
        with tempfile.TemporaryDirectory() as folder:
            fake,tracker,args=self.fixture(folder)
            args.fill_start='12:00';args.fill_end='14:00'
            for name in ('PRIORITY_ROOMS','MIN_BOOKING_MINUTES','MAX_BOOKING_HOURS','room_horizon_minutes'):
                setattr(fake,name,getattr(engine,name))
            fake.get_available_slots.return_value=[
                {'room':'Top','slots':[{'startHour':12.75,'endHour':13}]},
                {'room':'Other','slots':[{'startHour':13,'endHour':13.25}]},
                {'room':'Excluded','slots':[{'startHour':12.5,'endHour':14}]}]
            run(fake,None,args,{},tracker,today=NOW.date(),live_dates=[NOW.date()])
            result=json.loads(Path(args.fill_output).read_text(encoding='utf-8'))
            self.assertEqual(result['covered_minutes'],30)
            self.assertEqual(result['remaining'],[{'start':'12:30','end':'14:00','minutes':90}])
            self.assertIn('No new bookings',result['message'])
            reasons=' '.join(result['reasons'])
            self.assertIn('30 minutes',reasons)
            self.assertIn('Top 12:45–13:00 (15 min)',reasons)
            self.assertNotIn('Excluded',reasons)
            self.assertNotIn('quota',reasons.lower())
            fake.try_book_slot.assert_not_called()

    def test_exact_identity_and_verified_agenda_required_before_next_save(self):
        with tempfile.TemporaryDirectory() as folder:
            fake,tracker,args=self.fixture(folder)
            candidate={'room':'Other','start_hour':11.,'end_hour':12.,'minutes':60,'rank':1}
            saved={'room':'Other','date':str(NOW.date()),'start':'11:00','end':'12:00','duration_minutes':60,'receipt_id':'proof'}
            receipt={**saved,'kind':'create','status':'verified','event_url':'https://rwcmd.asimut.net/arrangement?eventId=99'}
            def save(*a,**kw):
                kw['before_save']({k:v for k,v in saved.items() if k!='receipt_id'})
                return saved
            fake.try_book_slot.side_effect=save
            fake.scan_agenda.return_value=(1,[event()])  # New ID absent: no second attempt.
            fake.BookingTracker.side_effect=None;fake.BookingTracker.return_value=tracker
            tracker.can_book=MagicMock(return_value=(True,''))
            with patch('fill_range.scoped_tracker',return_value=tracker),patch('fill_range.candidates',return_value=([candidate],[])),patch('mutation_receipts.load_journal',return_value={'receipts':{'proof':receipt}}):
                run(fake,None,args,{},tracker,today=NOW.date(),live_dates=[NOW.date()])
            self.assertEqual(fake.try_book_slot.call_count,1)
            result=json.loads(Path(args.fill_output).read_text());self.assertEqual(result['state'],'uncertain')
            self.assertEqual(result['saved']['receipt_id'],'proof')

    def test_changed_final_interval_stops_without_save_authority(self):
        with tempfile.TemporaryDirectory() as folder:
            fake,tracker,args=self.fixture(folder)
            candidate={'room':'Other','start_hour':11.,'end_hour':12.,'minutes':60,'rank':1}
            def save(*a,**kw):
                kw['before_save']({'room':'Other','date':str(NOW.date()),'start':'11:00','end':'11:30','duration_minutes':30})
            fake.try_book_slot.side_effect=save
            with patch('fill_range.scoped_tracker',return_value=tracker),patch('fill_range.candidates',return_value=([candidate],[])):
                run(fake,None,args,{},tracker,today=NOW.date(),live_dates=[NOW.date()])
            result=json.loads(Path(args.fill_output).read_text())
            self.assertEqual(result['state'],'blocked');self.assertNotIn('attempted',result)

    def test_fill_plans_across_room_gap_instead_of_greedy_rank(self):
        # Top is the sole room for the second gap; using it first loses 30 min.
        self.tracker.agenda_events=[event('11:30','12:00',room='Third')]
        tracker=scoped_tracker(engine,self.tracker,self.request,{})
        rows=[{'room':'Top','slots':[{'startHour':11,'endHour':11.5},{'startHour':12,'endHour':13}]},
              {'room':'Other','slots':[{'startHour':11,'endHour':11.5}]}]
        options,_=candidates(engine,rows,tracker,self.request,NOW)
        chosen=choose_candidate(engine,options,tracker,self.request,NOW)
        self.assertEqual((chosen['room'],chosen['start_hour'],chosen['minutes']),('Other',11,30))

    def test_multi_gap_success_counts_existing_and_pins_only_new_reservations(self):
        with tempfile.TemporaryDirectory() as folder:
            fake,tracker,args=self.fixture(folder)
            tracker.can_book=MagicMock(return_value=(True,''))
            fake.BookingTracker.side_effect=None;fake.BookingTracker.return_value=tracker
            choices=[{'room':'Other','start_hour':11,'end_hour':12,'minutes':60,'rank':1},
                     {'room':'Top','start_hour':12.5,'end_hour':13,'minutes':30,'rank':0}]
            receipts={};calls=[]
            def save(*a,**kw):
                c=choices[len(calls)];number=100+len(calls)
                b={'room':c['room'],'date':str(NOW.date()),'start':'11:00' if number==100 else '12:30',
                   'end':'12:00' if number==100 else '13:00','duration_minutes':c['minutes']}
                kw['before_save'](b)
                saved={**b,'receipt_id':str(number)}
                receipts[str(number)]={**saved,'kind':'create','status':'verified','event_url':f'https://rwcmd.asimut.net/arrangement?eventId={number}'}
                calls.append(saved);return saved
            fake.try_book_slot.side_effect=save
            def scan(*a,**kw):
                tracker.agenda_events=[event()]+[event(b['start'],b['end'],b['room'],int(b['receipt_id'])) for b in calls]
                # The real scan returns reservation summaries, without the
                # isReservation discriminator held in the complete tracker.
                return len(tracker.agenda_events),[{k:v for k,v in e.items() if k!='isReservation'} for e in tracker.agenda_events]
            fake.scan_agenda.side_effect=scan
            with patch('fill_range.scoped_tracker',return_value=tracker),patch('fill_range.candidates',side_effect=lambda *a:([choices[len(calls)]],[])),patch('mutation_receipts.load_journal',return_value={'receipts':receipts}):
                run(fake,None,args,{},tracker,today=NOW.date(),live_dates=[NOW.date()])
            result=json.loads(Path(args.fill_output).read_text())
            self.assertEqual((result['state'],result['covered_minutes'],len(result['bookings'])),('completed',120,2))
            self.assertEqual(set(json.loads(fake.settings_file.read_text())['manual_booking_overrides']),{'100','101'})

    def test_tempo_window_override_keeps_busy_and_expiry_guards(self):
        from tempo_coordination import Snapshot, manual_practice_window
        doc={'enabled':True,'dates':[str(NOW.date())],'windows':[],
             'busy':[{'date':str(NOW.date()),'start':'11:30','end':'12:00'}],'protected_event_ids':[9]}
        snap=Snapshot(doc,Path('unused'))
        self.assertFalse(snap.permits(NOW.date(),11,11.5,'Other'))
        with manual_practice_window(NOW.date(),11,13):
            self.assertTrue(snap.permits(NOW.date(),11,11.5,'Other'))
            self.assertFalse(snap.permits(NOW.date(),11.5,12,'Other'))
            self.assertFalse(snap.permits(NOW.date(),13,14,'Other'))
            self.assertFalse(Snapshot(doc,Path('unused'),'expired').permits(NOW.date(),11,11.5,'Other'))
        self.assertFalse(snap.permits(NOW.date(),11,11.5,'Other'))

    def test_recovery_uses_exact_cli_output_and_full_agenda(self):
        from fill_range import review_result
        from mutation_receipts import record_pending_create, mark_verified
        from agenda_snapshot import publish_agenda_snapshot
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); observed=datetime.now(LONDON); day=observed.date().isoformat()
            attempt={'room':'Other','date':day,'start':'13:00','end':'14:30',
                     'duration_minutes':90,'requested_at':observed.isoformat()}
            output=root/'custom-result.json'
            output.write_text(json.dumps({'state':'uncertain','attempted':attempt,
                'range':{'date':day,'start_time':'13:00','end_time':'14:30'}}))
            receipt_path=root/'data/mutation_receipts.json'
            receipt=record_pending_create(room='Other',booking_date=day,start='13:00',end='14:30',path=receipt_path)
            mark_verified(receipt['id'],event_url='https://rwcmd.asimut.net/arrangement?eventId=99',path=receipt_path)
            proof={**event('13:00','14:30','Other',99),'date':day,'title':'Reservation'}
            publish_agenda_snapshot([proof],[observed.date()],observed_at=datetime.now(LONDON),path=root/'data/agenda_snapshot.json')
            with patch('fill_range.local_now',return_value=observed):
                result=review_result(root,root,filename=output.name)
            self.assertEqual(result['state'],'completed')
            self.assertEqual(result['bookings'][0]['event_id'],99)
            self.assertEqual(set(json.loads((root/'data/settings.json').read_text())['manual_booking_overrides']),{'99'})


if __name__=='__main__':unittest.main()
