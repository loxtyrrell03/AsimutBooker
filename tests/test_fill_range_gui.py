import tempfile
from pathlib import Path
import tkinter as tk
import unittest
from unittest.mock import MagicMock

from fill_range_gui import FillRangePanel
from open_canvas_ui import install_theme
from quiet_focus import WeekPanel


class FillGuiTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=tk.Tk();self.root.withdraw();self.root.attributes('-alpha',0);self.root.maxsize(4000,2200)
        self.addCleanup(self.root.destroy);install_theme(self.root)
        self.controller=MagicMock(root=Path(self.folder.name),active=False)
        self.controller.snapshot.return_value=None
        self.close=MagicMock();self.details=MagicMock()
        self.panel=FillRangePanel(self.root,controller=self.controller,on_details=self.details,on_refresh=lambda:None,on_close=self.close)
        self.panel.pack(fill='x',padx=24,pady=24)

    def test_sizes_date_identity_and_dispatch(self):
        for width in (760,1040):
            self.root.geometry(f'{width}x760');self.root.deiconify();self.root.update()
            self.assertEqual(self.root.winfo_width(),width)
            for w in [self.panel.start_button,self.panel.close_button,*self.panel.entries]:
                self.assertGreater(w.winfo_width(),40)
                self.assertLessEqual(w.winfo_rootx()+w.winfo_width(),self.root.winfo_rootx()+width)
        self.panel.open_date('2030-10-14');self.panel.start_button.invoke()
        self.controller.start.assert_called_once_with({'date':'2030-10-14','start_time':'11:00','end_time':'13:00'},review=False)
        self.panel.close_button.invoke();self.close.assert_called_once()

    def test_uncertainty_disables_submit_and_keeps_review(self):
        self.controller.snapshot.return_value={'request_id':'fixture','state':'uncertain','text':'Check result'}
        self.panel.poll()
        self.assertEqual(str(self.panel.start_button.cget('state')),'disabled')
        self.panel.review.invoke();self.assertTrue(self.controller.start.call_args.kwargs['review'])

    def test_week_actions_belong_to_the_exact_day(self):
        selected=[]
        week=WeekPanel(self.root,on_calendar=lambda:None,on_refresh=lambda:None,on_details=lambda e:None,on_fill_day=selected.append)
        week.update_data([{'date':'2030-10-14','startTime':'12:00','endTime':'12:30','room':'Example','title':'Reservation','isReservation':True}],available=True,stale=False)
        week.day_fill_buttons['2030-10-14'].invoke()
        self.assertEqual(selected,['2030-10-14'])
        self.controller.start.assert_not_called()


if __name__=='__main__':unittest.main()
