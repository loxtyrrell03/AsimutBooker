"""Remote owner edits must survive planning, queued work and future scans."""
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from app_settings import load_settings, save_settings, SettingsError
from booking_blackouts import subtract_rebooking_blackout
from booking_preferences_guard import booking_preference_run, booking_save_boundary, BookingPreferencesChanged
from manual_cancellations import OBSERVED_KEY, TIMES_KEY
from manual_booking_overrides import KEY, manual_booking_ids, assert_automatic_change_allowed
from tests import test_manual_cancellations as cancellation_tests


class ManualEditTests(unittest.TestCase):
    setUp = cancellation_tests.ManualCancellationTests.setUp
    scan = cancellation_tests.ManualCancellationTests.scan

    def changed(self, **kw):
        self.scan([self.event])
        new = dict(self.event, **kw)
        windows = self.scan([new])
        self.assertEqual(manual_booking_ids(load_settings(self.path)), {123})
        return new, [(w.start_time, w.end_time) for w in windows]

    def test_later_start_same_end_protects_only_removed_prefix(self):
        new, windows = self.changed(startTime='14:30')
        self.assertEqual(windows, [('14:00', '14:30')])
        self.assertEqual(load_settings(self.path)[KEY]['123'],
                         {k: new[k] for k in ('date', 'room', 'startTime', 'endTime')})

    def test_earlier_end_protects_only_removed_tail(self):
        _, windows = self.changed(endTime='14:30')
        self.assertEqual(windows, [('14:30', '15:00')])

    def test_trim_both_ends_protects_both_gaps(self):
        _, windows = self.changed(startTime='14:15', endTime='14:45')
        self.assertEqual(windows, [('14:00', '14:15'), ('14:45', '15:00')])

    def test_later_shift_protects_prefix_without_blocking_remaining_session(self):
        _, windows = self.changed(startTime='14:30', endTime='15:30')
        self.assertEqual(windows, [('14:00', '14:30')])

    def test_earlier_shift_protects_tail(self):
        _, windows = self.changed(startTime='13:30', endTime='14:30')
        self.assertEqual(windows, [('14:30', '15:00')])

    def test_disjoint_move_and_date_move_protect_entire_original(self):
        for kw in (dict(startTime='16:00', endTime='17:00'), dict(date='2026-10-02')):
            save_settings({}, self.path)
            _, windows = self.changed(**kw)
            self.assertEqual(windows, [('14:00', '15:00')])

    def test_room_only_edit_pins_choice_without_excluding_time(self):
        _, windows = self.changed(room='Room B')
        self.assertEqual(windows, [])

    def test_manual_extension_pins_choice_without_inventing_gap(self):
        _, windows = self.changed(endTime='16:00')
        self.assertEqual(windows, [])

    def test_unchanged_scans_do_not_pin_or_invalidate_save(self):
        self.scan([self.event])
        before = load_settings(self.path)
        with booking_preference_run(self.path, before):
            self.now += timedelta(minutes=5)
            self.scan([self.event])
            with booking_save_boundary():
                pass
        self.assertNotIn(KEY, load_settings(self.path))

    def test_detected_edit_invalidates_already_prepared_save(self):
        self.scan([self.event])
        with booking_preference_run(self.path, load_settings(self.path)):
            self.scan([dict(self.event, startTime='14:30')])
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary():
                self.fail('stale Save became reachable')

    def test_queued_change_guard_only_blocks_edited_reservation(self):
        self.changed(startTime='14:30')
        with self.assertRaises(BookingPreferencesChanged):
            assert_automatic_change_allowed([123, 124], path=self.path)
        assert_automatic_change_allowed([124], path=self.path)

    def test_extension_goals_retired_without_removing_other_goals(self):
        save_settings({'extendable_bookings': [dict(eventId=123), dict(eventId=124)]}, self.path)
        self.changed(startTime='14:30')
        self.assertEqual(load_settings(self.path)['extendable_bookings'], [dict(eventId=124)])

    def test_stale_extension_goal_is_filtered_even_before_cleanup(self):
        import book_week as b
        entry=dict(eventId=123,event_url='https://rwcmd.asimut.net/arrangement?eventId=123',
            date='2099-10-01',room='B0.23',startTime='14:00',endTime='14:30',target_end='15:00',
            created_at='2026-01-01T10:00:00',horizon_days=5)
        data={'extendable_bookings':[entry]}
        self.assertEqual(b.load_extendable_bookings(data),[entry])
        data[KEY]={'123': {k:entry[k] for k in ('date','room','startTime','endTime')}}
        self.assertEqual(b.load_extendable_bookings(data),[])

    def test_explicit_reopen_is_not_reversed_by_later_scan(self):
        new, _ = self.changed(startTime='14:30')
        subtract_rebooking_blackout(self.day, '14:00', '14:30', path=self.path)
        self.assertEqual(self.scan([new]), ())
        self.assertEqual(manual_booking_ids(load_settings(self.path)), {123})

    def test_second_edit_retains_first_gap_and_cancellation_retires_pin(self):
        new, _ = self.changed(startTime='14:15')
        self.scan([dict(new, startTime='14:30')])
        windows = self.scan([])
        self.assertEqual([(w.start_time,w.end_time) for w in windows], [('14:00','15:00')])
        self.assertEqual(manual_booking_ids(load_settings(self.path)), set())

    def test_expired_pin_is_retired(self):
        self.changed(startTime='14:30')
        self.now = self.now.replace(hour=16)
        self.scan([])
        self.assertFalse(manual_booking_ids(load_settings(self.path)))

    def test_shortening_to_an_already_ended_time_still_protects_future_tail(self):
        self.scan([self.event])
        self.now = self.now.replace(hour=14, minute=45)
        windows = self.scan([dict(self.event, endTime='14:30')])
        self.assertEqual([(w.start_time,w.end_time) for w in windows], [('14:30','15:00')])
        self.assertFalse(manual_booking_ids(load_settings(self.path)))

    def receipt(self, kind, new, **extra):
        return dict(kind=kind, status='verified',
            verified_at=self.now.astimezone(timezone.utc).isoformat(),
            date=new['date'], room=new['room'], start=new['startTime'], end=new['endTime'],
            event_url='https://rwcmd.asimut.net/arrangement?eventId=123', **extra)

    def original(self):
        return dict(event_id=123, date=self.event['date'], room=self.event['room'],
                    start=self.event['startTime'], end=self.event['endTime'])

    def test_verified_booker_upgrade_and_extension_do_not_pin_or_blackout(self):
        for kind, new in [('upgrade',dict(self.event,room='Room B',startTime='16:00',endTime='17:00')),
                          ('extension',dict(self.event,endTime='16:00'))]:
            save_settings({}, self.path)
            self.scan([self.event])
            r=self.receipt(kind,new,**({'original': self.original()} if kind=='upgrade' else {}))
            with patch('manual_cancellations.load_journal',return_value={'receipts': {'r':r}}):
                self.assertEqual(self.scan([new]), ())
            self.assertNotIn(KEY,load_settings(self.path))

    def test_old_matching_receipt_does_not_explain_new_manual_edit(self):
        self.scan([self.event])
        new=dict(self.event,room='Room B',startTime='16:00',endTime='17:00')
        r=self.receipt('upgrade',new,original=self.original())
        r['verified_at']=(self.now-timedelta(days=1)).astimezone(timezone.utc).isoformat()
        with patch('manual_cancellations.load_journal',return_value={'receipts': {'r':r}}):
            self.assertEqual(len(self.scan([new])),1)
        self.assertEqual(manual_booking_ids(load_settings(self.path)), {123})

    def test_pending_internal_edit_retains_old_tuple_until_receipt_is_verified(self):
        self.scan([self.event])
        new=dict(self.event,room='Room B',startTime='16:00',endTime='17:00')
        r=self.receipt('upgrade',new,original=self.original())
        r['status']='pending'
        with patch('manual_cancellations.load_journal',return_value={'receipts': {'r':r}}):
            self.assertEqual(self.scan([new]),())
            self.assertEqual(load_settings(self.path)[OBSERVED_KEY]['123']['startTime'],'14:00')
            r['status']='verified'
            self.assertEqual(self.scan([new]),())
        self.assertNotIn(KEY,load_settings(self.path))

    def test_verified_consolidation_retires_donor_and_moves_anchor_without_pin(self):
        donor=dict(self.event,eventId=124,startTime='15:00',endTime='16:00')
        self.scan([self.event,donor])
        new=dict(self.event,room='Room B',startTime='16:00',endTime='18:00')
        r=self.receipt('consolidation',new,original=self.original(),
            originals=[self.original(),dict(self.original(),event_id=124,start='15:00',end='16:00')])
        with patch('manual_cancellations.load_journal',return_value={'receipts': {'r':r}}):
            self.assertEqual(self.scan([new]),())
        self.assertNotIn(KEY,load_settings(self.path))

    def test_verified_transfer_trim_does_not_pin_source(self):
        self.scan([self.event])
        new=dict(self.event,startTime='14:30')
        r=self.receipt('transfer',new,transfer=dict(originals=[self.original()],
            remaining=[dict(self.original(),start='14:30')], seed_before=None))
        with patch('manual_cancellations.load_journal',return_value={'receipts': {'r':r}}):
            self.assertEqual(self.scan([new]),())
        self.assertNotIn(KEY,load_settings(self.path))

    def test_transfer_compensation_new_id_does_not_blackout_restored_donor(self):
        self.scan([self.event])
        child = self.receipt('create',self.event,parent_id='p',transfer_role='restore:123')
        child['event_url']='https://rwcmd.asimut.net/arrangement?eventId=456'
        parent=dict(kind='transfer',id='p',status='resolved',transfer=dict(originals=[self.original()],seed_before=None))
        with patch('manual_cancellations.load_journal',return_value={'receipts': {'p':parent,'r':child}}):
            self.assertEqual(self.scan([dict(self.event,eventId=456)]),())
        self.assertNotIn(KEY,load_settings(self.path))

    def test_bad_observation_timestamp_stops_scan_without_consuming_evidence(self):
        self.scan([self.event])
        s=load_settings(self.path);s[TIMES_KEY]['123']='not a time';save_settings(s,self.path)
        with self.assertRaises(SettingsError):
            self.scan([dict(self.event,startTime='14:30')])
        self.assertEqual(load_settings(self.path),s)

    def test_partial_edit_installs_room_independent_tracker_conflict(self):
        import book_week as b
        from datetime import date
        from tests.test_config_and_agenda_regressions import _AgendaPage
        day=date(2099,10,1)
        old=dict(self.event,date=str(day),daysDiff=0,location='Room A',title='Reservation')
        new=dict(old,startTime='14:30')
        with patch.object(b,'LIVE_CATALOG_ROOM_NAMES',['Room A','Room B']):
            for event in (old,new):
                tracker=b.BookingTracker()
                b.scan_agenda(_AgendaPage(day,[event],[day]),tracker,day,ignored_events=[],
                    window_dates=[day],snapshot_path=self.path.with_name('agenda.json'))
        self.assertIn((14.0,14.5),tracker.conflict_ranges[str(day)])
        self.assertEqual(manual_booking_ids(load_settings(self.path)),{123})
