"""Changing synthetic weeks: production planners, independent in-memory service.

This exercises planning/extension/upgrade decisions, not browser receipts. The
real receipt, exact-Save and crash recovery suites cover that separate boundary.
No account, network, settings file or real reservation is used here.
"""
from contextlib import ExitStack, redirect_stdout
from dataclasses import replace
from datetime import date, datetime, timedelta
import io
import random
from types import SimpleNamespace
from unittest.mock import patch

import book_week as b
from advance_planner import AdvanceDay, allocate_advance_week
from booking_strategy import DailyPlanningPreferences
from daily_planner import select_day_plan
from room_upgrades import Reservation, find_room_upgrades, select_upgrade_portfolio, local_instant, clock_minutes
from session_preferences import tracker_sessions


START = date(2026, 9, 21)
ROOMS = ('Weston', 'Corus', 'Practice A', 'Practice B')
HORIZONS = dict(zip(ROOMS, (5*1440, 7*1440, 3*1440, 5*1440)))


class WeekSimulation:
    def __init__(self, seed=1, *, scarcity=.65, comfort=True, race_every=11, target=180):
        self.seed, self.target, self.race_every = seed, target, race_every
        self.now = datetime(2026, 9, 20, 12)
        self.days = tuple(START+timedelta(days=i) for i in range(7))
        self.planning = DailyPlanningPreferences(preferred_block_minutes=60 if comfort else 0,
            preferred_rest_minutes=30 if comfort else 0, prefer_fewer_room_changes=comfort)
        self.prefs = dict(enabled=True, strict_mode=True, start_hour=12, end_hour=20)
        self.events, self.intents, self.journal = {}, {}, []
        self.next_id, self.attempts = 1, 0
        self.metrics = dict(creates=0, extensions=0, upgrades=0, races=0, free_creates=0,
                            quota_refusals=0, checks=0, released_competitor_slots=0)
        rng = random.Random(seed)
        self.external = {}
        for day in self.days:
            for room in ROOMS:
                # Whole half-hours model competing students; later cancellations
                # reopen them. Some long gaps permit useful upgrades/extensions.
                self.external[day,room] = {minute for start in range(720,1200,30)
                    if rng.random() < scarcity for minute in (start,start+15)}
        self.classes = {day: ((840,900),) if i%3 == 0 else () for i,day in enumerate(self.days)}
        self.rng = rng

    def stamp(self, day, minute):
        return datetime.combine(day, datetime.min.time())+timedelta(minutes=minute)

    def credit(self, omit=None):
        used = sum(r.duration for r in self.events.values()
                   if r.event_id != omit and self.stamp(r.day,r.end) > self.now)
        return max(0,360-used)

    def agenda(self):
        return [{**r.as_booking(),'isReservation':True} for r in self.events.values()]

    def tracker(self):
        result = b.BookingTracker()
        for r in self.events.values():
            if r.day >= self.now.date():
                result.add_existing_event(r.day,r.start/60,r.end/60,is_reservation=True,room=r.room)
        for day, intervals in self.classes.items():
            if day >= self.now.date():
                for start,end in intervals:
                    result.add_existing_event(day,start/60,end/60,is_reservation=False)
        self.quota(None,result,())
        return result

    def quota(self, page, tracker, days):
        tracker.live_quota_minutes = self.credit()
        tracker.quota_observed_hours = tracker.get_total_booking_hours()
        for day in self.days:
            tracker.live_peak_minutes[str(day)] = 60
            tracker.peak_observed_minutes[str(day)] = tracker.get_peak_used_for_day(day)

    def grid(self, day):
        rows=[]
        for room in ROOMS:
            blocked=set(self.external[day,room])
            for r in self.events.values():
                if r.day==day and r.room==room:
                    blocked.update(range(r.start,r.end,15))
            gaps=[]
            start=None
            for minute in range(720,1215,15):
                if minute<1200 and minute not in blocked:
                    if start is None: start=minute
                elif start is not None:
                    gaps.append(dict(startHour=start/60,endHour=minute/60)); start=None
            rows.append(dict(room=room,slots=gaps))
        return rows

    def valid(self, candidate, *, old=None):
        others=[r for r in self.events.values() if old is None or r.event_id!=old.event_id]
        assert 30 <= candidate.duration <= 120
        assert candidate.start%15 == candidate.end%15 == 0
        if self.stamp(candidate.day,candidate.start) <= self.now:
            return False
        if self.stamp(candidate.day,candidate.end) > self.now+timedelta(minutes=HORIZONS[candidate.room]):
            return False
        if not 720 <= candidate.start < candidate.end <= 1200:
            return False
        if any(m in self.external[candidate.day,candidate.room] for m in range(candidate.start,candidate.end,15)):
            return False
        for r in others:
            if r.day != candidate.day: continue
            gap=60 if r.room==candidate.room else 0
            if candidate.start < r.end+gap and candidate.end > r.start-gap:
                return False
        if any(candidate.start < end and candidate.end > start for start,end in self.classes[candidate.day]):
            return False
        if candidate.day.weekday()<5:
            peak=lambda r:max(0,min(r.end,960)-max(r.start,540))
            if peak(candidate)+sum(peak(r) for r in others if r.day==candidate.day)>60:
                raise AssertionError('Production decision exceeded the whole-day peak cap')
        extra=candidate.duration-(old.duration if old else 0)
        free=self.now < self.stamp(candidate.day,candidate.start) and \
             self.stamp(candidate.day,candidate.end)<=self.now+timedelta(minutes=300)
        if extra>self.credit() and not free:
            self.metrics['quota_refusals']+=1
            return False
        if sum(r.duration for r in others if r.day==candidate.day)+candidate.duration>self.target:
            raise AssertionError('Production decision exceeded the daily target')
        return True

    def save(self, candidate, *, old=None, kind='creates'):
        self.attempts+=1
        # A competitor wins after observation but before Save. Never overwrite
        # already confirmed time or report this definite rejection as success.
        if old is None and self.race_every and self.attempts%self.race_every==0:
            self.external[candidate.day,candidate.room].add(candidate.start)
            self.metrics['races']+=1
            return False
        if not self.valid(candidate,old=old):
            return False
        if old is None and candidate.duration>self.credit():
            self.metrics['free_creates']+=1
        self.events[candidate.event_id]=candidate
        self.metrics[kind]+=1
        self.journal.append(dict(at=self.now.isoformat(),kind=kind,id=candidate.event_id,
            day=str(candidate.day),room=candidate.room,start=candidate.start,end=candidate.end))
        return True

    def create(self, item, *, free=False):
        end_limit=self.now+timedelta(minutes=min(HORIZONS[item.room],300) if free else HORIZONS[item.room])
        open_end=int((end_limit-self.stamp(item.target_date,0)).total_seconds()//900)*15
        end=min(item.end_minutes,open_end)
        if end-item.start_minutes<30: return False
        candidate=Reservation(self.next_id,item.target_date,item.room,item.start_minutes,end)
        if not self.save(candidate): return False
        self.next_id+=1
        if end<item.end_minutes:
            self.intents[candidate.event_id]=dict(**candidate.as_booking(),
                target_end=b.time_text(item.end_minutes),created_at=self.now.isoformat())
        return True

    def edit_end(self, page, booking, end, **kwargs):
        old=self.events[booking['eventId']]
        assert old.as_booking()['endTime']==booking['endTime']
        return self.save(replace(old,end=clock_minutes(end)),old=old,kind='extensions')

    def extend(self):
        for event_id, booking in tuple(self.intents.items()):
            old=self.events[event_id]
            if self.stamp(old.day,old.start)<=self.now:
                self.intents.pop(event_id,None); continue
            remaining=self.target-sum(r.duration for r in self.events.values() if r.day==old.day)
            b.try_extend_booking(None,booking,self.tracker(),remaining_daily_hours=remaining/60,
                time_prefs=self.prefs,agenda_reservations=self.agenda(),now=self.now)

    def advance(self):
        tracker=self.tracker()
        if self.credit()<30: return
        days=[]
        for day in self.days:
            if day<self.now.date(): continue
            confirmed=round(tracker.get_hours_for_day(day)*60)
            options=b.build_day_booking_opportunities(self.grid(day)[:2],day,tracker,self.prefs,
                self.planning,now=self.now,remaining_daily_hours=max(0,self.target-confirmed)/60)
            days.append(AdvanceDay(day,self.target,confirmed,
                sum(r.duration for r in self.events.values() if r.day==day and r.room in ROOMS[:2]),
                tuple(options),tracker.get_remaining_peak_minutes(day),
                existing_sessions=tracker_sessions(tracker,day)))
        allocations=allocate_advance_week(days,self.planning,now=self.now,budget_minutes=self.credit(),
            minimum_block_minutes=30,allow_fragmented_sessions=True,same_room_gap_minutes=60)
        for allocation in allocations:
            for item in allocation.sessions:
                if item.unlock_at<=self.now:
                    self.create(item)

    def short_notice(self):
        day=self.now.date()
        if day not in self.days: return
        for _ in range(8):
            tracker=self.tracker()
            remaining=self.target-round(tracker.get_hours_for_day(day)*60)
            if remaining<30: break
            options=b.build_day_booking_opportunities(self.grid(day),day,tracker,self.prefs,self.planning,
                now=self.now,remaining_daily_hours=remaining/60,free_horizon_only=True,
                include_free_horizon_intent=True)
            selected=select_day_plan(options,self.planning,now=self.now,target_minutes=remaining,
                allow_fragmented_sessions=True,remaining_peak_minutes=tracker.get_remaining_peak_minutes(day),
                same_room_gap_minutes=60,existing_sessions=tracker_sessions(tracker,day))
            current=[item for item in selected if item.unlock_at<=self.now]
            if not current or not self.create(current[0],free=True): break

    def upgrade(self):
        policy=SimpleNamespace(room_order=ROOMS,minimum_block_minutes=30,site_maximum_booking_minutes=120,
            booking_horizon=local_instant(self.now.date(),self.now.hour*60+self.now.minute)+timedelta(days=7),
            site_clock_offset_bounds=(0,0),horizon_minutes_for=lambda room:HORIZONS[room])
        policy.booking_dates=lambda today: tuple(today+timedelta(days=i) for i in range(8))
        aware=local_instant(self.now.date(),self.now.hour*60+self.now.minute)
        for day in self.days:
            if day<self.now.date(): continue
            options=[]
            events=self.agenda()
            for old in tuple(self.events.values()):
                if old.day==day and old.event_id not in self.intents:
                    options.extend(find_room_upgrades(old,events=events,available_data=self.grid(day),
                        policy=policy,now=aware,planning=self.planning,time_preferences=self.prefs,
                        peak_limit=60,freeze_minutes=0))
            chosen=select_upgrade_portfolio(options,policy=policy,planning=self.planning,
                time_preferences=self.prefs,events=events,peak_limit=60)
            if chosen:
                change=chosen[0]
                assert change.original.duration==change.replacement.duration
                self.save(change.replacement,old=change.original,kind='upgrades')

    def run(self):
        def remove(room, day, start):
            for event_id, booking in tuple(self.intents.items()):
                if (booking['room'],booking['date'],booking['startTime'])==(room,day,start):
                    self.intents.pop(event_id,None)
        def update(room, day, start, end):
            for booking in self.intents.values():
                if (booking['room'],booking['date'],booking['startTime'])==(room,day,start): booking['endTime']=end
        with ExitStack() as stack, redirect_stdout(io.StringIO()):
            stack.enter_context(patch.multiple(b,PRIORITY_ROOMS=list(ROOMS),MINIMUM_BLOCK_MINUTES=30,
                MIN_BOOKING_MINUTES=30,MAX_BOOKING_HOURS=2,MAX_ROLLING_QUOTA_HOURS=6,MAX_PEAK_HOURS=1,
                PEAK_START=9,PEAK_END=16,FREE_HORIZON_MINUTES=300,SAME_ROOM_GAP_MINUTES=60,
                ALLOW_FRAGMENTED_SESSIONS=True))
            stack.enter_context(patch.object(b,'room_horizon_minutes',side_effect=lambda room:HORIZONS[room]))
            stack.enter_context(patch.object(b,'refresh_quota_balances',side_effect=self.quota))
            stack.enter_context(patch.object(b,'edit_reservation_end_time',side_effect=self.edit_end))
            stack.enter_context(patch.object(b,'remove_extendable_booking',side_effect=remove))
            stack.enter_context(patch.object(b,'update_extendable_booking_end_time',side_effect=update))
            self.advance()
            for day in self.days:
                for minute in range(480,1171,30):
                    self.now=self.stamp(day,minute)
                    self.metrics['checks']+=1
                    # Cancellations release real gaps; confirmed reservations
                    # remain immutable to competing students.
                    if minute in (660,840,990):
                        for room in ROOMS:
                            cells=self.external[day,room]
                            releasable=sorted(c for c in cells if c>minute)
                            if releasable:
                                selected=self.rng.choice(releasable)
                                cells.difference_update((selected,selected+15))
                                self.metrics['released_competitor_slots']+=1
                    self.extend()
                    if minute==480: self.advance()
                    self.short_notice()
                    if minute in (660,840,990): self.upgrade()
        totals=[sum(r.duration for r in self.events.values() if r.day==day) for day in self.days]
        self.metrics.update(seed=self.seed,daily_minutes=totals,total_minutes=sum(totals),
            premium_minutes=sum(r.duration for r in self.events.values() if r.room in ROOMS[:2]),
            daily_target=self.target,remaining_intents=len(self.intents))
        return self.metrics
