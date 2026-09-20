import copy
from dataclasses import replace
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from booking_strategy import DailyPlanningPreferences
from room_upgrades import (Reservation, RoomUpgrade, RoomConsolidation, find_room_upgrades,
                           find_room_consolidations, select_upgrade_portfolio, apply_upgrade_to_events,
                           find_upgrade_opportunities, local_instant, availability_after_upgrade)


class UpgradePortfolioTests(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 9, 21)
        self.now = datetime(2026, 9, 18, 16, tzinfo=timezone.utc)
        self.policy = SimpleNamespace(room_order=("Weston", "Corus", "Fallback"),
            minimum_block_minutes=30, site_maximum_booking_minutes=120,
            booking_horizon=self.now + timedelta(days=7), site_clock_offset_bounds=(-1, 1),
            horizon_minutes_for=lambda room: 7 * 1440,
            booking_dates=lambda today: tuple(today + timedelta(days=i) for i in range(8)))
        self.planning = DailyPlanningPreferences(upgrade_freeze_hours=0)
        self.prefs = dict(enabled=True, strict_mode=True, start_hour=12, end_hour=18)
        self.originals = (Reservation(1, self.day, "Fallback", 720, 750),
                          Reservation(2, self.day, "Corus", 750, 810),
                          Reservation(3, self.day, "Fallback", 810, 840))
        self.events = [{**r.as_booking(), "isReservation": True} for r in self.originals]
        self.gaps = [dict(room="Weston", slots=[dict(startHour=12, endHour=14)])]

    def consolidate(self, **overrides):
        kwargs = dict(events=self.events, available_data=self.gaps, policy=self.policy, now=self.now,
                      planning=self.planning, time_preferences=self.prefs)
        kwargs.update(overrides)
        return find_room_consolidations(**kwargs)

    def select(self, candidates, **overrides):
        kwargs = dict(policy=self.policy, planning=self.planning, time_preferences=self.prefs,
                      events=self.events, same_room_gap=60)
        kwargs.update(overrides)
        return select_upgrade_portfolio(candidates, **kwargs)

    def test_three_pieces_become_one_full_session_in_the_highest_ranked_room(self):
        before = copy.deepcopy(self.events)
        candidates = self.consolidate()
        chosen = self.select(candidates)
        self.assertEqual(len(chosen), 1)
        upgrade = chosen[0]
        self.assertEqual(len(upgrade.originals), 3)
        self.assertEqual((upgrade.replacement.room, upgrade.replacement.start, upgrade.replacement.end), ("Weston", 720, 840))
        after = apply_upgrade_to_events(self.events, upgrade)
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]["eventId"], upgrade.original.event_id)
        self.assertEqual(self.events, before)

    def test_half_hour_earlier_move_changes_both_room_and_time(self):
        old = Reservation(1, self.day, "Fallback", 750, 810)
        candidates = find_room_upgrades(old, events=[{**old.as_booking(), "isReservation": True}],
            available_data=[dict(room="Weston", slots=[dict(startHour=12, endHour=13)])],
            policy=self.policy, now=self.now, planning=self.planning, time_preferences=self.prefs)
        self.assertEqual(candidates[0].replacement, Reservation(1, self.day, "Weston", 720, 780))

    def test_cannot_downgrade_a_weston_fragment_to_corus_for_convenience(self):
        self.events[0]["room"] = "Weston"
        self.gaps = [dict(room="Corus", slots=[dict(startHour=12, endHour=14)])]
        self.assertFalse(any(len(c.originals) == 3 for c in self.consolidate()))

    def test_anchor_can_expand_into_its_own_room_and_free_adjacent_gap(self):
        self.events[0]["room"] = "Weston"
        self.gaps[0]["slots"] = [dict(startHour=12.5, endHour=14)]
        candidates = [c for c in self.consolidate() if len(c.originals) == 3]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].original.event_id, 1)
        self.assertEqual(candidates[0].replacement.duration, 120)

    def test_insufficient_room_gap_never_loses_one_fragment(self):
        self.gaps[0]["slots"] = [dict(startHour=12, endHour=13.5)]
        self.assertFalse(any(len(c.originals) == 3 for c in self.consolidate()))

    def test_frozen_ignored_and_extending_fragments_cannot_be_retired(self):
        for changes in (dict(ignored_event_ids={2}), dict(freeze_minutes=8 * 1440),
                        dict(protected_extensions=[{**self.originals[1].as_booking(), "target_end": "14:00"}])):
            with self.subTest(changes=changes):
                self.assertFalse(any(len(c.originals) == 3 for c in self.consolidate(**changes)))

    def test_other_events_blackouts_and_full_horizon_still_apply(self):
        self.events.append(dict(eventId=8, date=self.day.isoformat(), room=None,
                                startTime="13:00", endTime="13:15", isReservation=False))
        self.assertFalse(any(len(c.originals) == 3 for c in self.consolidate()))
        self.events.pop()
        self.assertFalse(any(len(c.originals) == 3 for c in self.consolidate(blocked_intervals=[(795, 810)])))
        self.policy.horizon_minutes_for = lambda room: 2 * 1440
        self.assertFalse(self.consolidate())

    def test_overlapping_bookings_cannot_be_counted_as_two_distinct_hours(self):
        self.events[1].update(startTime="12:00", endTime="13:00")
        self.assertFalse(any(len(c.originals) == 3 for c in self.consolidate()))

    def test_consolidation_rejects_mismatched_duration_day_or_identity(self):
        for replacement in (Reservation(1, self.day, "Weston", 720, 780),
                            Reservation(9, self.day, "Weston", 720, 840),
                            Reservation(1, self.day + timedelta(days=1), "Weston", 720, 840)):
            with self.assertRaises(ValueError):
                RoomConsolidation(self.originals, replacement)

    def test_portfolio_uses_an_alternative_slot_to_allow_both_upgrades(self):
        # Greedily taking event 1's first slot blocks event 2 entirely.
        old1 = Reservation(1, self.day, "Fallback", 960, 1020)
        old2 = Reservation(2, self.day, "Fallback", 1020, 1080)
        choices = [RoomUpgrade(old1, Reservation(1, self.day, "Weston", 720, 780)),
                   RoomUpgrade(old1, Reservation(1, self.day, "Weston", 840, 900)),
                   RoomUpgrade(old2, Reservation(2, self.day, "Weston", 720, 780))]
        events = [{**r.as_booking(), "isReservation": True} for r in (old1, old2)]
        selected = self.select(choices, events=events)
        self.assertEqual({(c.original.event_id, c.replacement.start) for c in selected}, {(1, 840), (2, 720)})

    def test_portfolio_cannot_use_original_twice_or_double_spend_peak_quota(self):
        old1 = Reservation(1, self.day, "Fallback", 960, 1020)
        old2 = Reservation(2, self.day, "Fallback", 1020, 1080)
        choices = [RoomUpgrade(old1, Reservation(1, self.day, "Weston", 720, 780)),
                   RoomUpgrade(old1, Reservation(1, self.day, "Weston", 840, 900)),
                   RoomUpgrade(old2, Reservation(2, self.day, "Corus", 840, 900))]
        events = [{**r.as_booking(), "isReservation": True} for r in (old1, old2)]
        self.assertEqual(len(self.select(choices, events=events, peak_limit=60)), 1)
        chosen = self.select(choices, events=events)
        self.assertEqual(len(chosen), 2)
        self.assertEqual(len({c.original.event_id for c in chosen}), 2)

    def test_equal_quality_upgrades_consider_rest_against_the_whole_day(self):
        old = Reservation(1, self.day, 'Fallback', 1020, 1080)
        existing = Reservation(2, self.day, 'Corus', 960, 1020)
        choices = [RoomUpgrade(old, Reservation(1, self.day, 'Weston', start, start+60))
                   for start in (1020, 1050)]
        events = [{**r.as_booking(), 'isReservation': True} for r in (old, existing)]
        chosen = self.select(choices, events=events,
            time_preferences=dict(enabled=True, start_hour=12, end_hour=20),
            planning=replace(self.planning, preferred_rest_minutes=30))
        self.assertEqual(chosen[0].replacement.start, 1050)
        self.assertEqual(chosen[0].replacement.duration, 60)

    def test_comfort_cannot_replace_a_better_room_upgrade(self):
        old = Reservation(1, self.day, 'Fallback', 1020, 1080)
        existing = Reservation(2, self.day, 'Corus', 960, 1020)
        choices = [RoomUpgrade(old, Reservation(1, self.day, 'Weston', 1020, 1080)),
                   RoomUpgrade(old, Reservation(1, self.day, 'Corus', 1080, 1140))]
        chosen = self.select(choices,
            events=[{**r.as_booking(), 'isReservation': True} for r in (old, existing)],
            time_preferences=dict(enabled=True, start_hour=12, end_hour=20),
            planning=replace(self.planning, preferred_rest_minutes=60))
        self.assertEqual(chosen[0].replacement.room, 'Weston')

    def test_many_variants_do_not_exceed_python_recursion_limit(self):
        candidates = self.consolidate() * 150
        selected = self.select(candidates)
        self.assertEqual(len(selected), 1)
        self.assertEqual(len(selected[0].originals), 3)

    def test_future_short_horizon_room_is_planned_across_full_window_but_cannot_be_saved_now(self):
        self.policy.horizon_minutes_for = lambda room: 2 * 1440
        old = self.originals[0]
        kwargs = dict(events=self.events, policy=self.policy, available_data=self.gaps, now=self.now,
                      planning=self.planning, time_preferences=self.prefs)
        self.assertFalse(find_room_upgrades(old, **kwargs))
        self.assertFalse(self.consolidate())
        prospects = find_upgrade_opportunities(**kwargs)
        full = next(p for p in prospects if len(p.change.originals) == 3)
        self.assertGreater(full.opens_at, self.now + timedelta(hours=7))
        self.assertEqual(full.opens_at, local_instant(self.day, 840) - timedelta(days=2) + timedelta(seconds=1))

    def test_explicit_freeze_is_respected_even_for_a_room_that_opens_later(self):
        self.policy.horizon_minutes_for = lambda room: 12 * 60
        self.assertFalse(find_upgrade_opportunities(events=self.events, policy=self.policy,
            available_data=self.gaps, now=self.now, planning=self.planning, time_preferences=self.prefs,
            freeze_minutes=24 * 60))

    def test_resulting_availability_releases_old_rooms_without_marking_replacement_free(self):
        change = next(c for c in self.consolidate() if len(c.originals) == 3)
        gaps = {r['room']: r['slots'] for r in availability_after_upgrade(self.gaps, change)}
        self.assertEqual(gaps['Weston'], [])
        self.assertEqual(gaps['Fallback'], [{'startHour': 12.0, 'endHour': 12.5}, {'startHour': 13.5, 'endHour': 14.0}])
