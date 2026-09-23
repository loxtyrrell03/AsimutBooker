"""Editable synthetic review boards. No Booker imports, live data or requests."""
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BLUE, TINT, INK, MUTED = '#0868d9', '#eaf3ff', '#1d2430', '#667080'
PAGE, LINE, WHITE, AMBER = '#f8fafc', '#e1e6ee', '#ffffff', '#93620c'


class Board:
    def __init__(self, w, h, title, subtitle):
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{escape(title)}">',
                      f'<rect width="100%" height="100%" fill="{PAGE}"/>',
                      '<style>text{font-family:Segoe UI,Arial,sans-serif}</style>']
        self.text(24, 42, title, 29, bold=True)
        self.text(24, 74, subtitle, 15, MUTED, limit=w-48)
        self.text(24, 98, 'DESIGN PREVIEW · All events, rooms, dates and outcomes are invented examples.', 12, MUTED)

    def box(self, x, y, w, h, fill=WHITE, radius=14, stroke=LINE, dash=False):
        d = ' stroke-dasharray="5 4"' if dash else ''
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}"{d}/>')

    def text(self, x, y, value, size=14, color=INK, bold=False, anchor='start', limit=None):
        bound = f' data-max-width="{limit}"' if limit else ''
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{650 if bold else 400}" fill="{color}" text-anchor="{anchor}"{bound}>{escape(value)}</text>')

    def lines(self, x, y, rows, size=14, color=MUTED, step=23, limit=None):
        for n, row in enumerate(rows):
            self.text(x, y+n*step, row, size, color, limit=limit)

    def button(self, x, y, w, title, primary=False, h=44):
        self.box(x, y, w, h, BLUE if primary else WHITE, 10, BLUE if primary else LINE)
        self.text(x+w/2, y+h/2+5, title, 13, WHITE if primary else BLUE, True, 'middle', w-16)

    def line(self, x, y, w):
        self.parts.append(f'<path d="M{x} {y}h{w}" stroke="{LINE}"/>')

    def toggle(self, x, y, on=False):
        self.box(x, y, 42, 24, BLUE if on else '#c9d1dc', 12, BLUE if on else '#c9d1dc')
        self.parts.append(f'<circle cx="{x+(30 if on else 12)}" cy="{y+12}" r="9" fill="white"/>')

    def check(self, x, y, on=False):
        self.box(x, y, 22, 22, BLUE if on else WHITE, 5, BLUE if on else '#aab5c4')
        if on:
            self.parts.append(f'<path d="M{x+5} {y+11}l4 4 8-9" fill="none" stroke="white" stroke-width="2"/>')

    def help(self, x, y):
        self.parts.append(f'<circle cx="{x}" cy="{y}" r="10" fill="{TINT}"/>')
        self.text(x, y+4, '?', 13, BLUE, True, 'middle')

    def pill(self, x, y, label, w=112, amber=False):
        self.box(x, y, w, 24, '#fff2d9' if amber else TINT, 12, '#fff2d9' if amber else TINT)
        self.text(x+w/2, y+16, label, 11, AMBER if amber else BLUE, True, 'middle', w-10)

    def save(self, name):
        (ROOT/name).write_text('\n'.join(self.parts+['</svg>']), encoding='utf-8')


def brand(b, x, y):
    b.box(x, y, 30, 30, BLUE, 9, BLUE)
    for dx, h in ((8, 12), (15, 21), (22, 16)):
        b.parts.append(f'<path d="M{x+dx} {y+24}v-{h}" stroke="white" stroke-width="3" stroke-linecap="round"/>')
    b.text(x+42, y+22, 'Asimut', 21, bold=True)


def phone(b, x, y, w=390, h=860, tab='Today'):
    b.box(x, y, w, h, PAGE, 24)
    brand(b, x+20, y+18)
    b.text(x+w-20, y+38, 'Connected', 11, MUTED, anchor='end')
    b.line(x, y+66, w)
    b.box(x+1, y+h-66, w-2, 64, WHITE, 20)
    for i, label in enumerate(('Today', 'My Week', 'Calendar', 'Assistant', 'Settings')):
        nx=x+(i+.5)*w/5
        if label==tab:
            b.box(nx-w/10+4, y+h-59, w/5-8, 48, TINT, 12, TINT)
        b.text(nx, y+h-35, ('◷', '≡', '▦', '◇', '⚙')[i], 16, BLUE if label==tab else MUTED, anchor='middle')
        b.text(nx, y+h-17, label, 10, BLUE if label==tab else MUTED, anchor='middle')


def pc(b, x=24, y=122, w=980, h=522):
    b.box(x, y, w, h, PAGE, 20)
    b.box(x+1, y+1, w-2, 66, WHITE, 20)
    brand(b, x+24, y+18)
    for i, label in enumerate(('Today', 'My Week', 'Calendar', 'Assistant', 'Settings')):
        b.text(x+24, y+118+i*51, label, 14, BLUE if i==1 else MUTED, i==1)
    b.text(x+170, y+113, 'My Week', 27, bold=True)
    b.text(x+170, y+139, '21–27 September · 6 hours booked', 13, MUTED)
    b.text(x+w-24, y+111, 'Refresh', 13, BLUE, anchor='end')


def event_row(b, x, y, w, title, time, on=False, mode='toggle', reservation=False):
    b.box(x, y, w, 78, TINT if reservation else '#fff8ed', 12, LINE)
    offset=48 if mode=='check' else 16
    if mode=='check':
        b.check(x+14, y+17, on)
    b.text(x+offset, y+26, title, 15, BLUE if reservation else INK, True, limit=w-offset-115)
    b.text(x+offset, y+47, time, 12, MUTED, limit=w-offset-16)
    if reservation:
        b.text(x+w-16, y+25, 'Booked', 11, BLUE, anchor='end')
    elif mode=='toggle':
        b.toggle(x+w-60, y+15, on)
        b.text(x+offset, y+67, 'Allow practice during this event', 12, BLUE if on else MUTED)
    elif on:
        b.text(x+w-16, y+25, 'Selected', 11, BLUE, True, 'end')


def room_now(b, x, y, option):
    phone(b, x, y)
    b.text(x+20, y+109, 'Today', 29, bold=True)
    b.text(x+20, y+135, 'Wednesday 23 September', 13, MUTED)
    b.box(x+14, y+154, 362, 572)
    b.text(x+30, y+190, 'Find me a room now', 23, bold=True)
    b.box(x+30, y+208, 330, 46, '#f1f4f8', 11, '#f1f4f8')
    b.box(x+34, y+212, 161, 38, WHITE, 9)
    b.text(x+113, y+237, 'Preferred duration', 12, BLUE, True, 'middle')
    b.text(x+276, y+237, 'Longest possible', 12, MUTED, anchor='middle')
    b.text(x+30, y+282, 'Preferred duration', 13, MUTED)
    b.help(x+170, y+277)
    for i, label in enumerate(('30 min', '1 hour', '90 min', '2 hours')):
        b.button(x+30+i*84, y+296, 78, label, i==1)
    b.text(x+360, y+367, 'Custom duration', 12, BLUE, anchor='end')
    b.line(x+30, y+383, 330)
    b.text(x+30, y+413, 'Override events', 16, bold=True)
    b.help(x+173, y+408)
    b.toggle(x+318, y+395, True)
    b.text(x+30, y+438, 'This search only', 12, BLUE, True)
    if option=='A':
        b.check(x+30, y+458, True)
        b.text(x+64, y+475, 'Performance workshop', 14, bold=True)
        b.text(x+64, y+496, 'Today · 14:00–15:30 · Studio 1', 12, MUTED)
        b.check(x+30, y+516, False)
        b.text(x+64, y+533, 'Ensemble rehearsal', 14, bold=True)
        b.text(x+64, y+554, 'Today · 16:00–17:00 · Studio 2', 12, MUTED)
    else:
        b.button(x+30, y+459, 330, 'Choose today’s events' if option=='B' else 'Select events for this search')
        b.pill(x+30, y+519, '1 event selected', 134)
        b.text(x+30, y+564, 'Performance workshop · 14:00–15:30', 12, INK)
    b.text(x+30, y+592, 'Saved overrides: 1 event', 12, MUTED)
    b.text(x+360, y+592, 'View', 12, BLUE, anchor='end')
    b.button(x+30, y+611, 330, 'Find me a room now', True, 48)
    b.text(x+195, y+690, 'May book during the selected events.', 12, MUTED, anchor='middle')
    b.text(x+20, y+757, 'Also today', 18, bold=True)
    b.text(x+370, y+757, 'My Week →', 13, BLUE, anchor='end')


def details(b, x, y):
    b.box(x, y, 475, 348)
    b.text(x+20, y+33, 'Event details · every option', 18, bold=True)
    b.text(x+20, y+72, 'Performance workshop', 21, bold=True)
    b.lines(x+20, y+99, ['Wednesday 23 September · 14:00–15:30', 'Studio 1 · College event'], 13)
    b.text(x+20, y+162, 'Allow practice during this event', 15, bold=True)
    b.toggle(x+413, y+143, True)
    b.help(x+267, y+157)
    b.text(x+20, y+188, 'Applies to this dated event. It stays on your calendar.', 13, MUTED, limit=435)
    b.text(x+20, y+230, 'Saving will queue an automatic booking check.', 13, INK)
    b.button(x+20, y+247, 270, 'Save override', True)
    b.button(x+300, y+247, 155, 'Cancel')
    b.text(x+20, y+325, 'Before saving: review the exact event and consequence.', 12, MUTED)


def settings(b, x, y, option):
    b.box(x, y, 475, 348)
    b.text(x+20, y+33, 'Settings · Your practice', 18, bold=True)
    for i, (title, value) in enumerate((('Daily goal', '6 hours'), ('Preferred times', 'Noon–Rooms closed'), ('Event overrides', '1 saved event'))):
        yy=y+76+i*61
        b.text(x+20, yy, title, 15, BLUE if i==2 else INK, i==2)
        b.text(x+455, yy, value+'  ›', 13, MUTED, anchor='end')
        b.line(x+20, yy+21, 435)
    label={'A':'A · One switch beside each college event.', 'B':'B · Select several events on the calendar.', 'C':'C · Manage your choices in one searchable list.'}[option]
    b.text(x+20, y+252, label, 14, BLUE, True, limit=435)
    b.lines(x+20, y+281, ['Saved choices apply to automatic booking and room-now.', 'Temporary choices apply to one room-now request only.'], 12, limit=435)


def option_board(option):
    titles={'A':'Switches beside events', 'B':'Select on the calendar', 'C':'One event manager'}
    subs={'A':'Recommended · Change a lesson where you already see it; choose extra room-now overrides inline.',
          'B':'Select multiple dated events in a calendar mode, then review and save the group.',
          'C':'Search and manage event overrides on a dedicated page, with shortcuts from each event.'}
    b=Board(1460, 1100, f'{option} — {titles[option]}', subs[option])
    pc(b)
    x=194
    if option=='A':
        b.text(194, 297, 'WEDNESDAY 23', 12, MUTED, True)
        event_row(b, x, 311, 786, 'Performance workshop', '14:00–15:30 · Studio 1', True)
        event_row(b, x, 399, 786, 'Ensemble rehearsal', '16:00–17:00 · Studio 2')
        event_row(b, x, 487, 786, 'Room Weston', '17:30–18:30 · 1 hour of practice', reservation=True)
        b.text(x, 603, '1 changed event · automatic check after saving', 12, MUTED)
        b.button(677, 578, 177, 'Save overrides', True)
        b.button(864, 578, 116, 'Cancel')
    elif option=='B':
        b.button(x, 283, 197, 'Select events to override', True)
        b.text(410, 310, 'Click the college events you will skip.', 13, MUTED)
        for i, (day, title, time, on) in enumerate((('Wed 23', 'Performance workshop', '14:00–15:30', True), ('Thu 24', 'Seminar', '11:00–12:00', True), ('Fri 25', 'Piano lesson', '10:00–11:00', False))):
            xx=x+i*266
            b.box(xx, 342, 254, 174)
            b.text(xx+14, 369, day, 14, bold=True)
            b.box(xx+10, 387, 234, 112, '#fff8ed', 11, BLUE if on else LINE)
            b.check(xx+22, 401, on)
            b.text(xx+22, 447, title, 14, bold=True, limit=208)
            b.text(xx+22, 474, time, 13, MUTED)
        b.text(x, 548, '2 selected events · review before saving', 14, bold=True)
        b.text(x, 606, 'Automatic booking check after saving.', 12, MUTED)
        b.button(668, 578, 190, 'Review 2 overrides', True)
        b.button(868, 578, 112, 'Cancel')
    else:
        b.text(x, 296, 'Event overrides', 20, bold=True)
        b.box(x, 314, 486, 44)
        b.text(x+15, 342, 'Search events or rooms…', 14, MUTED)
        b.button(694, 314, 142, 'This week ▾')
        b.button(846, 314, 134, 'Saved only')
        event_row(b, x, 371, 786, 'Performance workshop', 'Wed 23 Sep · 14:00–15:30 · Studio 1', True, 'check')
        event_row(b, x, 461, 786, 'Ensemble rehearsal', 'Wed 23 Sep · 16:00–17:00 · Studio 2', False, 'check')
        b.text(x, 581, '1 selected event', 14, bold=True)
        b.text(x, 608, 'Automatic booking check after saving.', 12, MUTED)
        b.button(677, 578, 177, 'Save overrides', True)
        b.button(864, 578, 116, 'Cancel')
    room_now(b, 1034, 122, option)
    details(b, 24, 672)
    settings(b, 529, 672, option)
    b.text(24, 1060, 'Shared flow and narrow boards include saved choices, help, empty/error states, Stop and uncertain booking recovery.', 14, MUTED)
    b.save(f'option-{option.lower()}.svg')


def state_card(b, n, title, rows, action=None, primary=False, toggle=None, extra=None):
    col, row=n%4, n//4
    x, y=24+col*360, 126+row*306
    b.box(x, y, 338, 284)
    b.text(x+18, y+31, title, 17, bold=True, limit=302)
    if toggle is not None:
        b.text(x+18, y+70, 'Override events', 14, bold=True)
        b.toggle(x+278, y+51, toggle)
        start=y+108
    else:
        start=y+71
    b.lines(x+18, start, rows, 13, step=23, limit=302)
    if extra:
        b.pill(x+18, y+191, extra, 180)
    if action:
        b.button(x+18, y+224, 302, action, primary)


def states_board():
    b=Board(1460, 1402, 'Shared flow — exact events, clear scope', 'All three options use these states. A saved override and a one-off search choice have different lifetimes.')
    states=[
        ('First use / toggle off', ['No extra events selected.', 'Saved overrides: 1 event · View', 'Choose events only if you will skip them.'], 'Find me a room now', True, False, None),
        ('Toggle on / nothing selected', ['This search only', 'Select the events you will skip.', 'No extra events will be overridden.'], 'Choose events', False, True, None),
        ('Select / apply to this search', ['✓ Performance workshop', 'Today · 14:00–15:30 · Studio 1', '□ Ensemble rehearsal · 16:00–17:00', 'Saved: Seminar · 11:00–12:00'], 'Use 1 selected event', True, None, 'This search only'),
        ('Help / hover, focus or tap', ['Allow practice during selected events.', 'Events stay on your calendar.', 'Only saved choices affect later searches.', 'Close × or Escape; return focus.'], 'Close help', False, None, None),
        ('Review / persistent save', ['Allow practice during 1 event?', 'Performance workshop · Wed 23 Sep', '14:00–15:30 · Studio 1', 'Saving queues an automatic check.', 'Cancel keeps your saved choices.'], 'Save override', True, None, None),
        ('Saved / automatic booking off', ['Saved for this dated event.', 'Automatic booking is off.', 'Room-now will use this saved choice.', 'No automatic check was started.'], 'View event', False, None, 'Practice allowed'),
        ('Loading / refreshing', ['Checking your events…', 'Keep the draft visible while refreshing.', 'Event selection is unavailable until', 'the complete agenda is checked.'], 'Refreshing…', False, None, None),
        ('Empty / no eligible events', ['No remaining college events today.', 'Your saved choices still apply.', 'Try another date in Event overrides.', 'Search filter: No matching events.'], 'Back to room search', False, None, None),
        ('Unavailable / stale agenda', ['Could not check your events.', 'No new override has been applied.', 'Your draft is retained.', 'Refresh before selecting or saving.'], 'Refresh events', False, None, None),
        ('Changed event / stale save', ['This event changed since you selected it.', 'Review its current time before trying again.', 'Other draft choices are retained.', 'Nothing was saved.'], 'Review changed event', False, None, None),
        ('Finding / progress and Stop', ['Finding a room…', 'Override: Performance workshop', 'This request only · 14:00–15:30', 'Saved overrides: 1 event also applies.', 'Choices are locked during this request.'], 'Stop', False, None, None),
        ('Stopped / clear temporary choice', ['Stopped before booking.', 'No room was booked.', 'Temporary overrides cleared.', 'Saved event choices are unchanged.'], 'Start a new search', False, None, None),
        ('Booked / verified result', ['Room Weston · Today · 14:15–15:15', '1 hour booked', 'Overlaps Performance workshop', 'Allowed for this search only.', 'The lesson stays on your calendar.'], 'View booking', False, None, 'Booked'),
        ('No room / exact site refusal', ['No eligible room could be booked.', 'An actual site refusal remains visible:', '“ASIMUT did not allow this overlap.”', 'Temporary overrides cleared.'], 'Review events', False, None, None),
        ('Uncertain / lost delivery', ['The booking result needs checking.', 'The original request keeps its choices.', 'Check the outcome before a new search.', 'Do not submit a duplicate booking.'], 'Check booking status', False, None, None),
        ('Restore this event as a conflict', ['Practice will avoid this event again.', 'An existing overlapping room booking', 'will remain. View it to make changes.', 'Saving queues a check if automation is on.'], 'Save: avoid this event', True, None, None),
    ]
    for n, data in enumerate(states):
        state_card(b, n, *data)
    b.text(24, 1380, 'Phone sheets scroll above fixed actions; PC panels use the same wording and state transitions. These are proposed states, not live results.', 13, MUTED)
    b.save('states.svg')


def narrow_board():
    b=Board(1100, 1470, 'Narrow layouts — each option', '320px phone views and a 760px PC layout. Actions remain reachable at reduced height.')
    for i, option in enumerate('ABC'):
        x, y=24+i*364, 144
        b.text(x, 131, {'A':'A · Inline switches', 'B':'B · Calendar selection', 'C':'C · Event manager'}[option], 17, BLUE, True)
        phone(b, x, y, 320, 700, 'Settings' if option=='C' else 'My Week')
        b.text(x+16, y+111, 'Event overrides' if option=='C' else 'My Week', 26, bold=True)
        b.text(x+16, y+138, 'Wednesday 23 September', 12, MUTED)
        if option=='A':
            for j, (title, time, on) in enumerate((('Performance workshop', '14:00–15:30 · Studio 1', True), ('Ensemble rehearsal', '16:00–17:00 · Studio 2', False))):
                yy=y+157+j*143
                b.box(x+12, yy, 296, 130, '#fff8ed')
                b.text(x+26, yy+29, title, 15, bold=True, limit=266)
                b.text(x+26, yy+53, time, 12, MUTED)
                b.text(x+26, yy+98, 'Allow practice', 13, BLUE if on else MUTED)
                b.help(x+150, yy+94)
                b.toggle(x+248, yy+80, on)
            b.lines(x+16, y+477, ['1 changed event', 'Saving queues an automatic check.'], 12)
        elif option=='B':
            b.button(x+16, y+160, 288, 'Selecting events · Done', True)
            b.box(x+16, y+217, 288, 44)
            b.text(x+160, y+245, '‹     Wed 23     Thu 24     Fri 25     ›', 13, BLUE, anchor='middle', limit=270)
            b.check(x+28, y+281, True)
            b.text(x+64, y+298, 'Performance workshop', 14, bold=True, limit=240)
            b.text(x+64, y+321, '14:00–15:30 · Studio 1', 12, MUTED)
            b.check(x+28, y+354, False)
            b.text(x+64, y+371, 'Ensemble rehearsal', 14, bold=True)
            b.text(x+64, y+394, '16:00–17:00 · Studio 2', 12, MUTED)
            b.lines(x+16, y+477, ['2 selected events across 2 days', 'Review before saving.'], 12)
        else:
            b.box(x+16, y+160, 288, 44)
            b.text(x+30, y+188, 'Search events…', 14, MUTED)
            b.button(x+16, y+217, 140, 'This week ▾')
            b.button(x+164, y+217, 140, 'Saved only')
            for j, (title, time, on) in enumerate((('Performance workshop', 'Wed 23 · 14:00–15:30', True), ('Ensemble rehearsal', 'Wed 23 · 16:00–17:00', False))):
                yy=y+280+j*85
                b.check(x+20, yy, on)
                b.text(x+54, yy+17, title, 14, bold=True, limit=250)
                b.text(x+54, yy+40, time, 12, MUTED)
            b.lines(x+16, y+477, ['1 changed event', 'Saving queues an automatic check.'], 12)
        b.button(x+16, y+528, 180, 'Review overrides' if option=='B' else 'Save overrides', True)
        b.button(x+204, y+528, 100, 'Cancel')
        b.text(x+16, y+610, 'Saved choices affect later searches.', 12, MUTED)
    b.text(24, 890, '760px PC · A shown; B and C keep the same action row', 19, bold=True)
    b.box(24, 912, 760, 455, PAGE, 18)
    brand(b, 44, 931)
    b.text(44, 1006, 'Today', 14, MUTED)
    b.text(44, 1051, 'My Week', 14, BLUE, True)
    b.text(44, 1096, 'Calendar', 14, MUTED)
    b.text(44, 1141, 'Settings', 14, MUTED)
    b.text(174, 1010, 'My Week', 25, bold=True)
    event_row(b, 174, 1030, 590, 'Performance workshop', 'Wed 23 Sep · 14:00–15:30 · Studio 1', True)
    event_row(b, 174, 1120, 590, 'Ensemble rehearsal', 'Wed 23 Sep · 16:00–17:00 · Studio 2')
    b.text(174, 1237, 'Saving queues an automatic booking check.', 12, MUTED)
    b.button(174, 1258, 390, 'Save overrides', True)
    b.button(576, 1258, 188, 'Cancel')
    b.box(814, 912, 266, 455)
    b.text(834, 947, 'At 460px height', 18, bold=True)
    b.lines(834, 986, ['Event list scrolls within the sheet.', 'Save / Cancel stay above navigation.', '', 'At 320px width', 'Titles wrap; times stay readable.', 'Touch targets are at least 44px.', '', 'Help', 'Popover stays inside the viewport.', 'Tap × or press Escape to close.', '', 'Ordinary schedule pages retain', 'their existing document scrolling.'], 12, step=25, limit=226)
    b.text(24, 1413, 'Full-sized phone controls keep the existing blue/white palette. No extra app navigation destination is required.', 13, MUTED)
    b.save('narrow.svg')


def gallery():
    cards=[]
    for key, title, desc in (
        ('option-a','A — Switches beside events','Recommended. Select a lesson from your schedule; expand Override events directly in the room finder.'),
        ('option-b','B — Select on the calendar','Select several events across dates, then review the batch. Room-now uses a picker sheet.'),
        ('option-c','C — One event manager','A searchable page for event choices, with shortcuts from event details and room-now.'),
        ('states','Shared flow and states','Setup, persistent versus one-search scope, help, Save/Cancel, loading, empty, errors, progress, Stop and recovery.'),
        ('narrow','Small screens','Each option at 320px, a 760px PC example, and short-height behavior.')):
        cards.append(f'<article id="{key}"><h2>{title}</h2><p>{desc}</p><a href="{key}.svg"><img src="{key}.svg" alt="{title}"></a><a href="{key}.svg">Open full-size editable SVG</a></article>')
    content='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Asimut event overrides — design review</title><style>
    *{box-sizing:border-box}body{margin:0;background:#f8fafc;color:#1d2430;font:16px/1.55 "Segoe UI",sans-serif}main{max-width:1480px;margin:auto;padding:28px 24px}h1{font-size:34px;margin:0 0 12px;line-height:1.2}h2{font-size:24px;margin:0}p{color:#667080;margin:10px 0 18px}article{background:white;border:1px solid #e1e6ee;border-radius:18px;padding:20px;margin:24px 0}img{display:block;width:100%;height:auto;margin:18px 0}a{color:#0868d9}nav{display:flex;flex-wrap:wrap;gap:12px 24px}nav a{display:inline-block;min-height:44px;padding:10px 0}a:focus-visible{outline:3px solid #0868d9;outline-offset:4px}.note{background:#eaf3ff;color:#1d2430;padding:16px;border-radius:14px}@media(max-width:600px){main{padding:22px 12px}h1{font-size:28px}article{padding:12px}h2{font-size:22px}}
    </style><main><h1>Practise during events you’re skipping</h1><p>Three proposals in the current Asimut style. All example data is invented. These are designs awaiting your choice.</p><p class="note">Save an override for a specific dated event, or turn on <strong>Override events</strong> in Find me a room now and select events for that search only. Events remain on your calendar.</p><nav><a href="#option-a">A · Inline</a><a href="#option-b">B · Calendar</a><a href="#option-c">C · Manager</a><a href="#states">All states</a><a href="#narrow">Small screens</a><a href="README.md">Behavior and implementation notes</a></nav>'''
    (ROOT/'index.html').write_text(content+''.join(cards)+'</main></html>', encoding='utf-8')


if __name__=='__main__':
    for option in 'ABC':
        option_board(option)
    states_board()
    narrow_board()
    gallery()
