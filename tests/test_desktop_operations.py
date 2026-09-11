"""No live browser, booking, cancellation or preference writes."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from desktop_cancellation import DesktopCancellation
from desktop_operation_worker import execute
from phone_cancellation import CancellationNotStarted
from operation_control import operation_stage
from gui import AsimutBookerGUI


TARGET = dict(event_id=123, date='2099-01-01',room='Example',start_time='12:00',end_time='13:00')


class DesktopOperationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_stop_before_start_never_calls_booker(self):
        key = str(uuid4())
        stop = self.root/'data'/'desktop_operations'/key/'stop'
        stop.parent.mkdir(parents=True);stop.touch()
        booker = MagicMock()
        self.assertEqual(execute(key, ['--headless'],root=self.root,booker_main=booker),130)
        booker.assert_not_called()

    def test_stop_allows_current_verification_then_blocks_next_stage(self):
        key = str(uuid4()); events=[]
        def booker(flags):
            self.assertEqual(flags,['--headless','--plan-only'])
            events.append('Save entered')
            (self.root/'data'/'desktop_operations'/key/'stop').touch()
            events.append('Persisted result verified')
            operation_stage('Next booking')
            events.append('Second Save')
        self.assertEqual(execute(key,['--headless','--plan-only'],root=self.root,booker_main=booker),130)
        self.assertEqual(events,['Save entered','Persisted result verified'])

    def test_desktop_stop_writes_marker_without_terminating(self):
        app=object.__new__(AsimutBookerGUI)
        app.is_running=True;app._desktop_stop_path=self.root/'operation'/'stop'
        app.running_process=MagicMock();app.progress_var=MagicMock();app.stop_btn=MagicMock();app.log=MagicMock()
        app.stop_booker()
        self.assertTrue(app._desktop_stop_path.exists())
        app.running_process.terminate.assert_not_called()
        self.assertIn('verification',app.progress_var.set.call_args.args[0])

    def test_cancellation_is_reserved_once_and_finishes_outside_ui(self):
        controller=DesktopCancellation(self.root)
        other=DesktopCancellation(self.root)
        entered=threading.Event(); release=threading.Event()
        self.addCleanup(release.set)
        def cancel(target,progress):
            self.assertEqual(target,TARGET)
            progress('Checking persisted removal…');entered.set();release.wait(5)
            return dict(cancelled=True,reconciliation_required=False,message='Booking cancelled. This time will stay free.')
        with patch('desktop_cancellation.cancel_phone_reservation',side_effect=cancel) as action:
            controller.start(TARGET);self.assertTrue(entered.wait(3))
            self.assertEqual(other.snapshot()['state'],'running')
            with self.assertRaises(ValueError):controller.start(TARGET)
            with self.assertRaises(ValueError):other.start(TARGET)
            with self.assertRaises(ValueError):other.review()
            self.assertFalse(controller.thread.daemon)
            release.set();controller.thread.join(5)
            self.assertFalse(controller.thread.is_alive())
            action.assert_called_once()
        self.assertEqual(other.snapshot()['state'],'cancelled')

    def test_uncertain_result_blocks_restart_until_read_only_review(self):
        controller=DesktopCancellation(self.root)
        with patch('desktop_cancellation.cancel_phone_reservation',side_effect=RuntimeError('Interrupted')):
            controller.start(TARGET);controller.thread.join(5)
        restarted=DesktopCancellation(self.root)
        self.assertEqual(restarted.snapshot()['state'],'uncertain')
        with self.assertRaises(ValueError):restarted.start(TARGET)
        surface=MagicMock()
        surface.dispatch.side_effect=[{},dict(fresh=True,matches=[TARGET])]
        reviewed=restarted.review(surface=surface)
        self.assertEqual(reviewed['state'],'reviewed')
        self.assertIn('still present',reviewed['text'])
        self.assertEqual([c.args[0] for c in surface.dispatch.call_args_list],['refresh_booker_data','find_reservations'])

    def test_failed_preflight_is_rejected_not_reported_as_cancelled(self):
        controller=DesktopCancellation(self.root)
        with patch('desktop_cancellation.cancel_phone_reservation',side_effect=CancellationNotStarted('Booking changed. Refresh.')):
            controller.start(TARGET);controller.thread.join(5)
        self.assertEqual(controller.snapshot()['state'],'rejected')

    def test_review_requires_fresh_data(self):
        controller=DesktopCancellation(self.root)
        with patch('desktop_cancellation.cancel_phone_reservation',side_effect=RuntimeError()):
            controller.start(TARGET);controller.thread.join(5)
        surface=MagicMock();surface.dispatch.side_effect=[{},dict(fresh=False,matches=[])]
        with self.assertRaises(ValueError):controller.review(surface=surface)
        self.assertEqual(controller.snapshot()['state'],'uncertain')


if __name__=='__main__':unittest.main()
