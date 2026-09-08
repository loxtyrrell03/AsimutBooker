"""Focused, scrollable desktop operations page; callbacks remain host-owned."""
import tkinter as tk
from tkinter import ttk


def build_system_details(app, parent, colors, labels, symbols, state_colors):
    canvas = tk.Canvas(parent, background=colors['background'] if 'background' in colors else '#f4f5f7', highlightthickness=0)
    scroll = ttk.Scrollbar(parent, orient='vertical', command=canvas.yview)
    scroll.pack(side='right', fill='y')
    canvas.pack(side='left', fill='both', expand=True)
    canvas.configure(yscrollcommand=scroll.set)
    body = ttk.Frame(canvas, style='Page.TFrame', padding=(24, 20))
    window = canvas.create_window(0, 0, window=body, anchor='nw')
    canvas.bind('<Configure>', lambda e: canvas.itemconfigure(window, width=e.width))
    body.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
    app.system_details_body = body

    def label(host, text=None, variable=None, style='CardCaption.TLabel'):
        widget = ttk.Label(host, text=text, textvariable=variable, style=style, justify='left')
        widget.pack(fill='x', anchor='w')
        widget.bind('<Configure>', lambda e: widget.configure(wraplength=max(80, e.width)))
        return widget

    def card():
        frame = ttk.Frame(body, style='Card.TFrame', padding=(24, 18))
        frame.pack(fill='x', pady=(0, 16))
        return frame

    label(body, 'System details', style='Title.TLabel')
    subtitle = label(body, 'Automation, manual runs and connection health.', style='Caption.TLabel')
    subtitle.pack_configure(pady=(4, 18))
    status = card()
    hero = ttk.Frame(status, style='Card.TFrame')
    hero.pack(fill='x', pady=(0, 8))
    app.automation_state_label = ttk.Label(hero, text=symbols['unknown'], foreground=state_colors['unknown'], background=colors['surface'], font=(app.ui_font_family, 18, 'bold'))
    app.automation_state_label.pack(side='left', padx=(0, 10))
    label(hero, variable=app.automation_status_var, style='CardSection.TLabel')
    label(status, variable=app.automation_detail_var)
    ttk.Separator(status).pack(fill='x', pady=14)
    summaries = ttk.Frame(status, style='Card.TFrame')
    summaries.pack(fill='x')
    for col, (caption, key) in enumerate((('NEXT AUTOMATIC RUN', 'next_run'), ('LAST COMPLETED RUN', 'last_success'))):
        summaries.columnconfigure(col, weight=1, uniform='summary')
        cell = ttk.Frame(summaries, style='Card.TFrame')
        cell.grid(row=0, column=col, sticky='nsew', padx=(0, 16) if col == 0 else (16, 0))
        label(cell, caption, style='CardEyebrow.TLabel')
        label(cell, variable=app.health_headline_vars[key], style='CardStrong.TLabel').pack_configure(pady=(4, 0))

    runs = card()
    label(runs, 'Run manually', style='CardSection.TLabel')
    label(runs, 'Book rooms now using your saved preferences.').pack_configure(pady=(4, 12))
    actions = ttk.Frame(runs, style='Card.TFrame')
    actions.pack(fill='x')
    app.run_headless_btn = ttk.Button(actions, text='Run in background', command=lambda: app.run_booker(headless=True), style='Primary.TButton')
    app.run_visible_btn = ttk.Button(actions, text='Run with browser', command=lambda: app.run_booker(headless=False))
    app.stop_btn = ttk.Button(actions, text='Stop', command=app.stop_booker, style='Danger.TButton', state=tk.DISABLED)
    for button in (app.run_headless_btn, app.run_visible_btn, app.stop_btn):
        button.pack(side='left', padx=(0, 10))
    app.progress_var = tk.StringVar(value='')
    app.progress_label = label(runs, variable=app.progress_var)
    app.progress_label.pack_forget()

    def progress(*_):
        if app.progress_var.get().strip():
            app.progress_label.pack(fill='x', pady=(10, 0))
        else:
            app.progress_label.pack_forget()
    app.progress_var.trace_add('write', progress)

    health = card()
    header = ttk.Frame(health, style='Card.TFrame')
    header.pack(fill='x', pady=(0, 12))
    ttk.Label(header, text='Health checks', style='CardSection.TLabel').pack(side='left')
    ttk.Button(header, text='Refresh', command=app.refresh_status, style='Toolbar.TButton').pack(side='right')
    ttk.Button(header, text='Details', command=app.show_health_details, style='Toolbar.TButton').pack(side='right', padx=8)
    ttk.Label(header, textvariable=app.health_updated_var, style='CardCaption.TLabel').pack(side='right', padx=12)
    grid = ttk.Frame(health, style='Card.TFrame')
    grid.pack(fill='x')
    for col in range(3):
        grid.columnconfigure(col, weight=1, uniform='checks')
    for index, key in enumerate(app.health_headline_vars):
        cell = ttk.Frame(grid, style='Card.TFrame')
        cell.grid(row=index // 3, column=index % 3, sticky='ew', pady=8, padx=(0, 12))
        dot = ttk.Label(cell, text=symbols['unknown'], foreground=state_colors['unknown'], background=colors['surface'], font=(app.ui_font_family, 12, 'bold'))
        dot.pack(side='left', padx=(0, 8))
        label(cell, labels[key], style='CardStrong.TLabel')
        app.health_state_labels[key] = dot
    label(health, 'Open Details for the evidence behind each check.').pack_configure(pady=(12, 0))

    troubleshooting = card()
    tools = ttk.Frame(troubleshooting, style='Card.TFrame')
    def toggle():
        if tools.winfo_manager():
            tools.pack_forget()
            disclosure.configure(text='▸ Troubleshooting')
        else:
            tools.pack(fill='x', pady=(12, 0))
            disclosure.configure(text='▾ Troubleshooting')
    disclosure = ttk.Button(troubleshooting, text='▸ Troubleshooting', command=toggle, style='Toolbar.TButton')
    disclosure.pack(anchor='w')
    app.system_troubleshooting_toggle = disclosure
    for col in range(2):
        tools.columnconfigure(col, weight=1, uniform='tools')
    specs = (
        ('login_check_btn', 'Check login', app.check_repair_login),
        ('login_update_btn', 'Secure login', app.update_secure_login),
        (None, 'Find available rooms', app.show_scan_rooms_dialog),
        ('plan_refresh_btn', 'Refresh booking plan', app.refresh_booking_plan),
    )
    for index, (attribute, text, command) in enumerate(specs):
        button = ttk.Button(tools, text=text, command=command)
        button.grid(row=index // 2, column=index % 2, sticky='ew', padx=(0, 12), pady=5)
        if attribute:
            setattr(app, attribute, button)

    def wheel(event):
        if canvas.bbox('all')[3] > canvas.winfo_height():
            canvas.yview_scroll(-int(event.delta / 120), 'units')
        return 'break'
    def bind_wheel(widget):
        widget.bind('<MouseWheel>', wheel, add='+')
        for child in widget.winfo_children():
            bind_wheel(child)
    bind_wheel(parent)
