import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app_settings import atomic_write_json
from phone_operation_worker import execute
from phone_server import PhoneAssistantService, RequestLedger
from phone_system import timestamp, SystemConflict
from tests.test_phone_server import FakeRuntime


class RoomNowRequestTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.service=PhoneAssistantService(runtime_factory=FakeRuntime,
            ledger=RequestLedger(self.root/'ledger.json'),state_path=self.root/'state.json',workspace=self.root/'workspace')
        self.addCleanup(self.service.close)
        self.operation=self.service.operations
        self.payload={'request_id':str(uuid4()),'action':'room_now','args':{'mode':'longest','minutes':120}}

    def test_lost_delivery_is_closed_before_delayed_request_can_arrive(self):
        self.operation.runner=MagicMock()
        reply=self.operation.check_room_now_delivery({'request_id':self.payload['request_id']})
        self.assertTrue(reply['not_started'])
        self.assertTrue(self.operation.submit(self.payload)['duplicate'])
        self.operation.runner.assert_not_called()

    def test_duplicate_during_and_after_run_never_replays(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        def runner(*args):
            entered.set();release.wait(3);return {'state':'empty','message':'No room'}
        self.operation.runner=MagicMock(side_effect=runner)
        self.operation.submit(self.payload);self.assertTrue(entered.wait(2))
        self.assertTrue(self.operation.submit(self.payload)['duplicate'])
        self.assertFalse(self.operation.check_room_now_delivery({'request_id':self.payload['request_id']})['not_started'])
        self.operation.stop({'request_id':self.payload['request_id']})
        release.set();self.operation.thread.join(4)
        self.assertTrue(self.operation.submit(self.payload)['duplicate'])
        self.operation.runner.assert_called_once()

    def test_uncertain_review_dispatches_only_read_only_path(self):
        self.operation.runner=MagicMock(return_value={'state':'uncertain','message':'Check Save'})
        self.operation.submit(self.payload);self.operation.thread.join(3)
        newer={**self.payload,'request_id':str(uuid4())}
        with self.assertRaises(SystemConflict):self.operation.submit(newer)
        self.operation.runner.reset_mock(return_value=True)
        self.operation.runner.return_value={'state':'completed','message':'Verified'}
        self.operation.review_room_now({'request_id':self.payload['request_id']});self.operation.thread.join(3)
        self.assertEqual(self.operation.runner.call_args.args[1],'room_now_review')
        self.assertEqual(self.service.ledger.lookup(self.payload['request_id']),'accepted')

    def test_worker_binds_server_submission_time_and_returns_exact_result(self):
        directory=self.root/'work';directory.mkdir()
        submitted=timestamp();atomic_write_json(directory/'submitted.json',{'requested_at':submitted})
        def booker(flags):
            self.assertIn('--room-now-mode',flags);self.assertNotIn('--upgrades-only',flags)
            self.assertEqual(flags[flags.index('--room-now-requested-at')+1],submitted)
            atomic_write_json(directory/'room-now.json',{'state':'completed','message':'Booked','booking':{'event_id':9}})
            return 0
        self.assertEqual(execute('room_now',self.payload['args'],directory,root=self.root,booker_main=booker)['booking']['event_id'],9)

    def test_review_worker_never_replays_create_even_with_stop_marker(self):
        directory=self.root/'work';directory.mkdir();(directory/'stop').touch()
        booker=MagicMock(return_value=0)
        result=execute('room_now_review',{},directory,root=self.root,booker_main=booker)
        booker.assert_called_once_with(['--headless','--agenda-only'])
        self.assertEqual(result['state'],'stopped')

    def test_expired_queued_request_never_starts_booker(self):
        directory=self.root/'work';directory.mkdir()
        atomic_write_json(directory/'submitted.json',{'requested_at':'2020-01-01T12:00:00+00:00'})
        booker=MagicMock()
        result=execute('room_now',self.payload['args'],directory,root=self.root,booker_main=booker)
        self.assertEqual(result['state'],'blocked');self.assertIn('expired',result['message'])
        booker.assert_not_called()

    def test_failed_review_launch_retains_recoverable_uncertainty(self):
        self.operation.runner=MagicMock(return_value={'state':'uncertain','message':'Check Save'})
        self.operation.submit(self.payload);self.operation.thread.join(3)
        with patch('phone_operations.threading.Thread.start',side_effect=RuntimeError('Cannot start')):
            with self.assertRaises(RuntimeError):self.operation.review_room_now({'request_id':self.payload['request_id']})
        self.assertFalse(self.operation.active)
        self.assertEqual(self.operation.snapshot()['state'],'uncertain')


if __name__=='__main__':unittest.main()
