import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock
from uuid import uuid4

from app_settings import atomic_write_json
from desktop_fill_range import DesktopFillRange
from phone_operation_worker import execute
from phone_operations import PhoneOperations
from phone_server import PhoneAssistantService, RequestLedger
from phone_system import timestamp, SystemConflict
from tests.test_phone_server import FakeRuntime


class FillRequestTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.root=Path(folder.name)
        self.service=PhoneAssistantService(runtime_factory=FakeRuntime,ledger=RequestLedger(self.root/'ledger.json'),
            state_path=self.root/'state.json',workspace=self.root/'workspace')
        self.addCleanup(self.service.close);self.operations=self.service.operations
        self.payload={'request_id':str(uuid4()),'action':'fill_range',
                      'args':{'date':'2030-10-14','start_time':'11:00','end_time':'13:00'}}

    def test_lost_delivery_closes_id_before_a_delayed_request_arrives(self):
        self.operations.runner=MagicMock()
        self.assertTrue(self.operations.check_room_now_delivery({'request_id':self.payload['request_id']})['not_started'])
        self.assertTrue(self.operations.submit(self.payload)['duplicate'])
        self.operations.runner.assert_not_called()

    def test_partial_save_uncertainty_retains_owner_and_review_does_not_replay(self):
        self.operations.runner=MagicMock(return_value={'state':'uncertain','message':'Last Save needs checking.'})
        self.operations.submit(self.payload);self.operations.thread.join(3)
        self.assertEqual(self.operations.job['range'],self.payload['args'])
        with self.assertRaises(SystemConflict):self.operations.submit({**self.payload,'request_id':str(uuid4())})
        self.operations.runner.return_value={'state':'partial','message':'Verified one booking.'}
        self.operations.review_room_now({'request_id':self.payload['request_id']},action='fill_range')
        self.operations.thread.join(3)
        self.assertEqual(self.operations.runner.call_args.args[1],'fill_range_review')
        self.assertEqual(self.service.ledger.lookup(self.payload['request_id']),'accepted')
        self.assertTrue(self.operations.submit(self.payload)['duplicate'])

    def test_review_worker_enters_owned_review_mode_even_after_stop(self):
        directory=self.root/'work';directory.mkdir();(directory/'stop').touch()
        def review(flags):
            self.assertEqual(flags,['--headless','--fill-review-output',str(directory/'fill-range.json')])
            atomic_write_json(directory/'fill-range.json',{'state':'partial','message':'Checked'});return 0
        self.assertEqual(execute('fill_range_review',{},directory,root=self.root,booker_main=review)['state'],'partial')

    def test_worker_uses_persisted_submission_and_no_other_booking_mode(self):
        directory=self.root/'work';directory.mkdir();submitted=timestamp()
        atomic_write_json(directory/'submitted.json',{'requested_at':submitted})
        def fill(flags):
            self.assertEqual(flags[flags.index('--fill-requested-at')+1],submitted)
            self.assertNotIn('--scheduled',flags);self.assertNotIn('--upgrades-only',flags)
            atomic_write_json(directory/'fill-range.json',{'state':'partial','message':'One gap left','bookings':[{'event_id':100}]});return 0
        result=execute('fill_range',self.payload['args'],directory,root=self.root,booker_main=fill)
        self.assertEqual(result['bookings'][0]['event_id'],100)

    def test_completed_fill_survives_other_jobs_and_service_restart(self):
        self.operations.runner=MagicMock(return_value={'state':'empty','message':'No new bookings.',
            'range':self.payload['args'],'covered_minutes':30,'requested_minutes':120,
            'remaining':[{'start':'12:30','end':'13:00','minutes':30}]})
        self.operations.submit(self.payload);self.operations.thread.join(3)
        completed=self.operations.snapshot()
        self.operations.runner.return_value={'state':'completed','message':'Scan finished.'}
        self.operations.submit({'request_id':str(uuid4()),'action':'scan','args':{'dates':['2030-10-14']}})
        self.operations.thread.join(3)
        self.assertEqual(self.operations.snapshot()['action'],'scan')
        self.assertEqual(self.operations.fill_snapshot(),completed)
        restarted=PhoneOperations(self.service,state_dir=self.operations.directory)
        self.assertEqual(restarted.fill_snapshot(),completed)
        self.assertFalse(restarted.active)

    def test_previous_version_fill_result_is_recovered_without_replay(self):
        directory=self.operations.directory/self.payload['request_id']
        result={'state':'empty','message':'No new bookings.','range':self.payload['args'],'covered_minutes':30}
        atomic_write_json(directory/'result.json',result)
        atomic_write_json(directory/'fill-range.json',result)
        self.operations.runner=MagicMock()
        recovered=self.operations.fill_snapshot()
        self.assertEqual(recovered['request_id'],self.payload['request_id'])
        self.assertEqual(recovered['result'],result)
        self.assertFalse(recovered['active'])
        self.operations.runner.assert_not_called()

    def test_desktop_restart_keeps_uncertainty_and_rejects_duplicate(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        def runner(job,review):
            entered.set();release.wait(3);raise RuntimeError('Lost outcome')
        controller=DesktopFillRange(self.root,runner)
        controller.start(self.payload['args']);self.assertTrue(entered.wait(2))
        with self.assertRaises(ValueError):controller.start(self.payload['args'])
        release.set();controller.thread.join(4)
        restarted=DesktopFillRange(self.root,MagicMock())
        self.assertEqual(restarted.snapshot()['state'],'uncertain')
        with self.assertRaises(ValueError):restarted.start(self.payload['args'])
        restarted.runner.assert_not_called()


if __name__=='__main__':unittest.main()
