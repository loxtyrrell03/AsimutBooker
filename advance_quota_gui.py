"""Compact desktop editor for the shared advance-quota preferences."""
import tkinter as tk
from tkinter import ttk
from copy import deepcopy

from advance_preferences import apply_advance_quota, load_advance_quota
from open_canvas_ui import HelpTip
from app_settings import SettingsError
from booking_strategy import load_booking_strategy
from booking_rules import load_booking_rules
from practice_plan import load_practice_plan

DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')


def show_editor(app):
    document = app.load_settings()
    opening = load_advance_quota(document).to_dict()
    value = deepcopy(opening)
    dialog = app._open_detail_page('advance_quota')
    dialog.title('Advance quota')
    body = ttk.Frame(dialog, padding=24)
    body.pack(fill='both', expand=True)
    ttk.Label(body, text='Advance quota', font=(app.ui_display_font_family, 20, 'bold')).pack(anchor='w')
    ttk.Label(body, text='Choose how to spend your rolling advance hours. Existing bookings stay in place.', wraplength=680).pack(anchor='w', pady=(6, 16))
    if (not load_practice_plan(document).enabled or not load_booking_strategy(document).daily_planning.enabled
            or load_booking_rules(document).preset == 'legacy'):
        ttk.Label(body, text='These choices apply with a daily practice goal, Plan ahead enabled, and New or Custom booking rules.', wraplength=680).pack(anchor='w', pady=(0,12))
    variables, getters = {}, {}

    def form(parent):
        frame = ttk.Frame(parent)
        frame.pack(fill='x')
        frame.columnconfigure(3, weight=1)
        return frame

    def field(parent, key, title, *, choices=None, maximum=None, step=15, help_text=''):
        row = parent.grid_size()[1]
        variable = tk.StringVar(value=str(value[key]) if value[key] is not None else '')
        variables[key] = variable
        ttk.Label(parent, text=title, wraplength=230).grid(row=row, column=0, sticky='w', pady=8, padx=(0, 16))
        if choices:
            variable.set(next(label for label, val in choices.items() if val == value[key]))
            widget = ttk.Combobox(parent, textvariable=variable, values=tuple(choices), state='readonly', width=29)
            getters[key] = lambda: choices[variable.get()]
        else:
            widget = ttk.Spinbox(parent, textvariable=variable, from_=0, to=maximum, increment=step, width=12)
            getters[key] = lambda: None if key == 'fallback_lead_minutes' and not variable.get().strip() else int(variable.get())
        widget.grid(row=row, column=1, sticky='w', pady=8)
        if help_text:
            HelpTip(parent, help_text).grid(row=row, column=2, padx=6)
        return widget

    def check(parent, key, title, help_text=''):
        variable = tk.BooleanVar(value=value[key])
        variables[key] = variable
        getters[key] = variable.get
        line = ttk.Frame(parent); line.pack(fill='x', pady=8)
        ttk.Checkbutton(line, text=title, variable=variable).pack(side='left')
        if help_text:
            HelpTip(line, help_text).pack(side='left', padx=6)

    def details(title):
        outer = ttk.Frame(body); outer.pack(fill='x', pady=6)
        content = ttk.Frame(outer, padding=(10, 8))
        button = ttk.Button(outer, text='▸ ' + title)
        button.pack(anchor='w')
        def toggle():
            if content.winfo_manager():
                content.pack_forget(); button.configure(text='▸ ' + title)
            else:
                content.pack(fill='x'); button.configure(text='▾ ' + title)
        button.configure(command=toggle)
        return content

    main = form(body)
    field(main, 'distribution', 'Distribution', choices={'Spread across days':'balanced', 'Follow day priorities':'weighted', 'Group into fewer days':'concentrated', 'Best rooms and times first':'quality'}, help_text='Spread protects a daily block. Day priorities aim for proportionally more time on higher-priority days. Grouping favours fewer days; quality favours ranked rooms and periods.')
    field(main, 'anchor_minutes', 'Daily block to protect (minutes)', maximum=120, help_text='An aim for spreading and day priorities, not a guarantee or minimum. Six hours across seven days may mean 45–60 minutes per day.')
    field(main, 'block_minutes', 'Preferred advance session', choices={'Use Booking strategy':0, '30 minutes':30, '1 hour':60, '1½ hours':90, '2 hours':120})

    rooms = details('Rooms and time periods')
    room_form = form(rooms)
    field(room_form, 'room_mode', 'Rooms to use', choices={'Top favourite rooms':'top', 'Choose and rank rooms':'selected', 'All eligible favourite rooms':'all'})
    top_count = field(room_form, 'top_room_count', 'Number of top rooms', maximum=100, step=1, help_text='Used with Top favourite rooms. Your general room order and exclusions still apply.')
    check(rooms, 'room_fallback', 'Allow other eligible rooms as fallbacks')
    field(room_form, 'priority_mode', 'Room or time priority', choices={'Use Booking strategy':'inherit', 'Periods before room rank':'time_first', 'Room rank before periods':'room_first'})
    selection = ttk.Frame(rooms); selection.pack(fill='x')
    ttk.Label(selection, text='Selected room order', font=(app.ui_font_family, 11, 'bold')).pack(anchor='w', pady=(12, 4))
    room_list = tk.Listbox(selection, height=4, exportselection=False)
    room_list.pack(fill='x')
    def render_rooms(selection=0):
        room_list.delete(0, 'end')
        for name in value['room_order']: room_list.insert('end', name)
        if value['room_order']: room_list.selection_set(max(0, min(selection, len(value['room_order'])-1)))
    render_rooms()
    room_actions = ttk.Frame(selection); room_actions.pack(fill='x', pady=5)
    room_choice = tk.StringVar()
    eligible = [r for r in app.room_preferences.ordered_rooms if r not in app.room_preferences.excluded_rooms]
    ttk.Combobox(room_actions, textvariable=room_choice, values=eligible, state='readonly', width=23).pack(side='left')
    def add_room():
        name = room_choice.get()
        if name and name not in value['room_order']:
            value['room_order'].append(name); render_rooms(len(value['room_order'])-1)
    def move_room(delta):
        if not room_list.curselection(): return
        index = room_list.curselection()[0]
        if 0 <= index+delta < len(value['room_order']):
            value['room_order'][index], value['room_order'][index+delta] = value['room_order'][index+delta], value['room_order'][index]
            render_rooms(index+delta)
    def remove_room():
        if room_list.curselection():
            index = room_list.curselection()[0]; value['room_order'].pop(index); render_rooms(index)
    for title, callback in (('Add room', add_room), ('↑', lambda:move_room(-1)), ('↓', lambda:move_room(1)), ('Remove', remove_room)):
        ttk.Button(room_actions, text=title, command=callback, width=9).pack(side='left', padx=2)
    period_heading = ttk.Frame(rooms); period_heading.pack(fill='x', pady=(14, 4))
    count_controls = room_form.grid_slaves(row=top_count.grid_info()['row'])
    def room_mode_changed(*_):
        mode = getters['room_mode']()
        for widget in count_controls:
            widget.grid() if mode == 'top' else widget.grid_remove()
        if mode == 'selected': selection.pack(fill='x', before=period_heading)
        else: selection.pack_forget()
    variables['room_mode'].trace_add('write', room_mode_changed)
    room_mode_changed()
    ttk.Label(period_heading, text='Ranked time periods', font=(app.ui_font_family, 11, 'bold')).pack(side='left')
    HelpTip(period_heading, 'Earlier rows have higher priority. No custom periods uses Preferred times. Custom periods replace soft preferences only; strict hours still apply.').pack(side='left')
    period_body = ttk.Frame(rooms); period_body.pack(fill='x')
    period_vars = []
    clocks = [f'{m//60:02d}:{m%60:02d}' for m in range(0, 1440, 15)]
    def read_periods():
        return [dict(days=[d for d, v in enumerate(days) if v.get()], start=start.get(), end=end.get()) for days, start, end in period_vars]
    def render_periods():
        for widget in period_body.winfo_children(): widget.destroy()
        period_vars.clear()
        if not value['periods']:
            ttk.Label(period_body, text='Using your Preferred times.').pack(anchor='w')
        for index, period in enumerate(value['periods']):
            panel = ttk.LabelFrame(period_body, text=f'Priority {index+1}', padding=8); panel.pack(fill='x', pady=6)
            weekdays = ttk.Frame(panel); weekdays.pack(fill='x')
            day_vars = [tk.BooleanVar(value=d in period['days']) for d in range(7)]
            for d, var in enumerate(day_vars): ttk.Checkbutton(weekdays, text=DAYS[d][:3], variable=var).pack(side='left', padx=2)
            times = ttk.Frame(panel); times.pack(fill='x', pady=6)
            start, end = tk.StringVar(value=period['start']), tk.StringVar(value=period['end'])
            period_vars.append((day_vars, start, end))
            for title, var in (('From', start), ('Until', end)):
                ttk.Label(times, text=title).pack(side='left', padx=4)
                ttk.Combobox(times, textvariable=var, values=clocks, state='readonly', width=6).pack(side='left')
            for title, delta in (('↑', -1), ('↓', 1), ('Remove', 0)):
                button = ttk.Button(times, text=title, width=7, command=lambda i=index,d=delta:edit_period(i,d))
                button.pack(side='left', padx=2)
                if delta and not 0 <= index+delta < len(value['periods']): button.state(['disabled'])
    def edit_period(index, delta):
        value['periods'] = read_periods()
        if delta:
            value['periods'][index], value['periods'][index+delta] = value['periods'][index+delta], value['periods'][index]
        else: value['periods'].pop(index)
        render_periods()
    def add_period():
        value['periods'] = read_periods()
        if len(value['periods']) < 14:
            value['periods'].append(dict(days=list(range(7)), start='16:00', end='18:00')); render_periods()
    render_periods()
    ttk.Button(rooms, text='Add time period', command=add_period).pack(anchor='w', pady=6)
    field(form(rooms), 'period_mode', 'Outside these periods', choices={'Allow suitable alternatives':'prefer', 'Only within these periods':'only'})

    weekdays = details('Day priorities and limits')
    ttk.Label(weekdays, text='Priority 0 stops advance bookings on that weekday. Other weights apply to Follow day priorities.', wraplength=650).pack(anchor='w')
    day_form = form(weekdays)
    for col, title in enumerate(('Weekday', 'Priority (0–10)', 'Limit (minutes)')): ttk.Label(day_form, text=title).grid(row=0, column=col, sticky='w', padx=8)
    weight_vars, cap_vars = [], []
    for d, day in enumerate(DAYS):
        weight, cap = tk.StringVar(value=str(value['day_weights'][d])), tk.StringVar(value=str(value['day_caps_minutes'][d]))
        weight_vars.append(weight); cap_vars.append(cap)
        ttk.Label(day_form, text=day).grid(row=d+1, column=0, sticky='w', padx=8)
        ttk.Spinbox(day_form, textvariable=weight, from_=0, to=10, width=10).grid(row=d+1, column=1, sticky='w', pady=5, padx=8)
        ttk.Spinbox(day_form, textvariable=cap, from_=0, to=720, increment=15, width=10).grid(row=d+1, column=2, sticky='w', padx=8)
    ttk.Label(weekdays, text='Limit 0 uses your daily target. Limits count booked time and held extensions. Last-minute practice can still fill the rest of your target.', wraplength=650).pack(anchor='w', pady=8)

    wait = details('Waiting and credit reserve')
    check(wait, 'wait_for_opening', 'Plan for rooms whose booking window opens later', 'Keep an allocation for suitable future room openings. If off, consider only rooms bookable now. Fresh availability still decides every booking.')
    wait_form = form(wait)
    field(wait_form, 'reserve_minutes', 'Keep credit unused (minutes)', maximum=10080, help_text='Withhold this much available credit from advance planning. Imminent and last-minute practice may still consume remaining credit normally.')
    field(wait_form, 'fallback_lead_minutes', 'Release credit before start (minutes)', maximum=1440, help_text='With advance credit remaining, allow last-minute spending this long before start. Blank uses Booking strategy. Exhausted credit uses the College free window.')
    field(wait_form, 'date_order', 'Equal choices between dates', choices={'Use Booking strategy':'inherit', 'Nearest date first':'nearest', 'Furthest date first':'furthest'})
    error = tk.StringVar()
    ttk.Label(body, textvariable=error, foreground='#b42318', wraplength=650).pack(fill='x', pady=8)
    buttons = ttk.Frame(body); buttons.pack(fill='x', pady=10)
    def save():
        try:
            candidate = {**value, **{key: get() for key, get in getters.items()}, 'periods':read_periods(),
                         'day_weights':[int(v.get()) for v in weight_vars], 'day_caps_minutes':[int(v.get()) for v in cap_vars]}
            validated = load_advance_quota({'advance_quota': candidate})
            def mutate(settings):
                if load_advance_quota(settings).to_dict() != opening:
                    raise ValueError('Advance quota changed elsewhere. Cancel and reopen this editor before saving.')
                apply_advance_quota(settings, validated.to_dict())
            # A settings conflict leaves the draft open without disabling unrelated controls.
            from preference_runs import update_preferences
            import gui
            if not app.settings_available: raise ValueError('Settings are unavailable. Reopen after resolving the settings error.')
            update_preferences(mutate, gui.SETTINGS_FILE)
        except (SettingsError, TypeError, ValueError) as exc:
            error.set(str(exc)); return
        app._refresh_booking_plan_display()
        app.log('Advance quota preferences saved.', 'success')
        dialog.destroy()
    ttk.Button(buttons, text='Save advance quota', command=save).pack(side='left')
    ttk.Button(buttons, text='Cancel', command=dialog.destroy).pack(side='left', padx=8)
    return dialog
