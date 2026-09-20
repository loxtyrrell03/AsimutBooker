"""Behavioural counterexamples using synthetic rooms, dates and live balances."""
import contextlib
import io
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import advance_runtime as runtime
import book_week as b
from booking_quotas import refresh_quota_balances, QuotaBalance
from tests import test_advance_runtime as fixture
from tests import test_new_booking_quotas as free_fixture
from booking_strategy import DailyPlanningPreferences
from room_upgrades import Reservation, find_room_upgrades, find_upgrade_opportunities, local_instant


class SmartAdvanceScenarios(unittest.TestCase):
    def setUp(self):
        self.world = fixture.AdvanceRuntimeTests('test_read_only_week_discovery_never_mutates')
        self.world.setUp()
        self.addCleanup(self.world.doCleanups)

    def allocations(self, grids):
        w = self.world
        return runtime.plan_from_grids(b, grids, w.settings, w.practice,
            w.tracker, w.args, now=w.now)[1]

    def test_one_hour_budget_chooses_saved_preferred_peak_hour(self):
        w = self.world
        grid = [{'room':'Weston','slots':[
            {'startHour':12,'endHour':13}, {'startHour':16,'endHour':17}]}]
        for minutes in (30,45,60):
            w.tracker.live_quota_minutes = minutes
            selected, = self.allocations({w.days[0]: grid})[0].sessions
            self.assertEqual((selected.start_hour, selected.end_hour), (12,12+minutes/60))

    def test_soft_preference_accepts_useful_premium_fallback_on_otherwise_empty_day(self):
        w = self.world
        w.tracker.live_quota_minutes = 60
        grid = [{'room':'Weston','slots':[{'startHour':18,'endHour':19}]}]
        selected = self.allocations({w.days[0]: grid})[0].sessions
        self.assertTrue(selected, 'A soft preference must not silently become a strict cutoff')
        self.assertTrue(all(item.start_hour >= 18 and item.end_hour <= 19 for item in selected))

    def test_strict_preference_never_uses_the_same_outside_slot(self):
        w = self.world
        grid = [{'room':'Weston','slots':[{'startHour':18,'endHour':19}]}]
        with patch.object(b, 'load_time_preferences', return_value={
                'enabled':True,'strict_mode':True,'start_hour':12,'end_hour':18}):
            self.assertFalse(self.allocations({w.days[0]: grid})[0].sessions)

    def test_soft_fallback_is_counted_after_booking_before_the_next_allocation(self):
        w = self.world
        w.tracker.live_quota_minutes = 60
        w.tracker.add_existing_event(w.days[0], 18, 19, is_reservation=True, room='Weston')
        w.tracker.quota_observed_hours = w.tracker.get_total_booking_hours()
        grid = [{'room':'Weston','slots':[{'startHour':18,'endHour':19}]}]
        allocations = self.allocations({w.days[0]:grid, w.days[1]:grid})
        # Under the saved soft-time weighting, 45 minutes starting at 18:00
        # offers slightly more useful time than stretching it all the way to 19:00.
        self.assertEqual([item.minutes for item in allocations], [0,45])

    def test_one_declined_date_cannot_starve_six_bookable_dates(self):
        w = self.world
        failed_day = w.days[0]
        w.attempt.side_effect = lambda page, slot, day, *a, **kw: (
            (False, None) if day == failed_day else w.book(page, slot, day, *a, **kw))
        actions, tracker, _ = w.run_week()
        self.assertEqual(actions, 6)
        self.assertEqual(len({event[1] for event in w.events}), 6)
        self.assertEqual(sum(c.args[2] == failed_day for c in w.attempt.call_args_list), 1)
        self.assertGreaterEqual(tracker.get_remaining_quota_hours(), .75)

    def test_unknown_save_or_quota_refusal_stops_without_trying_another_date(self):
        w = self.world
        for error in (b.BookingVerificationError('uncertain'), b.QuotaWait('quota')):
            w.attempt.reset_mock()
            w.attempt.side_effect = error
            with self.subTest(error=error), self.assertRaises(type(error)):
                w.run_week()
            w.attempt.assert_called_once()
        self.assertFalse(w.events)

    def test_secure_open_corus_then_upgrade_when_five_day_weston_opens(self):
        w=self.world
        day=w.days[5]
        w.horizons.side_effect=lambda room: 7200 if room=='Weston' else 10080
        w.tracker.live_quota_minutes=60
        grid=[{'room':room,'slots':[{'startHour':12,'endHour':13}]} for room in ('Weston','Corus')]
        selected,=self.allocations({day:grid})[0].sessions
        self.assertEqual(selected.room,'Corus')
        old=Reservation(42,day,'Corus',selected.start_minutes,selected.end_minutes)
        events=[{**old.as_booking(),'isReservation':True}]
        now=local_instant(w.now.date(),14*60)
        policy=SimpleNamespace(room_order=('Weston','Corus','Other'),minimum_block_minutes=30,
            site_maximum_booking_minutes=120,booking_horizon=now+timedelta(days=7),
            site_clock_offset_bounds=(0,0),horizon_minutes_for=w.horizons,
            booking_dates=lambda today: tuple(today+timedelta(days=i) for i in range(8)))
        kwargs=dict(events=events,policy=policy,available_data=grid[:1],
            planning=DailyPlanningPreferences(),time_preferences={
                'enabled':True,'strict_mode':False,'start_hour':12,'end_hour':18},peak_limit=60)
        self.assertFalse(find_room_upgrades(old,now=now,**kwargs))
        prospects=find_upgrade_opportunities(now=now,**kwargs)
        self.assertTrue(prospects)
        opened=local_instant(day,old.end)-timedelta(days=5)+timedelta(seconds=1)
        changes=find_room_upgrades(old,now=opened,**kwargs)
        self.assertTrue(changes)
        best=changes[0].replacement
        self.assertEqual((best.event_id,best.duration,best.room),(42,60,'Weston'))


class DailyPeakScenarios(unittest.TestCase):
    def test_completed_peak_hour_cannot_be_reused_even_if_site_returns_credit(self):
        day = date(2030, 1, 7)  # Monday
        now = datetime(2030, 1, 7, 13)
        with patch.multiple(b, MAX_PEAK_HOURS=1, MAX_ROLLING_QUOTA_HOURS=6,
                FREE_HORIZON_MINUTES=300, PEAK_START=9, PEAK_END=16), \
                patch.object(b,'datetime',wraps=datetime) as clock, \
                contextlib.redirect_stdout(io.StringIO()):
            clock.now.return_value = now
            for intervals in (((10,11),), ((9,9.5),(11,11.5))):
                tracker = b.BookingTracker()
                for start, end in intervals:
                    tracker.add_existing_event(day, start, end, is_reservation=True, room='Corus')
                with patch('booking_quotas.read_quota_balance', return_value=QuotaBalance(0,60)):
                    refresh_quota_balances(None, tracker, (day,))
                self.assertEqual(tracker.get_remaining_peak_minutes(day), 0)
                for start, duration in ((14,30),(15.75,30)):
                    self.assertFalse(tracker.can_book('Weston',day,start,duration,now=now)[0])
                self.assertTrue(tracker.can_book('Weston',day,16,120,now=now)[0])
                tracker.live_quota_minutes = 360  # Completed practice released advance credit.
                self.assertFalse(tracker.can_book('Weston',day,14,60,now=now)[0])

    def test_shorter_peak_sessions_share_one_hour_across_rooms(self):
        day = date(2030, 1, 7)
        with patch.multiple(b, MAX_PEAK_HOURS=1, MAX_ROLLING_QUOTA_HOURS=6), \
                contextlib.redirect_stdout(io.StringIO()):
            tracker = b.BookingTracker()
            tracker.add_existing_event(day,12,12.5,is_reservation=True,room='Corus')
            self.assertTrue(tracker.can_book('Weston',day,14,30)[0])
            tracker.add_booking('Weston',day,14,14.5)
            self.assertFalse(tracker.can_book('Other',day,15,30)[0])

    def test_free_worker_selects_only_offpeak_after_completed_peak_hour(self):
        w=free_fixture.ShortNoticeIntegrationTests('test_full_quota_books_once_then_stops_at_daily_target')
        w.setUp()
        self.addCleanup(w.doCleanups)
        day=w.now.date()
        w.plan=b.PracticePlan(enabled=True,default_hours=3)
        w.tracker.add_existing_event(day,12,13,is_reservation=True,room='A')
        w.events.append((day,12,13))
        w.tracker.live_peak_minutes[str(day)]=60
        w.tracker.peak_observed_minutes[str(day)]=60
        w.grid.return_value=[{'room':'A','slots':[{'startHour':14,'endHour':18}]}]
        actions,tracker=w.run_pass()
        self.assertEqual(actions,1)
        self.assertEqual(w.events[1:],[(day,16,18)])
        self.assertEqual(tracker.get_peak_used_for_day(day),60)

    def test_room_upgrade_cannot_move_offpeak_time_into_already_full_peak(self):
        day=date(2030,1,7)
        now=local_instant(day,13*60)
        old=Reservation(42,day,'Corus',16*60,17*60)
        peak_booking=Reservation(41,day,'Corus',11*60,12*60)
        policy=SimpleNamespace(room_order=('Weston','Corus'),minimum_block_minutes=30,
            site_maximum_booking_minutes=120,booking_horizon=now+timedelta(days=7),
            site_clock_offset_bounds=(0,0),horizon_minutes_for=lambda room:10080,
            booking_dates=lambda today: tuple(today+timedelta(days=i) for i in range(8)))
        changes=find_room_upgrades(old,events=[{**r.as_booking(),'isReservation':True} for r in (old,peak_booking)],
            policy=policy,now=now,available_data=[{'room':'Weston','slots':[{'startHour':14,'endHour':15}]}],
            planning=DailyPlanningPreferences(),time_preferences={'enabled':True,'start_hour':12,'end_hour':18},peak_limit=60)
        self.assertFalse(changes)


if __name__ == '__main__':
    unittest.main()
