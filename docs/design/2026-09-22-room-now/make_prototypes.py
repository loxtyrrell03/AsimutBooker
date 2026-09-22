"""Editable, synthetic Room now review boards; no app or booking imports."""
from pathlib import Path
from html import escape

ROOT = Path(__file__).resolve().parent
BLUE, TINT, INK, MUTED = '#0868d9', '#eaf3ff', '#1d2430', '#667080'
PAGE, LINE, WHITE = '#f8fafc', '#e1e6ee', '#ffffff'
GREEN, AMBER = '#246d4b', '#785717'


class Board:
    def __init__(self, width, height, title):
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
                      f'<rect width="100%" height="100%" fill="{PAGE}"/>',
                      '<style>text{font-family:Segoe UI,Arial,sans-serif}</style>']

    def box(self, x, y, w, h, fill=WHITE, radius=16, stroke=LINE):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}"/>')

    def text(self, x, y, value, size=15, color=INK, bold=False, anchor='start', max_width=None):
        width = f' data-max-width="{max_width}"' if max_width else ''
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{650 if bold else 400}" fill="{color}" text-anchor="{anchor}"{width}>{escape(value)}</text>')

    def lines(self, x, y, values, size=14, color=MUTED, step=22, width=None):
        for n, value in enumerate(values):
            self.text(x, y+n*step, value, size, color, max_width=width)

    def button(self, x, y, w, label, primary=False, h=44, small=False):
        self.box(x, y, w, h, BLUE if primary else WHITE, 11, BLUE if primary else LINE)
        self.text(x+w/2, y+h/2+5, label, 13 if small else 15, WHITE if primary else BLUE, True, 'middle', w-18)

    def line(self, x, y, w):
        self.parts.append(f'<path d="M{x} {y}h{w}" stroke="{LINE}"/>')

    def help(self, x, y):
        self.parts.append(f'<circle cx="{x}" cy="{y}" r="10" fill="{TINT}"/>')
        self.text(x, y+4, '?', 13, BLUE, True, 'middle')

    def badge(self, x, y, value, w=70):
        self.box(x, y, w, 24, '#e5f2eb', 12, '#e5f2eb')
        self.text(x+w/2, y+16, value, 11, GREEN, True, 'middle', w-10)

    def save(self, name):
        (ROOT/name).write_text('\n'.join(self.parts+['</svg>']), encoding='utf-8')


def brand(b, x, y):
    b.box(x, y, 32, 32, BLUE, 10, BLUE)
    for dx, h in [(9, 11), (16, 21), (23, 16)]:
        b.parts.append(f'<path d="M{x+dx} {y+25}v-{h}" stroke="white" stroke-width="3" stroke-linecap="round"/>')
    b.text(x+43, y+23, 'Asimut', 21, bold=True)


def modes(b, x, y, w, longest=False):
    b.box(x, y, w, 44, '#f1f4f8', 11, '#f1f4f8')
    cell=(w-8)/2
    b.box(x+4+(cell if longest else 0), y+4, cell, 36, WHITE, 8, LINE)
    for n, s in enumerate(['Preferred duration', 'Longest possible']):
        b.text(x+4+cell*(n+.5), y+27, s, 12, BLUE if bool(n)==longest else MUTED, bool(n)==longest, 'middle', cell-10)


def duration(b, x, y, w, longest=False, narrow=False):
    b.text(x, y, 'Maximum duration' if longest else 'Preferred duration', 13, MUTED)
    b.help(x+139, y-5)
    labels=['30 min', '1 hour', '1½ hours', '2 hours']
    gap=6
    cell=(w-gap*3)/4
    for n, label in enumerate(labels):
        selected=n==(3 if longest else 1)
        b.box(x+n*(cell+gap), y+12, cell, 44, TINT if selected else WHITE, 10, BLUE if selected else LINE)
        b.text(x+n*(cell+gap)+cell/2, y+39, label, 12 if narrow else 13, BLUE if selected else INK, selected, 'middle', cell-8)
    b.text(x+w, y+80, 'Custom duration ›', 13, BLUE, anchor='end', max_width=w)


def editor(b, x, y, w, longest=False, title=True):
    if title:
        b.text(x, y+22, 'Find me a room now', 22, bold=True, max_width=w)
        y+=42
    modes(b, x, y, w, longest)
    duration(b, x, y+72, w, longest, w<320)
    b.button(x, y+171, w, 'Find me a room now', True)
    b.text(x+w/2, y+233, 'Books a room as soon as possible.', 12, MUTED, anchor='middle', max_width=w)


def next_card(b, x, y, w, compact=False):
    b.box(x, y, w, 157 if compact else 219, TINT, 20, TINT)
    b.text(x+20, y+30, 'UP NEXT', 11, BLUE, True)
    b.badge(x+w-88, y+14, 'Booked')
    b.text(x+20, y+69, 'Room Weston', 26, bold=True, max_width=w-40)
    b.text(x+20, y+101, 'Today · 17:00–18:00', 18)
    if compact:
        b.text(x+20, y+135, 'View booking →', 14, BLUE)
    else:
        b.text(x+20, y+133, '1 hour of practice', 14, MUTED)
        b.button(x+20, y+157, w-40, 'View booking')


def phone(b, x, y, w=360, h=716, active='Today'):
    b.box(x, y, w, h, PAGE, 25, '#cdd7e3')
    brand(b, x+20, y+19)
    b.text(x+63, y+66, 'Connected', 11, MUTED)
    b.line(x, y+82, w)
    by=y+h-67
    b.box(x+1, by, w-2, 66, WHITE, 23, WHITE)
    b.line(x+1, by, w-2)
    for n, label in enumerate(['Today','My Week','Calendar','Assistant','Settings']):
        bw=(w-12)/5
        bx=x+6+n*bw
        if label==active: b.box(bx, by+7, bw, 49, TINT, 11, TINT)
        b.text(bx+bw/2, by+28, ['◷','≡','▦','◇','⚙'][n], 19, BLUE if label==active else MUTED, anchor='middle')
        b.text(bx+bw/2, by+48, label, 10, BLUE if label==active else MUTED, anchor='middle', max_width=bw-2)


def desktop(b, x, y, w=1010, h=650, global_button=False, narrow=False):
    b.box(x, y, w, h, PAGE, 20, '#cdd7e3')
    b.box(x+1, y+1, w-2, 70, WHITE, 20, WHITE)
    brand(b, x+25, y+19)
    navx=x+205
    if narrow and global_button: navx=x+23
    nav_y=y+45+(65 if narrow and global_button else 0)
    for offset,label in [(0,'Today'),(67,'My Week'),(156,'Calendar'),(251,'Assistant'),(346,'Settings')]:
        b.text(navx+offset, nav_y, label, 14, BLUE)
    if global_button: b.button(x+w-211, y+15, 189, 'Find a room now', True, small=True)
    b.line(x, y+72+(65 if narrow and global_button else 0), w)


def overview(code, name, caption):
    b=Board(1460, 935, f'{code} — {name}; invented examples')
    b.text(28, 41, f'{code}  {name}', 30, bold=True)
    if code=='A':
        b.box(300, 18, 133, 29, TINT, 14, TINT)
        b.text(366, 38, 'Recommended', 13, BLUE, True, 'middle')
    b.text(28, 73, caption, 16, MUTED)
    b.text(28, 104, 'DESIGN PREVIEW · All bookings and availability are invented examples.', 12, MUTED)
    dx,dy,dw,dh=28,137,1010,650
    px,py,pw,ph=1072,137,360,716
    desktop(b,dx,dy,dw,dh,global_button=code=='C')
    phone(b,px,py,pw,ph)
    if code=='A':
        b.text(60, 264, 'Today', 32, bold=True)
        b.text(61, 292, 'Tuesday 22 September', 14, MUTED)
        b.box(60, 320, 945, 213)
        b.text(82, 356, 'Find me a room now', 24, bold=True)
        b.text(82, 383, 'Start soonest. Make the most of your time.', 14, MUTED)
        modes(b,82,407,348)
        b.text(82,485,'Books a room as soon as possible.',12,MUTED)
        duration(b,466,354,310)
        b.button(466,461,310,'Find me a room now',True)
        b.text(813,377,'Room order',13,MUTED)
        b.text(813,402,'Your saved order',14,BLUE)
        b.help(960,374)
        next_card(b,60,554,582,True)
        b.box(665,554,340,157)
        b.text(688,587,'Also today',18,bold=True)
        b.text(688,623,'15:00–16:00',15,bold=True)
        b.text(688,650,'Piano lesson',15,MUTED)
        b.text(688,682,'See my week →',14,BLUE)
        b.text(px+20, py+124, 'Today', 29, bold=True)
        b.text(px+20,py+149,'Tuesday 22 September',13,MUTED)
        b.box(px+14,py+167,pw-28,300)
        editor(b,px+28,py+182,pw-56)
        next_card(b,px+14,py+484,pw-28,True)
    elif code=='B':
        b.text(60, 264, 'Today', 32, bold=True)
        b.text(61,292,'Tuesday 22 September',14,MUTED)
        next_card(b,60,320,556)
        b.box(640,320,365,219)
        b.text(665,357,'This week',19,bold=True)
        b.text(665,414,'8 hours',33,bold=True)
        b.text(665,445,'booked practice',14,MUTED)
        b.text(665,502,'See my week →',15,BLUE)
        b.text(61,580,'Also today',21,bold=True)
        b.text(62,622,'15:00–16:00     Piano lesson',16,MUTED)
        b.box(45,674,976,95,WHITE,17)
        b.text(65,709,'Room now',19,bold=True)
        b.text(65,738,'Books as soon as possible',12,MUTED)
        b.button(316,694,288,'Longest possible · max 2h  ▾')
        b.button(626,694,374,'Find me a room now',True)
        b.text(px+20,py+124,'Today',29,bold=True)
        b.text(px+20,py+149,'Tuesday 22 September',13,MUTED)
        next_card(b,px+14,py+173,pw-28)
        b.text(px+20,py+435,'Also today',21,bold=True)
        b.text(px+20,py+469,'15:00–16:00   Piano lesson',15,MUTED)
        b.box(px+10,py+490,pw-20,147,WHITE,17)
        b.text(px+25,py+519,'Longest possible',14,bold=True)
        b.text(px+pw-24,py+519,'Max 2h ▾',14,BLUE,anchor='end')
        b.button(px+23,py+534,pw-46,'Find me a room now',True)
        b.text(px+pw/2,py+609,'Books a room as soon as possible.',12,MUTED,anchor='middle')
    else:
        b.text(60, 264, 'Today', 32, bold=True)
        next_card(b,60,295,455,True)
        b.text(60,504,'Also today',21,bold=True)
        b.text(60,542,'15:00–16:00   Piano lesson',16,MUTED)
        b.text(60,584,'See my week →',14,BLUE)
        b.box(557,240,446,491,WHITE,22)
        b.text(970,275,'×',25,MUTED,anchor='middle')
        editor(b,583,263,394,longest=True)
        b.line(583,577,394)
        b.text(583,610,'Room order',13,MUTED)
        b.text(583,639,'Use my saved room order',15,BLUE)
        b.text(583,682,'Close',14,BLUE)
        b.button(px+pw-116,py+22,100,'Room now',True,small=True)
        b.text(px+20,py+124,'Today',29,bold=True)
        b.text(px+20,py+156,'Room Weston · 17:00–18:00',14,MUTED)
        b.box(px+7,py+192,pw-14,445,WHITE,22)
        b.box(px+pw/2-20,py+204,40,4,'#c9d2df',2,'#c9d2df')
        b.text(px+pw-30,py+241,'×',23,MUTED,anchor='middle')
        editor(b,px+24,py+225,pw-48,longest=True)
        b.line(px+24,py+540,pw-48)
        b.text(px+24,py+570,'Your room order',14,BLUE)
        b.text(px+24,py+606,'Close',14,BLUE)
    b.text(29,834,{'A':'Everything is ready at the top of Today.','B':'Available while browsing Today, My Week and Calendar.','C':'Open from any primary view; choose, book, return.'}[code],18,bold=True)
    b.lines(29,864,{'A':['Pick a duration and press once. The panel becomes progress, then your booking.','Smallest addition to the existing layout; easiest to discover on launch.'],
                    'B':['Change mode or duration from the selector. Status replaces the bar during the request.','Very easy to reach while browsing; uses more persistent screen space.'],
                    'C':['The global button opens a phone sheet or PC dialog without changing the current page.','Keeps each page compact; one extra tap before booking.']}[code],14)
    b.save(f'option-{code.lower()}.svg')


def states():
    b=Board(1460,1510,'Shared Room now states — complete flow for A, B and C')
    b.text(28,43,'The complete booking flow',31,bold=True)
    b.text(28,75,'Shared by A, B and C. The same content appears in the Today panel, expanded bar, or booking sheet.',15,MUTED)
    b.text(28,102,'DESIGN PREVIEW · Invented examples. Search, booking and confirmations below do not perform real actions.',12,MUTED)
    cards=[
        ('1  Choose your duration','setup'),('2  Longest possible','longest'),('3  Custom duration','custom'),
        ('4  Search and check','search'),('5  Booked, shorter match','booked'),('6  No room available','empty'),
        ('7  Booking result unclear','uncertain'),('8  Stop safely','stop'),('9  A rule prevents booking','blocked'),
        ('10  Help and room settings','help'),('11  Already running / offline','busy'),('12  Verified booking details','details')]
    for i,(heading,state) in enumerate(cards):
        x=28+(i%3)*477;y=128+(i//3)*333;w=450
        b.box(x,y,w,309)
        b.text(x+20,y+32,heading,19,bold=True,max_width=w-40)
        if state in ('setup','longest'):
            longest=state=='longest'
            modes(b,x+20,y+49,w-40,longest)
            duration(b,x+20,y+120,w-40,longest)
            b.button(x+20,y+222,w-40,'Find me a room now',True)
            b.text(x+w/2,y+287,'Books a room as soon as possible.',12,MUTED,anchor='middle')
        elif state=='custom':
            b.text(x+20,y+71,'Preferred duration',14,MUTED)
            b.button(x+20,y+86,250,'1 hour 15 minutes  ▾')
            b.lines(x+20,y+160,['Choose from currently permitted lengths.','This choice is for this booking only.'],width=w-40)
            b.button(x+20,y+223,245,'Use this duration',True)
            b.button(x+278,y+223,150,'Cancel')
        elif state=='search':
            b.text(x+20,y+76,'Finding a room…',25,bold=True)
            b.lines(x+20,y+112,['Looking for up to 2 hours, starting soonest.','✓ Your timetable checked','● Checking available rooms'],width=w-40,step=30)
            b.button(x+20,y+223,130,'Stop')
            b.text(x+170,y+251,'Progress reflects actual steps.',12,MUTED,max_width=260)
        elif state=='booked':
            b.badge(x+20,y+53,'Booked',77)
            b.text(x+20,y+111,'Room Weston',26,bold=True)
            b.text(x+20,y+147,'Today · 14:15–15:00',21)
            b.lines(x+20,y+183,['45 min booked · you asked for 1 hour.','This was the closest match starting soonest.'],width=w-40)
            b.button(x+20,y+239,w-40,'View booking')
        elif state=='empty':
            b.text(x+20,y+77,'No eligible room left today.',22,bold=True,max_width=w-40)
            b.lines(x+20,y+113,['No room was booked.','You can change the duration or check again.'],width=w-40)
            b.button(x+20,y+174,242,'Try again',True)
            b.button(x+277,y+174,151,'Edit duration')
            b.lines(x+20,y+253,['If a later start is available today, book that','earliest start and show its exact time.'],12,width=w-40)
        elif state=='uncertain':
            b.box(x+17,y+57,w-34,130,'#fff4de',12,'#fff4de')
            b.text(x+31,y+88,'Checking whether it booked',20,AMBER,True,max_width=w-62)
            b.lines(x+31,y+121,['The connection was interrupted after Save.','Wait for the result before booking again.'],14,AMBER,width=w-62)
            b.button(x+20,y+222,w-40,'Check booking status')
        elif state=='stop':
            b.text(x+20,y+80,'Stopping…',25,bold=True)
            b.lines(x+20,y+121,['Finishing any booking already being saved.','We’ll show its confirmed result here.'],width=w-40)
            b.line(x+20,y+181,w-40)
            b.text(x+20,y+218,'Stopped before booking',18,bold=True)
            b.text(x+20,y+246,'No reservation was made.',14,MUTED)
            b.text(x+20,y+278,'If already saved: show the Booked result.',12,MUTED)
        elif state=='blocked':
            b.text(x+20,y+80,'Your practice hours start at 16:00.',19,bold=True,max_width=w-40)
            b.lines(x+20,y+122,['Your strict time preference prevents this start.','No room was booked.'],width=w-40)
            b.button(x+20,y+211,w-40,'Review preferred hours')
            b.text(x+20,y+284,'Also names quota, protected-time or access limits.',12,MUTED,max_width=w-40)
        elif state=='help':
            b.text(x+20,y+74,'Maximum duration',14,MUTED)
            b.help(x+170,y+69)
            b.box(x+20,y+91,w-40,85,TINT,12,TINT)
            b.lines(x+36,y+118,['Starts as soon as possible. Takes the longest','available session, up to your maximum.'],14,INK,width=w-72)
            b.text(x+w-36,y+111,'×',16,BLUE,anchor='middle')
            b.text(x+20,y+210,'Rooms ›  Your saved room order and requirements',13,BLUE,max_width=w-40)
            b.lines(x+20,y+248,['No usable setup: open the existing Rooms editor.','Duration choices don’t change your daily target.'],13,width=w-40)
        elif state=='busy':
            b.text(x+20,y+79,'Waiting for the current check…',21,bold=True,max_width=w-40)
            b.text(x+20,y+111,'Rechecks the start time when ready.',14,MUTED)
            b.button(x+20,y+133,130,'Stop')
            b.line(x+20,y+195,w-40)
            b.text(x+20,y+228,'Can’t connect to the PC',19,bold=True)
            b.lines(x+20,y+259,['Keep the duration choice. Offer Reconnect.','Never silently send a booking after reconnecting.'],13,width=w-40)
        elif state=='details':
            b.badge(x+20,y+54,'Booked',77)
            b.text(x+20,y+111,'Room Weston',26,bold=True)
            b.text(x+20,y+143,'Tuesday 22 September · 14:15–15:00',16)
            b.text(x+20,y+175,'45 minutes of practice',14,MUTED)
            b.button(x+20,y+194,w-40,'Open in Asimut')
            b.text(x+20,y+265,'Reconfirm on college Wi-Fi when available.',13,MUTED)
            b.text(x+20,y+290,'Existing change and cancellation controls remain.',12,MUTED,max_width=w-40)
    b.text(28,1486,'Search starts now and covers today. Uncertain Save results are reconciled before any further booking.',13,MUTED)
    b.save('states.svg')


def narrow():
    b=Board(1460,985,'Room now at 320px phone and 760px PC widths')
    b.text(28,43,'Small screens, the same controls',31,bold=True)
    b.text(28,77,'320px phone controls · 760px PC window · example data. The sheet content scrolls when the keyboard opens.',15,MUTED)
    phone(b,28,125,320,740)
    b.text(45,248,'Today',29,bold=True)
    b.box(38,269,300,319)
    editor(b,52,285,272,True)
    next_card(b,38,603,300,True)
    desktop(b,385,125,760,570,global_button=True,narrow=True)
    b.text(410,303,'Today',29,bold=True)
    b.box(409,324,712,329)
    b.text(429,361,'Find me a room now',23,bold=True)
    modes(b,429,384,337,True)
    duration(b,429,460,337,True)
    b.lines(798,404,['Starts as soon as possible.','Uses your saved room order.'],14,width=300)
    b.button(798,463,299,'Find me a room now',True)
    b.lines(798,538,['Books one session.','Up to your selected maximum.'],13,width=299)
    b.text(429,624,'Custom duration opens a short, scrollable editor.',13,MUTED)
    b.box(1180,125,252,570)
    b.text(1200,162,'Short viewport',20,bold=True)
    b.lines(1200,201,['Keep the selected mode,','duration and action visible.','Allow the content to scroll.','','The sheet has a visible','Close control and returns','focus to its launcher.','','Bar reserves layout space;','it never covers navigation,','an editor or the composer.'],14,width=212,step=27)
    b.box(385,722,1047,143)
    b.text(407,758,'One request, wherever you open it',22,bold=True)
    b.lines(407,792,['Returning to the panel shows progress or the result; it does not submit again.','Duration controls stay readable at 320px. Help fits the viewport and works with touch, hover and keyboard.'],15,width=1003)
    b.text(28,930,'A keeps the editor at the top of Today. B opens it from the action bar. C opens it from the header.',15,MUTED)
    b.save('narrow.svg')


def gallery():
    cards=''.join(f'<article><h2>{code} — {name}</h2><p>{caption}</p><a href="option-{code.lower()}.svg"><img src="option-{code.lower()}.svg" alt="{name}: desktop and phone proposal" /></a><a href="option-{code.lower()}.svg">Open editable SVG</a></article>'
                  for code,name,caption in [('A','Today panel','Recommended. Pick a duration and book from the top of Today.'),('B','Persistent action bar','Reach it while browsing Today, My Week or Calendar.'),('C','Global button and sheet','Open the controls from any primary view.')])
    (ROOT/'index.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Find me a room now — design review</title><style>
    *{box-sizing:border-box}body{margin:0;background:#f8fafc;color:#1d2430;font:16px/1.6 "Segoe UI",sans-serif}main{max-width:1460px;margin:auto;padding:32px 24px}h1{font-size:36px;line-height:1.2;margin:0 0 16px}h2{font-size:24px;margin:0}p{color:#667080;margin:8px 0 16px}article{background:white;border:1px solid #e1e6ee;border-radius:20px;padding:20px;margin:24px 0}img{display:block;width:100%;height:auto;border-radius:10px;margin:16px 0}a{color:#0868d9}a:focus-visible{outline:3px solid #0868d9;outline-offset:4px}nav{display:flex;gap:20px;flex-wrap:wrap}nav a{min-height:44px;padding:8px 0}.note{background:#eaf3ff;padding:16px 20px;border-radius:14px;color:#1d2430}@media(max-width:600px){main{padding:24px 12px}h1{font-size:28px}article{padding:14px}h2{font-size:22px}}</style><main>
    <h1>Find me a room now</h1><p>Three placements in the existing Asimut style. Every room, time and outcome shown is an invented example.</p>
    <p class="note">Start soonest. Choose a preferred duration, or Longest possible with a maximum such as 2 hours. Pressing the action searches and books one room.</p>
    <nav><a href="#options">Compare placements</a><a href="#states">Complete booking flow</a><a href="#narrow">Small screens</a><a href="README.md">Behavior and handoff notes</a></nav><section id="options">'''+cards+'''</section>
    <article id="states"><h2>Complete booking flow — all three options</h2><p>Duration selection, help, progress, shorter matches, empty results, Stop, recovery and booking details.</p><a href="states.svg"><img src="states.svg" alt="Twelve states shared by all three design proposals"></a><a href="states.svg">Open editable flow SVG</a></article>
    <article id="narrow"><h2>Small screens</h2><p>320px phone and 760px PC layouts; notes for reduced viewport height.</p><a href="narrow.svg"><img src="narrow.svg" alt="Narrow phone and PC layouts"></a><a href="narrow.svg">Open editable narrow SVG</a></article>
    </main></html>''',encoding='utf-8')


if __name__=='__main__':
    overview('A','Today panel','Duration and booking controls are ready at the top of Today, on phone and PC.')
    overview('B','Persistent action bar','A compact booking bar stays within reach while you browse your schedule.')
    overview('C','Global button and sheet','One header button opens a focused booking sheet on phone and a dialog on PC.')
    states()
    narrow()
    gallery()
