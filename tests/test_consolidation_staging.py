import copy
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import book_week as b
import mutation_receipts as journal
import consolidation_staging as staging
from booking_strategy import DailyPlanningPreferences
from room_upgrades import (Reservation, RoomUpgrade, RoomConsolidation, apply_upgrade_to_events,
                          classify_consolidation_outcome, consolidation_from_receipt)


def record(r):
    return dict(event_id=r.event_id, room=r.room, date=str(r.day),
                start=r.as_booking()['startTime'], end=r.as_booking()['endTime'])


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.day = date(2099, 1, 1)
        self.anchor = Reservation(1, self.day, 'Weston', 960, 1020)
        self.donor = Reservation(2, self.day, 'Fallback', 1020, 1080)
        self.new = Reservation(1, self.day, 'Weston', 960, 1080)
        self.change = RoomConsolidation((self.anchor, self.donor), self.new)
        self.bridge = RoomUpgrade(self.donor, Reservation(2, self.day, 'Corus', 1080, 1140))
        self.staged = replace(self.change, bridges=(self.bridge,))
        self.events = [{**r.as_booking(), 'isReservation': True} for r in self.change.originals]
        now = datetime(2098, 12, 29, tzinfo=timezone.utc)
        self.policy = SimpleNamespace(room_order=('Weston', 'Corus', 'Fallback'),
            minimum_block_minutes=30, site_maximum_booking_minutes=120,
            booking_horizon=now + timedelta(days=7), site_clock_offset_bounds=(-1, 1),
            horizon_minutes_for=lambda room: 7 * 1440,
            booking_dates=lambda today: tuple(today + timedelta(days=i) for i in range(8)))
        self.args = dict(events=self.events, available_data=[dict(room='Weston', slots=[dict(startHour=17, endHour=20)]),
                dict(room='Corus', slots=[dict(startHour=18, endHour=20)])], policy=self.policy, now=now,
            planning=DailyPlanningPreferences(upgrade_freeze_hours=0),
            time_preferences=dict(enabled=True, strict_mode=False, start_hour=12, end_hour=18))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'receipts.json'
        self.receipt = journal.record_pending_consolidation(room=self.new.room, booking_date=str(self.day),
            start='16:00', end='18:00', event_url=self.new.event_url, original=record(self.anchor),
            originals=[record(r) for r in self.change.originals],
            bridges=[dict(original=record(self.donor), replacement=record(self.bridge.replacement))], path=self.path)

    def test_overlapping_donor_moves_to_better_room_without_losing_minutes(self):
        chosen = staging.prepare_consolidation(self.change, **self.args)
        self.assertEqual(chosen.replacement, self.staged.replacement)
        self.assertEqual(chosen.bridges[0].replacement, self.bridge.replacement)
        interim = apply_upgrade_to_events(self.events, chosen.bridges[0])
        self.assertEqual(sum(Reservation.from_event(e).duration for e in interim), 120)
        self.assertEqual(chosen.action_count, 3)
        self.assertEqual(chosen.prepared.originals, (self.anchor, self.bridge.replacement))

    def test_strict_window_blocks_outside_staging(self):
        self.args['time_preferences']['strict_mode'] = True
        self.assertIsNone(staging.prepare_consolidation(self.change, **self.args))

    def test_rejected_intermediate_time_tries_another_available_time(self):
        first = staging.prepare_consolidation(self.change, **self.args)
        alternative = staging.prepare_consolidation(self.change,
            excluded_steps={(first.bridges[0].original, first.bridges[0].replacement)}, **self.args)
        self.assertEqual(alternative.replacement, first.replacement)
        self.assertNotEqual(alternative.bridges[0].replacement, first.bridges[0].replacement)

    def test_three_peak_fragments_clear_overlap_and_temporary_peak_quota(self):
        originals = (Reservation(1, self.day, 'Fallback', 720, 750),
                     Reservation(2, self.day, 'Corus', 750, 810),
                     Reservation(3, self.day, 'Fallback', 810, 840))
        change = RoomConsolidation(originals, Reservation(2, self.day, 'Weston', 720, 840))
        args = {**self.args, 'events': [{**r.as_booking(), 'isReservation': True} for r in originals],
                'available_data': [dict(room='Corus', slots=[dict(startHour=16, endHour=20)]),
                                   dict(room='Weston', slots=[dict(startHour=12, endHour=14)])]}
        chosen = staging.prepare_consolidation(change, **args)
        self.assertEqual(len(chosen.bridges), 2)
        self.assertTrue(all(b.replacement.start >= 960 for b in chosen.bridges))
        first, second = sorted((b.replacement for b in chosen.bridges), key=lambda r: r.start)
        self.assertGreaterEqual(second.start - first.end, 60)
        self.assertEqual(chosen.action_count, 5)

    def test_conflict_blackout_short_horizon_and_no_better_room_block_staging(self):
        for patch in (dict(blocked_intervals=((1080, 1200),)),
                      dict(available_data=self.args['available_data'][:1]),
                      dict(events=[*self.events, dict(eventId=9, date=str(self.day), room=None,
                           startTime='18:00', endTime='20:00', isReservation=False)])):
            with self.subTest(patch=patch):
                self.assertIsNone(staging.prepare_consolidation(self.change, **{**self.args, **patch}))
        self.policy.horizon_minutes_for = lambda room: 30
        self.assertIsNone(staging.prepare_consolidation(self.change, **self.args))

    def test_parent_roundtrip_rejects_wrong_identity_duration_and_overlap(self):
        self.assertEqual(consolidation_from_receipt(self.receipt), self.staged)
        for patch in (dict(event_id=3), dict(end='18:30'), dict(start='17:00', end='18:00')):
            invalid = copy.deepcopy(self.receipt)
            invalid['bridges'][0]['replacement'].update(patch)
            with self.assertRaises(journal.MutationReceiptError):
                journal._validate_receipt(invalid, invalid['id'])

    def test_each_crash_state_has_exact_identity_and_never_infers_missing_anchor(self):
        self.assertEqual(classify_consolidation_outcome(self.events, self.receipt), 'untouched')
        staged = apply_upgrade_to_events(self.events, self.bridge)
        self.assertEqual(classify_consolidation_outcome(staged, self.receipt), 'staged')
        secured = [{**e, **self.new.as_booking()} if e['eventId'] == 1 else e for e in staged]
        self.assertEqual(classify_consolidation_outcome(secured, self.receipt), 'secured')
        self.assertEqual(classify_consolidation_outcome(secured[:1], self.receipt), 'complete')
        self.assertEqual(classify_consolidation_outcome(staged[1:], self.receipt), 'uncertain')
        self.assertEqual(classify_consolidation_outcome([*secured, secured[0]], self.receipt), 'uncertain')
        self.assertEqual(classify_consolidation_outcome([secured[0], self.events[1]], self.receipt), 'uncertain')

    def test_parent_only_allows_recorded_steps_and_exact_reverse(self):
        for step in (self.bridge, RoomUpgrade(self.bridge.replacement, self.bridge.original), self.staged.prepared):
            self.assertTrue(staging.transaction_allows_step(self.receipt, step))
        self.assertFalse(staging.transaction_allows_step(self.receipt, self.change))
        wrong = RoomUpgrade(self.donor, replace(self.bridge.replacement, start=1095, end=1155))
        self.assertFalse(staging.transaction_allows_step(self.receipt, wrong))

    def test_fresh_step_validation_proves_bridge_anchor_and_exact_restoration(self):
        engine = self.engine()
        page = mock.MagicMock()
        def arguments(*args, **kwargs):
            self.assertIsNotNone(kwargs['verified_tracker'])
            return {**self.args, 'events': copy.deepcopy(self.events)}
        with mock.patch.object(staging, 'fresh_staging_arguments', side_effect=arguments):
            self.assertTrue(staging.revalidate_staging_step(engine, page, self.receipt, self.bridge))
            self.events = apply_upgrade_to_events(self.events, self.bridge)
            self.assertTrue(staging.revalidate_staging_step(engine, page, self.receipt, self.staged.prepared))
            reverse = RoomUpgrade(self.bridge.replacement, self.donor)
            self.args['available_data'].append(dict(room='Fallback', slots=[dict(startHour=17, endHour=18)]))
            self.assertTrue(staging.revalidate_staging_step(engine, page, self.receipt, reverse, restoring=True))
            self.args['available_data'] = []
            self.assertFalse(staging.revalidate_staging_step(engine, page, self.receipt, reverse, restoring=True))

    def engine(self):
        engine = mock.MagicMock()
        engine.BookingVerificationError = b.BookingVerificationError
        engine.UpgradePreview = b.UpgradePreview
        engine.list_pending_mutation_receipts.return_value = []
        engine.record_pending_consolidation.return_value = self.receipt
        engine.verify_consolidation_state.side_effect = lambda *a, **kw: (
            classify_consolidation_outcome(self.events, self.receipt), SimpleNamespace(agenda_events=copy.deepcopy(self.events)))
        def edit(page, step, **kwargs):
            self.events = [{**e, **step.replacement.as_booking()} if e['eventId'] == step.original.event_id else e for e in self.events]
            return True
        engine.edit_reservation_room_time.side_effect = edit
        return engine

    def test_real_sequence_records_parent_before_bridge_and_keeps_donor_until_anchor_secured(self):
        engine = self.engine()
        self.assertTrue(staging.execute_staged_consolidation(engine, mock.MagicMock(), self.staged,
                        revalidate=lambda: True, dry_run=False, freeze_minutes=0))
        steps = [c.args[1] for c in engine.edit_reservation_room_time.call_args_list]
        self.assertEqual(steps, [self.bridge, self.staged.prepared])
        self.assertEqual(classify_consolidation_outcome(self.events, self.receipt), 'secured')
        engine.cancel_reservation_exact.assert_not_called()
        names = [c[0] for c in engine.mock_calls]
        self.assertLess(names.index('record_pending_consolidation'), names.index('edit_reservation_room_time'))

    def test_rejected_anchor_restores_original_donor_without_cancelling(self):
        engine = self.engine()
        original_edit = engine.edit_reservation_room_time.side_effect
        def rejected(page, step, **kwargs):
            if isinstance(step, RoomConsolidation):
                return False
            return original_edit(page, step, **kwargs)
        engine.edit_reservation_room_time.side_effect = rejected
        self.assertFalse(staging.execute_staged_consolidation(engine, mock.MagicMock(), self.staged,
                         revalidate=lambda: True, dry_run=False, freeze_minutes=0))
        self.assertEqual(classify_consolidation_outcome(self.events, self.receipt), 'untouched')
        engine.resolve_mutation_receipt.assert_called_once()
        engine.cancel_reservation_exact.assert_not_called()

    def test_lost_bridge_response_remains_pending_and_recovery_restores_once(self):
        engine = self.engine()
        original_edit = engine.edit_reservation_room_time.side_effect
        def lost(*a, **kw):
            original_edit(*a, **kw)
            raise b.BookingVerificationError('Lost response')
        engine.edit_reservation_room_time.side_effect = lost
        with self.assertRaises(b.BookingVerificationError):
            staging.execute_staged_consolidation(engine, mock.MagicMock(), self.staged,
                revalidate=lambda: True, dry_run=False, freeze_minutes=0)
        self.assertEqual(classify_consolidation_outcome(self.events, self.receipt), 'staged')
        engine.resolve_mutation_receipt.assert_not_called()
        engine.edit_reservation_room_time.reset_mock(side_effect=True)
        engine.edit_reservation_room_time.side_effect = original_edit
        self.assertFalse(staging.restore_staged_consolidation(engine, mock.MagicMock(), self.receipt))
        self.assertEqual(engine.edit_reservation_room_time.call_count, 1)
        self.assertEqual(classify_consolidation_outcome(self.events, self.receipt), 'untouched')

    def test_failed_restoration_keeps_full_intermediate_hours_and_pending_parent(self):
        engine = self.engine()
        self.events = apply_upgrade_to_events(self.events, self.bridge)
        engine.edit_reservation_room_time.side_effect = None
        engine.edit_reservation_room_time.return_value = False
        with self.assertRaises(b.BookingVerificationError):
            staging.restore_staged_consolidation(engine, mock.MagicMock(), self.receipt)
        self.assertEqual(sum(Reservation.from_event(e).duration for e in self.events), 120)
        engine.resolve_mutation_receipt.assert_not_called()
        engine.cancel_reservation_exact.assert_not_called()
