import tempfile
from pathlib import Path
import tkinter as tk
import unittest
from unittest.mock import MagicMock

from room_now_gui import RoomNowPanel


class RoomNowGuiTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=tk.Tk();self.root.withdraw();self.root.attributes('-alpha',0)
        self.addCleanup(self.root.destroy)
        self.controller=MagicMock(root=Path(self.folder.name),active=False)
        self.controller.snapshot.return_value=None
        self.details=MagicMock();self.refresh=MagicMock()
        self.panel=RoomNowPanel(self.root,controller=self.controller,on_details=self.details,on_refresh=self.refresh)
        self.panel.pack(fill='x',padx=24,pady=24)

    def test_controls_fit_both_desktop_widths_and_dispatch_exact_choices(self):
        for width in (760,1040):
            self.root.geometry(f'{width}x760');self.root.deiconify();self.root.update()
            for widget in [self.panel.start_button,self.panel.custom,*self.panel.choices]:
                self.assertGreater(widget.winfo_width(),40)
                self.assertGreaterEqual(widget.winfo_rootx(),self.root.winfo_rootx())
                self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(),self.root.winfo_rootx()+width)
            self.assertLess(self.panel.winfo_reqheight(),530)
        self.panel.choose('longest',105);self.panel.start_button.invoke()
        self.controller.start.assert_called_once_with({'mode':'longest','minutes':105},review=False)
        self.assertEqual(self.panel.duration_label.cget('text'),'Maximum duration')

    def test_unknown_result_blocks_start_and_offers_read_only_review(self):
        self.controller.snapshot.return_value={'request_id':'fixture','state':'uncertain','text':'Check result'}
        self.panel.poll()
        self.assertEqual(str(self.panel.start_button.cget('state')),'disabled')
        self.panel.review.invoke()
        self.assertTrue(self.controller.start.call_args.kwargs['review'])

    def test_confirmed_result_opens_exact_existing_booking_details(self):
        booking={'event_id':123,'room':'Example','date':'2030-09-28','start':'14:15','end':'15:00','duration_minutes':45}
        self.controller.snapshot.return_value={'request_id':'fixture','state':'completed','text':'Booked','result':{'booking':booking,'requested_minutes':60}}
        self.panel.poll();self.panel.details.invoke()
        self.assertEqual(self.details.call_args.args[0]['eventId'],123)
        self.assertIn('requested 60',self.panel.status.cget('text'))


if __name__=='__main__':unittest.main()
