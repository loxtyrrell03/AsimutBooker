import itertools
import random
import unittest
from dataclasses import replace

from booking_strategy import DailyPlanningPreferences, load_booking_strategy, apply_booking_strategy_update, BookingStrategyError
from daily_planner import select_day_plan
from session_preferences import comfort_key, refine_plan
from tests.test_planning_oracles import opportunity, DAY, NOW, reference_plans


class SessionPreferenceTests(unittest.TestCase):
    def choose(self, options, **kwargs):
        fields = {k: kwargs.pop(k) for k in tuple(kwargs) if k in (
            'preferred_block_minutes', 'preferred_rest_minutes', 'prefer_fewer_room_changes')}
        return select_day_plan(options, DailyPlanningPreferences(**fields), now=NOW,
            target_minutes=kwargs.pop('target_minutes', 120),
            allow_fragmented_sessions=kwargs.pop('allow_fragmented_sessions', True),
            remaining_peak_minutes=60, same_room_gap_minutes=kwargs.pop('same_room_gap_minutes', 0), **kwargs)

    def test_schema_defaults_and_scoped_updates_preserve_existing_preferences(self):
        settings = {'unrelated': {'keep': 42}}
        self.assertEqual(load_booking_strategy(settings).daily_planning.preferred_block_minutes, 0)
        changed = apply_booking_strategy_update(settings, {'daily_planning': {
            'preferred_block_minutes': 60, 'preferred_rest_minutes': 30, 'prefer_fewer_room_changes': True}})
        self.assertEqual(changed.daily_planning.preferred_rest_minutes, 30)
        self.assertEqual(settings['unrelated'], {'keep': 42})
        self.assertEqual(load_booking_strategy(settings), changed)
        for field, invalid in (('preferred_block_minutes', 15), ('preferred_block_minutes', True),
                               ('preferred_rest_minutes', 31), ('preferred_rest_minutes', '30'),
                               ('prefer_fewer_room_changes', 1)):
            with self.subTest(field=field, invalid=invalid), self.assertRaises(BookingStrategyError):
                apply_booking_strategy_update(settings, {'daily_planning': {field: invalid}})

    def test_hour_blocks_and_rest_when_equal_good_hours_are_available(self):
        options = [opportunity(DAY, 'Weston', start, 120) for start in (960, 1020, 1050)]
        options = [replace(o, soft_preferred_window=None) for o in options]
        result = self.choose(options, preferred_block_minutes=60, preferred_rest_minutes=30)
        self.assertEqual(sorted((x.start_minutes, x.potential_minutes) for x in result), [(960,60),(1050,60)])

    def test_two_hour_preference_does_not_discard_two_scarce_half_hours(self):
        options = [opportunity(DAY, 'Weston', start, 30) for start in (960,1020)]
        result = self.choose(options, preferred_block_minutes=120, preferred_rest_minutes=120)
        self.assertEqual(sum(x.potential_minutes for x in result), 60)

    def test_rest_never_loses_hours_when_only_adjacent_rooms_exist(self):
        options = [opportunity(DAY, 'Weston', 960, 60), opportunity(DAY, 'Corus', 1020, 60)]
        result = self.choose(options, preferred_rest_minutes=60)
        self.assertEqual(sum(x.potential_minutes for x in result), 120)

    def test_rest_accounts_for_existing_practice_and_does_not_downgrade_room(self):
        options = [opportunity(DAY, 'Weston', start, 60) for start in (960,990)]
        result = self.choose(options, preferred_rest_minutes=30, target_minutes=60,
                             existing_sessions=((900,960,'Corus'),))
        self.assertEqual(result[0].start_minutes, 990)
        options = [opportunity(DAY,'Weston',960,60), opportunity(DAY,'Corus',990,60)]
        result = self.choose(options, preferred_rest_minutes=30, target_minutes=60,
                             existing_sessions=((900,960,'Corus'),))
        self.assertEqual(result[0].room, 'Weston')

    def test_existing_room_can_break_an_equal_quality_tie(self):
        options = [opportunity(DAY,room,960,60) for room in ('Weston','Corus')]
        options = [replace(o, room_priority=0) for o in options]
        result = self.choose(options, prefer_fewer_room_changes=True, target_minutes=60,
                             existing_sessions=((900,930,'Corus'),))
        self.assertEqual(result[0].room, 'Corus')

    def test_single_session_and_same_room_gap_remain_hard(self):
        options = [opportunity(DAY,'Weston',start,120) for start in (960,1020,1050)]
        self.assertEqual(len(self.choose(options, preferred_block_minutes=60,
                                        allow_fragmented_sessions=False)), 1)
        result = self.choose(options, preferred_block_minutes=60, same_room_gap_minutes=60)
        for left,right in itertools.combinations(sorted(result,key=lambda x:x.start_minutes),2):
            if left.room == right.room:
                self.assertGreaterEqual(right.start_minutes-left.end_minutes,60)

    def test_bounded_search_keeps_known_good_baseline(self):
        options = [opportunity(DAY,'Weston',960,120)]
        baseline = self.choose(options)
        result = refine_plan(baseline, options, DailyPlanningPreferences(preferred_block_minutes=60),
            now=NOW, remaining_peak_minutes=60, same_room_gap_minutes=0,
            allow_fragmented_sessions=True, state_limit=0)
        self.assertEqual(result, baseline)

    def test_required_source_survives_comfort_refinement(self):
        options = [opportunity(DAY,'Weston',960,120), opportunity(DAY,'Corus',1080,60)]
        result = self.choose(options, preferred_block_minutes=60, required_opportunity=options[1])
        self.assertTrue(any(i.room=='Corus' and i.start_minutes==1080 for i in result))

    def test_100_small_comfort_lineups_match_independent_enumeration(self):
        rng = random.Random(202609203)
        for case in range(100):
            options = [opportunity(DAY,rng.choice(('Weston','Corus')),rng.choice((960,990,1020,1050)),
                                   rng.choice((30,60,90))) for _ in range(4)]
            # Uniform good-time suitability; use an independent raw tuple score.
            options = [replace(o,soft_preferred_window=None,preferred_minutes=0) for o in options]
            planning = DailyPlanningPreferences(preferred_block_minutes=rng.choice((0,60,120)),
                preferred_rest_minutes=rng.choice((0,30,60)), prefer_fewer_room_changes=bool(case%2))
            plans,_ = reference_plans(options,120,60,0)
            def score(plan):
                ordered = sorted(plan,key=lambda v:v[1])
                block = sum(abs(end-start-planning.preferred_block_minutes) for _,start,end in plan) if planning.preferred_block_minutes else 0
                rest = sum(max(0,planning.preferred_rest_minutes-(b[1]-a[2])) for a,b in zip(ordered,ordered[1:]))
                swaps = sum(a[0]!=b[0] for a,b in zip(ordered,ordered[1:])) if planning.prefer_fewer_room_changes else 0
                return (-sum(end-start for _,start,end in plan),
                        sum(('Weston','Corus').index(room)*(end-start) for room,start,end in plan), block,rest,swaps,len(plan))
            actual = select_day_plan(options,planning,now=NOW,target_minutes=120,
                allow_fragmented_sessions=True,remaining_peak_minutes=60)
            raw = tuple((o.room,o.start_minutes,o.end_minutes) for o in actual)
            with self.subTest(case=case):
                self.assertEqual(score(raw),min(map(score,plans)))


if __name__ == '__main__':
    unittest.main()
