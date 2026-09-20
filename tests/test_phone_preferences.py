import json
from pathlib import Path
import tempfile
import unittest

from phone_preferences import read_phone_preferences, save_phone_preferences, PreferenceConflict


class PhonePreferencesTests(unittest.TestCase):
    def test_advance_controls_persist_scoped_with_revision_and_atomic_validation(self):
        before = self.path.read_bytes()
        current = read_phone_preferences(self.path)
        self.assertEqual(current['advance_quota']['distribution'], 'balanced')
        self.assertEqual(self.path.read_bytes(), before)
        self.save({'advance_quota':{'distribution':'weighted','day_weights':[2,1,0,1,1,1,1],
            'room_mode':'selected','room_order':['Corus','Weston'],'reserve_minutes':60}})
        saved = self.path.read_bytes()
        with self.assertRaises(PreferenceConflict):
            save_phone_preferences({'revision':current['revision'],'changes':{'advance_quota':{'distribution':'quality'}}},self.path)
        with self.assertRaises(ValueError):
            self.save({'advance_quota':{'room_order':[]},'practice_plan':{'default_hours':2}})
        self.assertEqual(self.path.read_bytes(),saved)
        self.assertEqual(read_phone_preferences(self.path)['advance_quota']['room_order'],['Corus','Weston'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'settings.json'
        self.path.write_text(json.dumps({'unrelated': {'keep': True}}))

    def save(self, changes):
        return save_phone_preferences({'revision': read_phone_preferences(self.path)['revision'], 'changes': changes}, self.path)

    def test_each_editor_persists_and_preserves_unrelated_values(self):
        self.save({'practice_plan': {'enabled': True, 'default_hours': 3.5}})
        self.save({'booking_days': [{'date': '2026-09-10', 'enabled': False}], 'practice_plan': {'date_overrides': [{'date': '2026-09-10', 'hours': 1.5}]}})
        self.save({'time_preferences': {'enabled': True, 'start_time': '12:30', 'end_time': '21:00', 'strict_mode': True}})
        self.save({'room_preferences': {'ordered_rooms': ['B0.29', 'Weston Gallery'], 'excluded_rooms': ['Weston Gallery']}})
        loaded = read_phone_preferences(self.path)
        self.assertEqual(loaded['practice_plan']['default_hours'], 3.5)
        self.assertEqual(loaded['practice_plan']['date_overrides'], {'2026-09-10': 1.5})
        self.assertEqual(loaded['disabled_dates'], ['2026-09-10'])
        self.assertEqual(loaded['time_preferences']['start_time'], '12:30')
        self.assertTrue(loaded['time_preferences']['strict_mode'])
        self.assertEqual(loaded['room_preferences']['ordered_rooms'], ['B0.29', 'Weston Gallery'])
        self.assertEqual(json.loads(self.path.read_text())['unrelated'], {'keep': True})
        self.save({'booking_days': [{'date': '2026-09-10', 'enabled': True}], 'practice_plan': {'date_overrides': [{'date': '2026-09-10', 'hours': None}]}})
        self.assertEqual(read_phone_preferences(self.path)['disabled_dates'], [])
        self.assertEqual(read_phone_preferences(self.path)['practice_plan']['date_overrides'], {})

    def test_stale_form_cannot_overwrite_a_newer_save(self):
        old = read_phone_preferences(self.path)
        self.save({'practice_plan': {'default_hours': 4}})
        with self.assertRaises(PreferenceConflict):
            save_phone_preferences({'revision': old['revision'], 'changes': {'practice_plan': {'default_hours': 1}}}, self.path)
        self.assertEqual(read_phone_preferences(self.path)['practice_plan']['default_hours'], 4)

    def test_invalid_multi_section_save_is_atomic(self):
        before = self.path.read_bytes()
        with self.assertRaises(Exception):
            self.save({'practice_plan': {'default_hours': 4}, 'time_preferences': {'start_time': '20:00', 'end_time': '10:00'}})
        self.assertEqual(before, self.path.read_bytes())
        for changes in ({'shell': 'anything'}, {'practice_plan': {'default_hours': 99}}, {'room_preferences': {'ordered_rooms': ['B0.29', 'B0.29']}}, {}):
            with self.subTest(changes=changes), self.assertRaises(Exception):
                self.save(changes)
            self.assertEqual(before, self.path.read_bytes())

    def test_read_is_sanitized_and_does_not_write_defaults(self):
        before = self.path.read_bytes()
        document = read_phone_preferences(self.path)
        self.assertNotIn('unrelated', document)
        self.assertEqual(before, self.path.read_bytes())


if __name__ == '__main__':
    unittest.main()
