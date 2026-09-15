"""Paired horizon transfers with simulated remote mutations and real durable state."""
import contextlib
import copy
import io
import inspect
import json
import sys
import tempfile
import types
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

import mutation_receipts as journal
import progressive_runtime as runtime
import progressive_state as state
from app_settings import SettingsError
from booking_strategy import DailyPlanningPreferences
from progressive_browser import PreparedSeed
from progressive_planner import TransferPlan, plan_progressive_transfer as real_planner
from progressive_transactions import record, transaction_payload
from room_upgrades import Reservation
from runtime_guard import parse_confirmed_event_id
from upgrade_validation import RoomPermissionRefusal


NOW = datetime(2030, 1, 1, 12, 30, tzinfo=timezone.utc)
DAY = date(2030, 1, 6)


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz is not None else NOW.replace(tzinfo=None)


class VerificationError(RuntimeError):
    pass


class SimContext:
    """An exact agenda; writes mutate it before optional transport failures."""
    def __init__(self, records, settings=None):
        self.actual = {r.event_id: r for r in records}
        self.settings = settings or {}
        self.gaps = []
        self.practice_plan = SimpleNamespace(enabled=False)
        self.actions = 0
        self.edits = []
        self.cancels = []
        self.creates = []
        self.fail_edit = None
        self.lose_edit = None
        self.seed_mode = 'success'
        self.restore_mode = 'success'
        self.next_id = 1000
        self.page = mock.MagicMock()
        self.policy = SimpleNamespace(room_order=('Best', 'Fallback'), minimum_block_minutes=30,
            horizon_minutes_for=lambda room: 5*24*60, site_clock_offset_bounds=(0, 0))
        self.engine = SimpleNamespace(BookingVerificationError=VerificationError,
            parse_confirmed_event_id=parse_confirmed_event_id,
            list_pending_mutation_receipts=journal.list_pending,
            verify_mutation_receipt=journal.mark_verified, resolve_mutation_receipt=journal.mark_resolved,
            SAME_ROOM_GAP_MINUTES=0, PEAK_START=9, PEAK_END=17,
            load_extendable_bookings=lambda: [],
            require_live_room_policy=lambda: self.policy,
            load_disabled_dates=lambda settings: set(), is_date_disabled=lambda day, disabled: False)
        self.scan(DAY)

    def scan(self, day, **_kwargs):
        self.day = day
        self.tracker = SimpleNamespace(agenda_active_event_ids=list(self.actual),
            agenda_events=[{**r.as_booking(), 'isReservation': True} for r in self.actual.values()])
        return dict(self.actual)

    def prove(self, r):
        if self.actual.get(r.event_id) != r:
            raise VerificationError('Persisted reservation differs')

    def arguments(self, *, seed=None):
        return {'seed': seed, 'events': self.tracker.agenda_events}

    def write_edit(self, receipt, old, new, *, restoring=False):
        self.prove(old)
        self.edits.append((old, new, restoring))
        if self.fail_edit == old.event_id:
            self.fail_edit = None
            return RoomPermissionRefusal(old.room, 'You are not allowed to book this room')
        if any(r.event_id != old.event_id and r.day == new.day and r.start < new.end and new.start < r.end
               for r in self.actual.values()):
            return False
        self.actual[old.event_id] = new
        if self.lose_edit == old.event_id:
            self.lose_edit = None
            raise VerificationError('Lost edit response after mutation')
        self.actions += 1
        return True

    def write_cancel(self, receipt, r):
        self.prove(r)
        self.cancels.append(r)
        del self.actual[r.event_id]
        self.actions += 1

    def prepare(self, engine, page, desired):
        return PreparedSeed(page, desired)

    def create(self, engine, prepared, parent, *, role='seed'):
        mode = self.seed_mode if role == 'seed' else self.restore_mode
        desired = prepared.desired
        self.creates.append((desired, role))
        if mode == 'denied':
            return RoomPermissionRefusal(desired.room, 'You are not allowed to book this room')
        child = journal.record_pending_create(room=desired.room, booking_date=desired.day.isoformat(),
            start=record(desired)['start'], end=record(desired)['end'], parent_id=parent['id'], transfer_role=role)
        made = replace(desired, event_id=self.next_id)
        self.next_id += 1
        self.actual[made.event_id] = made
        if mode == 'lost':
            raise VerificationError('Lost create response after mutation')
        journal.mark_verified(child['id'], event_url=made.event_url)
        return made


class ProgressiveRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        journal_path = Path(self.tmp)/'receipts.json'
        # Production journal functions bind their default path at definition
        # time, so every alias must pass an explicit owned path.
        originals = {name: getattr(journal, name) for name in
                     ('load_journal', 'record_pending', 'list_pending', 'mark_verified', 'mark_resolved', 'mark_transfer_step')}
        def local_call(function):
            def invoke(*args, **kwargs):
                if 'path' not in inspect.signature(function).bind_partial(*args, **kwargs).arguments:
                    kwargs['path'] = journal_path
                return function(*args, **kwargs)
            return invoke
        for name, function in originals.items():
            self.stack.enter_context(mock.patch.object(journal, name, side_effect=local_call(function)))
        for name in ('record_pending', 'load_journal', 'mark_transfer_step'):
            self.stack.enter_context(mock.patch.object(runtime, name, side_effect=local_call(originals[name])))
        self.stack.enter_context(mock.patch.object(state, 'STATE_FILE', Path(self.tmp)/'plans.json'))
        self.stack.enter_context(mock.patch.object(runtime, 'datetime', FixedDatetime))
        # Transaction/recovery tests isolate the separately tested day-capacity
        # solver; every edit still mutates the actual simulated agenda.
        capacity = types.ModuleType('progressive_capacity')
        capacity.preserves_transfer_capacity = mock.Mock(return_value=True)
        self.stack.enter_context(mock.patch.dict(sys.modules, progressive_capacity=capacity))
        self.original = Reservation(42, DAY, 'Fallback', 720, 840)
        self.target = Reservation(42, DAY, 'Best', 720, 840)
        self.prefix = replace(self.target, end=750)
        self.remaining = replace(self.original, start=750)
        self.plan = TransferPlan((self.original,), self.target, self.prefix, (self.remaining,), NOW)
        self.ctx = SimContext([self.original])
        self.saved = self.saved_plan(self.plan)
        state.update_state(lambda data: data['plans'].append(copy.deepcopy(self.saved)))
        self.stack.enter_context(mock.patch.object(runtime, 'prepare_seed', side_effect=lambda *a: self.ctx.prepare(*a)))
        self.prepare_destination = self.stack.enter_context(mock.patch.object(runtime, 'prepare_transfer_destination',
            side_effect=lambda engine, page, plan: PreparedSeed(page, plan.replacement, existing_seed=plan.seed)))
        self.stack.enter_context(mock.patch.object(runtime, 'save_seed', side_effect=lambda *a, **kw: self.ctx.create(*a, **kw)))
        self.stack.enter_context(mock.patch.object(runtime, 'preflight_transfer_destination', return_value=True))
        self.fresh = self.stack.enter_context(mock.patch.object(runtime, 'plan_progressive_transfer', side_effect=lambda *a, **kw: self.plan))

    def saved_plan(self, plan):
        return dict(id=str(uuid4()), fingerprint=state.fingerprint({}), originals=[record(r) for r in plan.originals],
            target=record(plan.target), seed=record(plan.seed) if plan.seed else None,
            next_at=NOW.isoformat(), observed_at=NOW.isoformat())

    def execute(self, **limits):
        return runtime.execute_transfer(self.ctx, self.plan, self.saved, SimpleNamespace(**limits))

    def pending_parent(self):
        return next(r for r in journal.list_pending() if r['kind'] == 'transfer')

    def total(self):
        return sum(r.duration for r in self.ctx.actual.values())

    def final_plan(self):
        seed = Reservation(70, DAY, 'Best', 720, 810)
        donor = Reservation(42, DAY, 'Fallback', 810, 840)
        target = replace(seed, end=840)
        self.ctx = SimContext([seed, donor])
        self.plan = TransferPlan((donor,), target, target, (), NOW,
                                 seed_event_id=seed.event_id, seed=seed)
        self.saved = self.saved_plan(self.plan)
        state.update_state(lambda data: data.update(plans=[copy.deepcopy(self.saved)]))
        return seed, donor

    def test_first_step_preserves_all_minutes_and_persists_exact_continuation(self):
        self.assertTrue(self.execute(max_actions=4))
        self.assertEqual(self.total(), 120)
        self.assertEqual(self.ctx.actual[42], self.remaining)
        self.assertEqual(self.ctx.actions, 2)
        self.assertFalse(journal.list_pending())
        saved, = state.load_state()['plans']
        self.assertEqual(saved['originals'], [record(self.remaining)])
        self.assertEqual(saved['seed']['event_id'], 1000)
        self.assertEqual(saved['target']['event_id'], 1000)
        self.assertEqual(datetime.fromisoformat(saved['next_at']), NOW + timedelta(minutes=15))
        self.assertEqual(state.protected_event_ids(), {42, 1000})

    def test_seed_denial_restores_fallback_and_records_bounded_permission_delay(self):
        self.ctx.seed_mode = 'denied'
        self.assertFalse(self.execute())
        self.assertEqual(self.ctx.actual, {42: self.original})
        self.assertEqual(self.total(), 120)
        self.assertEqual(self.ctx.actions, 2)
        self.assertFalse(journal.list_pending())
        data = state.load_state()
        self.assertFalse(data['plans'])
        self.assertEqual(datetime.fromisoformat(data['denials'][state.denial_key('Best', DAY)]), NOW+timedelta(minutes=30))

    def test_permission_refusal_during_preparation_releases_no_fallback_time(self):
        with mock.patch.object(runtime, 'prepare_transfer_destination', return_value=RoomPermissionRefusal('Best', 'You are not allowed to book this room')):
            self.assertFalse(self.execute())
        self.assertEqual(self.ctx.actual, {42: self.original})
        self.assertFalse(self.ctx.edits)
        self.assertFalse(journal.list_pending())
        self.assertIn(state.denial_key('Best', DAY), state.load_state()['denials'])

    def test_restarts_grow_prefix_to_completion_without_losing_minutes_or_duplicating_seed(self):
        self.assertTrue(self.execute())
        created = len(self.ctx.creates)
        while state.load_state()['plans']:
            # Simulate a fresh process using only durable hints and the live
            # agenda. The real planner has its own interval/capacity tests.
            self.saved, = state.load_state()['plans']
            old_ctx = self.ctx
            self.ctx = SimContext(list(old_ctx.actual.values()))
            seed = self.ctx.actual[self.saved['seed']['event_id']]
            donor = self.ctx.actual[42]
            end = seed.end + 15 if 840 - (seed.end + 15) >= 30 else 840
            target = replace(seed, end=840)
            keep = (replace(donor, start=end),) if end < 840 else ()
            self.plan = TransferPlan((donor,), target, replace(seed, end=end), keep, NOW,
                seed_event_id=seed.event_id, seed=seed)
            self.assertTrue(self.execute())
            created += len(self.ctx.creates)
            self.assertEqual(self.total(), 120)
            self.assertFalse(journal.list_pending())
            if keep:
                self.assertGreaterEqual(self.ctx.actual[42].duration, 30)
        self.assertEqual(created, 1)
        self.assertEqual(self.ctx.actual, {1000: Reservation(1000, DAY, 'Best', 720, 840)})

    def test_crash_after_continuation_persisted_is_idempotent_on_restart(self):
        with mock.patch.object(self.ctx.engine, 'verify_mutation_receipt', side_effect=RuntimeError('disk stopped after plan write')):
            with self.assertRaisesRegex(RuntimeError, 'disk stopped'):
                self.execute()
        self.assertEqual(len(state.load_state()['plans']), 1)
        parent = self.pending_parent()
        actions, creates = self.ctx.actions, len(self.ctx.creates)
        self.assertTrue(runtime.recover_transfer(self.ctx, parent))
        self.assertEqual(len(state.load_state()['plans']), 1)
        self.assertEqual(self.ctx.actions, actions)
        self.assertEqual(len(self.ctx.creates), creates)
        self.assertFalse(journal.list_pending())
        self.assertEqual(self.total(), 120)

    def test_unrelated_agenda_booking_is_unchanged_by_completed_transfer(self):
        other = Reservation(999, DAY, 'Best', 570, 600)
        self.ctx.actual[999] = other
        self.assertTrue(self.execute())
        self.assertEqual(self.ctx.actual[999], other)
        self.assertEqual(self.total(), 150)

    def test_lost_trim_response_recovers_exact_original_without_attempting_seed(self):
        self.ctx.lose_edit = 42
        with self.assertRaisesRegex(VerificationError, 'Lost edit'):
            self.execute()
        self.assertEqual(self.ctx.actual[42], self.remaining)
        self.assertFalse(self.ctx.creates)
        self.assertFalse(runtime.recover_transfer(self.ctx, self.pending_parent()))
        self.assertEqual(self.ctx.actual[42], self.original)
        self.assertEqual(self.total(), 120)
        self.assertFalse(journal.list_pending())

    def test_lost_seed_response_blocks_restoration_until_child_proof_then_finishes(self):
        self.ctx.seed_mode = 'lost'
        with self.assertRaisesRegex(VerificationError, 'Lost create'):
            self.execute()
        parent = self.pending_parent()
        with self.assertRaisesRegex(VerificationError, 'child remains uncertain'):
            runtime.recover_transfer(self.ctx, parent)
        child, = [r for r in journal.list_pending() if r['kind'] == 'create']
        journal.mark_verified(child['id'], event_url=self.ctx.actual[1000].event_url)
        self.assertTrue(runtime.recover_transfer(self.ctx, parent))
        self.assertEqual(self.total(), 120)
        self.assertEqual(len(self.ctx.creates), 1)
        self.assertFalse(journal.list_pending())

    def test_final_step_retires_last_minimum_donor_and_finishes_full_session(self):
        _, donor = self.final_plan()
        self.assertTrue(self.execute(max_actions=4))
        self.assertEqual(self.total(), 120)
        self.assertEqual(self.ctx.cancels, [donor])
        self.assertEqual(list(self.ctx.actual), [70])
        self.assertFalse(state.load_state()['plans'])
        self.assertFalse(journal.list_pending())

    def test_denied_final_extension_recreates_retired_donor_with_verified_new_id(self):
        seed, donor = self.final_plan()
        self.ctx.fail_edit = seed.event_id
        self.assertFalse(self.execute())
        self.assertEqual(self.total(), 120)
        self.assertEqual(self.ctx.actual[70], seed)
        self.assertNotIn(donor.event_id, self.ctx.actual)
        self.assertEqual(self.ctx.actual[1000], replace(donor, event_id=1000))
        self.assertEqual(self.ctx.creates, [(donor, 'restore:42')])
        self.assertFalse(journal.list_pending())

    def test_interrupted_donor_recreation_resumes_by_receipt_without_duplicate(self):
        seed, donor = self.final_plan()
        self.ctx.fail_edit = seed.event_id
        self.ctx.restore_mode = 'lost'
        with self.assertRaisesRegex(VerificationError, 'Lost create'):
            self.execute()
        parent = self.pending_parent()
        child, = [r for r in journal.list_pending() if r['kind'] == 'create']
        with self.assertRaisesRegex(VerificationError, 'child remains uncertain'):
            runtime.recover_transfer(self.ctx, parent)
        journal.mark_verified(child['id'], event_url=self.ctx.actual[1000].event_url)
        self.assertFalse(runtime.recover_transfer(self.ctx, parent))
        self.assertEqual(self.total(), 120)
        self.assertEqual(len(self.ctx.creates), 1)
        self.assertFalse(journal.list_pending())

    def test_failed_parent_finalization_after_recreation_does_not_create_again(self):
        seed, donor = self.final_plan()
        self.ctx.fail_edit = seed.event_id
        with mock.patch.object(self.ctx.engine, 'resolve_mutation_receipt', side_effect=RuntimeError('disk stopped')):
            with self.assertRaisesRegex(RuntimeError, 'disk stopped'):
                self.execute()
        self.assertFalse(state.load_state()['plans'])
        self.assertFalse(runtime.recover_transfer(self.ctx, self.pending_parent()))
        self.assertEqual(self.total(), 120)
        self.assertEqual(self.ctx.actual[1000], replace(donor, event_id=1000))
        self.assertEqual(len(self.ctx.creates), 1)
        self.assertFalse(journal.list_pending())

    def test_similar_unjournalled_seed_never_counts_as_the_transferred_booking(self):
        t = transaction_payload(self.plan, plan_id=self.saved['id'], baseline_ids=[42], minimum_minutes=30)
        parent = journal.record_pending('transfer', room='Best', booking_date=str(DAY), start='12:00', end='12:30', transfer=t)
        journal.mark_transfer_step(parent, 'source:42')
        self.ctx.actual = {42: self.remaining, 999: replace(self.prefix, event_id=999)}
        with self.assertRaisesRegex(VerificationError, 'restoration was refused'):
            runtime.recover_transfer(self.ctx, parent)
        self.assertEqual(self.ctx.actual[999].room, 'Best')
        self.assertEqual(len(journal.list_pending()), 1)
        self.assertEqual(self.total(), 120)

    def test_wrong_external_change_to_fallback_stops_before_recovery_mutation(self):
        self.ctx.lose_edit = 42
        with self.assertRaises(VerificationError):
            self.execute()
        self.ctx.actual[42] = replace(self.remaining, end=825)
        writes = len(self.ctx.edits)
        with self.assertRaisesRegex(VerificationError, 'Fallback changed'):
            runtime.recover_transfer(self.ctx, self.pending_parent())
        self.assertEqual(len(self.ctx.edits), writes)
        self.assertEqual(len(journal.list_pending()), 1)

    def test_user_deleted_donor_before_first_attempt_is_never_automatically_recreated(self):
        seed, donor = self.final_plan()
        t = transaction_payload(self.plan, plan_id=self.saved['id'], baseline_ids=[42, 70],
                                minimum_minutes=30, seed=seed)
        parent = journal.record_pending('transfer', room='Best', booking_date=str(DAY),
            start='12:00', end='14:00', transfer=t)
        del self.ctx.actual[42]  # External cancellation, before our first write.
        with self.assertRaises(VerificationError):
            runtime.recover_transfer(self.ctx, parent)
        self.assertFalse(self.ctx.creates)
        self.assertFalse(self.ctx.edits)
        self.assertEqual(self.ctx.actual, {70: seed})
        self.assertEqual(len(journal.list_pending()), 1)

    def test_action_limit_reserves_failure_recovery_before_any_write(self):
        self.assertFalse(self.execute(max_actions=3))
        self.assertFalse(self.ctx.edits)
        self.assertFalse(self.ctx.creates)
        self.assertFalse(journal.list_pending())
        self.assertFalse(self.execute(max_actions=4, max_action_minutes=15))
        self.assertEqual(self.total(), 120)

    def test_scheduled_deadline_defers_before_preparation_or_trimming(self):
        self.ctx.engine._booker_run_started_monotonic = 0
        with mock.patch.object(runtime.time, 'monotonic', return_value=600):
            self.assertFalse(self.execute(scheduled=True))
        self.prepare_destination.assert_not_called()
        self.assertFalse(self.ctx.edits)
        self.assertFalse(journal.list_pending())

    def test_slow_preparation_cannot_consume_recovery_budget_then_release_time(self):
        self.ctx.engine._booker_run_started_monotonic = 0
        clock = [0]
        original_scan = self.ctx.scan
        def slow_scan(*args, **kwargs):
            clock[0] = 600
            return original_scan(*args, **kwargs)
        self.ctx.scan = slow_scan
        with mock.patch.object(runtime.time, 'monotonic', side_effect=lambda: clock[0]):
            self.assertFalse(self.execute(scheduled=True))
        self.prepare_destination.assert_called_once()
        self.assertFalse(self.ctx.edits)
        self.assertFalse(journal.list_pending())

    def test_existing_prefix_permission_denied_before_trim_keeps_both_reservations(self):
        seed, donor = self.final_plan()
        with mock.patch.object(runtime, 'preflight_transfer_destination',
            return_value=RoomPermissionRefusal('Best', 'You are not allowed to book this room')):
            self.assertFalse(self.execute())
        prepared = self.prepare_destination.call_args.args[2]
        self.assertEqual(prepared.seed, seed)
        self.assertEqual(self.ctx.actual, {70: seed, 42: donor})
        self.assertFalse(self.ctx.edits)
        self.assertFalse(self.ctx.cancels)
        self.assertFalse(journal.list_pending())

    def test_prep_before_boundary_does_not_trim_early(self):
        self.plan = replace(self.plan, opens_at=NOW+timedelta(seconds=90))
        self.ctx.engine.wait_until_datetime = mock.Mock()  # Time does not advance in this fixture.
        self.assertFalse(self.execute())
        self.ctx.engine.wait_until_datetime.assert_called_once()
        self.assertFalse(self.ctx.edits)
        self.assertFalse(journal.list_pending())

    def run_process(self, args=None, settings=None):
        args, settings = args or SimpleNamespace(), settings or {}
        self.ctx.scan(DAY)
        with mock.patch.object(runtime, 'LiveContext', return_value=self.ctx):
            return runtime.process_progressive_upgrades(self.ctx.engine, self.ctx.page, settings,
                SimpleNamespace(enabled=False), args, self.ctx.tracker, 0, [])

    def test_readonly_and_exact_scope_never_mutate_cached_plan(self):
        for scope in ('check_only', 'agenda_only', 'plan_only', 'horizon_only', 'extensions_only', 'upgrade_dry_run'):
            self.run_process(SimpleNamespace(**{scope: True}))
        for args in (SimpleNamespace(only_date='2030-01-07'), SimpleNamespace(only_room='Other'),
                     SimpleNamespace(upgrade_event_id=999)):
            self.run_process(args)
        self.assertFalse(self.ctx.edits)
        self.assertFalse(journal.list_pending())
        self.assertEqual(len(state.load_state()['plans']), 1)

    def test_stale_preferences_discard_speculation_without_touching_bookings(self):
        self.run_process(settings={'room_preferences': {'priority_rooms': ['Fallback', 'Best']}})
        self.assertFalse(self.ctx.edits)
        self.assertFalse(state.load_state()['plans'])
        self.assertEqual(self.total(), 120)

    def test_permission_backoff_survives_reload_and_skips_only_matching_day_room(self):
        state.remember_denial('Best', DAY, now=NOW)
        self.run_process()
        self.assertFalse(self.ctx.edits)
        self.assertIn(state.denial_key('Best', DAY), state.load_state()['denials'])
        state.update_state(lambda data: data['denials'].update({state.denial_key('Best', DAY): (NOW-timedelta(seconds=1)).isoformat()}))
        self.run_process()
        self.assertEqual(self.total(), 120)
        self.assertEqual(self.ctx.actions, 2)


class ProgressiveProcessPlannerTests(unittest.TestCase):
    """The actual saved-plan dispatcher and actual planner, with simulated I/O."""
    saved_plan = ProgressiveRuntimeTests.saved_plan
    total = ProgressiveRuntimeTests.total

    def setUp(self):
        ProgressiveRuntimeTests.setUp(self)
        self.ctx.policy.site_maximum_booking_minutes = 120
        self.ctx.policy.booking_horizon = NOW+timedelta(days=7)
        self.ctx.policy.horizon_minutes_for = lambda room: (5 if room == 'Best' else 7)*1440
        self.ctx.policy.booking_dates = lambda today: tuple(today+timedelta(days=i) for i in range(8))
        self.ctx.gaps = [{'room': 'Best', 'slots': [{'startHour': 12, 'endHour': 14}]}]
        self.others = []
        self.ignored = set()
        self.blocked = []
        self.extensions = []
        self.prefs = {'enabled': True, 'strict_mode': True, 'start_hour': 12, 'end_hour': 16}
        def scan(day, **kwargs):
            result = SimContext.scan(self.ctx, day, **kwargs)
            self.ctx.tracker.agenda_events.extend(copy.deepcopy(self.others))
            return result
        self.ctx.scan = scan
        self.ctx.arguments = lambda *, seed=None: dict(events=self.ctx.tracker.agenda_events,
            available_data=self.ctx.gaps, policy=self.ctx.policy, now=FixedDatetime.now(timezone.utc),
            time_preferences=self.prefs, planning=DailyPlanningPreferences(), seed=seed,
            ignored_event_ids=self.ignored, blocked_intervals=self.blocked, protected_extensions=self.extensions)
        self.fresh.side_effect = real_planner

    def run_process(self, *, settings=None, args=None, practice=None):
        self.ctx.scan(DAY)
        details = []
        with mock.patch.object(runtime, 'LiveContext', return_value=self.ctx):
            actions, tracker = runtime.process_progressive_upgrades(self.ctx.engine, self.ctx.page,
                settings or {}, practice or SimpleNamespace(enabled=False), args or SimpleNamespace(),
                self.ctx.tracker, 0, details)
        return actions, tracker, details

    def active_prefix(self):
        seed = Reservation(1000, DAY, 'Best', 720, 750)
        self.ctx.actual = {42: self.remaining, 1000: seed}
        self.ctx.gaps = [{'room': 'Best', 'slots': [{'startHour': 12.5, 'endHour': 14}]}]
        self.saved.update(originals=[record(self.remaining)], seed=record(seed),
                          target=record(replace(self.target, event_id=1000)))
        state.update_state(lambda data: data.update(plans=[copy.deepcopy(self.saved)]))
        return seed

    def test_real_dispatcher_selects_ready_saved_plan_and_protects_continuation(self):
        actions, _, details = self.run_process()
        self.assertEqual(actions, 2)
        self.assertEqual(self.total(), 120)
        self.assertEqual(len(details), 1)
        self.assertEqual(state.protected_event_ids(), {42, 1000})

    def test_added_lesson_invalidates_entire_conflicting_plan_before_trim(self):
        self.others = [{'eventId': 999, 'date': str(DAY), 'room': 'Lesson room',
                        'startTime': '12:00', 'endTime': '13:00', 'isReservation': False}]
        actions, _, _ = self.run_process()
        self.assertEqual(actions, 0)
        self.assertEqual(self.ctx.actual[42], self.original)
        self.assertFalse(state.load_state()['plans'])

    def test_room_closure_or_competitor_taking_the_prefix_releases_nothing(self):
        for gaps in ([], [{'room': 'Best', 'slots': [{'startHour': 12.5, 'endHour': 14}]}]):
            state.update_state(lambda data: data.update(plans=[copy.deepcopy(self.saved)]))
            self.ctx.gaps = gaps
            actions, _, _ = self.run_process()
            self.assertEqual(actions, 0)
            self.assertFalse(self.ctx.edits)
            self.assertEqual(self.total(), 120)

    def test_ignored_original_blackout_and_other_extension_ownership_block_transfer(self):
        for label in ('ignored', 'blackout', 'extension'):
            state.update_state(lambda data: data.update(plans=[copy.deepcopy(self.saved)]))
            self.ignored = {42} if label == 'ignored' else set()
            self.blocked = [(720, 750)] if label == 'blackout' else []
            self.extensions = [{**self.original.as_booking(), 'target_end': '15:00'}] if label == 'extension' else []
            actions, _, _ = self.run_process()
            self.assertEqual(actions, 0, label)
        self.assertFalse(self.ctx.edits)
        self.assertEqual(self.total(), 120)

    def test_missed_runs_take_largest_current_prefix_without_overshrinking_tail(self):
        with mock.patch(__name__+'.NOW', NOW+timedelta(minutes=45)):
            actions, _, _ = self.run_process()
        self.assertEqual(actions, 2)
        self.assertEqual(self.ctx.actual[1000].end, 795)
        self.assertEqual(self.ctx.actual[42].duration, 45)
        self.assertEqual(self.total(), 120)

    def test_missed_entire_horizon_leaves_complete_upgrade_to_existing_sweep(self):
        with mock.patch(__name__+'.NOW', NOW+timedelta(minutes=90)):
            actions, _, _ = self.run_process()
        self.assertEqual(actions, 0)
        self.assertFalse(state.load_state()['plans'])
        self.assertFalse(self.ctx.edits)

    def test_user_zero_target_disabled_day_and_strict_hour_change_keep_bookings(self):
        for kind in ('target', 'disabled', 'hours'):
            state.update_state(lambda data: data.update(plans=[copy.deepcopy(self.saved)]))
            self.ctx.engine.is_date_disabled = lambda day, disabled: kind == 'disabled'
            self.prefs = {'enabled': True, 'strict_mode': True, 'start_hour': 14 if kind == 'hours' else 12, 'end_hour': 16}
            practice = SimpleNamespace(enabled=kind == 'target', target_for=lambda day: 0)
            actions, _, _ = self.run_process(practice=practice)
            self.assertEqual(actions, 0, kind)
        self.assertFalse(self.ctx.edits)
        self.assertEqual(self.total(), 120)

    def test_teacher_blocks_later_target_but_free_nearer_prefix_still_upgrades(self):
        self.active_prefix()
        self.ctx.gaps = [{'room': 'Best', 'slots': [{'startHour': 12.5, 'endHour': 13}]}]
        with mock.patch(__name__+'.NOW', NOW+timedelta(minutes=30)):
            actions, _, _ = self.run_process()
        self.assertEqual(actions, 2)
        self.assertEqual(self.ctx.actual[1000].end, 780)
        self.assertEqual(self.ctx.actual[42].start, 780)
        self.assertEqual(self.total(), 120)

    def test_exact_anchor_event_scope_allows_its_existing_transfer_to_continue(self):
        self.active_prefix()
        with mock.patch(__name__+'.NOW', NOW+timedelta(minutes=15)):
            actions, _, _ = self.run_process(args=SimpleNamespace(upgrade_event_id=1000))
        self.assertEqual(actions, 2)
        self.assertEqual(self.ctx.actual[1000].end, 765)
        self.assertEqual(self.total(), 120)

    def test_new_permission_denial_skips_other_starts_for_same_room_in_this_run(self):
        alternative = copy.deepcopy(self.saved)
        alternative['id'] = str(uuid4())
        alternative['target'].update(start='12:15', end='14:15')
        state.update_state(lambda data: data['plans'].append(alternative))
        self.ctx.gaps = [{'room': 'Best', 'slots': [{'startHour': 12, 'endHour': 14.25}]}]
        with (mock.patch(__name__+'.NOW', NOW+timedelta(minutes=15)),
              mock.patch.object(runtime, 'prepare_transfer_destination', return_value=RoomPermissionRefusal('Best', 'You are not allowed to book this room')) as prepare):
            actions, _, _ = self.run_process()
        self.assertEqual(prepare.call_count, 1)
        self.assertEqual(actions, 0)
        self.assertEqual(self.total(), 120)


class ProgressiveStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'plans.json'
        self.patch = mock.patch.object(state, 'STATE_FILE', self.path)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.journal_path = Path(self.tmp.name)/'receipts.json'
        original_reader = journal.load_journal
        journal_patch = mock.patch.object(journal, 'load_journal',
            side_effect=lambda path=None: original_reader(path or self.journal_path))
        journal_patch.start()
        self.addCleanup(journal_patch.stop)
        self.old = Reservation(42, DAY, 'Fallback', 720, 840)
        self.target = replace(self.old, room='Best')
        self.policy = SimpleNamespace(room_order=('Best','Fallback'), minimum_block_minutes=30,
            horizon_minutes_for=lambda room: 7200, site_clock_offset_bounds=(0, 0))

    def remember(self):
        prospect = SimpleNamespace(change=SimpleNamespace(originals=(self.old,), replacement=self.target))
        return state.remember_opportunities([prospect], settings={}, policy=self.policy, now=NOW, checked_dates={str(DAY)})

    def test_opportunities_roundtrip_without_becoming_mutation_authority(self):
        self.remember()
        saved, = state.load_state()['plans']
        self.assertEqual(saved['originals'], [record(self.old)])
        self.assertEqual(saved['target'], record(self.target))
        self.assertEqual(saved['fingerprint'], state.fingerprint({}))
        self.assertFalse(state.protected_event_ids())
        self.assertEqual(datetime.fromisoformat(saved['next_at']), NOW)
        self.remember()
        self.assertEqual(len(state.load_state()['plans']), 1)

    def test_extension_runtime_updates_do_not_invalidate_preference_fingerprint(self):
        self.assertEqual(state.fingerprint({}), state.fingerprint({'extendable_bookings': [{'eventId': 42}]}))
        self.assertNotEqual(state.fingerprint({}), state.fingerprint({'disabled_dates': [str(DAY)]}))

    def test_ui_state_keeps_plan_but_every_booking_preference_invalidates_it(self):
        self.assertEqual(state.fingerprint({}), state.fingerprint({'window_geometry': '800x600', 'calendar_view': 'week'}))
        for key in ('time_preferences', 'practice_plan', 'disabled_dates', 'room_preferences',
                    'booking_strategy', 'ignored_events', 'rebooking_blackouts', 'date_time_preferences'):
            self.assertNotEqual(state.fingerprint({}), state.fingerprint({key: {}}), key)

    def test_new_scan_preserves_active_continuation_and_expires_only_old_denials(self):
        self.remember()
        def active(data):
            p, = data['plans']
            p['originals'] = [record(replace(self.old, start=750))]
            p['seed'] = record(Reservation(1000, DAY, 'Best', 720, 750))
            p['target']['event_id'] = 1000
        state.update_state(active)
        state.remember_denial('Best', DAY, now=NOW-timedelta(hours=1))
        state.remember_denial('Fallback', DAY, now=NOW)
        self.remember()
        data = state.load_state()
        self.assertEqual(len(data['plans']), 1)
        self.assertEqual(state.protected_event_ids(), {42, 1000})
        self.assertNotIn(state.denial_key('Best', DAY), data['denials'])
        self.assertIn(state.denial_key('Fallback', DAY), data['denials'])

    def test_malformed_state_is_rejected_without_overwriting_existing_file(self):
        self.remember()
        initial = self.path.read_bytes()
        for mutate in (lambda d: d['plans'][0].update(originals=[]),
                       lambda d: d['plans'].append(copy.deepcopy(d['plans'][0])),
                       lambda d: d['plans'][0].update(fingerprint='unknown'),
                       lambda d: d['plans'][0].update(next_at='2030-01-01T12:30:00'),
                       lambda d: d['denials'].update({'Best': NOW.isoformat()}),
                       lambda d: d['denials'].update({state.denial_key('Best', DAY): '2030-01-01T12:30:00'})):
            with self.assertRaises(SettingsError):
                state.update_state(mutate)
            self.assertEqual(self.path.read_bytes(), initial)
        self.path.write_text('[]', encoding='utf8')
        self.assertEqual(state.load_state(), {'version': 1, 'plans': [], 'denials': {}})
        quarantined, = self.path.parent.glob('plans.json.corrupt-*')
        self.assertEqual(quarantined.read_text(), '[]')

    def test_malformed_hints_with_pending_transfer_keep_all_evidence_and_fail_closed(self):
        plan = TransferPlan((self.old,), self.target, replace(self.target, end=750),
            (replace(self.old, start=750),), NOW)
        t = transaction_payload(plan, plan_id=str(uuid4()), baseline_ids=[42], minimum_minutes=30)
        journal.record_pending('transfer', room='Best', booking_date=str(DAY),
            start='12:00', end='12:30', transfer=t, path=self.journal_path)
        self.path.write_text('[]', encoding='utf8')
        with self.assertRaises(SettingsError):
            state.load_state()
        self.assertEqual(self.path.read_text(), '[]')
        self.assertFalse(list(self.path.parent.glob('plans.json.corrupt-*')))

    def test_malformed_hints_do_not_hide_corrupt_mutation_journal(self):
        self.path.write_text('[]', encoding='utf8')
        self.journal_path.write_text('[]', encoding='utf8')
        with self.assertRaises(SettingsError):
            state.load_state()
        self.assertEqual(self.path.read_text(), '[]')
        self.assertFalse(list(self.path.parent.glob('plans.json.corrupt-*')))

    def test_update_can_rebuild_malformed_hints_under_existing_lock(self):
        self.path.write_text('[]', encoding='utf8')
        state.remember_denial('Best', DAY, now=NOW)
        self.assertIn(state.denial_key('Best', DAY), state.load_state()['denials'])
        self.assertEqual(len(list(self.path.parent.glob('plans.json.corrupt-*'))), 1)
