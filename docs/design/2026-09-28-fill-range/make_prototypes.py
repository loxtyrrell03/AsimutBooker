"""Editable fill-range designs; all schedules are invented examples."""
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('boards', ROOT.parent / '2026-09-22-room-now/make_prototypes.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.ROOT = ROOT


def form(b, x, y, w):
    b.text(x, y, 'Fill time range', 23, bold=True)
    b.help(x+w-12, y-7)
    b.text(x, y+32, 'Book the gaps around existing practice.', 13, m.MUTED)
    for offset, label, value in [(56, 'Date', 'Tuesday 29 September'), (138, 'From', '11:00'), (220, 'Until', '13:00')]:
        b.text(x, y+offset, label, 13, m.MUTED)
        b.box(x, y+offset+12, w, 44, radius=10)
        b.text(x+14, y+offset+40, value, 16)
    b.lines(x, y+294, ['Temporarily overrides your practice settings.', 'Keeps existing bookings. ASIMUT limits apply.'], 12, width=w)
    b.button(x, y+344, w, 'Fill this range', True, h=48)
    b.button(x, y+402, w, 'Cancel')


def option(code, title, subtitle):
    b=m.Board(1160, 860, title)
    b.text(32, 42, code+'  '+title, 26, bold=True)
    b.text(32, 74, subtitle, 15, m.MUTED)
    b.text(32, 102, 'Invented example data • Same controls and outcomes on phone and PC', 12, m.MUTED)
    b.box(28, 128, 694, 694)
    m.brand(b, 52, 150)
    b.text(52, 218, 'Today     My Week     Rooms     Calendar     Settings', 16, m.BLUE)
    if code=='A':
        b.text(52, 270, 'My Week', 30, bold=True)
        b.text(52, 314, 'Tuesday 29 September', 20, bold=True)
        b.button(510, 284, 185, 'Fill time range')
        b.text(52, 356, '11:30–12:00  ·  B0.29  ·  Booked', 16)
        b.line(52, 382, 643)
        b.text(52, 420, 'Fill Tuesday', 20, bold=True)
        b.text(52, 459, 'From  11:00                       Until  13:00', 18)
        b.text(52, 501, '30 min already booked · up to 90 min to fill', 15, m.MUTED)
        b.lines(52, 547, ['Temporarily overrides your practice settings.', 'Keeps existing bookings. ASIMUT limits apply.'], 14)
        b.button(52, 612, 310, 'Fill this range', True)
        b.button(378, 612, 150, 'Cancel')
        b.text(52, 711, 'Today also links to this dated form.', 14, m.MUTED)
    elif code=='B':
        b.button(464, 150, 231, 'Fill time range')
        b.text(52, 271, 'Today', 30, bold=True)
        b.box(52, 304, 643, 440, m.TINT)
        b.text(74, 345, 'A focused dialog over any current view', 19, bold=True)
        b.text(74, 398, 'Date   Tuesday 29 September', 18)
        b.text(74, 452, 'From   11:00       Until   13:00', 18)
        b.lines(74, 512, ['Temporarily overrides your practice settings.', 'Keeps existing bookings. ASIMUT limits apply.'], 14)
        b.button(74, 581, 300, 'Fill this range', True)
        b.button(391, 581, 160, 'Cancel')
    else:
        b.text(52, 270, 'Rooms', 30, bold=True)
        b.text(52, 315, 'Tuesday 29 September', 20, bold=True)
        b.text(172, 354, '11:00            11:30            12:00            12:30', 14, m.MUTED)
        for n,name in enumerate(['Weston','Corus','B0.29']):
            b.text(52, 403+n*60, name, 16)
            b.box(164, 378+n*60, 528, 45, m.TINT if n!=2 else '#e5f2eb', radius=4)
        b.text(52, 600, 'Fill my practice:   11:00  →  13:00', 19, bold=True)
        b.text(52, 639, 'Finds eligible gaps across your rooms.', 14, m.MUTED)
        b.button(52, 677, 310, 'Fill this range', True)
        b.text(52, 774, 'Phone uses time fields beneath the grid.', 14, m.MUTED)
    b.box(746, 128, 386, 694)
    m.brand(b, 766, 147)
    b.text(766, 220, 'My Week' if code=='A' else 'Fill practice' if code=='B' else 'Rooms', 28, bold=True)
    form(b, 766, 266, 346)
    b.save('option-'+code.lower()+'.svg')


def states():
    b=m.Board(1160, 990, 'Fill time range states')
    b.text(28, 42, 'Shared flow and states', 28, bold=True)
    b.text(28, 78, 'Invented examples • Date + times → Fill → live progress → confirmed coverage and remaining gaps', 15, m.MUTED)
    cards=[
        ('Ready / settings', ['Tue 29 Sep · 11:00–13:00', '30 min already booked · 90 min to fill', 'This request overrides targets, preferred times,', 'booking-off dates, breaks and session preferences.', 'Normal settings resume after this request.'], 'Fill this range'),
        ('Help / confirmation', ['The Fill button starts booking immediately.', 'Existing reservations stay in place.', 'Room requirements, live access, site limits', 'and actual agenda clashes remain visible.', 'Help opens on hover, focus or tap.'], 'Close help'),
        ('Checking / progress', ['Refreshing bookings and checking rooms…', 'Tue 29 Sep · 11:00–13:00', '30 min already covered · 90 min remaining', 'Each new booking is verified before continuing.', 'Progress remains visible after navigation.'], 'Stop'),
        ('Complete / booking details', ['120 of 120 min covered', '11:00–11:30 · Corus · Added', '11:30–12:00 · B0.29 · Existing', '12:00–13:00 · B1.09 · Added', 'Each row opens its exact booking details.'], 'View bookings'),
        ('Partial / unavailable / stopped', ['90 of 120 min covered · 30 min unfilled', '12:30–13:00: no eligible room available', 'Or: stopped; verified bookings remain.', 'Quota and too-short gaps have explicit reasons.', 'Retry refreshes the agenda and fills only gaps.'], 'Try remaining gaps'),
        ('Error / uncertain / disconnected', ['The last booking result needs checking.', 'No further bookings will be attempted.', 'Check the original request after reconnecting.', 'A lost response never triggers another Save.', 'Invalid dates/times show beside their field.'], 'Check booking status'),
    ]
    for n,(title,lines,button) in enumerate(cards):
        x=28+(n%2)*566;y=112+(n//2)*282
        b.box(x,y,538,260)
        b.text(x+20,y+35,title,20,bold=True)
        b.lines(x+20,y+70,lines,14,width=498)
        b.button(x+20,y+192,280,button,n in (0,3))
    b.save('states.svg')
    b=m.Board(1060,820,'Narrow layouts')
    b.text(24,40,'Phone 320 px · Phone 390 px · Short PC window',25,bold=True)
    for x,w in [(20,300),(340,350),(710,330)]:
        b.box(x,74,w,718)
        form(b,x+16,118,w-32)
        b.text(x+16,620,'90 / 120 min covered',20,bold=True)
        b.lines(x+16,655,['12:30–13:00 remains unfilled.', 'Review / retry stays beside the result.'],12,width=w-32)
        b.button(x+16,712,w-32,'Try remaining gaps')
    b.save('narrow.svg')


if __name__=='__main__':
    option('A','My Week day action','Fill a date beside its existing practice; opens inline on PC and as a focused phone form.')
    option('B','Always-available dialog','One header action opens the same date and time form from any tab.')
    option('C','Rooms range tool','Choose a date and time range next to the availability grid, then fill across rooms.')
    states()
    (ROOT/'index.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Fill time range — design review</title><style>body{margin:0;background:#f8fafc;color:#1d2430;font:16px Segoe UI,sans-serif}main{max-width:1160px;margin:auto;padding:20px}img{display:block;width:100%;height:auto;margin:18px 0 36px;border:1px solid #e1e6ee;border-radius:16px}a{color:#0868d9}p{line-height:1.6}</style><main><h1>Fill time range</h1><p>Three placements within the existing Quiet Focus design. All schedules and outcomes below are invented examples. A is recommended: the action sits beside the day it affects.</p>'''+''.join(f'<img src="{name}.svg" alt="{name} design board">' for name in ['option-a','option-b','option-c','states','narrow'])+'</main></html>',encoding='utf-8')
