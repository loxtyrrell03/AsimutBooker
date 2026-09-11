"""Quiet Focus shell adapter for the existing desktop controller."""
from datetime import datetime
import threading
import tkinter as tk
from tkinter import ttk
import webbrowser
from agenda_snapshot import AGENDA_SNAPSHOT_FILE, read_agenda_snapshot
from quiet_focus import TodayPanel, WeekPanel, ScrollPage, RoundedCard, label
from open_canvas_ui import PAGE, BLUE, WHITE, MUTED, DetailPage, HelpTip, install_theme


class QuietFocusGUI:
    def _create_quiet_shell(self):
        style = install_theme(self.root, self.ui_font_family)
        style.layout('Navigation.TNotebook', [])
        self.root.configure(menu='')
        self._detail_pages = {}
        self.topbar = tk.Frame(self.root, bg=WHITE, padx=28, pady=16)
        self.topbar.pack(fill='x')
        brand = tk.Frame(self.topbar, bg=WHITE)
        brand.grid(row=0, column=0, sticky='w')
        mark = tk.Canvas(brand, width=36, height=36, bg=WHITE, highlightthickness=0)
        mark.pack(side='left', padx=(0,12))
        mark.create_polygon(10,0,26,0,36,0,36,10,36,26,36,36,26,36,10,36,0,36,0,26,0,10,0,0,
                            smooth=True, fill=BLUE, outline='')
        for x, height in ((10,12),(18,22),(26,17)):
            mark.create_line(x,27,x,27-height,fill=WHITE,width=4,capstyle=tk.ROUND)
        label(brand,'Asimut',size=21,bold=True).pack(side='left')
        nav = tk.Frame(self.topbar,bg=WHITE)
        nav.grid(row=0,column=1,sticky='e')
        self.topbar.columnconfigure(1,weight=1)
        self.quiet_nav={}
        for i,(key,text) in enumerate((('today','Today'),('week','My Week'),('calendar','Calendar'),('assistant','Assistant'),('settings','Settings'))):
            button=ttk.Button(nav,text=text,style='QuietNav.TButton',command=lambda k=key:self._select_quiet_page(k))
            button.grid(row=0,column=i,padx=3)
            self.quiet_nav[key]=button
        def fit(event):
            if event.widget is not self.topbar: return
            row = 1 if event.width < 860 else 0
            nav.grid_configure(row=row,column=0 if row else 1,columnspan=2 if row else 1,
                               sticky='ew' if row else 'e',pady=(10,0) if row else 0)
        self.topbar.bind('<Configure>',fit)
        ttk.Separator(self.root).pack(fill='x')
        self.quiet_goal_var=tk.StringVar(value='Your daily routine')
        self.quiet_health_var=tk.StringVar(value='Checking auto-booking...')
        content=ttk.Frame(self.root,style='Page.TFrame')
        content.pack(fill='both',expand=True)
        return content

    def _open_detail_page(self,key,owner='settings'):
        return DetailPage(self,key,owner)

    def _select_quiet_page(self, page):
        targets = {'today': self.today_tab, 'week': self.week_tab, 'calendar': self.calendar_tab, 'assistant': self.assistant_tab, 'settings': self.preferences_page}
        if page == 'calendar':
            self.show_calendar_dialog()
            return
        self.main_notebook.select(targets[page])
        if page == 'settings' and hasattr(self, 'settings_hub'):
            self._show_settings_hub()
        self._sync_quiet_navigation()
        if page in ('today', 'week'):
            self._refresh_quiet_views()

    def _on_quiet_page_changed(self, _event=None):
        """Initialize pages for every notebook selection, including native tabs."""
        if not self.topbar.winfo_exists(): return
        self._sync_quiet_navigation()
        if (self.main_notebook.select() == str(self.calendar_tab)
                and getattr(self, 'calendar_dialog', None) is None):
            self.show_calendar_dialog()

    def _sync_quiet_navigation(self, _event=None):
        if not self.topbar.winfo_exists(): return
        selected = self.main_notebook.select()
        for key, target in (('today', self.today_tab), ('week', self.week_tab), ('calendar', self.calendar_tab), ('assistant', self.assistant_tab), ('settings', self.preferences_page)):
            active = selected == str(target) or key == 'settings' and selected in (str(self.system_tab), str(self.activity_tab))
            active = active or any(selected == str(detail.host) and detail.owner == key for detail in self._detail_pages.values())
            self.quiet_nav[key].state(['selected'] if active else ['!selected'])

    def _quiet_ask(self, prompt):
        self._select_quiet_page('assistant')
        self.assistant_panel.prefill_prompt(prompt)

    def _create_quiet_settings_body(self):
        style = ttk.Style(self.root)
        for base in ('Title.TLabel', 'Subtitle.TLabel'):
            style.configure('Settings.' + base, background=PAGE)
        style.configure('Settings.Title.TLabel', font=(self.ui_font_family, -32, 'bold'))
        style.configure('Settings.Subtitle.TLabel', font=(self.ui_font_family, -12))
        self.settings_scroll = ScrollPage(self.preferences_page)
        self.settings_scroll.pack(fill=tk.BOTH, expand=True)
        self.settings_sections = {}
        body = tk.Frame(self.settings_scroll.content, bg=PAGE, padx=24, pady=24)
        body.pack(fill=tk.BOTH, expand=True)
        return body

    def _create_settings_section(self, parent, title):
        if not self.settings_sections:
            self.settings_grid = tk.Frame(parent, bg=PAGE)
            self.settings_grid.pack(fill=tk.X)
            for column in (0, 1):
                self.settings_grid.columnconfigure(column, weight=1, uniform='settings')
        positions = {'Practice target': (0, 0), 'Preferred time': (0, 1),
                     'Booking days': (1, 0), 'Rooms': (1, 1),
                     'Booking strategy': (2, 0), 'Automatic booking': (2, 1)}
        row, column = positions[title]
        card = RoundedCard(self.settings_grid, fill=WHITE, padding=18, width=1)
        card.grid(row=row, column=column, sticky='new',
                  padx=(0, 5) if column == 0 else (5, 0), pady=(0, 10))
        self.settings_sections[title] = card
        label(card.content, title, size=15, bold=True).pack(anchor=tk.W, pady=(0, 7))
        content = ttk.Frame(card.content)
        content.pack(fill=tk.X)
        return content

    def _finish_quiet_layout(self, preferences):
        automatic = self._create_settings_section(preferences, 'Automatic booking')
        ttk.Label(automatic, text='View, install or repair the schedule.',
                  wraplength=300).pack(fill=tk.X, pady=(0, 6))
        ttk.Button(automatic, text='Manage schedule', command=self.view_scheduled_tasks).pack(anchor=tk.W)
        links = tk.Frame(preferences, bg=PAGE)
        self.settings_links=links
        links.pack(fill=tk.X, pady=(6, 0))
        for text, command in (
            ('System details', lambda:self.main_notebook.select(self.system_tab)),
            ('Activity and history', lambda:self.main_notebook.select(self.activity_tab)),
            ('Refresh bookings', self._refresh_quiet_agenda),
            ('Advanced tools', self._show_advanced_tools),
        ):
            ttk.Button(links, text=text, command=command, style='QuietLink.TButton').pack(side=tk.LEFT, padx=(0, 16))

        # Scope the card surface styles to Settings; dialogs keep their own styles.
        style = ttk.Style(self.root)
        def style_section(widget):
            if isinstance(widget, (ttk.Frame, ttk.Label, ttk.Checkbutton, ttk.Button,
                                   ttk.Entry, ttk.Combobox, ttk.Spinbox)):
                base = widget.cget('style') or widget.winfo_class()
                name = 'Settings.' + base
                if isinstance(widget, (ttk.Frame, ttk.Label, ttk.Checkbutton)):
                    style.configure(name, background=WHITE)
                if not isinstance(widget, ttk.Frame):
                    style.configure(name, font=(self.ui_font_family, -13))
                if isinstance(widget, (ttk.Button, ttk.Entry, ttk.Combobox, ttk.Spinbox)):
                    style.configure(name, padding=(7, 4))
                if isinstance(widget, ttk.Button):
                    widget.configure(width=0)
                if isinstance(widget, (ttk.Label, ttk.Entry, ttk.Combobox, ttk.Spinbox)):
                    widget.configure(font=(self.ui_font_family, -13))
                if isinstance(widget, ttk.Checkbutton):
                    style.configure(name, padding=(0, 2))
                    style.map(name, background=[('active', WHITE)])
                widget.configure(style=name)
            if isinstance(widget, ttk.Label) and widget.cget('wraplength'):
                # Summary text must fit alongside its editor button at minimum size.
                widget.bind('<Configure>', lambda e, w=widget: w.configure(wraplength=max(40, e.width)))
            for child in widget.winfo_children():
                style_section(child)
        for card in self.settings_sections.values():
            style_section(card.content)
        self._build_settings_hub(preferences)
        self.settings_scroll._bind_wheel()

        self.today_panel = TodayPanel(self.today_tab,
            on_find=lambda:self._quiet_ask('Help me find a practice room. Ask which date and time I want.'),
            on_week=lambda:self._select_quiet_page('week'), on_ask=self._quiet_ask,
            on_refresh=self._refresh_quiet_agenda, on_details=self._show_quiet_booking)
        self.today_panel.pack(fill=tk.BOTH, expand=True)
        self.week_panel = WeekPanel(self.week_tab, on_calendar=lambda:self.show_calendar_dialog(initial_view='week'),
            on_refresh=self._refresh_quiet_agenda, on_details=self._show_quiet_booking)
        self.week_panel.pack(fill=tk.BOTH, expand=True)
        self.main_notebook.bind('<<NotebookTabChanged>>', self._on_quiet_page_changed)
        self._sync_quiet_navigation()

    def _show_advanced_tools(self):
        old=self._detail_pages.get('tools')
        if old is not None and old.winfo_exists():
            self.main_notebook.select(old.host); return
        page=self._open_detail_page('tools')
        body=tk.Frame(page,bg=PAGE,padx=24,pady=20);body.pack(fill='both',expand=True)
        label(body,'Advanced tools',size=32,bold=True).pack(anchor='w',pady=(0,20))
        for title,callback in (('Open configuration',self.open_config),('Open logs folder',self.open_logs_folder),
                               ('Clean up old log files',self.show_cleanup_dialog),('Manage events',self.show_events_dialog),
                               ('Install or repair schedule',self.run_setup_script),('About Asimut',self.show_about)):
            ttk.Button(body,text=title,command=callback).pack(fill='x',pady=6)

    def _refresh_quiet_views(self):
        if not hasattr(self, 'today_panel'):
            return
        result = read_agenda_snapshot(AGENDA_SNAPSHOT_FILE)
        snapshot = result.snapshot
        events = snapshot.event_dicts() if snapshot else []
        checked = snapshot.observed_at.astimezone().strftime('%d %b, %H:%M') if snapshot else ''
        practice = getattr(self, 'practice_plan', None)
        goal = f'Daily goal · {practice.default_hours:g} hours' if practice and practice.enabled and practice.default_hours else 'Choose your daily goal in Settings'
        self.quiet_goal_var.set(goal.replace('Daily goal · ', ''))
        self.today_panel.update_data(events, available=snapshot is not None, stale=result.stale, checked=checked, goal=goal)
        plan_result = getattr(self, 'booking_plan_result', None)
        planned = []
        if plan_result and plan_result.snapshot and not plan_result.stale:
            for day in plan_result.snapshot.days:
                candidates = ([day.primary] if day.primary else []) + list(day.additional)
                planned.extend(candidate for candidate in candidates if candidate.potential_minutes > 0)
        from room_catalog import closed_practice_dates
        self.week_panel.update_data(events, available=snapshot is not None, stale=result.stale, checked=checked, planned=planned,
            closed_dates=closed_practice_dates(getattr(self,'room_catalog',None)),off_dates=getattr(self,'disabled_dates',()),
            plan_days=plan_result.snapshot.days if plan_result and plan_result.snapshot and not plan_result.stale else (),
            plan_stale=bool(plan_result and plan_result.stale))
        self.today_panel.refresh_button.configure(state=tk.DISABLED if self.is_running else tk.NORMAL)

    def _build_settings_hub(self,parent):
        self.settings_grid.pack_forget()
        # The original direct controls stay mounted in their editor, retaining drafts.
        self.settings_back=ttk.Button(parent,text='← Settings',style='QuietLink.TButton',command=self._show_settings_hub)
        self.settings_save_note=label(parent,'Changes in these quick controls save automatically.',size=13,color=MUTED)
        self.settings_hub=tk.Frame(parent,bg=PAGE)
        self.settings_hub.pack(fill='x',before=self.settings_links)
        self.settings_tiles={}
        specs=(('Practice target','Daily target'),('Preferred time','Preferred times'),
               ('Booking days','Practice dates'),('Rooms','Rooms'),
               ('Booking strategy','Booking strategy'),('Automatic booking','Automatic booking'))
        for i,(key,title) in enumerate(specs):
            tile=RoundedCard(self.settings_hub,fill=WHITE,padding=18,width=1)
            tile.grid(row=i//2,column=i%2,sticky='nsew',padx=(0,8) if i%2==0 else (8,0),pady=8)
            button=ttk.Button(tile.content,text=title+'  ›',style='Hub.TButton',command=lambda k=key:self._open_settings_group(k))
            button.pack(fill='x')
            summary=label(tile.content,size=13,color=MUTED,wraplength=330)
            summary.pack(fill='x',pady=(10,2))
            summary.bind('<Button-1>',lambda _event,k=key:self._open_settings_group(k))
            self.settings_tiles[key]=(tile,summary,button)
        self.settings_hub.bind('<Configure>',self._layout_settings_hub)
        self._show_settings_hub()

    def _layout_settings_hub(self,event):
        if event.widget is not self.settings_hub: return
        columns=1 if event.width<620 else 2
        for column in (0,1): self.settings_hub.columnconfigure(column,weight=1 if column<columns else 0,uniform='hub')
        for i,(tile,_,_) in enumerate(self.settings_tiles.values()):
            tile.grid_configure(row=i//columns,column=i%columns,padx=(0,8) if columns==2 and i%2==0 else (8,0) if columns==2 else 0)

    def _show_settings_hub(self):
        self.settings_grid.pack_forget(); self.settings_back.pack_forget(); self.settings_save_note.pack_forget()
        self.settings_hub.pack(fill='x',before=self.settings_links)
        p=self.practice_plan
        summaries={'Practice target':f'{p.default_hours:g} hours each practice day' if p.enabled else 'Daily target is off',
                   'Preferred time':self.time_prefs_dropdown.get() if self.time_prefs_enabled.get() else 'Any time',
                   'Booking days':'Choose dates and daily overrides',
                   'Rooms':'Room priority, requirements and sessions',
                   'Booking strategy':'When to wait and when to book',
                   'Automatic booking':self.quiet_health_var.get()}
        for key,(_,summary,_) in self.settings_tiles.items(): summary.configure(text=summaries[key])
        self.settings_scroll.canvas.yview_moveto(0)

    def _open_settings_group(self,key):
        self.settings_hub.pack_forget()
        self.settings_back.pack(anchor='w',pady=8,before=self.settings_links)
        for card in self.settings_sections.values(): card.grid_remove()
        self.settings_grid.columnconfigure(0,weight=1)
        self.settings_grid.columnconfigure(1,weight=0)
        self.settings_sections[key].grid(row=0,column=0,columnspan=2,sticky='ew',padx=0)
        self.settings_grid.pack(fill='x',before=self.settings_links)
        self.settings_save_note.pack(anchor='w',pady=(4,10),before=self.settings_grid)
        self.settings_scroll.canvas.yview_moveto(0)

    def _refresh_quiet_agenda(self):
        if self.is_running or self.login_operation_in_progress:
            return
        self.is_running = True
        self._prepare_desktop_run()
        for control in (self.run_visible_btn, self.run_headless_btn, self.plan_refresh_btn):
            control.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.progress_var.set('Checking your bookings…')
        self.today_panel.refresh_button.configure(state=tk.DISABLED)
        self.today_panel.freshness.configure(text='Checking your bookings…')
        threading.Thread(target=self._run_booker_thread,
            args=(True, ('--agenda-only', '--wait-for-runtime-seconds', '180'), 'Agenda refresh'), daemon=False).start()

    def _show_quiet_booking(self, event):
        if not event or not event.get('isReservation'):
            return
        key = 'booking:' + str(event.get('eventId') or (event['date'],event.get('room'),event['startTime'],event['endTime']))
        previous = self._detail_pages.get(key)
        if previous is not None and previous.winfo_exists():
            self.main_notebook.select(previous.host); return
        dialog = self._open_detail_page(key,owner='today')
        dialog.title('Your booking')
        dialog.geometry('560x670')
        dialog.minsize(500, 600)
        dialog.configure(bg=PAGE)
        dialog.transient(self.root)
        body = tk.Frame(dialog, bg=PAGE, padx=24, pady=18);body.pack(fill=tk.BOTH, expand=True)
        label(body,'Your booking',size=29,bold=True).pack(anchor=tk.W)
        label(body,'From your last checked agenda',size=13,color='#667080').pack(anchor=tk.W,pady=(8,24))
        label(body,f"Room {event.get('room','not shown')}",size=32,bold=True,wraplength=470).pack(anchor=tk.W)
        label(body,datetime.fromisoformat(event['date']).strftime('%A, %d %B'),size=18).pack(anchor=tk.W,pady=(16,8))
        label(body,f"{event['startTime']}–{event['endTime']}",size=27).pack(anchor=tk.W)
        card = RoundedCard(body, fill='#EAF3FF', padding=20);card.pack(fill=tk.X,pady=28)
        label(card.content,'Before you practise',size=16,bold=True).pack(anchor=tk.W)
        label(card.content,'Reconfirm in Asimut on college Wi-Fi when reconfirmation becomes available.',size=14,color='#667080',wraplength=400).pack(anchor=tk.W,pady=(10,0))
        ttk.Button(body,text='Open in Asimut',command=lambda:webbrowser.open('https://rwcmd.asimut.net/'),style='Primary.TButton').pack(fill=tk.X)
        identity=f"{event.get('room')} on {event['date']} from {event['startTime']} to {event['endTime']}"
        def ask(prompt):
            dialog.destroy()
            self._quiet_ask(prompt)
        ttk.Button(body,text='Ask to change booking',command=lambda:ask(f'I would like to change my booking in {identity}. Ask what I want to change, then check the live agenda.')).pack(fill=tk.X,pady=12)
        ttk.Button(body,text='Cancel booking',command=lambda:self._show_cancel_booking(event),style='Danger.TButton').pack(fill=tk.X,pady=(0,16))

    def _show_cancel_booking(self,event):
        from desktop_cancellation import DesktopCancellation
        from phone_cancellation import validate_target
        if not hasattr(self,'_desktop_cancellation'):
            self._desktop_cancellation=DesktopCancellation()
        controller=self._desktop_cancellation
        previous=self._detail_pages.get('cancellation')
        if previous is not None and previous.winfo_exists():
            self.main_notebook.select(previous.host); return
        page=self._open_detail_page('cancellation',owner='week')
        body=tk.Frame(page,bg=PAGE,padx=24,pady=16);body.pack(fill='both',expand=True)
        label(body,'Cancel booking',size=32,bold=True).pack(anchor='w',pady=(0,20))
        status=tk.StringVar()
        try:
            target=validate_target(dict(event_id=event.get('eventId'),date=event['date'],room=event.get('room'),start_time=event['startTime'],end_time=event['endTime']))
            old=controller.snapshot()
            if old and old['state'] in {'running','uncertain'}: target=old['target']
        except Exception as exc:
            label(body,str(exc),color='#B73332',wraplength=620).pack(fill='x')
            ttk.Button(body,text='Refresh bookings',command=self._refresh_quiet_agenda).pack(anchor='w',pady=16)
            return
        card=RoundedCard(body,fill=WHITE,padding=24);card.pack(fill='x',pady=(0,20))
        label(card.content,'Room '+target['room'],size=24,bold=True).pack(anchor='w')
        label(card.content,datetime.fromisoformat(target['date']).strftime('%A, %d %B'),size=17).pack(anchor='w',pady=8)
        label(card.content,target['start_time']+'–'+target['end_time'],size=21).pack(anchor='w')
        label(body,'This removes this booking and keeps this time free from automatic rebooking.',wraplength=650).pack(fill='x',pady=12)
        label(body,'You can reopen the time later in Settings → Advanced tools → Manage events.',size=13,color=MUTED,wraplength=650).pack(fill='x')
        text=ttk.Label(body,textvariable=status,wraplength=620);text.pack(fill='x',pady=20)
        progress=ttk.Progressbar(body,mode='indeterminate')
        actions=tk.Frame(body,bg=PAGE);actions.pack(fill='x',pady=12)
        poll_id=[None]
        review_result=[]
        reviewing=[False]
        override=[None]
        published=[None]

        def refresh():
            if not page.winfo_exists(): return
            try:
                job=controller.snapshot()
            except Exception:
                status.set('The cancellation record could not be read. Check System details before another action.')
                cancel.configure(state='disabled');return
            relevant=job and job['target']==target
            state=job['state'] if relevant else None
            busy=(controller.active if state in {'running','uncertain'} else controller.lock.acquired) or reviewing[0]
            if review_result:
                override[0]=review_result.pop();reviewing[0]=False;busy=controller.lock.acquired
            if override[0]: status.set(override[0])
            elif relevant: status.set(job['text'])
            if state=='running' and not busy:
                status.set('The previous cancellation was interrupted. Review its outcome before another action.')
            cancel.configure(state='normal' if state in {None,'rejected','reviewed'} and not busy else 'disabled')
            keep.configure(state='disabled' if busy else 'normal',text='Back to My Week' if state else 'Keep booking')
            review.configure(state='normal' if state in {'running','uncertain'} and not busy else 'disabled')
            if state in {'running','uncertain'}:
                if not review.winfo_manager(): review.pack(anchor='w',pady=8)
            else: review.pack_forget()
            if busy:
                if not progress.winfo_manager(): progress.pack(fill='x',before=actions);progress.start(15)
            else:
                progress.stop();progress.pack_forget()
            if state=='cancelled' and published[0]!=job['request_id']:
                self._refresh_quiet_views()
                published[0]=job['request_id']
            poll_id[0]=page.after(400,refresh)

        def start():
            override[0]=None
            try:
                controller.start(target)
            except Exception as exc:
                override[0]=str(exc);status.set(str(exc));return
            status.set('Checking your exact booking… You can leave this page while verification finishes.')
            cancel.configure(state='disabled')

        def review_outcome():
            reviewing[0]=True
            override[0]=None
            status.set('Reading the live agenda to review the previous outcome…')
            def work():
                try: review_result.append(controller.review()['text'])
                except Exception as exc: review_result.append(str(exc))
            threading.Thread(target=work,daemon=False).start()

        keep=ttk.Button(actions,text='Keep booking',command=page.destroy);keep.pack(side='left')
        cancel=ttk.Button(actions,text='Cancel this booking',style='Danger.TButton',command=start);cancel.pack(side='right')
        review=ttk.Button(body,text='Review outcome',command=review_outcome);review.pack(anchor='w',pady=8)
        label(body,'A cancellation already sent will finish verification if you close this window.',size=13,color=MUTED,wraplength=620).pack(fill='x',pady=12)
        def dispose(event):
            if event.widget is page:
                try: progress.stop()
                except tk.TclError: pass
                if poll_id[0]: page.after_cancel(poll_id[0])
        page.bind('<Destroy>',dispose,add='+')
        refresh()
