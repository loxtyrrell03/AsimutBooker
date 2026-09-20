from contextlib import ExitStack, redirect_stdout
from datetime import date, datetime
import io
import unittest
from unittest.mock import patch

import book_week as b
from tests import test_horizon_snipe_flow as fixture


class FreeHorizonPreparationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.HorizonSnipeFlowTests('test_exact_plus_thirty_creates_minimum_and_tracks_two_hour_target')
        self.fixture.NOW=datetime(2026,9,21,11,28)
        self.fixture.TARGET_DATE=date(2026,9,21)
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def execute(self, *, boundary=None, start=16, allowed=True):
        f=self.fixture
        now=[f.NOW]
        page=f.build_page()
        start_text=b.time_text(round(start*60))
        end_text=b.time_text(round(start*60)+30)
        for control,value in ((page.start_input,start_text),(page.end_input,end_text)):
            control.input_value.side_effect=None
            control.input_value.return_value=value
        tracker=b.BookingTracker()
        with redirect_stdout(io.StringIO()):
            tracker.add_existing_event(f.TARGET_DATE,10,11,is_reservation=True,room='Other')
        tracker.live_quota_minutes=0
        tracker.quota_observed_hours=tracker.get_total_booking_hours()
        slot=dict(f.slot(),start_hour=start,end_hour=start+2,free_horizon_intent=True,
                  horizon_minutes=300,bookable_from=boundary or f.NOW.replace(hour=11,minute=30))
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None): return now[0]
        with ExitStack() as stack, redirect_stdout(io.StringIO()):
            mocks=[stack.enter_context(p) for p in f.patches(page)]
            receipt=mocks[6]
            extension=mocks[8]
            stack.enter_context(patch.object(b,'datetime',Clock))
            stack.enter_context(patch.multiple(b,FREE_HORIZON_MINUTES=300,MAX_PEAK_HOURS=1))
            stack.enter_context(patch.object(b,'page_booking_snapshot',return_value=dict(
                room='B0.29',date=str(f.TARGET_DATE),start=start_text,end=end_text)))
            sequence=[]
            def wait(deadline,*args,**kwargs):
                receipt.assert_not_called()
                page.start_input.fill.assert_called_once_with(start_text)
                page.end_input.fill.assert_called_once_with(end_text)
                self.assertEqual(page.mouse.click.call_count,1)  # Opened, never Saved early.
                sequence.append('form_ready_before_boundary')
                now[0]=deadline
            waiting=stack.enter_context(patch.object(b,'wait_until_datetime',side_effect=wait))
            def validate(*args,**kwargs):
                self.assertGreaterEqual(now[0],slot['bookable_from'])
                self.assertEqual(kwargs,dict(expected_start_time=start_text,
                    expected_date=f.TARGET_DATE,expected_room='B0.29'))
                sequence.append('exact_boundary_check')
                return allowed,'test'
            validation=stack.enter_context(patch.object(b,'refresh_new_booking_validation',side_effect=validate))
            result=b.try_horizon_snipe(page,slot,f.TARGET_DATE,tracker,0,remaining_daily_hours=3,
                time_prefs={'enabled':False})
            return result,sequence,receipt.call_count,extension.call_args,validation.call_count,waiting.call_count

    def test_1630_end_opens_1130_and_form_is_ready_before_then(self):
        result,sequence,receipts,extension,_,waits=self.execute()
        self.assertTrue(result)
        self.assertEqual(sequence,['form_ready_before_boundary','exact_boundary_check'])
        self.assertEqual((receipts,waits),(1,1))
        self.assertEqual(extension.args[2:5],(16,16.5,18))

    def test_1100_cannot_authorize_a_1600_to_1630_session(self):
        result,_,receipts,_,checks,waits=self.execute(boundary=datetime(2026,9,21,11))
        self.assertFalse(result)
        self.assertEqual((receipts,checks,waits),(0,0,0))

    def test_preparation_never_bypasses_an_already_used_peak_hour(self):
        result,_,receipts,_,checks,waits=self.execute(start=15.5,boundary=datetime(2026,9,21,11))
        self.assertFalse(result)
        self.assertEqual((receipts,checks,waits),(0,0,0))

    def test_boundary_refusal_never_creates_a_receipt_or_save(self):
        result,sequence,receipts,extension,_,_=self.execute(allowed=False)
        self.assertFalse(result)
        self.assertEqual(receipts,0)
        self.assertIsNone(extension)
        self.assertEqual(sequence,['form_ready_before_boundary','exact_boundary_check'])
