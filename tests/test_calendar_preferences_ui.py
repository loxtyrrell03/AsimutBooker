from datetime import date, timedelta
import json

from calendar_preferences_ui import open_calendar_preferences
from phone_preferences import read_phone_preferences, save_phone_preferences
from tests.test_desktop_settings import DesktopSettingsTests


class CalendarPreferencesTests(DesktopSettingsTests):
    def test_day_settings_save_without_changing_default_and_cancel_preserves_values(self):
        day = date.today() + timedelta(days=10)
        self.app.show_calendar_dialog()
        editor = open_calendar_preferences(self.app, [day], self.settings)
        self.root.update()
        controls = editor.calendar_controls
        controls['enabled'].set(False)
        controls['hours'].set('2.5')
        controls['mode'].set('Custom time')
        controls['start'].set('17:00')
        controls['end'].set('19:00')
        controls['strict'].set(True)
        controls['save'].invoke()
        saved = read_phone_preferences(self.settings)
        self.assertIn(day.isoformat(), saved['disabled_dates'])
        self.assertEqual(saved['practice_plan']['date_overrides'][day.isoformat()], 2.5)
        self.assertEqual(saved['date_time_preferences'][day.isoformat()]['start_time'], '17:00')
        self.assertFalse(saved['time_preferences']['enabled'])
        controls['start'].set('18:00')
        editor.destroy()
        self.assertEqual(read_phone_preferences(self.settings)['date_time_preferences'][day.isoformat()]['start_time'], '17:00')

    def test_bulk_enabled_change_preserves_individual_times_and_stale_form_refuses_save(self):
        days = [date.today() + timedelta(days=i) for i in (10, 11)]
        first = read_phone_preferences(self.settings)
        times = {day.isoformat(): {'enabled': True, 'strict_mode': True, 'start_time': f'{14+i}:00', 'end_time': '20:00'} for i, day in enumerate(days)}
        save_phone_preferences({'revision': first['revision'], 'changes': {'date_time_preferences': times}}, self.settings)
        self.app.show_calendar_dialog()
        editor = open_calendar_preferences(self.app, days, self.settings)
        self.root.update()
        controls = editor.calendar_controls
        controls['dates'].selection_set(0, 'end')
        controls['dates'].event_generate('<<ListboxSelect>>')
        self.root.update()
        controls['enabled'].set(False)
        controls['save'].invoke()
        self.assertEqual(read_phone_preferences(self.settings)['date_time_preferences'], times)
        controls['hours'].set('3')
        current = read_phone_preferences(self.settings)
        save_phone_preferences({'revision': current['revision'], 'changes': {'practice_plan': {'default_hours': 4}}}, self.settings)
        controls['save'].invoke()
        self.assertIn('another device', controls['status'].get())
        self.assertEqual(read_phone_preferences(self.settings)['practice_plan']['date_overrides'], {})
        editor.destroy()
