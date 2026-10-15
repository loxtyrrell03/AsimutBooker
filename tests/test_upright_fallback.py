"""Room-group priority through real planners, with synthetic data only."""
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import book_week as b
from booking_strategy import DailyPlanningPreferences
from fill_range import FillRequest, candidates as fill_candidates, choose_candidate
from live_room_policy import build_live_room_policy
from room_now import LONDON, RoomNowRequest, candidates as now_candidates
from room_preferences import (RoomPreferencesError, apply_room_preferences_update,
                              filter_effective_rooms, load_room_preferences)


NOW = datetime(2030, 9, 30, 10, tzinfo=LONDON)
PRIMARY = ['Practice: Grand Piano', 'Practice: Grand Piano x 2', 'B0.03']


def metadata(kind):
    return {'room_type_tags': [] if kind == 'Piano: Upright' else [kind],
            'instrument_tags': [kind] if kind == 'Piano: Upright' else [], 'features': []}


META = {'Grand': metadata(PRIMARY[0]), 'Corus': metadata('B0.03'),
        'B1.05': metadata('Practice: Upright Piano'), 'A1.01': metadata('Practice: Upright Piano'),
        'Hopkins': metadata('Piano: Upright'), 'Unknown': metadata('Studio'),
        'Empty': metadata('Practice: No Piano')}


def preferences(**updates):
    return load_room_preferences({'room_preferences': {
        'ordered_rooms': ['Hopkins', 'Grand', 'A1.01', 'B1.05', 'Corus'],
        'acceptable_room_type_tags': PRIMARY, 'upright_fallback': True, **updates}})


def policy(prefs=None):
    catalog = SimpleNamespace(require_fresh=lambda: None, complete=True,
        observed_at=NOW, booking_horizon=NOW+timedelta(days=7), global_horizon_minutes=10080,
        minimum_booking_minutes=30, maximum_booking_minutes=120, minimum_booking_gap_minutes=60,
        rooms=tuple(SimpleNamespace(name=name, horizon_minutes=10080, location_id=index+1)
                    for index, name in enumerate(META)), as_live_metadata=lambda: META,
        site_clock_offset_bounds=(0, 0))
    return build_live_room_policy(catalog, prefs or preferences())


def grid(*entries):
    return [{'room': room, 'slots': [{'startHour': start, 'endHour': end}]}
            for room, start, end in entries]


class UprightFallbackTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.policy = policy()
        self.stack.enter_context(patch.multiple(b, ACTIVE_ROOM_POLICY=self.policy,
            PRIORITY_ROOMS=list(self.policy.room_order),
            ROOM_HORIZON_MINUTES=dict(self.policy.room_horizon_minutes),
            MINIMUM_BLOCK_MINUTES=30, MAX_BOOKING_HOURS=2, MAX_PEAK_HOURS=2,
            FREE_HORIZON_MINUTES=300, FREE_HORIZON_OVERRIDES_PEAK=True,
            ALLOW_FRAGMENTED_SESSIONS=True, SAME_ROOM_GAP_MINUTES=60))
        self.tracker = b.BookingTracker()
        self.planning = DailyPlanningPreferences()
        self.times = {'enabled': True, 'strict_mode': True, 'start_hour': 13, 'end_hour': 15}

    def opportunities(self, rows, **kwargs):
        return b.build_day_booking_opportunities(rows, NOW.date(), self.tracker,
            self.times, self.planning, now=NOW.replace(tzinfo=None),
            remaining_daily_hours=4, **kwargs)

    def test_explicit_metadata_order_and_exclusions(self):
        self.assertEqual(self.policy.room_order, ('Grand', 'Corus', 'B1.05', 'Hopkins', 'A1.01'))
        self.assertEqual(dict(self.policy.fallback_room_tiers),
                         {'Grand': 0, 'Corus': 0, 'B1.05': 1, 'Hopkins': 2, 'A1.01': 2})
        self.assertEqual(filter_effective_rooms(preferences(excluded_rooms=['B1.05']), META),
                         ('Grand', 'Corus', 'Hopkins', 'A1.01'))
        self.assertEqual(filter_effective_rooms(preferences(required_feature_terms=['stool']), META), ())

    def test_disabled_fallback_preserves_grand_filter_and_legacy_document(self):
        prefs = preferences(upright_fallback=False)
        self.assertEqual(filter_effective_rooms(prefs, META), ('Grand', 'Corus'))
        self.assertNotIn('upright_fallback', prefs.to_dict())
        for value in (1, 'true', None):
            with self.subTest(value=value), self.assertRaises(RoomPreferencesError):
                preferences(upright_fallback=value)

    def test_scoped_edits_keep_fallback_and_unrelated_preferences(self):
        settings = {'room_preferences': preferences().to_dict(), 'unrelated': {'keep': True}}
        apply_room_preferences_update(settings, {'minimum_block_minutes': 60})
        self.assertTrue(load_room_preferences(settings).upright_fallback)
        self.assertEqual(settings['unrelated'], {'keep': True})
        apply_room_preferences_update(settings, {'upright_fallback': False})
        self.assertFalse(load_room_preferences(settings).upright_fallback)

    def test_longer_earlier_upright_cannot_displace_any_grand(self):
        rows = self.opportunities(grid(('Grand', 14.5, 15), ('B1.05', 13, 15), ('Hopkins', 13, 15)))
        self.assertTrue(rows)
        self.assertEqual({row.room for row in rows}, {'Grand'})
        plan = b.build_display_day_plan(NOW.date(), rows, self.tracker, self.planning,
            now=NOW.replace(tzinfo=None), target_minutes=240)
        self.assertIsNotNone(plan)

    def test_no_grand_uses_b_upright_before_longer_other_building(self):
        rows = self.opportunities(grid(('Grand', 14, 14.25), ('B1.05', 14.5, 15), ('Hopkins', 13, 15)))
        self.assertEqual({row.room for row in rows}, {'B1.05'})
        rows = self.opportunities(grid(('Hopkins', 13, 15)))
        self.assertEqual({row.room for row in rows}, {'Hopkins'})

    def test_conflicted_or_outside_window_grand_does_not_block_fallback(self):
        self.tracker.add_conflict(NOW.date(), 14, 15)
        rows = self.opportunities(grid(('Grand', 14, 15), ('Corus', 16, 18), ('B1.05', 13, 14)))
        self.assertEqual({row.room for row in rows}, {'B1.05'})

    def test_later_free_window_grand_prevents_ready_upright(self):
        self.times['end_hour'] = 20
        self.tracker.live_quota_minutes = 0
        rows = self.opportunities(grid(('Grand', 19, 20), ('B1.05', 13, 15)),
                                  free_horizon_only=True, include_free_horizon_intent=True)
        self.assertTrue(rows)
        self.assertEqual({row.room for row in rows}, {'Grand'})
        self.assertTrue(all(row.unlock_at > NOW.replace(tzinfo=None) for row in rows))

    def test_no_same_time_downgrade_without_full_day_replan(self):
        rows = b.same_time_room_backups(grid(('Grand', 14, 15), ('B1.05', 13, 14)),
            {'room': 'Grand', 'start_hour': 13, 'end_hour': 14}, NOW.date(),
            self.tracker, self.times, self.planning, now=NOW.replace(tzinfo=None),
            excluded_rooms={'Grand'}, remaining_daily_hours=4)
        self.assertEqual(rows, [])

    def test_fallback_still_uses_planner_when_foresight_disabled(self):
        self.assertTrue(b.uses_time_aware_planner(replace(self.planning, enabled=False), {}))

    def test_fill_uses_grands_first_within_exact_requested_window(self):
        request = FillRequest.create({'date': str(NOW.date()), 'start_time': '13:00', 'end_time': '15:00'}, NOW.isoformat())
        rows = grid(('Grand', 14.5, 15), ('B1.05', 13, 15), ('Hopkins', 13, 15))
        options, _ = fill_candidates(b, rows, self.tracker, request, NOW)
        chosen = choose_candidate(b, options, self.tracker, request, NOW)
        self.assertEqual((chosen['room'], chosen['start_hour'], chosen['end_hour']), ('Grand', 14.5, 15))
        options, _ = fill_candidates(b, grid(('Grand', 16, 18), ('B1.05', 14, 14.5), ('Hopkins', 13, 15)), self.tracker, request, NOW)
        self.assertEqual({item['room'] for item in options}, {'B1.05'})

    def test_room_now_waits_for_later_grand_over_immediate_upright(self):
        options = now_candidates(b, grid(('Grand', 14.5, 15), ('B1.05', 13, 15)), self.tracker,
            RoomNowRequest('longest', 120, NOW), self.times, 4, now=NOW)
        self.assertEqual({item['room'] for item in options}, {'Grand'})

    def test_upgrades_choose_grands_and_never_downgrade_into_uprights(self):
        from room_upgrades import Reservation, find_room_upgrades
        original = Reservation(42, NOW.date(), 'Hopkins', 780, 840)
        values = dict(events=[{**original.as_booking(), 'isReservation': True}],
            available_data=grid(('Grand', 14, 15), ('B1.05', 13, 15)), policy=self.policy,
            now=NOW, time_preferences=self.times, planning=self.planning, freeze_minutes=0)
        results = find_room_upgrades(original, **values)
        self.assertTrue(results)
        self.assertEqual({item.replacement.room for item in results}, {'Grand'})
        original = replace(original, room='Grand')
        values.update(events=[{**original.as_booking(), 'isReservation': True}],
                      available_data=grid(('B1.05', 13, 15)), _allow_room_downgrade=True)
        self.assertEqual(find_room_upgrades(original, **values), ())


if __name__ == '__main__':
    unittest.main()
