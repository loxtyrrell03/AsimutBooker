import copy
import tempfile
import unittest
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from booking_strategy import DailyPlanningPreferences
import progressive_discovery as discovery
import progressive_state as state
from room_upgrades import Reservation, find_upgrade_opportunities, local_instant


class PartialDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 9, 21)
        self.now = local_instant(self.day, 750) - timedelta(days=5) + timedelta(seconds=1)
        self.originals = (Reservation(42, self.day, 'Fallback', 720, 840),)
        self.gaps = [{'room': 'Best', 'slots': [{'startHour': 12, 'endHour': 12.5}]}]
        self.policy = SimpleNamespace(room_order=('Best', 'Better', 'Fallback'),
            minimum_block_minutes=30, site_maximum_booking_minutes=120,
            booking_horizon=self.now + timedelta(days=7), site_clock_offset_bounds=(-1, 1),
            horizon_minutes_for=lambda room: (5 if room == 'Best' else 7) * 1440,
            booking_dates=lambda today: tuple(today + timedelta(days=i) for i in range(8)))
        self.settings = {}
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'plans.json'
        self.patch = mock.patch.object(state, 'STATE_FILE', self.path)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def args(self, **overrides):
        args = dict(events=[{**r.as_booking(), 'isReservation': True} for r in self.originals],
            available_data=self.gaps, policy=self.policy, now=self.now,
            eligible_event_ids={r.event_id for r in self.originals},
            time_preferences={'enabled': True, 'strict_mode': True, 'start_hour': 12, 'end_hour': 16},
            planning=DailyPlanningPreferences())
        args.update(overrides)
        return args

    def discover(self, **overrides):
        return discovery.find_partial_upgrade_opportunities(**self.args(**overrides))

    def remember(self, prospects):
        return state.remember_opportunities(prospects, settings=self.settings, policy=self.policy,
                                           now=self.now, checked_dates={str(self.day)})

    def test_sparse_minimum_gap_is_discovered_without_any_full_session_gap(self):
        self.assertEqual(find_upgrade_opportunities(**self.args()), ())
        prospect, = self.discover()
        self.assertEqual(prospect.change.replacement.start, 720)
        self.assertEqual(prospect.change.replacement.end, 840)
        self.assertEqual(prospect.first_step.replacement.end, 750)
        self.assertEqual(prospect.first_step.remaining, (replace(self.originals[0], start=750),))
        self.assertEqual(prospect.opens_at, self.now)
        saved = self.remember((prospect,))['plans']
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['next_at'], self.now.isoformat())
        self.assertIsNone(saved[0]['seed'])

    def test_sparse_gap_inside_full_horizon_survives_cache_and_whole_ready_does_not(self):
        self.now = local_instant(self.day, 840) - timedelta(days=5) + timedelta(seconds=1)
        prospect, = self.discover()
        saved = self.remember((prospect,))['plans']
        self.assertEqual(len(saved), 1)
        self.assertLess(prospect.opens_at, self.now)
        # Merely adding a similarly named field to ordinary/unchecked evidence
        # must not opt it into the partial-only cache exception.
        fake = SimpleNamespace(change=prospect.change, first_step=prospect.first_step, opens_at=prospect.opens_at)
        self.assertEqual(self.remember((fake,))['plans'], [])

    def test_full_free_room_gap_retains_existing_discovery_and_atomic_upgrade_path(self):
        self.gaps[0]['slots'] = [{'startHour': 12, 'endHour': 14}]
        self.assertFalse(any(p.change.replacement.start == 720 for p in self.discover()))
        self.assertTrue(find_upgrade_opportunities(**self.args()))

    def test_two_minimum_fragments_can_supply_a_useful_partial_consolidation(self):
        self.originals = (replace(self.originals[0], end=750),
                          Reservation(43, self.day, 'Better', 750, 780))
        prospect, = self.discover()
        self.assertEqual(len(prospect.change.originals), 2)
        self.assertEqual(prospect.change.replacement.duration, 60)
        self.assertEqual(prospect.first_step.remaining, (self.originals[1],))
        self.assertEqual(prospect.first_step.adjustments, ((self.originals[0], None),))

    def test_single_minimum_source_and_fifteen_minute_tail_cannot_be_split(self):
        for end in (750, 765):
            self.originals = (replace(self.originals[0], end=end),)
            with self.subTest(duration=end - 720):
                self.assertEqual(self.discover(), ())

    def test_larger_sparse_gap_never_creates_a_fifteen_minute_fallback(self):
        self.now = local_instant(self.day, 825) - timedelta(days=5) + timedelta(seconds=1)
        self.gaps[0]['slots'] = [{'startHour': 12, 'endHour': 13.75}]
        prospects = self.discover()
        same_start = next(p for p in prospects if p.change.replacement.start == 720)
        self.assertEqual(same_start.first_step.replacement.end, 810)
        self.assertEqual(same_start.first_step.remaining[0].duration, 30)

    def test_shifted_sparse_start_preserves_total_time_and_preference(self):
        self.originals = (replace(self.originals[0], start=750, end=870),)
        prospect, = self.discover()
        self.assertEqual(prospect.first_step.remaining, (replace(self.originals[0], end=840),))
        self.assertEqual(sum(r.duration for r in prospect.first_step.remaining)
                         + prospect.first_step.replacement.duration, 120)

    def test_unknown_clock_conflicts_blackouts_and_protected_sources_are_rejected(self):
        events = self.args()['events']
        lesson = {'eventId': 50, 'date': str(self.day), 'room': 'Class', 'startTime': '12:00',
                  'endTime': '12:30', 'isReservation': False}
        self.assertEqual(self.discover(events=[*events, lesson]), ())
        self.assertEqual(self.discover(blocked_intervals=((720, 750),)), ())
        self.assertEqual(self.discover(ignored_event_ids=(42,)), ())
        self.assertEqual(self.discover(protected_extensions=({**self.originals[0].as_booking(), 'target_end': '14:30'},)), ())
        self.policy.site_clock_offset_bounds = None
        self.assertEqual(self.discover(), ())

    def test_grid_cannot_be_reused_for_unrelated_dates(self):
        other = replace(self.originals[0], event_id=43, day=self.day + timedelta(days=1))
        events = [*self.args()['events'], {**other.as_booking(), 'isReservation': True}]
        with self.assertRaises(ValueError):
            self.discover(events=events, eligible_event_ids=None)
        self.assertTrue(self.discover(events=events))  # exact date's allowed IDs retain the correct scope

    def test_bounded_target_queue_prefers_higher_ranked_rooms_and_keeps_inputs_unchanged(self):
        rooms = tuple(f'Room {index:02}' for index in range(31))
        self.policy.room_order = rooms
        self.originals = (replace(self.originals[0], room=rooms[-1]),)
        self.gaps = [{'room': room, 'slots': [{'startHour': 12, 'endHour': 12.5}]} for room in rooms[:-1]]
        self.policy.horizon_minutes_for = lambda room: (7 if room == rooms[-1] else 5) * 1440
        args = self.args()
        before = copy.deepcopy((args['events'], args['available_data']))
        with mock.patch.object(discovery, 'MAX_PARTIAL_TARGET_CHECKS', 3), mock.patch.object(
                discovery, 'plan_progressive_transfer', wraps=discovery.plan_progressive_transfer) as checked:
            prospects = discovery.find_partial_upgrade_opportunities(**args)
        self.assertEqual(checked.call_count, 3)
        self.assertEqual(tuple(p.change.replacement.room for p in prospects), rooms[:3])
        self.assertEqual((args['events'], args['available_data']), before)

    def test_fragment_families_are_considered_with_many_possible_pairs(self):
        self.policy.room_order = ('Best', 'Better', 'Fallback', 'Third', 'Fourth')
        self.originals = tuple(Reservation(42 + i, self.day, self.policy.room_order[i + 1],
            720 + i * 30, 750 + i * 30) for i in range(4))
        prospects = self.discover()
        self.assertTrue(any(len(p.change.originals) == 3 for p in prospects))


class PartialDiscoveryDispatchTests(unittest.TestCase):
    def setUp(self):
        from tests.test_room_upgrade_runtime import UpgradeRuntimeTests
        self.fixture = UpgradeRuntimeTests('test_runner_refreshes_before_and_after_save_and_counts_only_verified_edits')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.tracker = self.fixture.prepare_runner()
        self.fixture.args.max_actions = None
        self.fixture.gaps = [{'room': 'Best', 'slots': [{'startHour': 14, 'endHour': 14.5}]}]

    def test_newly_discovered_sparse_gap_dispatches_once_in_the_same_run(self):
        import room_upgrade_runtime as runtime
        sequence = []
        def remember(prospects, **kwargs):
            self.assertTrue(any(isinstance(p, discovery.PartialUpgradeOpportunity) for p in prospects))
            sequence.append('remembered')
        def process(*args):
            self.assertEqual(sequence, ['remembered'])
            sequence.append('dispatched')
            return args[-2] + 2, args[-3]
        with mock.patch.object(runtime, 'remember_opportunities', side_effect=remember), \
                mock.patch('progressive_runtime.process_progressive_upgrades', side_effect=process) as dispatch:
            actions, _ = self.fixture.run_runner(self.tracker)
        self.assertEqual(actions, 2)
        dispatch.assert_called_once()
        self.assertEqual(sequence, ['remembered', 'dispatched'])
        self.fixture.edit.assert_not_called()

    def test_scoped_and_preview_runs_never_dispatch_autonomous_partial_changes(self):
        for patch in ({'only_date': str(self.fixture.day)}, {'upgrade_dry_run': True}, {'max_actions': 1}):
            original = copy.copy(self.fixture.args)
            self.fixture.args.__dict__.update(patch)
            with self.subTest(scope=patch), mock.patch('progressive_runtime.process_progressive_upgrades') as dispatch:
                self.fixture.run_runner(self.tracker)
                dispatch.assert_not_called()
            self.fixture.args = original


if __name__ == '__main__':
    unittest.main()
