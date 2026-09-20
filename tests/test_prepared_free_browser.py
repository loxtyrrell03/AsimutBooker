"""Actual Chromium controls and check requests around an injected clock edge."""
from contextlib import ExitStack, redirect_stdout
from datetime import date, datetime
import io
import json
import time
import unittest
from unittest.mock import Mock, patch
from playwright.sync_api import sync_playwright
import book_week as b


HTML = """<input id='startDate' value='16:00'><input id='endDate' value='16:30'>
<button disabled>Save</button><script>
const start=document.querySelector('#startDate'), end=document.querySelector('#endDate'), save=document.querySelector('button');
let committed=end.value; window.saved=false; window.approved=false;
end.addEventListener('change',async()=>{
 if(end.value===committed)return; committed=end.value; save.disabled=true;
 const payload={event:{id:0,st:`2026-09-21T${start.value}:00+01:00`,en:`2026-09-21T${end.value}:00+01:00`,rs:[{id:80}]}};
 const reply=await fetch('/services/v2/event/type=check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
 const result=await reply.json();
 if(payload.event.en.slice(11,16)!==end.value)return;
 setTimeout(()=>{window.approved=result.response.success;save.disabled=!window.approved;},75);
});
save.addEventListener('click',async()=>{
 window.clicked=performance.now();
 const reply=await fetch('/services/v2/event',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({start:start.value,end:end.value})});
 window.saved=(await reply.json()).success;
});</script>"""


class PreparedFreeBrowserTests(unittest.TestCase):
    def test_form_is_ready_and_one_exact_approved_save_follows_the_boundary(self):
        now=[datetime(2026,9,21,11,28)]
        edge=datetime(2026,9,21,11,30)
        elapsed=[]
        saves=[]
        checks=[]
        clock_start=[None]
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None): return now[0]
        with sync_playwright() as p, ExitStack() as stack, redirect_stdout(io.StringIO()):
            browser=p.chromium.launch(headless=True)
            stack.callback(browser.close)
            context=browser.new_context(service_workers='block')
            def route(route):
                request=route.request
                if request.method=='GET':
                    route.fulfill(content_type='text/html',body=HTML);return
                payload=request.post_data_json
                if request.url.endswith('type=check'):
                    checks.append(payload)
                    success=now[0]>=edge and payload['event']['en'][11:16]=='16:30'
                    route.fulfill(content_type='application/json',body=json.dumps({'response':{'success':success}}))
                else:
                    self.assertGreaterEqual(now[0],edge)
                    self.assertEqual(payload,{'start':'16:00','end':'16:30'})
                    saves.append(payload)
                    elapsed.append(time.perf_counter()-clock_start[0])
                    route.fulfill(content_type='application/json',body='{"success":true}')
            context.route('**/*',route)
            page=context.new_page()
            page.goto('https://rwcmd.asimut.net/event?eventId=0')
            stack.enter_context(patch.object(b,'datetime',Clock))
            stack.enter_context(patch.multiple(b,ACTIVE_ROOM_POLICY=Mock(all_room_location_ids={'Weston':80}),
                ROOM_HORIZON_MINUTES={'Weston':7200},MINIMUM_BLOCK_MINUTES=30,FREE_HORIZON_MINUTES=300,
                SITE_CLOCK_OFFSET_BOUNDS=(0,0)))
            stack.enter_context(patch.object(b,'get_room_slot_coordinates',return_value={'x':2,'y':2}))
            stack.enter_context(patch.object(b,'enter_new_booking_form',return_value=True))
            stack.enter_context(patch.object(b,'page_booking_snapshot',return_value=dict(
                room='Weston',date='2026-09-21',start='16:00',end='16:30')))
            stack.enter_context(patch.object(b,'_visible_save_rejection',return_value=None))
            stack.enter_context(patch.object(b,'_visible_room_permission_refusal',return_value=None))
            receipt=stack.enter_context(patch.object(b,'record_pending_create',return_value={'id':'fixture'}))
            stack.enter_context(patch.object(b,'save_extendable_booking'))
            def wait(deadline,*args,**kwargs):
                self.assertEqual(page.locator('#startDate').input_value(),'16:00')
                self.assertEqual(page.locator('#endDate').input_value(),'16:30')
                self.assertFalse(saves)
                receipt.assert_not_called()
                self.assertTrue(page.get_by_role('button',name='Save').is_visible())
                now[0]=deadline
                clock_start[0]=time.perf_counter()
            stack.enter_context(patch.object(b,'wait_until_datetime',side_effect=wait))
            def persisted(*args,**kwargs):
                page.wait_for_function('window.saved===true')
                self.assertEqual(len(saves),1)
                return True
            stack.enter_context(patch.object(b,'wait_for_created_booking_outcome',side_effect=persisted))
            tracker=b.BookingTracker()
            tracker.live_quota_minutes=0
            tracker.quota_observed_hours=0
            result=b.try_horizon_snipe(page,dict(room='Weston',start_hour=16,end_hour=18,duration=120,
                free_horizon_intent=True,horizon_minutes=300,booking_minutes=30,bookable_from=edge),
                date(2026,9,21),tracker,0,remaining_daily_hours=2,time_prefs={'enabled':False})
            self.assertTrue(result)
            self.assertEqual([r['event']['en'][11:16] for r in checks],['16:15','16:30'])
            receipt.assert_called_once()
            self.assertLess(elapsed[0],1.5,'No broad rescan or fixed post-opening wait should precede Save')
        print(f'Prepared Chromium free-window Save: {elapsed[0]*1000:.0f}ms after simulated opening (local intercepted service)')
