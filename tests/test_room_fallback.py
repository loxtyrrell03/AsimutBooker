import contextlib
import io
import unittest
from datetime import date, datetime
from unittest import mock

import book_week as b


class RoomFallbackTests(unittest.TestCase):
    rooms = ['Weston Gallery', 'Corus Recital Room', 'B0.11', 'B0.13']
    day = date(2026, 9, 14)
    now = datetime(2026, 9, 8, 12, 30)

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(mock.patch.multiple(
            b, PRIORITY_ROOMS=self.rooms,
            ROOM_HORIZON_MINUTES={room:7*1440 for room in self.rooms},
            MINIMUM_BLOCK_MINUTES=30, MAX_BOOKING_HOURS=2,
            ALLOW_FRAGMENTED_SESSIONS=True, SAME_ROOM_GAP_MINUTES=60,
        ))
        now = self.now
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now if tz is None else now.replace(tzinfo=tz)
        self.stack.enter_context(mock.patch.object(b,'datetime',Clock))
        self.tracker = b.BookingTracker()
        self.prefs = {'enabled':True, 'start_hour':12, 'end_hour':18, 'strict_mode':True}
        self.planning = b.DailyPlanningPreferences()
        self.slot = {'room':self.rooms[0],'start_hour':12,'end_hour':14,'duration':2}

    def grid(self, *rooms):
        return [{'room':room,'slots':[{'startHour':8,'endHour':18}]} for room in rooms]

    def backups(self, grid, **kwargs):
        values=dict(now=self.now, excluded_rooms={self.rooms[0]},remaining_daily_hours=3)
        values.update(kwargs)
        return b.same_time_room_backups(grid,self.slot,self.day,self.tracker,self.prefs,self.planning,**values)

    def test_backups_preserve_exact_time_and_follow_room_order(self):
        result=self.backups(self.grid(*reversed(self.rooms)))
        self.assertEqual([item.room for item in result],self.rooms[1:])
        self.assertEqual({(item.start_minutes,item.end_minutes) for item in result},{(720,840)})

    def test_fresh_occupancy_horizon_exclusions_conflicts_and_budgets_apply(self):
        occupied=self.grid('Corus Recital Room')
        occupied[0]['slots']=[{'startHour':12.5,'endHour':18}]
        self.assertEqual(self.backups(occupied),[])
        with mock.patch.dict(b.ROOM_HORIZON_MINUTES,{'Corus Recital Room':3*1440}):
            self.assertEqual(self.backups(self.grid('Corus Recital Room')),[])
        self.assertEqual(self.backups(self.grid('Unapproved room')),[])
        self.assertEqual(self.backups(self.grid('B0.11'), remaining_daily_hours=1),[])
        self.assertEqual(self.backups(self.grid('B0.11'), reserved_peak_minutes=90),[])
        self.assertEqual(self.backups(self.grid('B0.11'), reserved_weekly_minutes=28*60),[])
        self.prefs['start_hour']=13
        self.assertEqual(self.backups(self.grid('B0.11')),[])
        self.prefs['start_hour']=12
        self.tracker.add_conflict(self.day,13,14)
        self.assertEqual(self.backups(self.grid('B0.11')),[])

    def invoke(self, **kwargs):
        return b.attempt_booking_with_room_fallback(
            object(),self.slot,self.day,self.tracker,6,time_prefs=self.prefs,
            daily_planning=self.planning,remaining_daily_hours=3,max_action_minutes=30,**kwargs)

    def environment(self, outcomes, scans):
        attempt=self.stack.enter_context(mock.patch.object(b,'try_book_slot',side_effect=outcomes))
        scan=self.stack.enter_context(mock.patch.object(b,'get_available_slots',side_effect=scans))
        refresh=self.stack.enter_context(mock.patch.object(b,'go_back'))
        self.stack.enter_context(mock.patch.object(b,'assert_calendar_date'))
        journal=self.stack.enter_context(mock.patch.object(b,'list_pending_mutation_receipts',return_value=[]))
        return attempt,scan,refresh,journal

    def test_failed_primary_and_backup_refresh_before_next_best_room(self):
        receipt={'event_id':123,'room':'B0.13'}
        attempt,scan,refresh,journal=self.environment(
            [False,False,receipt], [self.grid(*self.rooms),self.grid('Weston Gallery','Corus Recital Room','B0.13')])
        result,actual=self.invoke()
        self.assertEqual(result,receipt)
        self.assertEqual(actual['room'],'B0.13')
        self.assertEqual([call.args[1]['room'] for call in attempt.call_args_list],['Weston Gallery','Corus Recital Room','B0.13'])
        self.assertEqual(scan.call_count,2)
        self.assertEqual(refresh.call_count,2)
        for call in attempt.call_args_list:
            self.assertEqual((call.args[1]['start_hour'],call.args[1]['end_hour']),(12,14))
            self.assertEqual(call.kwargs['max_action_minutes'],30)

    def test_uncertain_save_or_unreadable_journal_never_falls_back(self):
        for state in ([{'id':'pending'}],OSError('journal unreadable')):
            with self.subTest(state=state), contextlib.ExitStack() as stack:
                attempt=stack.enter_context(mock.patch.object(b,'try_book_slot',return_value=False))
                journal=stack.enter_context(mock.patch.object(b,'list_pending_mutation_receipts'))
                journal.side_effect=state if isinstance(state,Exception) else None
                journal.return_value=state
                refresh=stack.enter_context(mock.patch.object(b,'go_back'))
                with self.assertRaises(b.BookingVerificationError): self.invoke()
                self.assertEqual(attempt.call_count,1)
                refresh.assert_not_called()

    def test_verification_error_propagates_and_room_scope_never_expands(self):
        with mock.patch.object(b,'try_book_slot',side_effect=b.BookingVerificationError('uncertain')),mock.patch.object(b,'go_back') as refresh:
            with self.assertRaises(b.BookingVerificationError): self.invoke()
            refresh.assert_not_called()
        attempt,scan,refresh,_=self.environment([False],[])
        self.assertFalse(self.invoke(only_room='Weston Gallery')[0])
        scan.assert_not_called()

    def test_all_rooms_are_attempted_at_most_once(self):
        attempt,scan,_,_=self.environment([False]*4,[self.grid(*self.rooms)]*3)
        self.assertFalse(self.invoke()[0])
        self.assertEqual([call.args[1]['room'] for call in attempt.call_args_list],self.rooms)
        self.assertEqual(scan.call_count,3)

    def test_horizon_fallback_keeps_boundary_and_reports_actual_room(self):
        self.day=date(2026,9,15)
        self.slot.update(bookable_from=self.now,booking_minutes=30,duration=120,
                         target_date=self.day,days_ahead=7,horizon_minutes=7*1440)
        _,scan,_,_=self.environment([], [self.grid(*self.rooms)])
        with mock.patch.object(b,'try_horizon_snipe',side_effect=[False,True]) as attempt:
            result,actual=self.invoke(horizon=True)
        self.assertTrue(result)
        self.assertEqual(actual['room'],'Corus Recital Room')
        self.assertEqual(actual['bookable_from'],self.now)
        self.assertEqual(actual['booking_minutes'],30)
        self.assertEqual(attempt.call_count,2)

    def test_horizon_fallback_does_not_spend_pending_extension_budget(self):
        self.day=date(2026,9,15)
        self.slot.update(bookable_from=self.now,booking_minutes=30,duration=120,
                         target_date=self.day,days_ahead=7,horizon_minutes=7*1440)
        self.environment([], [self.grid(*self.rooms)])
        with mock.patch.object(b,'try_horizon_snipe',return_value=False) as attempt:
            result,_=self.invoke(horizon=True,planning_context={
                'extension_target_by_date':{self.day.isoformat():120}})
        self.assertFalse(result)
        self.assertEqual(attempt.call_count,1)

    def test_delayed_category_menu_retries_only_unsaved_form_entry(self):
        page=mock.Mock(url=b.ASIMUT_OVERVIEW_URL)
        option=mock.Mock()
        option.is_enabled.return_value=True
        with mock.patch.object(b,'wait_for_student_booking_option',return_value=option),mock.patch.object(
            b,'wait_for_new_booking_form',side_effect=[False,True]
        ),mock.patch.object(b,'record_pending_create') as receipt:
            self.assertTrue(b.enter_new_booking_form(page))
        self.assertEqual(option.click.call_count,2)
        page.mouse.click.assert_not_called()
        receipt.assert_not_called()

    def test_category_retry_stops_after_navigation_and_rejects_saved_event(self):
        page=mock.Mock(url='https://rwcmd.asimut.net/event?eventId=0')
        with mock.patch.object(b,'wait_for_student_booking_option') as menu,mock.patch.object(
            b,'wait_for_new_booking_form',return_value=True
        ):
            self.assertTrue(b.enter_new_booking_form(page))
            menu.assert_not_called()
            page.url='https://rwcmd.asimut.net/arrangement?eventId=123'
            self.assertFalse(b.enter_new_booking_form(page))
            menu.assert_not_called()


if __name__ == '__main__':
    unittest.main()
