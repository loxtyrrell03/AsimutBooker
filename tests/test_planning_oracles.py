"""Small exhaustive reference searches, independent of production optimizers.

These use invented schedules only. They prove the stated objectives for these
finite line-ups, not globally optimal choices under unknown future occupancy.
"""
import itertools
import random
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from fractions import Fraction
from types import SimpleNamespace

from advance_planner import AdvanceDay, allocate_advance_week
from booking_strategy import DailyPlanningPreferences
from daily_planner import BookingOpportunity, select_day_plan
from progressive_planner import plan_progressive_transfer
from room_upgrades import (Reservation, RoomUpgrade, RoomConsolidation,
                          local_instant, select_upgrade_portfolio)

DAY = date(2030, 1, 7)
NOW = datetime(2030, 1, 1, 10)


def peak(start, end):
    return max(0, min(end, 960)-max(start, 540))


def opportunity(day, room, start, duration, *, window=(720,1080), horizon=7200):
    end = start+duration
    return BookingOpportunity(room=room, target_date=day, start_minutes=start,
        end_minutes=end, unlock_at=datetime.combine(day,datetime.min.time())
        + timedelta(minutes=start+30-horizon), room_priority=('Weston','Corus').index(room),
        initial_minutes=30, potential_minutes=duration,
        preferred_minutes=max(0,min(end,960)-max(start,720)),
        soft_preferred_minutes=max(0,min(end,window[1])-max(start,window[0])),
        peak_minutes=peak(start,end), peak_window_start_minutes=540,
        peak_window_end_minutes=960, source_gap_start_minutes=start,
        source_gap_end_minutes=end, soft_preferred_window=window, peak_target_minutes=60)


def reference_plans(options, target, peak_limit, gap, fragmented=True):
    # Enumerate legal prefixes and compatible subsets directly; never call the
    # production enumerator, resize helper, feasibility check or plan selector.
    variants = sorted({(o.room,o.start_minutes,o.start_minutes+duration)
        for o in options for duration in range(30,min(target,o.potential_minutes)+1,15)})
    window = options[0].soft_preferred_window if options else None
    def value(item):
        _, start, end = item
        distance = max(window[0]-start,end-window[1],0)/60 if window else 0
        return (end-start)*2**(-distance**2)
    variants = [item for item in variants if value(item) >= 15]
    plans = [()]
    def visit(first, chosen, minutes, used_peak):
        for index in range(first,len(variants)):
            room,start,end = variants[index]
            duration=end-start
            if minutes+duration > target or used_peak+peak(start,end) > peak_limit:
                continue
            if any(start < b+(gap if room == r else 0) and end > a-(gap if room == r else 0)
                   for r,a,b in chosen):
                continue
            candidate=(*chosen,variants[index])
            plans.append(candidate)
            if fragmented:
                visit(index+1,candidate,minutes+duration,used_peak+peak(start,end))
    visit(0,(),0,0)
    penalized=any(value(v) < v[2]-v[1] for v in variants)
    def score(plan):
        return (round(sum(round(value(v),8)-(7.5 if penalized else 0) for v in plan),7),-len(plan))
    return plans,score


class PlanningOracleTests(unittest.TestCase):
    def test_160_small_day_lineups_match_exhaustive_useful_time_and_session_count(self):
        rng=random.Random(2026092001)
        planning=DailyPlanningPreferences()
        for case in range(160):
            target=rng.choice((60,90,120,150))
            gap=rng.choice((0,30,60))
            peak_limit=rng.choice((0,30,60))
            fragmented=case%5 != 0
            starts=(720,750,780,960,990,1020) if case%2 else (990,1020,1050,1080,1110)
            options=[opportunity(DAY,rng.choice(('Weston','Corus')),rng.choice(starts),
                                 rng.choice((30,45,60))) for _ in range(4)]
            expected,score=reference_plans(options,target,peak_limit,gap,fragmented)
            actual=select_day_plan(options,planning,now=NOW,target_minutes=target,
                allow_fragmented_sessions=fragmented,remaining_peak_minutes=peak_limit,
                same_room_gap_minutes=gap)
            raw=tuple((o.room,o.start_minutes,o.end_minutes) for o in actual)
            with self.subTest(case=case):
                self.assertIn(tuple(sorted(raw)),[tuple(sorted(plan)) for plan in expected])
                self.assertEqual(score(raw),max(map(score,expected)))

    def test_120_small_weeks_match_exhaustive_fair_coverage(self):
        rng=random.Random(2026092002)
        planning=DailyPlanningPreferences()
        for case in range(120):
            days=[]
            menus=[]
            budget=rng.choice((60,90,120,150,180))
            gap=rng.choice((0,60))
            fragmented=case%4 != 0
            for offset in range(3):
                day=DAY+timedelta(days=offset)
                target=rng.choice((90,120,240))
                existing=rng.choice((0,30,60))
                options=tuple(opportunity(day,rng.choice(('Weston','Corus')),
                    rng.choice((720,750,960,990)),rng.choice((30,45,60)),
                    horizon=rng.choice((4320,7200,10080))) for _ in range(3))
                days.append(AdvanceDay(day,target,existing,existing,options,60))
                possibilities,_=reference_plans(options,target-existing,60,gap,fragmented)
                menus.append(sorted({sum(end-start for _,start,end in plan) for plan in possibilities}))
            def score(minutes):
                covered=[d.quality_minutes+m for d,m in zip(days,minutes)]
                return (tuple(sorted(Fraction(min(60,n),60) for n in covered)),
                    tuple(sorted(Fraction(min(d.target_minutes,n),d.target_minutes)
                                 for d,n in zip(days,covered))),sum(minutes))
            feasible=[row for row in itertools.product(*menus) if sum(row)<=budget]
            selected=allocate_advance_week(days,planning,now=NOW,budget_minutes=budget,
                minimum_block_minutes=30,allow_fragmented_sessions=fragmented,same_room_gap_minutes=gap)
            with self.subTest(case=case):
                self.assertEqual(score(tuple(a.minutes for a in selected)),max(map(score,feasible)))

    def test_120_upgrade_lineups_match_exhaustive_compatible_improvements(self):
        rng=random.Random(2026092003)
        rooms=('Weston','Corus','Fallback')
        policy=SimpleNamespace(room_order=rooms)
        planning=DailyPlanningPreferences(enabled=False)
        originals=tuple(Reservation(i+1,DAY,'Fallback',1080+i*120,1110+i*120) for i in range(3))
        events=[{**r.as_booking(),'isReservation':True} for r in originals]
        for case in range(120):
            gap=rng.choice((0,60))
            choices=[]
            for index in range(7):
                start=rng.choice((720,750,780,960,990,1020))
                room=rng.choice(rooms[:2])
                if index==6:
                    choices.append(RoomConsolidation(originals[:2],Reservation(1,DAY,room,start,start+60)))
                else:
                    old=rng.choice(originals)
                    choices.append(RoomUpgrade(old,replace(old,room=room,start=start,end=start+30)))
            def valid(changes):
                ids=[old.event_id for c in changes for old in c.originals]
                if len(ids)!=len(set(ids)) or sum(peak(c.replacement.start,c.replacement.end) for c in changes)>60:
                    return False
                for a,b in itertools.combinations((c.replacement for c in changes),2):
                    distance=gap if a.room==b.room else 0
                    if a.start < b.end+distance and a.end > b.start-distance:
                        return False
                return True
            def score(changes):
                return (sum(sum(rooms.index(old.room)*old.duration for old in c.originals)
                            -rooms.index(c.replacement.room)*c.replacement.duration for c in changes),
                        sum(len(c.originals)-1 for c in changes),
                        -sum(abs(c.replacement.start-min(o.start for o in c.originals)) for c in changes))
            feasible=[tuple(c for i,c in enumerate(choices) if mask&(1<<i)) for mask in range(1<<len(choices))]
            expected=max(score(row) for row in feasible if valid(row))
            selected=select_upgrade_portfolio(choices,policy=policy,planning=planning,
                time_preferences={'enabled':False},events=events,same_room_gap=gap,peak_limit=60)
            with self.subTest(case=case):
                self.assertTrue(valid(selected))
                self.assertEqual(score(selected),expected)

    def test_428_progressive_horizon_cases_match_independent_prefix_choices(self):
        for horizon in (3,5):
          for start,end in ((720,780),(900,1020),(960,1080)):
            for has_seed in (False,True):
              for free_end in range(start+30,end+1,15):
                for open_end in range(start+30,end+1,15):
                    seed=Reservation(99,DAY,'Weston',start,start+30) if has_seed else None
                    old=Reservation(42,DAY,'Fallback',start+30 if has_seed else start,end)
                    group=(seed,old) if seed else (old,)
                    target=Reservation(99 if seed else 42,DAY,'Weston',start,end)
                    now=local_instant(DAY,open_end)-timedelta(days=horizon)+timedelta(seconds=1)
                    policy=SimpleNamespace(room_order=('Weston','Fallback'),minimum_block_minutes=30,
                        site_maximum_booking_minutes=120,booking_horizon=now+timedelta(days=7),
                        site_clock_offset_bounds=(-1,1),allow_fragmented_sessions=True,
                        booking_dates=lambda today: tuple(today+timedelta(days=i) for i in range(8)),
                        horizon_minutes_for=lambda room: (horizon if room=='Weston' else 7)*1440)
                    gap_start=seed.end if seed else start
                    gaps=[{'room':'Weston','slots':([{'startHour':gap_start/60,'endHour':free_end/60}]
                        if free_end>gap_start else [])}]
                    possible=[finish for finish in range(start+(45 if seed else 30),end+1,15)
                        if finish<=free_end and (end-finish==0 or end-finish>=30) and (seed or finish<end)]
                    ready=[finish for finish in possible if finish<=open_end]
                    expected=max(ready) if ready else min(possible,default=None)
                    if not seed and free_end==end and open_end==end:
                        expected=None
                    change=RoomConsolidation(group,target) if seed else RoomUpgrade(old,target)
                    result=plan_progressive_transfer(change,
                        events=[{**r.as_booking(),'isReservation':True} for r in group],
                        available_data=gaps,policy=policy,now=now,peak_limit=60,seed=seed,
                        time_preferences={'enabled':True,'strict_mode':True,'start_hour':12,'end_hour':18},
                        planning=DailyPlanningPreferences())
                    with self.subTest(horizon=horizon,start=start,seed=bool(seed),free=free_end,opened=open_end):
                        self.assertEqual(result.replacement.end if result else None,expected)
                        if result:
                            self.assertEqual(sum(r.duration for r in result.remaining)+result.replacement.duration,end-start)
                            self.assertLessEqual(sum(peak(r.start,r.end) for r in (*result.remaining,result.replacement)),60)


if __name__=='__main__':
    unittest.main()
