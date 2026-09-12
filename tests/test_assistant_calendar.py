import unittest
from unittest.mock import patch
from datetime import date, timedelta

from assistant_calendar import WEEKDAYS, calendar_day, validate_cancellation_weekdays
from assistant_context import build_assistant_context
from assistant_tools import AssistantToolError
from tests import test_assistant_tools as fixtures


class CalendarConstraintTests(unittest.TestCase):
    def test_each_named_weekday_rejects_every_other_weekday(self):
        monday = date(2026, 9, 14)
        for index, name in enumerate(WEEKDAYS):
            for offset in range(7):
                with self.subTest(request=name, target=offset):
                    target = {'date': (monday + timedelta(days=offset)).isoformat()}
                    if offset == index:
                        validate_cancellation_weekdays(f'Cancel my {name} noon booking', [target])
                    else:
                        with self.assertRaisesRegex(ValueError, 'Cancellation blocked'):
                            validate_cancellation_weekdays(f'Cancel my {name} noon booking', [target])

    def test_lists_exclusions_ranges_and_calendar_rollovers(self):
        for request, allowed, denied in [
            ('Cancel Wednesday and Friday', '2026-09-18', '2026-09-17'),
            ('Cancel everything except Friday', '2026-09-17', '2026-09-18'),
            ('Cancel Wednesday but not Friday', '2026-09-16', '2026-09-18'),
            ('Cancel Friday-Monday', '2026-09-20', '2026-09-22'),
            ('Cancel my Sun booking', '2027-01-03', '2027-01-04'),
            ('Cancel Sunday 14 September', '2026-09-13', '2026-09-14'),
        ]:
            with self.subTest(request=request):
                validate_cancellation_weekdays(request, [{'date': allowed}])
                with self.assertRaises(ValueError):
                    validate_cancellation_weekdays(request, [{'date': denied}])
        # These are boundary requests, not restrictions to one weekday.
        for request in ['Cancel from Monday until 20 September', 'Cancel the week of Monday 14 September']:
            validate_cancellation_weekdays(request, [{'date': '2026-09-16'}])
        self.assertEqual(calendar_day('2028-02-29')['weekday'], 'Tuesday')

    def test_plural_negative_and_preserved_days_are_not_authorized(self):
        for request, allowed, denied in [
            ('Cancel my Sundays', '2026-09-13', '2026-09-14'),
            ('Cancel Wednesday, not Friday', '2026-09-16', '2026-09-18'),
            ('Cancel Friday but keep Wednesday', '2026-09-18', '2026-09-16'),
            ('Cancel Wednesday through Friday except Thursday', '2026-09-18', '2026-09-17'),
            ('Cancel from Monday onward except Friday', '2026-09-17', '2026-09-18'),
            ("Don't cancel Wednesday; cancel Friday", '2026-09-18', '2026-09-16'),
            ('The example says "cancel Wednesday". Cancel Friday only.', '2026-09-18', '2026-09-16'),
        ]:
            with self.subTest(request=request):
                validate_cancellation_weekdays(request, [{'date': allowed}])
                with self.assertRaises(ValueError):
                    validate_cancellation_weekdays(request, [{'date': denied}])

    def test_finer_exclusion_does_not_prohibit_the_entire_weekday(self):
        # This veto only checks weekdays; date/time selection has a separate role.
        for request, target in [
            ('Cancel this Wednesday but not next Wednesday', '2026-09-16'),
            ('Cancel Wednesday this week, not Wednesday next week', '2026-09-16'),
            ('Cancel all bookings except Friday at noon', '2026-09-18'),
            ('Cancel all bookings except Friday afternoon', '2026-09-18'),
        ]:
            with self.subTest(request=request):
                validate_cancellation_weekdays(request, [{'date': target, 'start_time': '09:00'}])


class CancellationWeekdayIntegrationTests(unittest.TestCase):
    setUp = fixtures.AssistantToolSurfaceTests.setUp
    tearDown = fixtures.AssistantToolSurfaceTests.tearDown
    _publish = fixtures.AssistantToolSurfaceTests._publish
    _reservation = staticmethod(fixtures.AssistantToolSurfaceTests._reservation)

    def test_real_cancellation_handler_blocks_wrong_day_for_date_and_id_selections(self):
        for selection in ({'date': '2026-09-14', 'start_time': '12:00'}, {'event_ids': [9876]}):
            with self.subTest(selection=selection):
                self.surface.begin_turn()
                self._publish([self._reservation(9876, '2026-09-14', '12:00', '14:00')])
                selected = self.surface.dispatch('find_reservations', selection)
                self.assertEqual(selected['matches'][0]['weekday'], 'Monday')
                before = self.paths.settings.read_bytes()
                request = 'cancel my sunday 12 pm booking'
                with self.assertRaisesRegex(AssistantToolError, '2026-09-14 is Monday'):
                    self.surface.dispatch('cancel_reservations', {
                        'selection_id': selected['selection_id'], 'request_quote': request,
                    }, user_request=request)
                self.assertEqual(self.commands, [])
                self.assertEqual(self.paths.settings.read_bytes(), before)

    def test_calendar_includes_empty_sunday_and_labels_monday_event(self):
        self._publish([self._reservation(9875, '2026-09-12'), self._reservation(9876, '2026-09-14')])
        context = build_assistant_context(['agenda'], paths=self.paths)['sections']['agenda']
        self.assertIn({'date': '2026-09-13', 'weekday': 'Sunday'}, context['calendar_dates'])
        self.assertEqual(context['events'][-1]['weekday'], 'Monday')
        result = self.surface.dispatch('find_reservations', {'date': '2026-09-13', 'start_time': '12:00'})
        self.assertTrue(result['fresh'])
        self.assertEqual(result['matches'], [])
        self.assertIsNone(result['selection_id'])

    def test_agenda_carries_closures_and_survives_an_unreadable_catalog(self):
        self._publish([self._reservation(9876, '2026-09-14')])
        with patch('assistant_context.closed_practice_dates', return_value=('2026-09-13',)):
            agenda = build_assistant_context(['agenda'], paths=self.paths)['sections']['agenda']
        self.assertEqual(agenda['practice_room_closures']['closed_dates'], ['2026-09-13'])
        with patch('assistant_context.load_cached_catalog', side_effect=ValueError('bad cache')):
            agenda = build_assistant_context(['agenda'], paths=self.paths)['sections']['agenda']
        self.assertTrue(agenda['available'])
        self.assertTrue(agenda['practice_room_closures']['unavailable'])
        self.assertEqual(agenda['practice_room_closures']['closed_dates'], [])

    def test_mixed_batch_is_blocked_before_cancelling_even_a_matching_first_item(self):
        self._publish([self._reservation(9875, '2026-09-13'), self._reservation(9876, '2026-09-14')])
        selected = self.surface.dispatch('find_reservations', {'event_ids': [9875, 9876]})
        request = 'Cancel my Sunday bookings'
        with self.assertRaisesRegex(AssistantToolError, 'Monday'):
            self.surface.dispatch('cancel_reservations', {
                'selection_id': selected['selection_id'], 'request_quote': request,
            }, user_request=request)
        self.assertEqual(self.commands, [])


if __name__ == '__main__':
    unittest.main()
