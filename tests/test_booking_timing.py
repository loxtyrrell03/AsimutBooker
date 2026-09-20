from datetime import datetime
from types import SimpleNamespace
import unittest
from booking_timing import scheduled_work_fits, seconds_before_next_preparation


class BookingTimingTests(unittest.TestCase):
    def test_routine_work_leaves_three_minutes_for_the_next_preparation(self):
        args=SimpleNamespace(scheduled=True,target_time=None)
        self.assertTrue(scheduled_work_fits(args,now=datetime(2026,9,21,11,23)))
        self.assertFalse(scheduled_work_fits(args,now=datetime(2026,9,21,11,26)))
        self.assertFalse(scheduled_work_fits(args,now=datetime(2026,9,21,11,28)))
        self.assertTrue(scheduled_work_fits(args,now=datetime(2026,9,21,11,30)))
        self.assertEqual(seconds_before_next_preparation(datetime(2026,9,21,11,23)),240)

    def test_the_current_prepared_booking_keeps_its_boundary(self):
        args=SimpleNamespace(scheduled=True,target_time='11:30')
        self.assertTrue(scheduled_work_fits(args,now=datetime(2026,9,21,11,28),edge_work=True))
        self.assertFalse(scheduled_work_fits(args,now=datetime(2026,9,21,11,28)))
        self.assertFalse(scheduled_work_fits(args,now=datetime(2026,9,21,11,42),edge_work=True))

    def test_manual_scopes_are_not_silently_time_limited(self):
        self.assertTrue(scheduled_work_fits(SimpleNamespace(scheduled=False),
            now=datetime(2026,9,21,11,28),reserve_seconds=600))
