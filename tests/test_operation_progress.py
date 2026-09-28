"""Non-authoritative progress cannot interrupt an owned booking operation."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app_settings import SettingsError
from operation_control import operation_stage
from phone_operation_worker import execute, main


class OperationProgressTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / 'job'
        self.replace = os.replace

    def reader_lock(self, source, target):
        if Path(target).name == 'progress.json':
            error = PermissionError(13, 'Access is denied')
            error.winerror = 5
            raise error
        return self.replace(source, target)

    def test_windows_progress_replace_failure_does_not_abort_normal_run(self):
        stages = []
        def booker(flags):
            self.assertEqual(flags, ['--headless'])
            for text in ['Reading room availability', 'Checking booking result']:
                operation_stage(text)
                stages.append(text)
            return 0
        with patch('app_settings.os.replace', side_effect=self.reader_lock):
            result = execute('run', {}, self.directory, root=self.root, booker_main=booker)
        self.assertEqual(len(stages), 2)
        self.assertEqual(result['state'], 'completed')
        self.assertIn('progress_warning', result)
        diagnostic = json.loads((self.directory / 'progress-warning.json').read_text(encoding='utf-8'))
        self.assertEqual(diagnostic['winerror'], 5)
        self.assertEqual(diagnostic['file'], 'progress.json')
        self.assertNotIn(str(self.root), json.dumps(diagnostic))
        self.assertLess((self.directory / 'progress-warning.json').stat().st_size, 1000)
        self.assertEqual(list(self.directory.glob('*.tmp')), [])

    def test_pending_booking_receipt_still_makes_result_uncertain(self):
        with patch('app_settings.os.replace', side_effect=self.reader_lock), \
                patch('mutation_receipts.list_pending', return_value=[{'id': 'pending'}]):
            result = execute('run', {}, self.directory, root=self.root, booker_main=lambda flags: 0)
        self.assertEqual(result['state'], 'uncertain')

    def test_stop_still_prevents_booking_when_progress_is_unwritable(self):
        self.directory.mkdir()
        (self.directory / 'stop').touch()
        booker = Mock()
        with patch('app_settings.os.replace', side_effect=self.reader_lock):
            result = execute('run', {}, self.directory, root=self.root, booker_main=booker)
        booker.assert_not_called()
        self.assertNotEqual(result['state'], 'completed')

    def test_authoritative_result_write_failure_still_propagates(self):
        payload = dict(request_id='aa5a7ab4-670f-4822-a446-28fbb1c2deec', action='run', args={})
        def denied_result(source, target):
            if Path(target).name == 'result.json':
                raise PermissionError(13, 'Access is denied')
            return self.replace(source, target)
        with patch('phone_operation_worker.ROOT', self.root), \
                patch('phone_operation_worker.sys.stdin', io.StringIO(json.dumps(payload))), \
                patch('phone_operation_worker.execute', return_value={'state': 'completed'}), \
                patch('app_settings.os.replace', side_effect=denied_result), self.assertRaises(SettingsError):
            main()


if __name__ == '__main__':
    unittest.main()
