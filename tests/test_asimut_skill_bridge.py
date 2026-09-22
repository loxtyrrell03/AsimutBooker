"""Skill bridge behavior using isolated real app validators and mock ASIMUT."""
import importlib.util
from pathlib import Path
from unittest import TestCase, mock

from app_settings import load_settings, save_settings
from assistant_tools import dynamic_tool_specs, MUTATING_TOOLS
from phone_preferences import read_phone_preferences, save_phone_preferences
from tests import test_assistant_time_edits as time_fixtures
from tests import test_assistant_tools as fixtures

SPEC = importlib.util.spec_from_file_location('skill_bridge',
    Path(__file__).resolve().parents[1] / 'skills/asimut/scripts/booker.py')
bridge_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge_module)


class SkillBridgeTests(TestCase):
    tearDown = fixtures.AssistantToolSurfaceTests.tearDown
    _publish = fixtures.AssistantToolSurfaceTests._publish
    _reservation = staticmethod(fixtures.AssistantToolSurfaceTests._reservation)
    _find_by_ids = fixtures.AssistantToolSurfaceTests._find_by_ids
    prepare = time_fixtures.AssistantTimeEditTests.prepare
    runner = time_fixtures.AssistantTimeEditTests.runner

    def setUp(self):
        fixtures.AssistantToolSurfaceTests.setUp(self)
        self.lock = mock.patch('assistant_tools.ASSISTANT_MUTATION_LOCK_FILE', self.root/'mutation.lock')
        self.lock.start()
        self.addCleanup(self.lock.stop)
        kick = mock.patch('preference_runs.kick')
        self.kick = kick.start()
        self.addCleanup(kick.stop)
        self.bridge = bridge_module.Bridge(self.surface, dynamic_tool_specs(), MUTATING_TOOLS,
            extras={'save_phone_preferences': lambda args: save_phone_preferences(args, self.paths.settings),
                    'read_phone_preferences': lambda args: read_phone_preferences(self.paths.settings)})

    def execute(self, steps, request='', **kwargs):
        return self.bridge.execute({'user_request':request, 'steps':steps}, **kwargs)

    def edit_steps(self, extra=False):
        return [{'tool':'find_reservations','name':'chosen','arguments':{'event_ids':[42,43] if extra else [42]}},
                {'tool':'edit_reservation_time','selection_from':'chosen',
                 'arguments':{'mode':'trim_start','new_start_time':'13:30'}}]

    def test_read_only_rejects_whole_batch_before_any_dispatch(self):
        with mock.patch.object(self.surface,'dispatch') as dispatch, self.assertRaises(ValueError):
            self.execute([{'tool':'get_booker_context'},
                          {'tool':'update_booker_preferences','arguments':{'practice_plan':{'default_hours':6}}}],
                         request='Set my daily goal to six hours.',read_only=True)
        dispatch.assert_not_called()
        self.assertEqual(load_settings(self.paths.settings)['practice_plan']['default_hours'],2)

    def test_mutation_without_real_request_is_rejected(self):
        with self.assertRaises(ValueError):
            self.execute([{'tool':'run_booker','arguments':{'max_actions':1}}])
        self.assertFalse(self.commands)

    def test_unknown_later_tool_prevents_earlier_write(self):
        before=load_settings(self.paths.settings)
        with self.assertRaises(ValueError):
            self.execute([{'tool':'update_booker_preferences','arguments':{'practice_plan':{'default_hours':6}}},
                          {'tool':'invented_booking_api'}],request='Set my daily goal to six hours.')
        self.assertEqual(load_settings(self.paths.settings),before)

    def test_saved_preferences_preserve_other_values_and_queue_check(self):
        request='Set my daily goal to six hours.'
        result=self.execute([{'tool':'update_booker_preferences','arguments':{'practice_plan':{'default_hours':6}}}],request)
        self.assertTrue(result['dispatch_completed'])
        saved=load_settings(self.paths.settings)
        self.assertEqual(saved['practice_plan']['default_hours'],6)
        self.assertEqual(saved['unrelated_user_value'],{'preserve':True})
        self.assertEqual(saved['preference_run']['state'],'pending')
        self.kick.assert_called_once_with(self.paths.settings)

    def test_user_text_is_bound_without_shell_interpolation(self):
        request='Set my goal to six hours. Literal text: $(whoami); `echo secret`'
        with mock.patch.object(self.surface,'dispatch',return_value={}) as dispatch:
            self.execute([{'tool':'update_booker_preferences','arguments':{'practice_plan':{'default_hours':6}}}],request)
        self.assertEqual(dispatch.call_args.args[1]['request_quote'],request)
        self.assertEqual(dispatch.call_args.kwargs['user_request'],request)

    def test_phone_revision_save_covers_rules_and_rejects_stale_retry(self):
        revision=read_phone_preferences(self.paths.settings)['revision']
        steps=[{'tool':'save_phone_preferences','arguments':{'revision':revision,'changes':{
            'booking_rules':{'preset':'custom','rolling_quota_hours':8}}}}]
        result=self.execute(steps,'Change my configured rolling allowance to eight hours.')
        self.assertTrue(result['dispatch_completed'])
        saved=load_settings(self.paths.settings)
        self.assertEqual(saved['booking_rules']['rolling_quota_hours'],8)
        self.assertFalse(self.execute(steps,'Change my configured rolling allowance to eight hours.')['dispatch_completed'])
        self.assertEqual(load_settings(self.paths.settings),saved)

    def test_find_then_trim_uses_same_selection_context_and_verified_result(self):
        self.prepare()
        self.runner()
        result=self.execute(self.edit_steps(), 'Trim my booking to start at 13:30 and keep its end.')
        self.assertTrue(result['dispatch_completed'])
        self.assertEqual(result['results'][1]['data']['status'],'verified_changed')
        self.assertEqual(self.commands[0][-2:],['--trim-start','13:30'])
        self.assertEqual(result['results'][1]['data']['requested']['end_time'],'14:45')

    def test_ambiguous_edit_does_not_choose_one_or_run_next_step(self):
        self.prepare(extra=True)
        result=self.execute(self.edit_steps(extra=True)+[{'tool':'run_booker','arguments':{'max_actions':1}}],
                            'Trim that booking to 13:30.')
        self.assertFalse(result['dispatch_completed'])
        self.assertEqual(result['stopped_at'],1)
        self.assertFalse(self.commands)

    def test_uncertain_edit_stops_batch_without_resubmitting(self):
        self.prepare()
        self.runner(persisted=False,exit_code=5)
        result=self.execute(self.edit_steps()+[{'tool':'run_booker','arguments':{'max_actions':1}}],
                            'Trim my booking to 13:30.')
        self.assertFalse(result['dispatch_completed'])
        self.assertTrue(result['results'][1]['data']['reconciliation_required'])
        self.assertEqual(len(self.commands),1)

    def test_no_booking_made_does_not_trigger_another_run(self):
        with mock.patch.object(self.surface,'dispatch',return_value={'verified_actions':0,'command':{'exit_code':0}}) as dispatch:
            result=self.execute([{'tool':'run_booker','arguments':{'max_actions':1}}]*2,'Book more practice today.')
        self.assertFalse(result['dispatch_completed'])
        dispatch.assert_called_once()

    def test_missing_selection_stops_without_mutation(self):
        self.prepare()
        steps=self.edit_steps()
        steps[0]['arguments']={'event_ids':[999]}
        self.assertFalse(self.execute(steps,'Trim my booking to 13:30.')['dispatch_completed'])
        self.assertFalse(self.commands)

    def test_read_only_settings_do_not_mutate_state(self):
        before=load_settings(self.paths.settings)
        result=self.execute([{'tool':'read_phone_preferences'}],read_only=True)
        self.assertTrue(result['dispatch_completed'])
        self.assertIn('revision',result['results'][0]['data'])
        self.assertEqual(load_settings(self.paths.settings),before)
