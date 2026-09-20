import copy
from dataclasses import replace
from datetime import datetime, timedelta
import itertools
import random
import unittest
from unittest.mock import patch

from advance_preferences import (AdvanceQuotaPreferences, AdvancePeriod, load_advance_quota,
    apply_advance_quota, preference_schema, rooms_for, session_quality)
from booking_strategy import BookingStrategyError
from daily_planner import select_day_plan
from tests import test_advance_planner as planner_fixture, test_advance_runtime as runtime_fixture
import advance_runtime
import book_week


class PreferencesTests(unittest.TestCase):
    def test_old_settings_read_without_mutation_and_scoped_patch(self):
        settings = {'unrelated':True, 'booking_strategy':{'reverse_date_order':True}}
        before = copy.deepcopy(settings)
        self.assertEqual(load_advance_quota(settings), AdvanceQuotaPreferences())
        self.assertEqual(settings, before)
        apply_advance_quota(settings, {'distribution':'concentrated', 'room_mode':'selected', 'room_order':['Corus','Weston']})
        apply_advance_quota(settings, {'reserve_minutes':60})
        self.assertEqual(settings['booking_strategy'], before['booking_strategy'])
        self.assertEqual(settings['advance_quota']['room_order'], ['Corus','Weston'])
        self.assertEqual(set(preference_schema()['properties']), set(AdvanceQuotaPreferences().to_dict()))

    def test_invalid_patches_are_atomic(self):
        bad = [{'distribution':'whatever'}, {'room_mode':'selected'}, {'room_order':['A','A']},
            {'day_weights':[1]*6}, {'day_weights':[True]*7}, {'reserve_minutes':5},
            {'reserve_minutes':-15}, {'anchor_minutes':15}, {'block_minutes':45},
            {'day_caps_minutes':[1440]*7}, {'wait_for_opening':1}, {'fallback_lead_minutes':1500},
            {'unknown':True}, {'periods':[dict(days=[0], start='15:01', end='16:00')]},
            {'periods':[dict(days=[], start='15:00', end='16:00')]},
            {'periods':[dict(days=[0], start='15:00', end='15:15')]},
            {'periods':[dict(days=[0], start='14:00', end='16:00'),dict(days=[0,1],start='15:00',end='17:00')]}]
        for patch in bad:
            settings = {'unrelated':True}
            with self.subTest(patch=patch), self.assertRaises(BookingStrategyError):
                apply_advance_quota(settings, patch)
            self.assertEqual(settings, {'unrelated':True})

    def test_explicit_rank_exclusions_and_optional_fallback(self):
        policy = replace(AdvanceQuotaPreferences(), room_mode='selected',room_order=('Corus','Excluded','Weston'))
        self.assertEqual(rooms_for(policy, ('Weston','Other','Corus')), ('Corus','Weston'))
        self.assertEqual(rooms_for(replace(policy,room_fallback=True), ('Weston','Other','Corus')), ('Corus','Weston','Other'))
        self.assertEqual(rooms_for(replace(policy,room_mode='top',top_room_count=1), ('Weston','Other')), ('Weston',))


class AllocationChoicesTests(unittest.TestCase):
    fixture = planner_fixture.AdvancePlannerTests()

    def allocate(self, days, **kwargs):
        return self.fixture.allocate(days, **kwargs)

    def test_grouping_spread_weights_and_zero_day_are_distinct(self):
        days = [self.fixture.day(i, target=120, start=16,end=18) for i in range(7)]
        balanced = self.allocate(days)
        grouped = self.allocate(days, quota_preferences=replace(AdvanceQuotaPreferences(),distribution='concentrated'))
        self.assertEqual(sorted(d.minutes for d in balanced), [45,45,45,45,60,60,60])
        self.assertEqual(sorted(d.minutes for d in grouped), [0,0,0,0,120,120,120])
        weighted = self.allocate(days[:3], budget=180, quota_preferences=replace(AdvanceQuotaPreferences(),distribution='weighted',day_weights=(2,1,0,1,1,1,1)))
        self.assertEqual([d.minutes for d in weighted], [120,60,0])

    def test_day_cap_counts_booked_and_held_time(self):
        days = [replace(self.fixture.day(0, confirmed=45,quality=45,held=30),advance_cap_minutes=90), self.fixture.day(1)]
        result = self.allocate(days, budget=120)
        self.assertEqual([d.minutes for d in result], [0,120])

    def test_ranked_periods_and_room_priority_choose_expected_intervals(self):
        day = self.fixture.day(0, start=12,end=18)
        policy = replace(AdvanceQuotaPreferences(), periods=(AdvancePeriod((0,), '17:00','18:00'), AdvancePeriod((0,), '12:00','13:00')))
        chosen = self.allocate([day], budget=60, quota_preferences=policy)[0].sessions
        self.assertEqual([(x.start_minutes,x.end_minutes) for x in chosen], [(1020,1080)])
        # A worse room at the best time competes with the best room at the second period.
        top = tuple(x for x in self.fixture.day(0,start=12,end=13).opportunities)
        other = tuple(replace(x,room='Corus',room_priority=1) for x in self.fixture.day(0,start=17,end=18).opportunities)
        mixed = replace(day,opportunities=(*top,*other))
        time_choice = self.allocate([mixed],budget=60,quota_preferences=policy)[0].sessions
        room_choice = self.allocate([mixed],budget=60,quota_preferences=replace(policy,priority_mode='room_first'))[0].sessions
        self.assertEqual({x.room for x in time_choice},{'Corus'})
        self.assertEqual({x.room for x in room_choice},{'Weston'})

    def test_quality_distribution_prioritizes_best_day_instead_of_spreading(self):
        days = [self.fixture.day(0,start=16,end=18),self.fixture.day(1,start=16,end=18)]
        days[0] = replace(days[0],opportunities=tuple(replace(x,room_priority=1) for x in days[0].opportunities))
        policy = replace(AdvanceQuotaPreferences(),distribution='quality')
        self.assertEqual([d.minutes for d in self.allocate(days,budget=120,quota_preferences=policy)], [0,120])

    def test_custom_quality_matches_independent_small_portfolio_oracle(self):
        rng = random.Random(210921)
        planning, now = self.fixture.planning, self.fixture.now
        policy = replace(AdvanceQuotaPreferences(),periods=(AdvancePeriod((0,), '17:00','18:00'),AdvancePeriod((0,), '16:00','17:00')))
        for case in range(40):
            base = self.fixture.day(0,start=16,end=18)
            starts = rng.sample(range(960,1051,15),4)
            # Fixed 30-minute opportunities keep the independent enumeration small.
            opportunities = tuple(replace(next(x for x in base.opportunities if x.start_minutes == start),
                end_minutes=start+30,potential_minutes=30,initial_minutes=30,preferred_minutes=0,
                soft_preferred_minutes=30,room=f'R{i%2}',room_priority=i%2) for i,start in enumerate(starts))
            candidates = []
            for n in range(5):
                for subset in itertools.combinations(opportunities,n):
                    if sum(x.potential_minutes for x in subset)>90: continue
                    ordered=sorted(subset,key=lambda x:x.start_minutes)
                    if any(a.end_minutes>b.start_minutes for a,b in zip(ordered,ordered[1:])): continue
                    candidates.append(subset)
            def score(items):
                scores=[session_quality(x,policy,planning) for x in items]
                return (sum(x.potential_minutes for x in items),*(sum(row[i] for row in scores) for i in range(3)),-len(items))
            expected=max(map(score,candidates))
            actual=select_day_plan(opportunities,planning,now=now,target_minutes=90,allow_fragmented_sessions=True,
                remaining_peak_minutes=60,same_room_gap_minutes=0,quality_score=lambda x:session_quality(x,policy,planning))
            self.assertEqual(score(actual),expected,case)


class RuntimeChoicesTests(unittest.TestCase):
    setUp = runtime_fixture.AdvanceRuntimeTests.setUp
    open = runtime_fixture.AdvanceRuntimeTests.open
    quota = runtime_fixture.AdvanceRuntimeTests.quota
    book = runtime_fixture.AdvanceRuntimeTests.book
    scan = runtime_fixture.AdvanceRuntimeTests.scan
    publish = runtime_fixture.AdvanceRuntimeTests.publish
    run_week = runtime_fixture.AdvanceRuntimeTests.run_week
    def plan(self, patch):
        self.settings['advance_quota'] = patch
        return advance_runtime.plan_from_grids(book_week,self.grids,self.settings,self.practice,self.tracker,self.args,now=self.now)

    def test_custom_room_order_periods_reserve_and_day_zero(self):
        _, allocations, _ = self.plan(dict(room_mode='selected',room_order=['Other','Corus'],
            reserve_minutes=60,day_weights=[0,1,1,1,1,1,1],period_mode='only',periods=[dict(days=list(range(7)),start='16:00',end='18:00')]))
        self.assertEqual(sum(x.minutes for x in allocations),300)
        self.assertEqual(allocations[0].minutes,0)
        self.assertTrue(all(x.room=='Other' and x.start_minutes>=960 for d in allocations for x in d.sessions))

    def test_only_periods_do_not_override_general_strict_hours(self):
        self.stack.enter_context(patch.object(book_week,'load_time_preferences',return_value=dict(enabled=True,strict_mode=True,start_hour=12,end_hour=13)))
        _, allocations, _=self.plan(dict(period_mode='only',periods=[dict(days=list(range(7)),start='16:00',end='18:00')]))
        self.assertEqual(sum(x.minutes for x in allocations),0)

    def test_wait_off_does_not_allocate_to_unopened_five_day_rooms(self):
        self.horizons.return_value=7200
        _, allocations, _=self.plan(dict(wait_for_opening=False))
        self.assertEqual([x.minutes for x in allocations][-2:],[0,0])
        self.assertTrue(all(x.unlock_at<=self.now for d in allocations for x in d.sessions))

    def test_reserve_can_hold_all_advance_credit(self):
        _, allocations, _=self.plan(dict(reserve_minutes=360))
        self.assertFalse(any(x.minutes for x in allocations))

    def test_prepared_seed_reports_actual_fallback_room_and_confirmed_minutes(self):
        self.now=datetime(2026,9,20,14,59)
        self.args.target_time='15:00'
        self.args.max_actions=1
        original=advance_runtime.plan_from_grids
        def imminent(*args,**kwargs):
            days,allocations,context=original(*args,**kwargs)
            return days, tuple(replace(a,sessions=tuple(replace(x,unlock_at=self.now+timedelta(minutes=1)) for x in a.sessions)) for a in allocations),context
        def seed(page,slot,day,tracker,offset,**kwargs):
            self.assertTrue(kwargs['horizon'])
            self.events.append(('Corus',day,slot['start_hour'],slot['start_hour']+.5))
            return True,{**slot,'room':'Corus','booking_minutes':30}
        self.attempt.side_effect=seed
        with patch.object(advance_runtime,'plan_from_grids',side_effect=imminent):
            actions,_,_=self.run_week()
        self.assertEqual(actions,1)
        self.assertEqual(len(self.details),1)
        room,day,start,end=self.events[0]
        self.assertEqual(self.details[0],f'{day} {room} {book_week.time_text(round(start*60))}-{book_week.time_text(round(end*60))}')

    def test_room_fallback_uses_custom_rank_at_the_same_exact_interval(self):
        options=book_week.same_time_room_backups(self.grids[self.days[0]],dict(start_hour=16,end_hour=17),
            self.days[0],self.tracker,dict(enabled=False),self.fixture_planning(),now=self.now,
            excluded_rooms=set(),allowed_rooms=('Other','Corus','Weston'),remaining_daily_hours=1)
        self.assertEqual([x.room for x in options],['Other','Corus','Weston'])
        self.assertTrue(all((x.start_minutes,x.end_minutes)==(960,1020) for x in options))

    @staticmethod
    def fixture_planning():
        return planner_fixture.AdvancePlannerTests.planning

    def test_release_lead_changes_only_wait_with_remaining_credit(self):
        from short_notice_bookings import reserve_advance_credit
        planning=self.fixture_planning()
        item=planner_fixture.AdvancePlannerTests().day(0,start=16,end=17).opportunities[0]
        inherited=reserve_advance_credit([item],planning,active=True)[0]
        custom=reserve_advance_credit([item],planning,active=True,release_lead_minutes=30)[0]
        self.assertEqual(custom.unlock_at-inherited.unlock_at,timedelta(minutes=90))
        self.assertEqual(reserve_advance_credit([item],planning,active=False,release_lead_minutes=30),[item])


if __name__ == '__main__': unittest.main()
