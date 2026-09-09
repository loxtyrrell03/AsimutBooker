import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import book_week
import phone_server
from agenda_snapshot import apply_verified_reservation, publish_agenda_snapshot, read_agenda_snapshot


class ConfirmedBookingPublicationTests(unittest.TestCase):
    def setUp(self):
        self.receipt = dict(id='confirmed-test', kind='extension', status='verified',
                            room='Weston Gallery', date='2026-09-16', start='12:00', end='14:00',
                            event_url='https://rwcmd.asimut.net/arrangement?eventId=12345')

    def test_extension_updates_exact_event_and_preserves_full_scan_age(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'agenda.json'
            observed = datetime(2026, 9, 9, 13, 13, tzinfo=timezone.utc)
            events = [dict(date='2026-09-16', startTime='12:00', endTime='13:45',
                           title='Reservation', isReservation=True, room='Weston Gallery', eventId=12345),
                      dict(date='2026-09-16', startTime='15:00', endTime='16:00',
                           title='Class', isReservation=False, room=None, eventId=67890)]
            publish_agenda_snapshot(events, [date(2026, 9, 16)], observed_at=observed, path=path)
            self.assertTrue(apply_verified_reservation(self.receipt, path=path))
            result = read_agenda_snapshot(path).snapshot
            self.assertEqual(result.events[0].end_time, '14:00')
            self.assertEqual(result.events[1].title, 'Class')
            self.assertEqual(result.observed_at, observed)
            original = path.read_bytes()
            self.assertFalse(apply_verified_reservation({**self.receipt, 'room': 'Wrong room'}, path=path))
            self.assertFalse(apply_verified_reservation({**self.receipt, 'status': 'pending'}, path=path))
            self.assertEqual(path.read_bytes(), original)

    def test_verified_publication_precedes_history_and_never_duplicates_notification(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(book_week, '_mark_mutation_verified', return_value=self.receipt), \
             mock.patch.object(book_week, 'apply_verified_reservation') as publish, \
             mock.patch.object(book_week, 'send_notification') as notify, \
             mock.patch.object(book_week, '_published_receipts', set()), \
             mock.patch.object(book_week, '_notified_booking_details', set()), \
             mock.patch.object(book_week, 'history_file', Path(directory) / 'history.json'):
            book_week.verify_mutation_receipt(self.receipt['id'])
            publish.assert_called_once_with(self.receipt)
            notify.assert_called_once()
            self.assertIn('12:00-14:00', notify.call_args.args[1])
            book_week.verify_mutation_receipt(self.receipt['id'])
            book_week.save_history(1, 2, ['EXTENDED: Weston Gallery 2026-09-16 12:00-14:00'])
            notify.assert_called_once()

    def test_create_is_published_and_unverified_mutation_is_not(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'agenda.json'
            publish_agenda_snapshot([], [date(2026, 9, 16)], path=path)
            self.assertTrue(apply_verified_reservation({**self.receipt, 'kind': 'create'}, path=path))
            self.assertEqual(read_agenda_snapshot(path).snapshot.events[0].event_id, 12345)
        with mock.patch.object(book_week, '_mark_mutation_verified', side_effect=book_week.MutationReceiptError('unverified')), \
             mock.patch.object(book_week, 'apply_verified_reservation') as publish, \
             mock.patch.object(book_week, 'send_notification') as notify:
            with self.assertRaises(book_week.MutationReceiptError):
                book_week.verify_mutation_receipt('unverified')
            publish.assert_not_called()
            notify.assert_not_called()

    def test_display_failure_does_not_hide_verified_success_or_notification(self):
        with mock.patch.object(book_week, '_mark_mutation_verified', return_value=self.receipt), \
             mock.patch.object(book_week, 'apply_verified_reservation', side_effect=OSError('disk')), \
             mock.patch.object(book_week, 'send_notification') as notify, \
             mock.patch.object(book_week, '_published_receipts', set()), \
             mock.patch.object(book_week, '_notified_booking_details', set()):
            self.assertEqual(book_week.verify_mutation_receipt(self.receipt['id']), self.receipt)
            notify.assert_called_once()

    def test_external_cache_change_signals_existing_phone_stream(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(phone_server, 'APP_DIR', Path(directory)):
            data = Path(directory) / 'data'
            data.mkdir()
            service = phone_server.PhoneAssistantService(state_path=data / 'assistant.json')
            try:
                service.publish_booker_changes()
                cursor = service.events.cursor
                (data / 'agenda_snapshot.json').write_text('{}')
                service.publish_booker_changes()
                self.assertGreater(service.events.cursor, cursor)
                unchanged = service.events.cursor
                service.publish_booker_changes()
                self.assertEqual(service.events.cursor, unchanged)
            finally:
                service.close()
