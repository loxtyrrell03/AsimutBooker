import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from booking_rules_ui import show_booking_rules_dialog
from booking_rules import PRESET_LABELS, load_booking_rules

def children(widget):
    for child in widget.winfo_children():
        yield child
        yield from children(child)

class RulesDialogTests(unittest.TestCase):
    def test_presets_custom_clock_validation_and_save(self):
        root=tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        settings={'keep':True,'booking_rules':{'preset':'legacy'}}
        app=SimpleNamespace(root=root,ui_font_family='Segoe UI',load_settings=lambda:settings,
                            log=lambda *args:None,_update_settings=lambda f:bool(f(settings)))
        dialog=show_booking_rules_dialog(app)
        selector=next(w for w in children(dialog) if isinstance(w,ttk.Combobox))
        entries=[w for w in children(dialog) if isinstance(w,ttk.Entry) and not isinstance(w,ttk.Combobox)]
        save=next(w for w in children(dialog) if isinstance(w,ttk.Button) and w.cget('text')=='Save rules')
        self.assertEqual(entries[1].get(),'120')
        selector.set(PRESET_LABELS['new']); selector.event_generate('<<ComboboxSelected>>')
        self.assertEqual(entries[1].get(),'60')
        selector.set(PRESET_LABELS['custom']); selector.event_generate('<<ComboboxSelected>>')
        entries[4].delete(0,'end'); entries[4].insert(0,'12:75')
        with patch('booking_rules_ui.messagebox.showerror') as error:
            save.invoke()
            error.assert_called_once()
        self.assertEqual(load_booking_rules(settings).preset,'legacy')
        entries[4].delete(0,'end'); entries[4].insert(0,'24:00')
        checkbox=next(w for w in children(dialog) if isinstance(w,ttk.Checkbutton))
        checkbox.invoke()
        save.invoke()
        self.assertTrue(load_booking_rules(settings).free_horizon_overrides_peak)
        self.assertEqual(load_booking_rules(settings).peak_end_minutes,1440)
        self.assertTrue(settings['keep'])
