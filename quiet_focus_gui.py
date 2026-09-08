"""Quiet Focus shell adapter for the existing desktop controller."""
from datetime import datetime
import threading
import tkinter as tk
from tkinter import ttk
import webbrowser
from agenda_snapshot import AGENDA_SNAPSHOT_FILE, read_agenda_snapshot
from quiet_focus import TodayPanel, WeekPanel, ScrollPage, RoundedCard, label


class QuietFocusGUI:
    def _create_quiet_shell(self):
        style = ttk.Style(self.root)
        style.layout('Navigation.TNotebook', [('Notebook.client', {'sticky': 'nswe'})])
        style.configure('QuietLink.TButton', foreground='#0868D9', background='#FFFFFF', borderwidth=0, padding=(8, 9), font=(self.ui_font_family, -14))
        style.configure('QuietNav.TButton', foreground='#667080', background='#F4F5F7', borderwidth=0, anchor='w', padding=(18, 14), font=(self.ui_font_family, -15))
        style.map('QuietNav.TButton', background=[('selected', '#EAF3FF'), ('active', '#EAF3FF')], foreground=[('selected', '#0868D9')])
        self.sidebar = tk.Frame(self.root, bg='#F4F5F7', width=210)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar.pack_propagate(False)
        brand = tk.Frame(self.sidebar, bg='#F4F5F7')
        brand.pack(fill=tk.X, padx=24, pady=(34, 28))
        mark = tk.Canvas(brand, width=36, height=36, bg='#F4F5F7', highlightthickness=0)
        mark.pack(side=tk.LEFT, padx=(0, 12))
        mark.create_rectangle(0, 0, 36, 36, fill='#0868D9', outline='')
        for x, height in ((9, 12), (16, 22), (23, 17)):
            mark.create_line(x, 27, x, 27-height, fill='white', width=4, capstyle=tk.ROUND)
        label(brand, 'Asimut', size=21, bold=True).pack(anchor='w')
        label(brand, 'BOOKER', size=10, color='#667080').pack(anchor='w')
        self.quiet_nav = {}
        for key, text in (('today', 'Today'), ('week', 'My Week'), ('calendar', 'Calendar'), ('assistant', 'Assistant')):
            button = ttk.Button(self.sidebar, text=text, style='QuietNav.TButton', command=lambda k=key:self._select_quiet_page(k))
            button.pack(fill=tk.X, padx=14, pady=4)
            self.quiet_nav[key] = button
        intent = tk.Frame(self.sidebar, bg='#F4F5F7')
        intent.pack(fill=tk.X, padx=26, pady=(48, 10))
        label(intent, 'YOUR PRACTICE', size=11, color='#667080').pack(anchor='w')
        self.quiet_goal_var = tk.StringVar(value='Your daily routine')
        label(intent, size=15, bold=True, textvariable=self.quiet_goal_var, wraplength=160).pack(anchor='w', pady=(14, 6))
        ttk.Button(intent, text='Edit preferences', command=lambda:self._select_quiet_page('settings'), style='QuietNav.TButton').pack(anchor='w')
        bottom = tk.Frame(self.sidebar, bg='#F4F5F7')
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=20)
        self.quiet_health_var = tk.StringVar(value='Checking auto-booking…')
        label(bottom, size=12, color='#667080', textvariable=self.quiet_health_var, wraplength=175).pack(anchor='w', padx=12, pady=(0, 8))
        ttk.Button(bottom, text='Automatic schedule', command=self.view_scheduled_tasks, style='QuietNav.TButton').pack(fill=tk.X, pady=(0, 16))
        button = ttk.Button(bottom, text='Settings', style='QuietNav.TButton', command=lambda:self._select_quiet_page('settings'))
        button.pack(fill=tk.X)
        self.quiet_nav['settings'] = button
        content = ttk.Frame(self.root, style='Page.TFrame')
        content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        return content

    def _select_quiet_page(self, page):
        targets = {'today': self.today_tab, 'week': self.week_tab, 'calendar': self.calendar_tab, 'assistant': self.assistant_tab, 'settings': self.preferences_page}
        if page == 'calendar':
            self.show_calendar_dialog()
            return
        self.main_notebook.select(targets[page])
        self._sync_quiet_navigation()
        if page in ('today', 'week'):
            self._refresh_quiet_views()

    def _on_quiet_page_changed(self, _event=None):
        """Initialize pages for every notebook selection, including native tabs."""
        self._sync_quiet_navigation()
        if (self.main_notebook.select() == str(self.calendar_tab)
                and getattr(self, 'calendar_dialog', None) is None):
            self.show_calendar_dialog()

    def _sync_quiet_navigation(self, _event=None):
        selected = self.main_notebook.select()
        for key, target in (('today', self.today_tab), ('week', self.week_tab), ('calendar', self.calendar_tab), ('assistant', self.assistant_tab), ('settings', self.preferences_page)):
            active = selected == str(target) or key == 'settings' and selected in (str(self.system_tab), str(self.activity_tab))
            self.quiet_nav[key].state(['selected'] if active else ['!selected'])

    def _quiet_ask(self, prompt):
        self._select_quiet_page('assistant')
        self.assistant_panel.prefill_prompt(prompt)

    def _create_quiet_settings_body(self):
        style = ttk.Style(self.root)
        for base in ('Title.TLabel', 'Subtitle.TLabel'):
            style.configure('Settings.' + base, background='#FFFFFF')
        self.settings_scroll = ScrollPage(self.preferences_page)
        self.settings_scroll.pack(fill=tk.BOTH, expand=True)
        self.settings_sections = {}
        body = tk.Frame(self.settings_scroll.content, bg='#FFFFFF', padx=34, pady=30)
        body.pack(fill=tk.BOTH, expand=True)
        return body

    def _create_settings_section(self, parent, title, *, before=None):
        card = RoundedCard(parent, fill='#F7F9FC', padding=22)
        options = {'before': self.settings_sections[before]} if before else {}
        card.pack(fill=tk.X, pady=(0, 18), **options)
        self.settings_sections[title] = card
        label(card.content, title, size=19, bold=True).pack(anchor=tk.W, pady=(0, 16))
        content = ttk.Frame(card.content)
        content.pack(fill=tk.X)
        return content

    def _finish_quiet_layout(self, preferences):
        automatic = self._create_settings_section(preferences, 'Automatic booking')
        ttk.Button(automatic, text='Manage schedule', command=self.view_scheduled_tasks).pack(side=tk.RIGHT)
        ttk.Label(automatic, text='View, install or repair the automatic schedule.',
                  wraplength=430).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 16))
        links = tk.Frame(preferences, bg='#FFFFFF')
        links.pack(fill=tk.X, pady=(6, 0))
        for text, command in (
            ('System details', lambda:self.main_notebook.select(self.system_tab)),
            ('Activity and history', lambda:self.main_notebook.select(self.activity_tab)),
            ('Refresh bookings', self._refresh_quiet_agenda),
        ):
            ttk.Button(links, text=text, command=command, style='QuietLink.TButton').pack(side=tk.LEFT, padx=(0, 16))

        # Scope the card surface styles to Settings; dialogs keep their own styles.
        style = ttk.Style(self.root)
        def style_section(widget):
            if isinstance(widget, (ttk.Frame, ttk.Label, ttk.Checkbutton)):
                base = widget.cget('style') or widget.winfo_class()
                name = 'Settings.' + base
                style.configure(name, background='#F7F9FC')
                if isinstance(widget, ttk.Checkbutton):
                    style.map(name, background=[('active', '#F7F9FC')])
                widget.configure(style=name)
            if isinstance(widget, ttk.Label) and widget.cget('wraplength'):
                # Summary text must fit alongside its editor button at minimum size.
                widget.bind('<Configure>', lambda e, w=widget: w.configure(wraplength=max(40, e.width)))
            for child in widget.winfo_children():
                style_section(child)
        for card in self.settings_sections.values():
            style_section(card.content)
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
        self.week_panel.update_data(events, available=snapshot is not None, stale=result.stale, checked=checked, planned=planned)
        self.today_panel.refresh_button.configure(state=tk.DISABLED if self.is_running else tk.NORMAL)

    def _refresh_quiet_agenda(self):
        if self.is_running or self.login_operation_in_progress:
            return
        self.is_running = True
        for control in (self.run_visible_btn, self.run_headless_btn, self.plan_refresh_btn):
            control.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.progress_var.set('Checking your bookings…')
        self.today_panel.refresh_button.configure(state=tk.DISABLED)
        self.today_panel.freshness.configure(text='Checking your bookings…')
        threading.Thread(target=self._run_booker_thread,
            args=(True, ('--agenda-only', '--wait-for-runtime-seconds', '180'), 'Agenda refresh'), daemon=True).start()

    def _show_quiet_booking(self, event):
        if not event or not event.get('isReservation'):
            return
        dialog = tk.Toplevel(self.root)
        dialog.title('Your booking')
        dialog.geometry('560x670')
        dialog.minsize(500, 600)
        dialog.configure(bg='#FFFFFF')
        dialog.transient(self.root)
        page = ScrollPage(dialog);page.pack(fill=tk.BOTH, expand=True)
        body = tk.Frame(page.content, bg='#FFFFFF', padx=30, pady=24);body.pack(fill=tk.BOTH, expand=True)
        ttk.Button(body,text='← Back',command=dialog.destroy,style='QuietLink.TButton').pack(anchor=tk.W,pady=(0,16))
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
        ttk.Button(body,text='Ask to cancel booking',command=lambda:ask(f'Cancel my reservation in {identity}. Check the live agenda and exact booking before acting.'),style='Danger.TButton').pack(fill=tk.X,pady=(0,16))
        label(body,'The assistant checks the current booking before making changes.',size=13,color='#667080',wraplength=440).pack(anchor=tk.W)
