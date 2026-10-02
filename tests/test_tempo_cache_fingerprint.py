"""Display cache renewal never relaxes the exact coordination Save guard."""
import contextlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app_settings import atomic_write_json
from booking_plan import (BookingPlanError, BookingPlanSnapshot, booking_plan_fingerprint,
                          read_booking_plan, write_booking_plan)
from booking_preferences_guard import (BookingPreferencesChanged, booking_preference_run,
                                       booking_save_boundary)
import tempo_coordination as tempo


NOW = datetime(2030, 1, 1, 8, tzinfo=timezone.utc)
DAY = '2030-01-02'


def document(**changes):
    value = dict(version=1, instance_id='synthetic-tempo', revision=1, enabled=True,
                 generated_at=NOW.isoformat(), valid_until=(NOW+timedelta(hours=1)).isoformat(),
                 dates=[DAY], busy=[dict(date=DAY, start='15:00', end='15:30')],
                 windows=[dict(date=DAY, start='09:00', end='11:00', rooms=['Practice A'],
                               task_id='practice', block_id='exact-block')],
                 opportunity_windows=[dict(date=DAY, start='12:00', end='14:00', rooms=[])],
                 protected_event_ids=[42, 43],
                 practice_targets=[dict(date=DAY, minutes=180, completed_minutes=30)],
                 room_upgrades=[dict(event_id=42, date=DAY, start='09:00', end='10:00',
                                     from_room='Practice A', rooms=['Practice B'])],
                 room_extensions=[dict(event_id=43, date=DAY, start='16:00', end='16:30',
                                       target_end='17:00', room='Practice A')])
    value.update(changes)
    return value


class TempoCacheFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        folder = Path(self.stack.enter_context(TemporaryDirectory()))
        self.path = folder / 'coordination.json'
        self.plan_path = folder / 'booking_plan.json'
        self.config_path = folder / 'rules.yaml'
        self.settings_path = folder / 'settings.json'
        self.now = NOW
        self.stack.enter_context(patch.object(tempo, 'SNAPSHOT_PATH', self.path))
        self.stack.enter_context(patch.object(tempo, 'RUNTIME_LOCK_PATH', folder / 'runtime.lock'))
        self.stack.enter_context(patch.object(tempo, '_now', side_effect=lambda value=None: value or self.now))

    def publish(self, value):
        return tempo.publish_snapshot(value, path=self.path, now=self.now)

    def fingerprint(self, settings=None):
        return booking_plan_fingerprint(settings or {}, config_path=self.config_path)

    def cache(self):
        plan = BookingPlanSnapshot(version=1, generated_at=NOW, valid_until=NOW+timedelta(minutes=20),
            policy_observed_at=NOW, strategy_fingerprint=self.fingerprint(), status='active',
            summary='Synthetic plan', days=())
        write_booking_plan(plan, self.plan_path)
        return plan

    def current(self):
        return read_booking_plan(self.plan_path, now=self.now, expected_strategy_fingerprint=self.fingerprint())

    def test_lease_only_renewal_keeps_cache_current_but_exact_revision_changes(self):
        self.publish(document())
        old = tempo.read_snapshot(now=self.now)
        cached = self.cache()
        self.now += timedelta(minutes=5)
        self.publish(document(revision=2, generated_at=self.now.isoformat(),
                              valid_until=(self.now+timedelta(hours=1)).isoformat()))
        renewed = tempo.read_snapshot(now=self.now)
        self.assertEqual(renewed.planning_fingerprint, old.planning_fingerprint)
        self.assertNotEqual(renewed.fingerprint, old.fingerprint)
        self.assertFalse(self.current().stale)
        self.assertEqual(self.current().snapshot, cached)

    def test_renewal_never_extends_plan_or_live_policy_evidence_ttl(self):
        self.publish(document())
        cached = self.cache()
        self.now += timedelta(minutes=19)
        self.publish(document(revision=2, generated_at=self.now.isoformat(),
                              valid_until=(self.now+timedelta(hours=1)).isoformat()))
        self.assertFalse(self.current().stale)
        self.now += timedelta(minutes=1)
        result = self.current()
        self.assertTrue(result.stale)
        self.assertIn('expired', result.reason)
        self.assertEqual(result.snapshot.policy_observed_at, cached.policy_observed_at)
        self.assertEqual(result.snapshot.valid_until, cached.valid_until)

    def test_expired_coordination_still_invalidates_an_unexpired_cache(self):
        self.publish(document(valid_until=(NOW+timedelta(minutes=5)).isoformat()))
        self.cache()
        self.now += timedelta(minutes=5)
        self.assertTrue(tempo.read_snapshot(now=self.now).problem)
        self.assertTrue(self.current().stale)

    def test_future_dated_live_document_remains_blocked_and_stale(self):
        self.publish(document())
        self.cache()
        future = self.now+timedelta(minutes=3)
        atomic_write_json(self.path, document(revision=2, generated_at=future.isoformat(),
                          valid_until=(future+timedelta(hours=1)).isoformat()))
        self.assertTrue(tempo.read_snapshot(now=self.now).problem)
        self.assertTrue(self.current().stale)

    def test_missing_corrupt_unknown_and_rolled_back_documents_preserve_blocked_scope(self):
        self.publish(document(revision=1))
        self.publish(document(revision=2))
        self.cache()
        for broken in (None, '{invalid', document(version=2, revision=3), document(revision=1)):
            with self.subTest(broken=broken):
                if broken is None:
                    self.path.unlink(missing_ok=True)
                elif isinstance(broken, str):
                    self.path.write_text(broken, encoding='utf-8')
                else:
                    atomic_write_json(self.path, broken)
                scope = tempo.read_snapshot(now=self.now)
                self.assertTrue(scope.enabled)
                self.assertTrue(scope.problem)
                self.assertFalse(scope.permits(DAY, 9, 10, 'Practice A'))
                self.assertTrue(self.current().stale)

    def test_unverifiable_accepted_document_raises_instead_of_reusing_cache(self):
        self.publish(document())
        self.cache()
        tempo._accepted_path(self.path).write_text('{invalid', encoding='utf-8')
        with self.assertRaises(BookingPlanError):
            self.fingerprint()

    def test_every_permission_family_and_ownership_remains_in_cache_identity(self):
        original = tempo.Snapshot(tempo.validate_snapshot(document()), self.path)
        cases = {
            'owner': dict(instance_id='another-owner'),
            'dates': dict(dates=[DAY, '2030-01-03']),
            'busy': dict(busy=[]),
            'windows': dict(windows=[]),
            'selected rooms': dict(windows=[dict(date=DAY, start='09:00', end='11:00', rooms=['Practice B'])]),
            'selected identity': dict(windows=[dict(date=DAY, start='09:00', end='11:00', rooms=['Practice A'], task_id='other', block_id='exact-block')]),
            'opportunities': dict(opportunity_windows=[]),
            'targets': dict(practice_targets=[dict(date=DAY, minutes=181, completed_minutes=30)]),
            'completion credit': dict(practice_targets=[dict(date=DAY, minutes=180, completed_minutes=31)]),
            'upgrades': dict(room_upgrades=[]),
            'extensions': dict(room_extensions=[]),
            'protected reservations': dict(protected_event_ids=[42, 43, 44]),
            'disabled': dict(enabled=False),
        }
        with patch.object(tempo, 'current_snapshot', return_value=original):
            baseline = self.fingerprint()
        for label, changes in cases.items():
            with self.subTest(label=label):
                changed = tempo.Snapshot(tempo.validate_snapshot(document(**changes)), self.path)
                with patch.object(tempo, 'current_snapshot', return_value=changed):
                    self.assertNotEqual(self.fingerprint(), baseline)
        # The exclusion list must not silently omit a future normalized field.
        future = replace(original, document=dict(original.document, future_permission={'enabled': True}))
        self.assertNotEqual(future.planning_fingerprint, original.planning_fingerprint)

    def test_optional_field_presence_is_preserved_without_semantic_guesswork(self):
        raw = document(opportunity_windows=[])
        original = tempo.Snapshot(tempo.validate_snapshot(raw), self.path)
        del raw['opportunity_windows']
        without = tempo.Snapshot(tempo.validate_snapshot(raw), self.path)
        self.assertNotEqual(original.planning_fingerprint, without.planning_fingerprint)

    def test_preferences_and_advanced_rules_still_invalidate_cache_identity(self):
        self.publish(document())
        fingerprint = self.fingerprint({'practice_plan': {'default_hours': 3}})
        self.assertNotEqual(self.fingerprint({'practice_plan': {'default_hours': 4}}), fingerprint)
        self.config_path.write_text('changed-rules', encoding='utf-8')
        self.assertNotEqual(self.fingerprint({'practice_plan': {'default_hours': 3}}), fingerprint)

    def test_same_permission_renewal_still_refuses_inflight_save_through_production_guard(self):
        self.publish(document())
        atomic_write_json(self.settings_path, {})
        with booking_preference_run(self.settings_path, {}):
            pinned = tempo.current_snapshot()
            cached_fingerprint = self.fingerprint()
            self.now += timedelta(minutes=1)
            self.publish(document(revision=2, generated_at=self.now.isoformat(),
                                  valid_until=(self.now+timedelta(hours=1)).isoformat()))
            self.assertEqual(self.fingerprint(), cached_fingerprint)
            self.assertNotEqual(tempo.read_snapshot(now=self.now).fingerprint, pinned.fingerprint)
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary(
                    day=DAY, start='09:00', end='10:00', room='Practice A'):
                self.fail('A lease renewal must still reject the pinned run at final Save')

    def test_real_permission_change_during_run_still_stales_cache_after_run(self):
        self.publish(document())
        self.cache()
        with tempo.coordination_run(now=self.now):
            pinned = self.fingerprint()
            self.publish(document(revision=2, windows=[]))
            self.assertEqual(self.fingerprint(), pinned)
        self.assertTrue(self.current().stale)

    def test_legacy_revision_hash_requires_one_normal_refresh_without_migrating_artifact(self):
        self.publish(document())
        plan = self.cache()
        # Existing caches have the previous full-document hash. They stay
        # stale until the ordinary worker publishes new observed evidence.
        with patch.object(tempo.Snapshot, 'planning_fingerprint', property(lambda scope: scope.fingerprint)):
            previous_fingerprint = self.fingerprint()
        self.assertNotEqual(plan.strategy_fingerprint, previous_fingerprint)
        old = replace(plan, strategy_fingerprint=previous_fingerprint)
        write_booking_plan(old, self.plan_path)
        before = self.plan_path.read_bytes()
        self.assertTrue(self.current().stale)
        self.assertEqual(self.plan_path.read_bytes(), before)
