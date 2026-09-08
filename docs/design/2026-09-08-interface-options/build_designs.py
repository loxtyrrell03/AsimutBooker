"""Generate original SVG design options. Sample content only; no application imports."""
from pathlib import Path
from html import escape
import json

OUT = Path(__file__).resolve().parent
INK = '#1d2430'
MUTED = '#667080'
LINE = '#e5e8ed'
WHITE = '#ffffff'


class SVG:
    def __init__(self, w, h):
        self.w, self.h, self.parts = w, h, []

    def rect(self, x, y, w, h, fill, r=0, stroke=None, dash=False):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}"' + (f' stroke="{stroke}" stroke-width="1"' if stroke else '') + (' stroke-dasharray="5 4"' if dash else '') + '/>')

    def text(self, x, y, value, size=15, color=INK, weight=400, anchor='start', spacing=0):
        self.parts.append(f'<text x="{x}" y="{y}" font-family="Segoe UI, Arial, sans-serif" font-size="{size}" font-weight="{weight}" fill="{color}" text-anchor="{anchor}" letter-spacing="{spacing}">{escape(str(value))}</text>')

    def lines(self, x, y, lines, size=15, color=MUTED, gap=23, weight=400):
        for i, line in enumerate(lines): self.text(x, y+i*gap, line, size, color, weight)

    def line(self, x1, y1, x2, y2, color=LINE, width=1):
        self.parts.append(f'<path d="M{x1} {y1}H{x2}" stroke="{color}" stroke-width="{width}"/>' if y1 == y2 else f'<path d="M{x1} {y1}L{x2} {y2}" stroke="{color}" stroke-width="{width}"/>')

    def circle(self, x, y, r, fill, stroke=None):
        self.parts.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{fill}"' + (f' stroke="{stroke}" stroke-width="1.5"' if stroke else '') + '/>')

    def icon(self, x, y, name, color=MUTED, size=22):
        paths = {
            'today': '<rect x="3" y="3" width="18" height="18" rx="5"/><path d="M7 9h10M7 13h6M7 17h4"/>',
            'calendar': '<rect x="3" y="5" width="18" height="16" rx="4"/><path d="M7 3v4M17 3v4M3 11h18M8 15h1M14 15h1"/>',
            'chat': '<path d="M21 11a8 8 0 0 1-8 8H7l-4 3V11a9 9 0 0 1 18 0Z"/><path d="M7 10h10M7 14h6"/>',
            'settings': '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3" fill="white"/><circle cx="16" cy="17" r="3" fill="white"/>',
            'arrow': '<path d="m9 5 7 7-7 7"/>',
            'back': '<path d="m15 5-7 7 7 7"/>',
            'plus': '<path d="M12 5v14M5 12h14"/>',
            'check': '<path d="m5 12 4 4L19 6"/>',
            'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
            'room': '<path d="M6 21V3h12v18M3 21h18M14 12h1"/>',
            'spark': '<path d="m12 2 2.8 7.2L22 12l-7.2 2.8L12 22l-2.8-7.2L2 12l7.2-2.8Z"/>',
            'send': '<path d="M12 19V5m-6 6 6-6 6 6"/>',
            'refresh': '<path d="M20 9a8 8 0 1 0 0 6M20 3v6h-6"/>',
            'pause': '<path d="M8 5v14M16 5v14"/>',
        }
        self.parts.append(f'<g transform="translate({x} {y}) scale({size/24})" fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{paths[name]}</g>')

    def pill(self, x, y, label, bg, color, w=None):
        w = w or len(label)*7+24
        self.rect(x, y, w, 28, bg, 14)
        self.text(x+w/2, y+19, label, 12, color, 600, 'middle')

    def button(self, x, y, w, label, accent, secondary=False, h=44):
        self.rect(x, y, w, h, '#ffffff' if secondary else accent, 12, LINE if secondary else None)
        self.text(x+w/2, y+h/2+5, label, 14, INK if secondary else WHITE, 600, 'middle')

    def group(self, content, x=0, y=0, scale=1):
        self.parts.append(f'<g transform="translate({x} {y}) scale({scale})">{content}</g>')

    def save(self, name):
        raw = f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{escape(name)}">'+''.join(self.parts)+'</svg>'
        (OUT/f'{name}.svg').write_text(raw, encoding='utf-8')
        return raw


THEMES = [
    dict(name='Quiet Focus', accent='#0868d9', tint='#eaf3ff', board='#edf1f6', bg='#f8fafc', desc='Your next session. Everything else, one tap away.', active=0),
    dict(name='Week at a Glance', accent='#23705d', tint='#e7f2ec', board='#f0f1ec', bg='#fafbf8', desc='See your time. Shape your week.', active=1),
    dict(name='Personal Assistant', accent='#6b4cce', tint='#f0ebfc', board='#f0edf7', bg='#fbfaff', desc='Ask naturally. See exactly what happens.', active=2),
]


def brand(s, x, y, t):
    s.rect(x, y, 34, 34, t['accent'], 10)
    for i, h in enumerate([10, 20, 15]): s.rect(x+8+i*7, y+25-h, 4, h, WHITE, 2)


def desktop_shell(t):
    s = SVG(1120, 760)
    s.rect(0, 0, 1120, 760, WHITE, 20, '#d8dce4')
    s.rect(1, 1, 206, 758, '#f4f5f7', 20)
    s.rect(188, 1, 19, 758, '#f4f5f7')
    s.line(207, 1, 207, 759)
    brand(s, 24, 36, t)
    s.text(70, 51, 'Asimut', 19, INK, 650)
    s.text(70, 69, 'BOOKER', 9, MUTED, 600, spacing=2)
    for i, (label, ic) in enumerate([('Today', 'today'), ('My Week', 'calendar'), ('Assistant', 'chat')]):
        yy = 118+i*52
        if i == t['active']: s.rect(14, yy, 179, 44, t['tint'], 11)
        col = t['accent'] if i == t['active'] else MUTED
        s.icon(28, yy+11, ic, col)
        s.text(64, yy+28, label, 14, col, 600 if i == t['active'] else 400)
    s.text(28, 337, 'YOUR PRACTICE', 10, MUTED, 600, spacing=1.4)
    s.text(28, 369, '2 hours a day', 15, INK, 600)
    s.text(28, 393, 'Weekdays · afternoons', 12, MUTED)
    s.text(28, 425, 'Edit preferences', 13, t['accent'], 600)
    s.rect(18, 598, 171, 68, WHITE, 13, LINE)
    s.circle(35, 620, 3.5, '#287c59')
    s.text(46, 625, 'Auto-booking is on', 12, INK, 600)
    s.text(32, 648, 'Pause', 12, t['accent'], 600)
    s.icon(29, 700, 'settings', MUTED, 20)
    s.text(63, 715, 'Settings', 14, MUTED)
    s.text(999, 27, '—', 16, MUTED)
    s.rect(1045, 16, 10, 10, 'none', 1, MUTED)
    s.text(1089, 27, '×', 18, MUTED)
    return s


def phone_shell(t, detail=False):
    s = SVG(390, 844)
    s.rect(0, 0, 390, 844, t['bg'], 34, '#d3d7df')
    s.text(30, 32, '9:41', 14, INK, 600)
    s.rect(144, 13, 102, 25, '#20242c', 13)
    for i in range(4): s.rect(300+i*4, 26-i*2, 2.5, 4+i*2, INK, 1)
    s.rect(328, 20, 24, 11, 'none', 3, INK)
    s.rect(331, 23, 17, 5, INK, 1)
    s.rect(353, 23, 2, 5, INK, 1)
    if not detail:
        s.rect(1, 751, 388, 60, WHITE)
        s.line(1, 751, 389, 751)
        for i,(label,ic) in enumerate([('Today','today'),('My Week','calendar'),('Assistant','chat'),('Settings','settings')]):
            col = t['accent'] if i == t['active'] else MUTED
            if i == t['active']: s.rect(23+i*94, 759, 54, 30, t['tint'], 15)
            s.icon(39+i*94, 763, ic, col, 21)
            s.text(50+i*94, 805, label, 10, col, 600 if i==t['active'] else 400, 'middle')
    s.rect(137, 828, 116, 4, INK, 2)
    return s


def day_strip(s, x, y, w, t, compact=False):
    step = w/7
    for i, day in enumerate(['M','T','W','T','F','S','S']):
        xx=x+i*step
        if i==1: s.rect(xx+2,y,step-5,70 if not compact else 58,t['accent'],14)
        s.text(xx+step/2,y+20,day,11,WHITE if i==1 else MUTED,500,'middle')
        s.text(xx+step/2,y+44,7+i,18,WHITE if i==1 else INK,600,'middle')
        if not compact and i<5: s.circle(xx+step/2,y+59,2,WHITE if i==1 else t['accent'])


def focus(t):
    d=desktop_shell(t)
    d.text(248, 79, 'Tuesday, 8 September', 13, MUTED)
    d.text(248, 122, 'A little space to practise.', 32, INK, 600, spacing=-.8)
    d.button(904, 78, 172, 'Find a room', t['accent'])
    d.rect(248, 159, 520, 276, t['tint'], 24)
    d.text(276, 194, 'UP NEXT', 11, t['accent'], 650, spacing=1.6)
    d.pill(655, 175, 'Booked', WHITE, t['accent'], 83)
    d.text(276, 243, 'Room B0.29', 34, INK, 600, spacing=-.7)
    d.text(276, 277, 'Today · 14:00–16:00', 20, INK, 500)
    d.icon(276, 302, 'room', t['accent'], 19)
    d.text(305, 317, 'AHC · Piano practice', 14, MUTED)
    d.text(276, 355, 'Reconfirm on college Wi-Fi when available.', 12, MUTED)
    d.button(276, 374, 138, 'View booking', t['accent'])
    d.text(445, 401, '2 hours set aside for you', 13, MUTED)
    d.rect(788, 159, 288, 276, '#fafbfc', 24, LINE)
    d.text(814, 193, 'This week', 18, INK, 600)
    d.text(814, 251, '8', 42, INK, 600)
    d.text(847, 250, '/ 10 hours', 18, MUTED)
    d.text(814, 278, 'booked towards your goal', 13, MUTED)
    d.rect(814, 300, 234, 6, '#e2e8ef', 3)
    d.rect(814, 300, 187, 6, t['accent'], 3)
    d.lines(814, 338, ['Friday is still being arranged.', 'We’ll keep checking for you.'], 13, MUTED, 21)
    d.text(814, 406, 'See my week', 14, t['accent'], 600)
    d.icon(1026, 389, 'arrow', t['accent'], 18)
    d.text(248, 479, 'Also today', 20, INK, 600)
    d.text(1024, 478, 'My Week', 13, t['accent'], 600)
    d.rect(248, 500, 828, 88, WHITE, 17, LINE)
    d.rect(269, 520, 4, 46, '#b3a6ce', 2)
    d.text(292, 536, '10:00', 16, INK, 600)
    d.text(292, 558, '11:00', 12, MUTED)
    d.text(399, 536, 'Performance class', 16, INK, 600)
    d.text(399, 561, 'College event · Recital room', 13, MUTED)
    d.pill(965, 527, 'Class', '#f2eff8', '#74628e', 79)
    d.rect(248, 616, 828, 84, '#f8fafc', 17)
    d.icon(270, 640, 'chat', t['accent'], 24)
    d.text(312, 647, 'Need to change your plans?', 15, INK, 600)
    d.text(312, 672, 'Ask for a room, move a session, or take a day off.', 13, MUTED)
    d.button(917, 636, 138, 'Ask Assistant', t['accent'], True)
    d.text(249, 734, 'Updated just now', 11, MUTED)
    p=phone_shell(t)
    p.text(24, 86, 'Tuesday, 8 September', 13, MUTED)
    p.text(24, 127, 'Today', 34, INK, 650, spacing=-.8)
    p.circle(345, 109, 20, WHITE, LINE)
    p.icon(335, 99, 'refresh', t['accent'], 20)
    p.rect(20, 153, 350, 287, t['tint'], 23)
    p.text(42, 187, 'UP NEXT', 10, t['accent'], 650, spacing=1.4)
    p.pill(263, 168, 'Booked', WHITE, t['accent'], 86)
    p.text(42, 232, 'Room B0.29', 30, INK, 650, spacing=-.6)
    p.text(42, 265, '14:00–16:00', 23, INK, 500)
    p.text(42, 294, 'AHC · Piano practice', 14, MUTED)
    p.lines(42, 329, ['Reconfirm on college Wi-Fi', 'when available.'], 12, MUTED, 19)
    p.button(42, 370, 306, 'View booking', t['accent'], h=48)
    p.text(24, 479, 'Also today', 19, INK, 600)
    p.text(24, 513, '10:00', 14, INK, 600)
    p.text(103, 513, 'Performance class', 15, INK, 600)
    p.text(103, 536, 'Recital room · 1 hour', 12, MUTED)
    p.line(24, 559, 366, 559)
    p.text(24, 590, '8 of 10 hours booked this week', 14, INK, 500)
    p.rect(24, 607, 342, 5, '#e1e7ee', 3)
    p.rect(24, 607, 274, 5, t['accent'], 3)
    p.button(24, 645, 342, 'Find a room', t['accent'], h=50)
    p.text(195, 722, 'Auto-booking on · Updated just now', 11, MUTED, 400, 'middle')
    return d,p


def week(t):
    d=desktop_shell(t)
    d.text(247, 88, 'My Week', 32, INK, 600, spacing=-.8)
    d.text(247, 119, '7–13 September 2026', 14, MUTED)
    d.button(910, 75, 164, 'Plan my practice', t['accent'])
    d.rect(247, 148, 829, 57, '#f6f8f5', 14)
    d.pill(264, 162, '8 hours booked', t['tint'], t['accent'], 128)
    d.text(414, 181, '2 hours still planned', 13, MUTED)
    d.text(934, 181, '‹   Today   ›', 14, t['accent'], 600)
    gx,gy,cw,ch=293, 287, 156, 54
    for i,day in enumerate(['MON 7','TUE 8','WED 9','THU 10','FRI 11']):
        if i==1: d.rect(gx+i*cw+4,219,cw-8,49,t['tint'],12)
        d.text(gx+i*cw+cw/2,248,day,12,t['accent'] if i==1 else MUTED,600,'middle',.6)
    for i in range(8):
        d.text(250,gy+i*ch+5,f'{9+i:02}:00',11,MUTED)
        d.line(gx,gy+i*ch,1073,gy+i*ch)
    for i in range(6): d.line(gx+i*cw,gy-10,gx+i*cw,706,'#eef0ed')
    def event(day,start,length,title,sub,planned=False,lesson=False):
        x,y=gx+day*cw+7,gy+(start-9)*ch
        bg='#f0edf6' if lesson else '#f3f6f2' if planned else t['tint']
        col='#746087' if lesson else t['accent']
        d.rect(x,y,cw-14,length*ch-5,bg,10,col if planned else None,planned)
        d.rect(x+8,y+12,3,max(12,length*ch-30),col,2)
        d.text(x+20,y+27,title,13,col,600)
        d.text(x+20,y+49,sub,11,col)
        if length>=1.5: d.text(x+20,y+74,'Planned' if planned else 'Booked',10,col,600)
    event(0,13,2,'B0.27','13:00–15:00')
    event(1,10,1,'Performance','10:00–11:00',lesson=True)
    event(1,14,2,'B0.29','14:00–16:00')
    event(2,11,1,'Ensemble','11:00–12:00',lesson=True)
    event(2,14,2,'B0.27','14:00–16:00')
    event(3,13,2,'B0.29','13:00–15:00')
    event(4,14,2,'Finding a room','14:00–16:00',planned=True)
    d.circle(256,733,3,t['accent']); d.text(266,737,'Booked',11,MUTED)
    d.rect(340,728,9,9,'none',2,t['accent'],True); d.text(357,737,'Planned · not booked yet',11,MUTED)
    d.circle(540,733,3,'#746087');d.text(550,737,'College event',11,MUTED)
    d.text(972,737,'Updated just now',10,MUTED)
    p=phone_shell(t)
    p.text(24,91,'My Week',32,INK,650,spacing=-.8)
    p.circle(345,80,20,t['tint']);p.icon(334,69,'plus',t['accent'])
    p.text(24,123,'September 2026',14,MUTED)
    day_strip(p,22,145,348,t)
    p.rect(24,234,342,48,t['tint'],13)
    p.text(42,264,'8 hours booked',14,t['accent'],600)
    p.text(344,264,'2 hours planned',12,MUTED,400,'end')
    p.text(24,320,'Tuesday 8',20,INK,600)
    p.pill(292,299,'Today',WHITE,t['accent'],73)
    p.text(24,363,'10:00',13,MUTED,600)
    p.rect(91,340,275,88,'#f0edf6',16)
    p.text(110,370,'Performance class',15,'#66517e',600)
    p.text(110,395,'10:00–11:00 · Recital room',12,'#746087')
    p.text(24,461,'14:00',13,MUTED,600)
    p.rect(91,444,275,137,t['tint'],16)
    p.text(110,477,'Room B0.29',20,t['accent'],600)
    p.text(110,505,'14:00–16:00 · AHC',13,t['accent'])
    p.pill(110,527,'Booked',WHITE,t['accent'],79)
    p.icon(332,535,'arrow',t['accent'],16)
    p.text(24,619,'Your evenings are free.',14,MUTED)
    p.button(24,654,342,'Plan my practice',t['accent'],h=50)
    p.text(195,733,'Planned sessions are not booked yet.',11,MUTED,400,'middle')
    return d,p


def assistant(t):
    d=desktop_shell(t)
    d.text(248,86,'Assistant',28,INK,600,spacing=-.5)
    d.text(248,113,'A little help with your practice.',14,MUTED)
    d.button(701,68,123,'New chat',t['accent'],True)
    d.line(852,55,852,710)
    d.rect(250,155,573,82,t['tint'],17)
    d.text(274,184,'Keep Friday afternoon free.',17,INK,500)
    d.text(274,212,'I’ll practise on the other weekdays.',15,INK)
    brand(d,250,268,t)
    d.lines(301,284,['You have 8 hours booked from Monday to Thursday.', 'Friday is only planned, so there’s nothing to cancel.'],15,INK,26)
    d.rect(301,344,487,163,WHITE,18,LINE)
    d.text(325,377,'Friday, 11 September',19,INK,600)
    d.pill(650,359,'Planned',t['tint'],t['accent'],111)
    d.text(325,408,'14:00–16:00 · Room not booked yet',14,MUTED)
    d.text(325,439,'Turn off practice for this day?',14,INK)
    d.button(325,455,192,'Keep Friday free',t['accent'])
    d.text(301,542,'Your other days will stay as they are.',13,MUTED)
    d.rect(248,613,576,80,WHITE,19,'#d8d0ec')
    d.text(269,645,'Ask about your practice…',15,MUTED)
    d.text(269,675,'Try “Find me a piano room tomorrow”',11,MUTED)
    d.circle(793,653,19,t['accent']);d.icon(782,642,'send',WHITE,22)
    d.text(879,88,'YOUR NEXT SESSION',10,MUTED,600,spacing=1)
    d.text(879,130,'B0.29',29,INK,600)
    d.text(879,160,'Today · 14:00–16:00',13,MUTED)
    d.pill(879,179,'Booked','#e9f3ee','#2c7555',81)
    d.text(879,243,'View booking',13,t['accent'],600)
    d.line(879,270,1083,270)
    d.text(879,309,'A week that works.',17,INK,600)
    for i,(day,time) in enumerate([('Mon','2h booked'),('Tue','2h booked'),('Wed','2h booked'),('Thu','2h booked'),('Fri','2h planned')]):
        yy=351+i*47
        d.text(879,yy,day,13,MUTED)
        d.text(1080,yy,time,13,t['accent'] if i==4 else INK,500,'end')
    d.text(879,620,'Open My Week',13,t['accent'],600)
    d.text(248,735,'Updated just now',11,MUTED)
    p=phone_shell(t)
    p.text(24,88,'Assistant',28,INK,650,spacing=-.5)
    p.icon(337,67,'plus',t['accent'])
    p.rect(20,112,350,59,WHITE,16,LINE)
    p.circle(40,140,3,'#287c59')
    p.text(54,137,'Next · B0.29',14,INK,600)
    p.text(54,156,'Today, 14:00–16:00 · Booked',11,MUTED)
    p.icon(337,130,'arrow',MUTED,17)
    p.rect(59,195,307,83,t['tint'],19)
    p.lines(79,222,['Keep Friday afternoon free.', 'I’ll practise on the other days.'],14,INK,23)
    brand(p,24,302,t)
    p.lines(24,364,['You have 8 hours booked this week.', 'Friday is only planned, so there’s', 'nothing to cancel.'],15,INK,24)
    p.rect(24,446,342,155,WHITE,18,LINE)
    p.text(44,477,'Friday, 11 September',18,INK,600)
    p.text(44,504,'14:00–16:00 · Not booked yet',13,MUTED)
    p.button(44,531,302,'Keep Friday free',t['accent'],h=48)
    p.rect(20,651,350,66,WHITE,18,'#d8d0ec')
    p.text(39,689,'Ask about your practice…',14,MUTED)
    p.circle(337,684,20,t['accent']);p.icon(326,673,'send',WHITE,22)
    return d,p


def details(t, n):
    p=phone_shell(t,True)
    p.icon(23,67,'back',t['accent']);p.text(53,84,'Back',14,t['accent'],500)
    if n==1:
        p.text(24,140,'Your booking',30,INK,600,spacing=-.5)
        p.pill(24,162,'Booked','#e9f3ee','#2c7555',90)
        p.text(24,234,'Room B0.29',33,INK,600,spacing=-.6)
        p.text(24,269,'Tuesday, 8 September',17,INK)
        p.text(24,307,'14:00–16:00',27,INK,500)
        p.line(24,334,366,334)
        for yy,ic,title,sub in [(367,'room','AHC','Piano practice room'),(443,'clock','2 hours','Time reserved for your practice')]:
            p.icon(25,yy-16,ic,t['accent'])
            p.text(65,yy,title,16,INK,600);p.text(65,yy+24,sub,13,MUTED)
        p.rect(24,500,342,110,t['tint'],16)
        p.text(44,529,'Before you practise',15,INK,600)
        p.lines(44,555,['Reconfirm in Asimut on college Wi-Fi', 'when reconfirmation becomes available.'],12,MUTED,21)
        p.button(24,636,342,'Open in Asimut',t['accent'],h=50)
        p.button(24,699,342,'Ask to change booking',t['accent'],True,h=48)
        p.text(195,786,'Cancel booking',14,'#bb433e',500,'middle')
    elif n==2:
        p.text(24,140,'Make time for practice.',27,INK,600,spacing=-.6)
        p.text(24,171,'Your routine, your way.',14,MUTED)
        p.text(24,222,'Daily goal',14,INK,600)
        p.rect(24,239,342,91,WHITE,18,LINE)
        p.circle(57,284,20,t['tint']);p.text(57,291,'−',24,t['accent'],400,'middle')
        p.text(195,295,'2 hours',30,INK,600,'middle')
        p.circle(333,284,20,t['tint']);p.icon(322,273,'plus',t['accent'])
        p.text(24,374,'Days to practise',14,INK,600)
        for i,day in enumerate(['M','T','W','T','F','S','S']):
            p.circle(45+i*50,411,21,t['accent'] if i<5 else WHITE,LINE if i>=5 else None)
            p.text(45+i*50,416,day,13,WHITE if i<5 else MUTED,600,'middle')
        p.text(24,473,'Preferred time',14,INK,600)
        p.rect(24,490,342,52,WHITE,13,LINE)
        p.text(42,523,'Afternoons',15,INK,500);p.text(347,523,'12:00–18:00',13,MUTED,400,'end')
        p.text(24,580,'Only book within these times',13,INK)
        p.rect(316,559,50,30,t['accent'],15);p.circle(351,574,11,WHITE)
        p.line(24,612,366,612)
        p.text(24,647,'Favourite rooms',15,INK,500);p.icon(342,629,'arrow',MUTED,20)
        p.text(24,671,'B0.29, B0.27 · Piano',12,MUTED)
        p.button(24,724,342,'Save practice plan',t['accent'],h=52)
        p.text(195,802,'Rooms are booked when they become available.',10,MUTED,400,'middle')
    else:
        p.text(24,140,'Friday is yours.',31,INK,600,spacing=-.7)
        p.circle(195,234,46,t['tint']);p.icon(171,210,'check',t['accent'],48)
        p.text(195,319,'No practice planned',23,INK,600,'middle')
        p.text(195,350,'Friday, 11 September',15,MUTED,400,'middle')
        p.rect(24,390,342,156,WHITE,19,LINE)
        p.text(44,424,'What changed',17,INK,600)
        p.lines(44,457,['Auto-booking is off for Friday.', 'There were no bookings to cancel.', 'Your other days are unchanged.'],13,MUTED,29)
        p.button(24,591,342,'See my week',t['accent'],h=50)
        p.button(24,654,342,'Undo this change',t['accent'],True,h=48)
        p.text(195,754,'A clear result, after every request.',12,MUTED,400,'middle')
    d=desktop_shell(t)
    d.text(248,88,['Your booking','Practice preferences','Friday is yours.'][n-1],30,INK,600)
    d.text(248,122,['Everything you need for your next session.','Choose a routine. We’ll take care of finding rooms.','Your practice plan has been updated.'][n-1],14,MUTED)
    if n==1:
        d.rect(248,160,475,455,t['tint'],24)
        d.pill(276,185,'Booked',WHITE,t['accent'],85)
        d.text(276,272,'Room B0.29',38,INK,600)
        d.text(276,316,'Tuesday, 8 September',19,INK)
        d.text(276,359,'14:00–16:00',30,INK,500)
        d.text(276,406,'AHC · Piano practice · 2 hours',15,MUTED)
        d.line(276,437,694,437,'#cbdcf1')
        d.text(276,474,'Before you practise',17,INK,600)
        d.lines(276,505,['Reconfirm in Asimut on college Wi-Fi', 'when reconfirmation becomes available.'],14,MUTED,24)
        d.button(276,552,190,'Open in Asimut',t['accent'])
        d.text(758,190,'Change of plans?',20,INK,600)
        d.lines(758,224,['Ask to change the time or room.', 'We’ll check what’s possible.'],14,MUTED,24)
        d.button(758,289,306,'Ask to change booking',t['accent'],True)
        d.text(758,387,'Cancel this booking',15,'#b5423e',600)
        d.lines(758,419,['You’ll review the exact room and time', 'before the booking is cancelled.'],13,MUTED,23)
    elif n==2:
        d.rect(248,163,828,438,'#fafbf9',22,LINE)
        labels=[('Daily goal','2 hours a day'),('Days to practise','Mon   Tue   Wed   Thu   Fri'),('Preferred time','Afternoons · 12:00–18:00'),('Favourite rooms','B0.29, B0.27 · Piano')]
        for i,(title,val) in enumerate(labels):
            y=211+i*98
            d.text(274,y,title,14,MUTED);d.text(530,y,val,17,INK,600)
            d.text(1017,y,'Edit',13,t['accent'],600)
            if i<3:d.line(274,y+36,1050,y+36)
        d.text(530,456,'Only book within these times',12,t['accent'],500)
        d.button(889,634,187,'Save practice plan',t['accent'])
        d.button(771,634,104,'Cancel',t['accent'],True)
        d.text(248,714,'Future dates stay planned until their booking window opens.',12,MUTED)
    else:
        d.circle(315,228,42,t['tint']);d.icon(291,204,'check',t['accent'],48)
        d.text(385,216,'No practice planned',27,INK,600)
        d.text(385,250,'Friday, 11 September',16,MUTED)
        d.rect(248,316,828,225,WHITE,22,LINE)
        for i,txt in enumerate(['Auto-booking is off for Friday.','There were no bookings to cancel.','Monday to Thursday stay as they are.']):
            d.icon(275,347+i*60,'check',t['accent'],21);d.text(314,364+i*60,txt,17,INK)
        d.button(248,582,170,'See my week',t['accent'])
        d.button(435,582,184,'Undo this change',t['accent'],True)
    return d,p


def board(t,n,d,p,detail=False):
    s=SVG(1720,1080)
    s.rect(0,0,1720,1080,t['board'])
    s.text(56,54,f'0{n}  /  ASIMUT BOOKER',12,MUTED,600,spacing=2)
    s.text(56,110,t['name']+(' · the next step' if detail else ''),41,INK,600,spacing=-1)
    s.text(56,145,t['desc'],17,MUTED)
    s.text(1175,75,'DESKTOP + PHONE',11,MUTED,600,spacing=1.7)
    s.text(1175,102,'Design study · sample content',12,MUTED)
    s.rect(50,190,1132,772,'#dde2e9',25)
    s.group(''.join(d.parts),56,194)
    s.rect(1253,168,406,860,'#d6dbe4',43)
    s.group(''.join(p.parts),1261,176)
    captions=[('Calm by default','Your next booking comes first. A single primary action keeps decisions simple.'),('Your time, clearly arranged','Booked rooms, classes, and plans share one useful calendar.'),('Conversation with clarity','Short requests become concrete actions, with a readable result every time.')]
    title,desc=captions[n-1]
    if detail:title,desc='A clear next step','Dedicated details and preferences keep the main screen simple.'
    s.text(56,1001,title,18,INK,600)
    s.text(56,1032,desc,14,MUTED)
    return s


def main():
    for n,(theme,build) in enumerate(zip(THEMES,[focus,week,assistant]),1):
        d,p=build(theme)
        d.save(f'option-{n}-desktop');p.save(f'option-{n}-phone')
        board(theme,n,d,p).save(f'option-{n}')
        dd,pp=details(theme,n)
        dd.save(f'option-{n}-desktop-detail');pp.save(f'option-{n}-phone-detail')
        board(theme,n,dd,pp,True).save(f'option-{n}-flow')
    (OUT/'themes.json').write_text(json.dumps(THEMES,indent=2)+'\n',encoding='utf-8')
    print('Created 18 SVGs: three paired directions, with desktop and phone follow-up screens.')


if __name__ == '__main__': main()
