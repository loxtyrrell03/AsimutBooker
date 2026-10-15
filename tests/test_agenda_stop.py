"""Interrupt ordinary agenda reads; preserve read-only proof after Save."""
from contextlib import ExitStack, redirect_stdout
from datetime import date, timedelta
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Timer
import time
import unittest
from unittest.mock import Mock, patch

import book_week as b
from playwright.sync_api import sync_playwright
from operation_control import OperationStopped, check_operation_stop, operation_verification, owned_operation
from tests.test_config_and_agenda_regressions import _AgendaPage


class AgendaStopTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(TemporaryDirectory()))
        self.stop = self.root / 'stop'
        self.day = date(2030, 10, 15)
        self.dates = tuple(self.day + timedelta(days=i) for i in range(8))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch.object(b, 'safe_goto'))
        self.stack.enter_context(patch.object(b, 'LIVE_CATALOG_ROOM_NAMES', []))
        self.stack.enter_context(patch('tempo_coordination.SNAPSHOT_PATH', self.root / 'coordination.json'))
        self.publish = self.stack.enter_context(patch.object(b, 'publish_agenda_snapshot'))
        self.remember = self.stack.enter_context(patch('manual_cancellations.remember_agenda', return_value=[]))

    def scan(self, page):
        return b.scan_agenda(page, b.BookingTracker(), self.day, ignored_events=[],
                            window_dates=self.dates, snapshot_path=self.root / 'agenda.json')

    def test_stop_during_each_agenda_read_phase_prevents_publication(self):
        for phase in ('initial', 'discovery', 'capture'):
            with self.subTest(phase=phase):
                self.stop.unlink(missing_ok=True)
                page = _AgendaPage(self.day, [], self.dates)
                observed = dict(phase='initial', waited_after_stop=0, largest_wait=0)
                evaluate = page.evaluate
                def read(expression, *args):
                    if expression == '() => document.body.innerText' and phase == 'discovery' and observed['phase'] == 'initial':
                        return 'Loading'
                    if expression.startswith('window.scrollTo(0, document.body.scrollHeight)'):
                        observed['phase'] = 'discovery'
                    if expression.startswith('window.scrollBy'):
                        observed['phase'] = 'capture'
                    return evaluate(expression, *args)
                def wait(milliseconds):
                    observed['largest_wait'] = max(observed['largest_wait'], milliseconds)
                    if self.stop.exists():
                        observed['waited_after_stop'] += milliseconds
                    if observed['phase'] == phase:
                        self.stop.touch()
                page.evaluate = read
                page.wait_for_timeout = wait
                with owned_operation(self.stop, Mock(), Mock()), self.assertRaises(OperationStopped):
                    self.scan(page)
                self.assertEqual(observed['waited_after_stop'], 0)
                self.assertLessEqual(observed['largest_wait'], 250)
                self.publish.assert_not_called()
                self.remember.assert_not_called()

    def test_submitted_booking_verification_finishes_with_stop_pending(self):
        self.stop.touch()
        page = _AgendaPage(self.day, [], self.dates)
        with owned_operation(self.stop, Mock(), Mock()):
            with operation_verification():
                self.assertEqual(self.scan(page), (0, []))
            with self.assertRaises(OperationStopped):
                check_operation_stop()
        self.publish.assert_called_once()
        self.remember.assert_called_once()

    def test_real_browser_wait_observes_stop_without_finishing_initial_delay(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content('<p>Invented agenda loading fixture</p>')
                timer = Timer(.1, self.stop.touch)
                started = time.monotonic()
                timer.start()
                try:
                    with owned_operation(self.stop, Mock(), Mock()), self.assertRaises(OperationStopped):
                        self.scan(page)
                finally:
                    timer.join(2)
                    timer.cancel()
                self.assertLess(time.monotonic() - started, 2)
                self.publish.assert_not_called()
                self.remember.assert_not_called()
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
