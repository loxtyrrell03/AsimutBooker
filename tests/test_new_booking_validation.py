import json
import unittest
from datetime import date
from unittest import mock

from playwright.sync_api import sync_playwright

import book_week as b


HTML = """<input id='endDate' value='18:00'><button disabled>Save</button><script>
const end=document.getElementById('endDate'), save=document.querySelector('button');
let committed=end.value;
end.addEventListener('change',async()=>{
 if(end.value===committed)return;
 committed=end.value;save.disabled=true;
 const response=await fetch('/services/v2/event/type=check',{method:'POST',
 headers:{'Content-Type':'application/json'},body:JSON.stringify({event:{id:0,
 st:'2026-09-22T17:15:00+01:00',en:`2026-09-22T${end.value}:00+01:00`,rs:[{id:80}]}})});
 const result=await response.json();setTimeout(()=>save.disabled=!result.response.success,150);
});</script>"""


class NewBookingValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = sync_playwright().start()
        cls.browser = cls.p.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.p.stop()

    def validate(self, *, rejected=False):
        requests = []
        with self.browser.new_context(service_workers="block") as context:
            def route(request):
                if request.request.method == "GET":
                    request.fulfill(content_type="text/html", body=HTML)
                    return
                data = request.request.post_data_json
                requests.append(data)
                success = not rejected or "17:45:00" in data["event"]["en"]
                request.fulfill(content_type="application/json",
                    body=json.dumps({"response": {"success": success}}))
            context.route("**/*", route)
            page = context.new_page()
            page.goto("https://rwcmd.asimut.net/event?eventId=0")
            end = page.locator("#endDate")
            # The former fill/click sequence sends no check when unchanged.
            end.fill("18:00")
            page.wait_for_timeout(20)
            self.assertEqual(requests, [])
            with mock.patch.object(b, "ACTIVE_ROOM_POLICY", mock.Mock(all_room_location_ids={"B0.13": 80})):
                result = b.refresh_new_booking_validation(page, "18:00",
                    expected_start_time="17:15", expected_date=date(2026, 9, 22), expected_room="B0.13")
            if result[0]:
                self.assertTrue(page.get_by_role("button", name="Save").is_enabled())
            self.assertEqual([x["event"]["en"][11:16] for x in requests], ["17:45", "18:00"])
            return result

    def test_commits_unchanged_end_and_waits_for_delayed_save_enable(self):
        self.assertTrue(self.validate()[0])

    def test_intermediate_approval_cannot_override_final_rejection(self):
        self.assertFalse(self.validate(rejected=True)[0])
