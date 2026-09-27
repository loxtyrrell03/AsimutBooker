"""The user's Save -> updating -> new plan flow, with no site access."""
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch, Mock

import gui
from agenda_snapshot import publish_agenda_snapshot
from booking_plan import BookingPlanReadResult, BookingPlanSnapshot, DayPlan, PlanCandidate
from quiet_focus import WeekEventCard


def labels(widget):
    for child in widget.winfo_children():
        if isinstance(child,tk.Label): yield child.cget('text')
        yield from labels(child)


class WeekAndSettingsTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        folder=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.settings=folder/'settings.json'
        self.settings.write_text(json.dumps({'practice_plan':{'enabled':True,'default_hours':6}}))
        self.agenda=folder/'agenda.json'
        self.stack.enter_context(patch('gui.SETTINGS_FILE',self.settings))
        self.stack.enter_context(patch('quiet_focus_gui.AGENDA_SNAPSHOT_FILE',self.agenda))
        self.kick=self.stack.enter_context(patch('preference_runs.kick'))
        self.root=tk.Tk();self.root.withdraw();self.addCleanup(self.close_app)
        with patch.object(gui.AsimutBookerGUI,'_initialize_assistant'),patch.object(gui.AsimutBookerGUI,'refresh_status'):
            self.app=gui.AsimutBookerGUI(self.root)
        self.root.update_idletasks()

    def close_app(self):
        self.app.assistant_panel.close()
        for callback in self.root.tk.call('after','info'):
            self.root.tk.call('after','cancel',callback)
        self.root.destroy()

    def saved(self): return json.loads(self.settings.read_text())

    def test_target_is_a_draft_until_save_and_cancel_restores_saved_value(self):
        app=self.app
        app._open_settings_group('Practice target')
        editor=app.settings_editors['Practice target']
        app.practice_default_hours.set('4')
        app.practice_default_spin.event_generate('<FocusOut>')
        self.root.update()
        self.assertEqual(self.saved()['practice_plan']['default_hours'],6)
        self.kick.assert_not_called()
        self.assertEqual(editor['note'].cget('text'),'Unsaved changes')
        app._show_settings_hub();app._open_settings_group('Practice target')
        self.assertEqual(app.practice_default_hours.get(),'4')
        editor['save'].invoke()
        self.assertEqual(self.saved()['practice_plan']['default_hours'],4)
        self.assertEqual(self.saved()['preference_run']['state'],'pending')
        self.kick.assert_called_once()
        self.assertTrue(editor['save'].instate(['disabled']))
        app.practice_default_hours.set('5')
        editor['cancel'].invoke()
        self.assertEqual(app.practice_default_hours.get(),'4')
        self.assertEqual(self.saved()['practice_plan']['default_hours'],4)

    def test_time_and_strategy_controls_also_require_save(self):
        app=self.app
        app._open_settings_group('Preferred time')
        app.time_prefs_enable_cb.invoke()
        app.time_prefs_dropdown.set('Custom...')
        app.custom_start_time.set('12:00');app.custom_end_time.set('Rooms closed')
        app.custom_end_time_control.event_generate('<<ComboboxSelected>>')
        self.assertNotIn('time_preferences',self.saved())
        app.settings_editors['Preferred time']['save'].invoke()
        self.assertEqual(self.saved()['time_preferences']['custom_start_hour'],12)
        self.assertEqual(self.saved()['time_preferences']['end_boundary'],'rooms_closed')
        old=app.reverse_date_order.get()
        app._open_settings_group('Booking strategy');app.reverse_date_order_cb.invoke()
        self.assertNotIn('booking_strategy',self.saved())
        app.settings_editors['Booking strategy']['cancel'].invoke()
        self.assertEqual(app.reverse_date_order.get(),old)
        app.reverse_date_order_cb.invoke()
        app.settings_editors['Booking strategy']['save'].invoke()
        self.assertEqual(self.saved()['booking_strategy']['reverse_date_order'],not old)

    def install_plan(self):
        now=datetime.now(timezone.utc);day=(datetime.now().date()+timedelta(days=1)).isoformat()
        candidate=PlanCandidate('Corus Recital Room',day,'13:00','15:00',now+timedelta(hours=1),30,120,0,'waiting','Not booked yet.')
        row=DayPlan(day,360,150,30,60,'waiting',candidate,(),(),0,'Waiting for the free window')
        snapshot=BookingPlanSnapshot(1,now,now+timedelta(minutes=5),now,'old','active','Planned',(row,))
        publish_agenda_snapshot([], [datetime.fromisoformat(day).date()],path=self.agenda)
        self.app.booking_plan_result=BookingPlanReadResult(snapshot,True,'Preferences changed')
        value=self.saved();value['practice_plan']['default_hours']=4
        value['preference_run']={'state':'running','message':'Checking'}
        self.settings.write_text(json.dumps(value))
        return snapshot

    def test_stale_plan_stays_visible_until_new_four_hour_plan_arrives(self):
        snapshot=self.install_plan();app=self.app
        app._refresh_quiet_views()
        self.assertEqual(len([w for w in app.week_panel.rows.winfo_children() if isinstance(w,WeekEventCard)]),1)
        text=list(labels(app.week_panel))
        self.assertIn('Previous plan · not confirmed',text)
        self.assertIn('Previous target: 6 hours',text)
        self.assertIn('4 hours per day',app.week_panel.plan_status.cget('text'))
        self.assertTrue(app.week_panel.refresh_button.instate(['disabled']))
        value=self.saved();value['preference_run']['state']='completed';self.settings.write_text(json.dumps(value))
        row=replace(snapshot.days[0],target_minutes=240,primary=replace(snapshot.days[0].primary,end_time='14:30',potential_minutes=90))
        app.booking_plan_result=BookingPlanReadResult(replace(snapshot,days=(row,)),False,'')
        app._refresh_quiet_views()
        text=list(labels(app.week_panel))
        self.assertIn('Daily target: 4 hours',text)
        self.assertIn('14:30',text)
        self.assertNotIn('15:00',text)
        self.assertEqual(app.week_panel.refresh_button.cget('text'),'Refresh plan')

    def test_week_button_runs_plan_only_and_exposes_failure_and_retry(self):
        app=self.app
        with patch('gui.threading.Thread') as thread:
            app.week_panel.refresh_button.invoke()
        self.assertEqual(thread.call_args.kwargs['args'],(True,('--plan-only','--wait-for-runtime-seconds','180'),'Plan refresh'))
        self.assertTrue(app._quiet_plan_refresh_active)
        app._quiet_plan_refresh_finished(1)
        app.is_running=False;app._refresh_quiet_views()
        self.assertIn('did not finish',app.week_panel.plan_status.cget('text'))
        self.assertTrue(app.week_panel.refresh_button.instate(['!disabled']))

    def test_week_polls_local_plan_files_without_starting_another_booking_run(self):
        app=self.app
        callbacks=[]
        # The actual notebook selection, file-change watch and refresh callbacks.
        with patch.object(app.root,'after',side_effect=lambda _,fn:callbacks.append(fn) or 'watch'), \
             patch.object(app,'_refresh_booking_plan_display') as refresh, \
             patch.object(app,'_refresh_quiet_views'),patch.object(app,'refresh_booking_plan') as scan:
            app.main_notebook.select(app.week_tab)
            app._watch_week_plan()
            first=refresh.call_count
            callbacks.pop()()  # No new files: no rerender or site request.
            self.assertEqual(refresh.call_count,first)
            with patch('quiet_focus_gui.Path.stat',return_value=Mock(st_mtime_ns=-1)):
                callbacks.pop()()
            self.assertEqual(refresh.call_count,first+1)
            scan.assert_not_called()
        app._week_watch_id=None

    def test_edit_day_opens_exact_date_without_calendar_and_saves_only_that_day(self):
        app=self.app
        day=(datetime.now().date()+timedelta(days=1)).isoformat()
        other=(datetime.now().date()+timedelta(days=2)).isoformat()
        value=self.saved();value['practice_plan']['date_overrides']={other:2}
        self.settings.write_text(json.dumps(value))
        app.practice_default_hours.set('5')  # An unrelated unsaved Settings draft.
        app.week_panel.update_data([],available=True,stale=False,closed_dates=[day])
        app.week_panel.day_edit_buttons[day].invoke()
        editor=app._detail_pages['dates:'+day]
        self.assertEqual(editor.owner,'week')
        controls=editor.calendar_controls
        self.assertEqual(controls['dates'].size(),1)
        controls['hours'].set('3.5');controls['mode'].set('Custom time')
        controls['start'].set('16:00');controls['end'].set('Rooms closed');controls['strict'].set(True)
        controls['enabled'].set(False)
        self.assertNotIn(day,self.saved()['practice_plan']['date_overrides'])
        controls['save'].invoke()
        from phone_preferences import read_phone_preferences
        saved=read_phone_preferences(self.settings)
        self.assertEqual(saved['practice_plan']['default_hours'],6)
        self.assertEqual(saved['practice_plan']['date_overrides'],{other:2,day:3.5})
        self.assertEqual(saved['date_time_preferences'][day],{'enabled':True,'start_time':'16:00','end_time':'rooms_closed','strict_mode':True})
        self.assertFalse(saved['time_preferences']['enabled'])
        self.assertEqual(saved['disabled_dates'],[day])
        self.assertEqual(app.practice_default_hours.get(),'5')
        self.assertEqual(app.settings_editors['Practice target']['note'].cget('text'),'Unsaved changes')
        self.kick.assert_called_once()
        controls['hours'].set('4.5')
        editor.back()
        reopened=app._edit_week_day(day)
        self.assertIs(reopened,editor)
        self.assertEqual(controls['hours'].get(),'4.5')
        editor.destroy()  # Cancel discards only this date's unsaved form.
        self.assertEqual(self.saved()['practice_plan']['date_overrides'][day],3.5)
        self.assertEqual(app.main_notebook.select(),str(app.week_tab))

    def test_day_editor_restores_default_without_changing_other_dates(self):
        app=self.app
        day=(datetime.now().date()+timedelta(days=1)).isoformat()
        value=self.saved();value['practice_plan']['date_overrides']={day:3.5}
        value['date_time_preferences']={day:{'enabled':True,'start_time':'16:00','end_time':'20:00','strict_mode':True}}
        self.settings.write_text(json.dumps(value))
        editor=app._edit_week_day(day);controls=editor.calendar_controls
        controls['hours'].set('');controls['mode'].set('Use default time')
        controls['save'].invoke()
        self.assertNotIn(day,self.saved()['practice_plan']['date_overrides'])
        self.assertNotIn(day,self.saved()['date_time_preferences'])
        editor.destroy()


if __name__=='__main__': unittest.main()
