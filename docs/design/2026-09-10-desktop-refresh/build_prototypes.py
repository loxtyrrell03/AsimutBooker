"""Editable review artwork only. No imports from, or writes to, the application."""
from pathlib import Path
from contextlib import contextmanager
from html import escape
import json
import math
import re
import xml.etree.ElementTree as ET
from PIL import ImageFont

ROOT = Path(__file__).resolve().parent
BLUE, PALE, INK, MUTED = '#0868D9', '#EAF3FF', '#1D2430', '#667080'
BG, LINE, WHITE, GREEN, RED = '#F8FAFC', '#E1E6EE', '#FFFFFF', '#2B805B', '#B73332'
AMBER, WARN, BAD = '#93620C', '#FFF8ED', '#FFF1F0'
FONTS = {False: 'C:/Windows/Fonts/segoeui.ttf', True: 'C:/Windows/Fonts/seguisb.ttf'}
OPTIONS = {'a': ('Quiet desktop', 'A familiar home, made lighter.'),
           'b': ('Open canvas', 'More space. One clear focus.'),
           'c': ('Week workspace', 'Your schedule and its details, together.')}
TITLES = dict(today='Today', week='My Week', calendar='Calendar', timeline='Calendar',
              fortnight='Calendar', **{'three-day': 'Calendar'}, plan='Practice plan',
              dates='Edit practice dates', booking='Your booking', settings='Settings',
              practice='Daily target & times', rooms='Rooms', strategy='Booking strategy',
              system='System', run='Finding practice rooms', scan='Available rooms',
              activity='Activity & history', events='Events & protected time', tools='Advanced tools',
              setup='Welcome to Asimut', assistant='Assistant', cancel='Cancel this booking?',
              cancelling='Cancelling booking', feedback='Loading, empty & offline',
              recovery='Keep your place', confirmations='Confirm an action',
              narrow='Daily target & times', **{'narrow-settings': 'Settings', 'minimum-settings': 'Settings'})
NAV = [('today', 'Today', 'home'), ('week', 'My Week', 'list'),
       ('calendar', 'Calendar', 'calendar'), ('assistant', 'Assistant', 'chat'),
       ('settings', 'Settings', 'sliders')]
ICONS = {
 'home': 'M3 10L12 3L21 10V21H15V14H9V21H3Z',
 'calendar': 'M5 5H19Q21 5 21 7V20H3V7Q3 5 5 5M7 3V7M17 3V7M3 10H21M7 14H9M15 14H17M7 17H9',
 'list': 'M9 6H21M9 12H21M9 18H21M3 6H4M3 12H4M3 18H4',
 'chat': 'M4 4H20V17H10L4 21Z',
 'sliders': 'M3 6H8M12 6H21M3 12H15M19 12H21M3 18H5M9 18H21M8 3V9M15 9V15M5 15V21',
 'arrow': 'M4 12H20M14 6L20 12L14 18',
 'refresh': 'M20 8A9 9 0 1 0 20 17M20 3V8H15',
 'clock': 'M12 3A9 9 0 1 0 12 21A9 9 0 1 0 12 3M12 7V12L16 14',
 'door': 'M4 21H20M6 21V3L18 5V21M14 13H14.1',
 'check': 'M4 12L9 17L20 6',
 'close': 'M6 6L18 18M6 18L18 6',
 'chevron': 'M9 5L16 12L9 19',
 'left': 'M15 5L8 12L15 19',
 'stop': 'M5 5H19V19H5Z',
 'search': 'M10 3A7 7 0 1 0 10 17A7 7 0 1 0 10 3M15 15L21 21',
}

class Canvas:
    def __init__(self, option, key, w=1200, h=800):
        self.option, self.key, self.w, self.h = option, key, w, h
        self.items, self.text_boxes, self.errors, self.controls = [], [], [], []
        self.rect(0, 0, w, h, BG, 0)

    @contextmanager
    def link(self, key, label=''):
        self.items.append(f'<a href="{self.option}-{key}.svg" aria-label="{escape(label or TITLES[key])}">')
        yield
        self.items.append('</a>')

    def rect(self, x, y, w, h, fill=WHITE, r=16, stroke=None, dash=None):
        attrs = f' stroke="{stroke}" stroke-width="1"' if stroke else ''
        if dash: attrs += f' stroke-dasharray="{dash}"'
        self.items.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}"{attrs}/>')

    def line(self, x, y, w, color=LINE):
        self.items.append(f'<path d="M{x} {y}h{w}" stroke="{color}" fill="none"/>')

    def text(self, x, y, value, size=16, color=INK, bold=False, anchor='start', strike=False, check=True):
        f = ImageFont.truetype(FONTS[bool(bold)], round(size * 10))
        width = f.getlength(str(value))/10
        left = x if anchor == 'start' else x-width if anchor == 'end' else x-width/2
        box = (left, y-size*.82, left+width, y+size*.22, str(value))
        if check:
            if left < -0.5 or left+width > self.w+.5 or box[1] < 0 or box[3] > self.h:
                self.errors.append(f'Out of canvas: {value} {box[:4]}')
            self.text_boxes.append(box)
        attr = ' text-decoration="line-through"' if strike else ''
        self.items.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{600 if bold else 400}" text-anchor="{anchor}"{attr}>{escape(str(value))}</text>')
        return width

    def wrap(self, x, y, value, width, size=14, color=MUTED, bold=False):
        f = ImageFont.truetype(FONTS[bool(bold)], size*10)
        words, row = value.split(), ''
        for word in words:
            test = (row+' '+word).strip()
            if row and f.getlength(test)/10 > width:
                self.text(x,y,row,size,color,bold); y += size+7; row=word
            else: row=test
        if row: self.text(x,y,row,size,color,bold); y += size+7
        return y

    def icon(self, name, x, y, size=20, color=MUTED):
        self.items.append(f'<path transform="translate({x} {y}) scale({size/24})" d="{ICONS[name]}" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" fill="none"/>')

    def button(self, x,y,w,label,key=None,primary=False,danger=False,small=False):
        h=36 if small else 42
        def draw():
            self.rect(x,y,w,h,RED if danger else BLUE if primary else WHITE,11,None if primary or danger else LINE)
            self.controls.append((x,y,x+w,y+h,len(self.text_boxes),label))
            self.text(x+w/2,y+h/2+5,label,14,WHITE if primary or danger else INK,True,'middle')
        if key:
            with self.link(key,label): draw()
        else: draw()

    def pill(self,x,y,label,kind='blue'):
        font=ImageFont.truetype(FONTS[True],120)
        w=font.getlength(label)/10+22
        fill,color = (PALE,BLUE) if kind=='blue' else (WHITE,GREEN) if kind=='green' else (WHITE,MUTED)
        self.rect(x,y,w,26,fill,13)
        self.text(x+11,y+18,label,12,color,True)
        return w

    def row(self,x,y,w,title,detail='',key=None,color=INK):
        def draw():
            self.text(x,y+20,title,16,color,True)
            if detail: self.text(x,y+44,detail,13,MUTED)
            self.icon('chevron',x+w-22,y+12,18,BLUE)
            self.line(x,y+62,w)
        if key:
            with self.link(key,title): draw()
        else: draw()

    def field(self,x,y,w,label,value,help=False):
        self.text(x,y+14,label,14,INK,True)
        self.rect(x,y+26,w,42,WHITE,10,LINE)
        self.text(x+12,y+53,value,15)
        if help:
            self.rect(x+w-23,y-3,22,22,PALE,11)
            self.text(x+w-12,y+13,'?',13,BLUE,True,'middle')

    def toggle(self,x,y,w,label,on=True):
        self.text(x,y+21,label,15)
        self.rect(x+w-44,y,44,26,BLUE if on else LINE,13)
        self.rect(x+w-(23 if on else 41),y+3,20,20,WHITE,10)

    def notice(self,x,y,w,title,detail,kind='warn',action=None):
        self.rect(x,y,w,106,PALE if kind=='info' else BAD if kind=='error' else WARN,14)
        color=BLUE if kind=='info' else RED if kind=='error' else AMBER
        self.text(x+18,y+28,title,16,color,True)
        self.wrap(x+18,y+52,detail,w-36,14,color)
        if action: self.text(x+18,y+89,action,14,color,True)

    def shell(self):
        narrow=self.w<900; o=self.option
        self.rect(0,0,self.w,34,WHITE,0)
        self.text(18,23,'Asimut Booker',12,MUTED)
        self.line(self.w-114,17,12,MUTED)
        self.rect(self.w-72,12,10,10,WHITE,0,MUTED)
        self.icon('close',self.w-34,8,19)
        self.line(0,34,self.w)
        selected=self.key
        if selected in ('timeline','fortnight','three-day','plan','dates'): selected='calendar'
        elif selected in ('booking','cancel','cancelling'): selected='today'
        elif selected not in ('today','week','calendar','assistant'): selected='settings'
        if o=='b':
            self.rect(0,35,self.w,82,WHITE,0)
            self.brand(32,55,not narrow)
            navx=88 if narrow else 430
            for i,(k,label,ic) in enumerate(NAV):
                nx=navx+i*(124 if not narrow else 125)
                with self.link(k):
                    if k==selected: self.rect(nx,56,114,42,PALE,12)
                    self.text(nx+57,82,label,14,BLUE if k==selected else MUTED,k==selected,'middle')
            self.line(0,117,self.w)
            x=32 if narrow else 132; y=208; w=self.w-x-(32 if narrow else 132)
        else:
            rail=narrow or o=='c'; sw=88 if rail else 200
            self.rect(0,35,sw,self.h-35,WHITE,0)
            self.brand(26 if rail else 24,62,not rail)
            for i,(k,label,ic) in enumerate(NAV):
                ny=134+i*(76 if rail else 58)
                if k=='settings': ny=self.h-132
                with self.link(k):
                    if k==selected: self.rect(12,ny,sw-24,62 if rail else 46,PALE,13)
                    self.icon(ic,34 if rail else 26,ny+9 if rail else ny+13,21,BLUE if k==selected else MUTED)
                    self.text(sw/2 if rail else 61,ny+49 if rail else ny+29,label,11 if rail else 15,BLUE if k==selected else MUTED,k==selected,'middle' if rail else 'start')
            if not rail:
                self.text(26,self.h-220,'AUTO-BOOKING',10,MUTED,True)
                self.rect(26,self.h-203,6,6,GREEN,3)
                self.text(40,self.h-195,'On · next 12:13',12,MUTED)
            self.items.append(f'<path d="M{sw} 35V{self.h}" stroke="{LINE}"/>')
            x=sw+32; y=178; w=self.w-x-40
        self.text(x,y-58,TITLES[self.key],32,INK,True)
        if self.key in ('today','calendar','timeline','fortnight','three-day','plan','week'):
            self.text(x,y-28,'Thursday 10 September',14,MUTED)
            self.button(x+w-152,y-74,152,'Refresh bookings','feedback')
        else:
            subtitles={'settings':'Your practice, your preferences.', 'minimum-settings':'Your practice, your preferences.', 'narrow-settings':'Your practice, your preferences.', 'assistant':'Make or change a plan in your own words.',
                       'booking':'From your checked agenda', 'narrow':'Default practice preferences',
                       'setup':'Connect your college account to get started.'}
            self.text(x,y-28,subtitles.get(self.key,'Settings' if selected=='settings' else 'Thursday 10 September'),14,MUTED)
        self.line(x,self.h-44,w)
        self.text(x,self.h-20,f'{self.option.upper()} · {OPTIONS[self.option][0]}   /   Design preview · invented example data',11,MUTED)
        self.x,self.y,self.cw=x,y,w
        return x,y,w

    def brand(self,x,y,wordmark=True):
        self.rect(x,y,36,36,BLUE,11)
        for dx,h in [(10,11),(17,21),(24,15)]: self.rect(x+dx-2,y+27-h,4,h,WHITE,2)
        if wordmark: self.text(x+48,y+24,'Asimut',20,INK,True)

    def save(self,path):
        # Text overlap catches labels and copy colliding; shapes may intentionally overlap.
        for i,a in enumerate(self.text_boxes):
            for b in self.text_boxes[i+1:]:
                ix=min(a[2],b[2])-max(a[0],b[0]); iy=min(a[3],b[3])-max(a[1],b[1])
                if ix>2 and iy>2: self.errors.append(f'Text overlap: {a[4]!r} / {b[4]!r}')
            for control in self.controls:
                if i==control[4]: continue
                ix=min(a[2],control[2])-max(a[0],control[0]); iy=min(a[3],control[3])-max(a[1],control[1])
                if ix>2 and iy>2: self.errors.append(f'Text crosses control: {a[4]!r} / {control[5]!r}')
        title=f'{self.option.upper()} · {OPTIONS[self.option][0]} · {TITLES[self.key]}'
        svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}" font-family="Segoe UI, sans-serif"><title>{escape(title)}</title><desc>Design proposal. All values are invented examples. Local links navigate static mockups only.</desc>' + ''.join(self.items)+'</svg>'
        path.write_text(svg,encoding='utf-8')
        return svg

def hero(c,x,y,w,h=284):
    c.rect(x,y,w,h,PALE,24)
    c.text(x+24,y+34,'UP NEXT',11,BLUE,True)
    c.pill(x+w-100,y+18,'Booked','green')
    c.text(x+24,y+89,'Room A1.06',30,INK,True)
    c.text(x+24,y+133,'12:00–14:00',27)
    c.icon('door',x+24,y+153,18)
    c.text(x+51,y+167,'2 hours of practice',14,MUTED)
    c.text(x+24,y+204,'Reconfirm on college Wi-Fi when available.',12,MUTED)
    c.button(x+24,y+h-64,w-48,'View booking','booking',True)

def agenda(c,x,y,w):
    c.text(x,y+22,'Also today',20,INK,True)
    c.row(x,y+46,w,'15:00–16:00  ·  Chamber music','Class · Recital room','week')
    c.row(x,y+121,w,'17:00–18:00  ·  Room A1.09','Booked practice · 1 hour','booking')
    c.text(x,y+219,'Your daily target',13,MUTED)
    c.text(x+w,y+219,'3 hours',14,INK,True,'end')
    c.line(x,y+239,w)

def today(c):
    x,y,w=c.x,c.y,c.cw
    if c.option=='b':
        hero(c,x,y,w,284)
        # B uses a single, broad booking summary; retain the phone's quiet lower rows.
        c.row(x,y+302,w,'Also today','15:00 Chamber music · 17:00 Room A1.09','week')
        c.row(x,y+380,w,'12 hours booked this week','7–13 September · classes and planned time excluded','week')
        c.button(x,y+464,148,'Find a room','assistant',True)
        c.button(x+160,y+464,154,'Ask Assistant','assistant')
    else:
        left=math.floor(w*.54); right=w-left-30
        hero(c,x,y,left,304)
        agenda(c,x+left+30,y,right)
        c.button(x+left+30,y+261,right,'Find a room','assistant')
        c.text(x,y+360,'This week',20,INK,True)
        c.text(x,y+406,'12 hours',30,INK,True)
        c.text(x,y+432,'booked · 7–13 September',14,MUTED)
        with c.link('week'):
            c.text(x,y+473,'See my week',14,BLUE,True); c.icon('arrow',x+101,y+458,19,BLUE)
        c.rect(x+left+30,y+336,right,147,WHITE,18,LINE)
        c.icon('chat',x+left+50,y+357,22,BLUE)
        c.text(x+left+50,y+403,'Need to change your plans?',16,INK,True)
        c.button(x+left+50,y+423,right-40,'Ask Assistant','assistant')
    c.text(x,c.h-62,'Last checked today at 11:42',12,MUTED)

def settings(c):
    x,y,w=c.x,c.y,c.cw; gap=18; cw=(w-gap)/2
    compact=c.h<800
    step=112 if compact else 132; ch=94 if compact else 114
    rows=[('Daily target','3 hours each practice day','practice','clock'),
          ('Preferred times','12:00–20:00 · flexible','practice','clock'),
          ('Practice dates','Choose days and daily overrides','dates','calendar'),
          ('Rooms','Favourites, requirements and sessions','rooms','door'),
          ('Booking strategy','When to wait and when to book','strategy','sliders'),
          ('Automatic booking','On · next run today at 12:13','system','refresh')]
    for i,(title,detail,key,icon) in enumerate(rows):
        xx=x+(i%2)*(cw+gap); yy=y+(i//2)*step
        with c.link(key):
            c.rect(xx,yy,cw,ch,WHITE,16,LINE)
            c.icon(icon,xx+18,yy+18,19,BLUE)
            c.text(xx+48,yy+34,title,16,INK,True)
            c.wrap(xx+18,yy+65,detail,cw-52,13)
            c.icon('chevron',xx+cw-32,yy+ch-38,17,BLUE)
    c.row(x,y+(352 if compact else 408),w,'System','Manual runs, login and health checks','system')
    c.row(x,y+(422 if compact else 478),w,'Activity & history','Recent runs, bookings and logs','activity')

def segments(c,x,y,w,selected):
    keys=['calendar','fortnight','timeline','three-day','plan']; labels=['Month','Fortnight','Week','3 days','Plan']
    sw=min(w/5,112)
    c.rect(x,y,sw*5,38,WHITE,11,LINE)
    for i,(k,l) in enumerate(zip(keys,labels)):
        with c.link(k):
            if selected==k: c.rect(x+i*sw+3,y+3,sw-6,32,PALE,8)
            c.text(x+(i+.5)*sw,y+25,l,13,BLUE if selected==k else MUTED,selected==k,'middle')

def calendar(c):
    x,y,w=c.x,c.y,c.cw
    segments(c,x,y,w,c.key)
    c.text(x,y+83,'September 2026',21,INK,True)
    c.button(x+246,y+53,34,'‹',small=True)
    c.button(x+288,y+53,34,'›',small=True)
    c.button(x+330,y+53,74,'Today','calendar',small=True)
    c.button(x+w-168,y+53,168,'Edit selected dates','dates',small=True)
    cols=7; gridw=w if c.option=='b' else w-280; cell=gridw/7
    for i,day in enumerate(['Mon','Tue','Wed','Thu','Fri','Sat','Sun']): c.text(x+i*cell+14,y+126,day,12,MUTED)
    weeks=2 if c.key=='fortnight' else 5
    step=80 if weeks==2 else 64
    for j in range(weeks):
        for i in range(7):
            # September 2026 begins on Tuesday; fortnight starts Monday 7th.
            n=(7 if weeks==2 else 0)+j*7+i
            if not 1<=n<=30: continue
            xx=x+i*cell; yy=y+143+j*step
            selected=n==10; off=n in (12,13); closed=n==20
            fill=PALE if selected else BAD if closed else '#F1F4F8' if off else WHITE
            with c.link('dates'):
                c.rect(xx+2,yy,cell-5,step-8,fill,10,BLUE if selected else LINE)
                c.text(xx+14,yy+25,str(n),15,RED if closed else INK,selected,'start',off or closed)
                if n in (7,8,9,10,11): c.text(xx+14,yy+46,{7:'3h',8:'1h',9:'3h',10:'3h',11:'2h'}[n],12,BLUE)
                if off: c.text(xx+14,yy+46,'Off',11,MUTED)
                if closed: c.text(xx+14,yy+46,'Closed',11,RED)
    below=y+143+weeks*step+24
    c.text(x,below,'Hours in cells = booked practice',12,MUTED)
    if c.option!='b':
        rx=x+gridw+26; rw=w-gridw-26
        c.text(rx,y+128,'Thursday 10',20,INK,True)
        c.text(rx,y+158,'3 hours booked · 3 hour target',12,MUTED)
        c.row(rx,y+184,rw,'12:00–14:00','Room A1.06 · booked','booking')
        c.row(rx,y+261,rw,'17:00–18:00','Room A1.09 · booked','booking')
        c.button(rx,y+353,rw,'Edit this day','dates')
        c.text(rx,y+440,'20 September',14,RED,True,'start',True)
        c.wrap(rx,y+464,'Practice rooms closed',rw,13,RED)
    else:
        c.text(x+w,below,'Off dates keep existing bookings',12,MUTED,anchor='end')
    if weeks==2:
        c.row(x,below+25,gridw,'Thursday 10 · 12:00–14:00','Room A1.06 · booked practice','booking')

def timeline(c):
    x,y,w=c.x,c.y,c.cw
    segments(c,x,y,w,'three-day' if c.key=='three-day' else 'timeline')
    c.text(x,y+84,'7–13 September',21,INK,True)
    c.button(x+220,y+54,34,'‹',small=True)
    c.button(x+262,y+54,34,'›',small=True)
    c.button(x+304,y+54,74,'Today','timeline',small=True)
    inspector=280 if c.option=='c' else 0
    tw=w-inspector; axis=47; count=3 if c.key=='three-day' else 7; cell=(tw-axis)/count
    c.button(x+tw-120,y+54,120,'Edit dates','dates',small=True)
    dx=x+axis; gy=y+145; gh=324
    labels=['Thu 10','Fri 11','Sat 12'] if count==3 else ['Mon 7','Tue 8','Wed 9','Thu 10','Fri 11','Sat 12','Sun 13']
    for i,label in enumerate(labels):
        selected=label=='Thu 10'; off='Sat' in label or 'Sun' in label
        c.rect(dx+i*cell,gy-40,cell-5,34,PALE if selected else BG,8)
        with c.link('dates'): c.text(dx+(i+.5)*cell-2,gy-18,label,12,BLUE if selected else MUTED,selected,'middle',off)
        if off: c.rect(dx+i*cell,gy,cell-5,gh,'#F1F4F8',8)
    for j,hour in enumerate(range(9,20,2)):
        yy=gy+j*58
        c.text(x,yy+5,f'{hour:02}:00',11,MUTED); c.line(dx,yy,tw-axis-4)
    def block(col,start,duration,title,detail,plan=False,cl=False):
        bx=dx+col*cell+4; by=gy+(start-9)*29; bw=cell-13; bh=duration*29
        with c.link('plan' if plan else 'booking'):
            c.rect(bx,by,bw,bh,WHITE if plan else '#F1F4F8' if cl else PALE,8,BLUE if plan else None,'4 3' if plan else None)
            c.text(bx+8,by+20,'Not booked yet' if plan and bh<=44 else title,10 if plan and bh<=44 else 12,MUTED if cl else BLUE,True)
            if bh>44: c.text(bx+8,by+39,detail,10,MUTED)
            if plan and bh>65: c.text(bx+8,by+57,'Not booked yet',10,BLUE)
    if count==7:
        block(0,12,3,'A1.04','12:00–15:00'); block(1,10,2,'Class','10:00–12:00',cl=True)
        block(1,17,1,'A1.04','17:00–18:00')
        block(2,14,3,'A1.08','14:00–17:00'); block(3,12,2,'A1.06','12:00–14:00')
        block(3,15,1,'Class','15:00–16:00',cl=True); block(3,17,1,'A1.09','17:00–18:00')
        block(4,10,2,'A1.09','10:00–12:00'); block(4,16,1,'A1.08','16:00–17:00',plan=True)
    else:
        block(0,12,2,'Room A1.06','12:00–14:00'); block(0,15,1,'Chamber music','15:00–16:00',cl=True)
        block(0,17,1,'Room A1.09','17:00–18:00'); block(1,10,2,'Room A1.09','10:00–12:00')
        block(1,16,1,'Room A1.08','16:00–17:00',plan=True)
    c.text(x,y+509,'Booked',12,BLUE); c.text(x+84,y+509,'Class / event',12,MUTED)
    c.rect(x+188,y+496,16,14,WHITE,3,BLUE,'3 2'); c.text(x+211,y+509,'Not booked yet',12,BLUE)
    c.text(x,y+541,'12–13 September: booking off. Existing events stay visible.',12,MUTED)
    if inspector:
        rx=x+tw+25; rw=inspector-25
        c.rect(rx,y+56,rw,465,WHITE,18,LINE)
        c.text(rx+20,y+92,'Thursday 10',20,INK,True)
        c.pill(rx+20,y+111,'Booked','green')
        c.text(rx+20,y+177,'Room A1.06',23,INK,True)
        c.text(rx+20,y+212,'12:00–14:00',20)
        c.text(rx+20,y+244,'2 hours of practice',13,MUTED)
        c.wrap(rx+20,y+290,'Reconfirm on college Wi-Fi when available.',rw-40,13)
        c.button(rx+20,y+345,rw-40,'View booking','booking',True)
        c.button(rx+20,y+399,rw-40,'Edit this day','dates')

def week(c):
    x,y,w=c.x,c.y,c.cw; split=c.option!='b'; lw=w*.64 if split else w
    c.text(x,y+23,'Thursday 10 September',20,INK,True)
    c.row(x,y+42,lw,'12:00–14:00  ·  Room A1.06','Booked practice · 2 hours','booking')
    c.row(x,y+118,lw,'17:00–18:00  ·  Room A1.09','Booked practice · 1 hour','booking')
    c.text(x,y+220,'Friday 11 September',20,INK,True)
    c.text(x,y+247,'2 hours booked · 3 hour target · 1 hour planned',13,MUTED)
    c.rect(x,y+267,lw,110,WHITE,16,BLUE,'5 4')
    c.text(x+20,y+299,'16:00–17:00  ·  Room A1.08',18,INK,True)
    c.text(x+20,y+327,'Not booked yet · booking starts opening Friday at 12:00',13,BLUE)
    with c.link('plan'): c.text(x+20,y+354,'View plan',14,BLUE,True)
    c.text(x,y+417,'Saturday 12 September',19,MUTED,True,'start',True)
    c.text(x,y+444,'Booking off · existing bookings remain visible',13,MUTED)
    c.text(x,y+490,'Sunday 20 September',19,RED,True,'start',True)
    c.text(x,y+517,'Practice rooms closed',13,RED)
    if split:
        rx=x+lw+34; rw=w-lw-34
        c.text(rx,y+24,'This week',20,INK,True)
        c.text(rx,y+73,'12 hours',30,INK,True)
        c.text(rx,y+101,'booked · 7–13 September',13,MUTED)
        c.line(rx,y+126,rw)
        c.wrap(rx,y+153,'Planned sessions are possible future bookings. They are not included in booked hours.',rw,14)
        c.button(rx,y+237,rw,'Open calendar','calendar')

def editor_footer(c,x,y,w,save='Save changes',dest='settings'):
    c.line(x,y,w)
    c.text(x,y+35,'Unsaved changes',12,MUTED)
    c.button(x+w-258,y+14,104,'Discard',dest)
    c.button(x+w-142,y+14,142,save,dest,True)

def practice(c):
    x,y,w=c.x,c.y,c.cw; narrow=c.w<900; cw=(w-24)/2
    c.rect(x,y,w,397,WHITE,18,LINE)
    px=x+24; pw=w-48; half=(pw-24)/2
    c.field(px,y+23,half,'Daily target','3 hours')
    c.field(px+half+24,y+23,half,'Applies to','Enabled practice dates')
    c.line(px,y+113,pw)
    c.text(px,y+151,'Preferred times',18,INK,True)
    c.field(px,y+174,half,'From','12:00')
    c.field(px+half+24,y+174,half,'Until','20:00')
    c.toggle(px,y+266,pw,'Only book within these times',False)
    c.wrap(px,y+329,'Flexible times favour this window. If no suitable room is available, your target can remain unfilled.',pw-12,14)
    if narrow:
        # Deliberately bounded open help state above the footer, with visible focus.
        qx=px+244; qy=y+267
        c.rect(qx-5,qy-5,32,32,WHITE,16,BLUE)
        c.text(qx+11,qy+17,'?',16,BLUE,True,'middle')
        c.rect(x+18,y+405,w-36,89,PALE,14,BLUE)
        c.text(x+34,y+431,'Strict preferred times',14,BLUE,True)
        c.wrap(x+34,y+454,'When on, rooms outside this window will not be booked. Escape closes this help.',w-76,13,BLUE)
    else:
        with c.link('dates'): c.row(x,y+412,w,'Overrides for individual dates','Set a different target or time for a specific day','dates')
    editor_footer(c,x,y+501,w)

def dates(c):
    x,y,w=c.x,c.y,c.cw; left=270; rw=w-left-26; rx=x+left+26
    c.rect(x,y,left,479,WHITE,18,LINE)
    c.text(x+20,y+34,'Selected dates',18,INK,True)
    for i,(d,sel) in enumerate([('Thu 10 September',True),('Fri 11 September',True),('Sat 12 September',False),('Sun 13 September',False)]):
        yy=y+67+i*61
        c.rect(x+16,yy,left-32,49,PALE if sel else WHITE,10)
        c.text(x+28,yy+30,d,14,BLUE if sel else INK,sel)
        if sel: c.icon('check',x+left-48,yy+15,18,BLUE)
    c.button(x+20,y+361,left-40,'Select visible dates','dates')
    c.text(x+20,y+440,'2 dates selected',13,MUTED)
    c.text(rx,y+27,'Change these two dates',20,INK,True)
    c.toggle(rx,y+56,rw,'Book on these dates',True)
    c.field(rx,y+103,rw,'Daily target','3 hours · override')
    c.field(rx,y+190,rw,'Preferred time','Custom · 12:00–20:00')
    c.toggle(rx,y+287,rw,'Only book within these times',False)
    c.notice(rx,y+342,rw,'Existing bookings stay in place','Turning booking off prevents new bookings. It does not cancel reservations.','info')
    editor_footer(c,x,y+497,w,'Save 2 dates','calendar')

def booking(c):
    x,y,w=c.x,c.y,c.cw; bw=min(w,720) if c.option=='b' else w*.6
    c.pill(x,y,'Booked','green')
    c.text(x,y+79,'Room A1.06',34,INK,True)
    c.text(x,y+120,'Thursday 10 September',19)
    c.text(x,y+174,'12:00–14:00',30)
    c.text(x,y+208,'2 hours reserved for practice',15,MUTED)
    c.notice(x,y+244,bw,'Before you practise','Reconfirm in Asimut on college Wi-Fi when it becomes available.','info')
    c.button(x,y+376,bw,'Open in Asimut',None,True)
    c.button(x,y+430,bw,'Ask to change booking','assistant')
    c.button(x,y+484,bw,'Cancel booking','cancel',danger=True)
    if c.option!='b':
        rx=x+bw+36; rw=w-bw-36
        c.text(rx,y+36,'Booking details',19,INK,True)
        c.row(rx,y+57,rw,'Checked today, 11:42','Current agenda snapshot','feedback')
        c.wrap(rx,y+162,'Changes and cancellations check the current booking before acting.',rw,14)
        c.button(rx,y+256,rw,'Back to Today','today')

def plan(c):
    x,y,w=c.x,c.y,c.cw
    c.text(x,y+24,'Friday 11 September',21,INK,True)
    c.text(x,y+53,'1 hour booked · 3 hour target · 2 hours planned',14,MUTED)
    for i,(time,room,reason) in enumerate([('14:00–15:00','Room A1.08','Booking starts opening Friday at 10:00'),('17:00–18:00','Room A1.06','Booking starts opening Friday at 13:00')]):
        yy=y+80+i*163
        c.rect(x,yy,w,142,WHITE,18,BLUE,'5 4')
        c.text(x+22,yy+32,f'SESSION {i+1} · NOT BOOKED YET',11,BLUE,True)
        c.text(x+22,yy+67,time,25,INK,True)
        c.text(x+230,yy+65,room,18)
        c.text(x+22,yy+104,reason,14,MUTED)
        c.button(x+w-152,yy+82,130,'Alternatives','scan',small=True)
    c.notice(x,y+416,w,'Waiting for booking to open','The plan will be checked again before booking. Room availability may change.','info')

def rooms(c):
    x,y,w=c.x,c.y,c.cw; lw=w*.47; rx=x+lw+28; rw=w-lw-28
    c.text(x,y+24,'Room order',19,INK,True)
    c.field(x,y+43,lw,'Search rooms','Search by name')
    for i,room in enumerate(['A1.06','A1.08','A1.09','Weston Gallery']):
        yy=y+139+i*66
        c.rect(x,yy,lw,54,WHITE,12,LINE)
        c.text(x+16,yy+33,str(i+1),12,MUTED)
        c.text(x+45,yy+33,room,15,INK,True)
        c.text(x+lw-90,yy+32,'↑   ↓',16,BLUE)
        c.icon('check',x+lw-32,yy+18,17,BLUE)
    c.text(x,y+442,'Use arrows to change priority.',13,MUTED)
    c.text(rx,y+24,'Room requirements',19,INK,True)
    c.field(rx,y+43,rw,'Instrument','Piano')
    c.field(rx,y+128,rw,'Room type','Any practice room')
    c.field(rx,y+213,rw,'Required features','No additional requirements')
    c.field(rx,y+298,rw,'Minimum session','30 minutes')
    c.toggle(rx,y+396,rw,'Allow separate sessions',True)
    editor_footer(c,x,y+495,w)

def strategy(c):
    x,y,w=c.x,c.y,c.cw; col=(w-24)/2
    c.toggle(x,y+3,w,'Plan ahead before choosing rooms',True)
    fields=[('Look ahead','4 hours'),('Preferred peak times','12:00–16:00'),('Desired peak session','2 hours'),('Later choices needed','2 suitable sessions'),('Fallback lead time','15 minutes'),('After peak hours','Prefer suitable time'),('Date order','Nearest eligible date first'),('When choices compete','Time fit, then room priority')]
    for i,(l,v) in enumerate(fields): c.field(x+(i%2)*(col+24),y+65+(i//2)*87,col,l,v,help=True)
    c.wrap(x,y+443,'Waiting may leave part of your daily target unfilled. Each booking uses fresh availability.',w,14)
    editor_footer(c,x,y+493,w)

def system(c):
    x,y,w=c.x,c.y,c.cw
    c.rect(x,y,w,123,WHITE,18,LINE)
    c.rect(x+20,y+27,7,7,GREEN,4)
    c.text(x+40,y+36,'Automatic booking is on',20,INK,True)
    c.text(x+20,y+74,'Next run 12:13 · last completed run 11:58',14,MUTED)
    c.button(x+w-166,y+22,144,'Manage schedule','confirmations')
    c.text(x,y+167,'Run manually',19,INK,True)
    c.button(x,y+186,180,'Run in background','run',True)
    c.button(x+192,y+186,176,'Run with browser','run')
    c.button(x+380,y+186,184,'Scan availability','scan')
    c.text(x,y+274,'Health checks',19,INK,True)
    rows=[('Saved sign-in','Ready',GREEN),('Booking safety','Clear',GREEN),('Automatic schedule','Installed',GREEN),('Wake from sleep','Not verified',MUTED)]
    for i,(l,v,color) in enumerate(rows):
        xx=x+(i%2)*(w/2+12); yy=y+306+(i//2)*58
        c.text(xx,yy,l,14,MUTED); c.text(xx+180,yy,v,14,color,True)
    c.line(x,y+395,w)
    c.row(x,y+410,w,'Troubleshooting','Repair sign-in, refresh agenda, inspect logs and settings','tools')
    c.button(x,y+490,140,'Check sign-in','setup')
    c.button(x+152,y+490,154,'Refresh health','feedback')

def run(c):
    x,y,w=c.x,c.y,c.cw; pw=min(w,770)
    c.rect(x,y,pw,267,WHITE,20,LINE)
    c.text(x+24,y+38,'Finding a suitable room',22,INK,True)
    for i,(s,done) in enumerate([('Checked your agenda',True),('Checking room availability',False),('Booking and verifying your session',False)]):
        yy=y+82+i*54
        c.rect(x+24,yy-15,24,24,PALE,12)
        if done: c.icon('check',x+28,yy-11,16,BLUE)
        else: c.text(x+36,yy+2,str(i+1),12,BLUE,True,'middle')
        c.text(x+63,yy+3,s,16,INK if done or i==1 else MUTED,i==1)
    c.button(x+24,y+210,126,'Stop','recovery')
    c.wrap(x,y+308,'Stop prevents the next booking. If a save has started, verification finishes first.',pw,14)
    c.notice(x,y+370,pw,'Already booked','Room A1.06 · Thursday 10 September · 12:00–14:00. This verified booking stays in place.','info')

def scan(c):
    x,y,w=c.x,c.y,c.cw; col=(w-252)/2
    c.field(x,y,col,'Date','Friday 11 September')
    c.field(x+col+16,y,col,'Room','All eligible rooms')
    c.field(x+2*col+32,y,220,'Minimum duration','1 hour')
    c.button(x,y+89,154,'Scan availability','run',True)
    c.button(x+166,y+89,126,'Export CSV')
    c.text(x,y+167,'Last successful scan · today at 11:40',13,MUTED)
    c.line(x,y+186,w)
    columns=[0,.40,.66,.85]
    for rel,label in zip(columns,['Room','Available time','Duration','Status']): c.text(x+w*rel,y+216,label,12,MUTED,True)
    for i,(room,time,duration) in enumerate([('A1.06','14:00–16:00','2 hours'),('A1.08','17:00–18:00','1 hour'),('Weston Gallery','16:00–18:00','2 hours')]):
        yy=y+265+i*66
        for rel,label in zip(columns,[room,time,duration,'Available']): c.text(x+w*rel,yy,label,14,BLUE if label=='Available' else INK)
        c.line(x,yy+23,w)
    c.notice(x,y+439,w,'Availability only','A scan does not reserve these rooms. Availability is checked again before booking.','info')

def activity(c):
    x,y,w=c.x,c.y,c.cw
    for i,(time,title,desc) in enumerate([('11:58','Run completed','No additional booking needed'),('10:13','Booked Room A1.06','Thursday 10 September · 12:00–14:00'),('Yesterday','Cancelled Room A1.08','Friday 11 September · 09:00–10:00 · time protected')]):
        yy=y+i*104
        c.text(x,yy+29,time,13,MUTED)
        c.row(x+125,yy,w-125,title,desc,'booking' if i==1 else 'events')
    c.button(x,y+354,146,'Refresh history','feedback')
    c.button(x+158,y+354,126,'View logs','tools')
    c.row(x,y+426,w,'Clear displayed activity','Clears this view; saved history and bookings remain','activity')
    with c.link('confirmations'): c.text(x,y+529,'Clear saved history…',14,RED)

def events(c):
    x,y,w=c.x,c.y,c.cw
    c.text(x,y+25,'Calendar conflicts',20,INK,True)
    c.row(x,y+44,w,'Chamber music · Thursday 10 · 15:00–16:00','Recital room · respected when finding practice','confirmations')
    c.notice(x,y+126,w,'Ignoring an event allows overlapping bookings','Only ignore an event when you no longer need that time kept free.',action='Review event choice')
    c.text(x,y+281,'Protected time',20,INK,True)
    c.row(x,y+307,w,'Friday 11 September · 09:00–10:00','Protected after cancellation · no automatic rebooking','confirmations')
    c.button(x,y+399,195,'Reopen selected time','confirmations')
    c.wrap(x,y+481,'Reopening permits future bookings. It does not restore a cancelled reservation.',w,14)

def tools(c):
    x,y,w=c.x,c.y,c.cw; col=(w-32)/2
    c.row(x,y,col,'Sign-in','Check or repair your college session','setup')
    c.row(x,y+77,col,'Refresh agenda & plan','Keep the last result while updating','run')
    c.row(x,y+154,col,'Availability scan','Find free time and export results','scan')
    c.row(x,y+231,col,'Supported settings','Edit validated advanced rules','strategy')
    c.row(x,y+308,col,'Old log files','Review inactive files before removal','confirmations')
    c.row(x,y+385,col,'Setup & about','App version and secure credential setup','setup')
    rx=x+col+32
    c.rect(rx,y,col,448,WHITE,18,LINE)
    c.text(rx+20,y+36,'Logs',20,INK,True)
    c.text(rx+20,y+69,'Today · 10 September',13,MUTED)
    c.line(rx+20,y+88,col-40)
    for i,(time,msg) in enumerate([('11:58','Run completed'),('11:57','Agenda checked'),('11:56','Room availability checked')]):
        c.text(rx+20,y+124+i*60,time,12,MUTED)
        c.text(rx+76,y+124+i*60,msg,13)
    c.button(rx+20,y+328,col-40,'Open logs folder')
    c.wrap(rx+20,y+405,'Status summaries keep account details private.',col-40,13)

def setup(c):
    x,y,w=c.x,c.y,c.cw; pw=min(660,w)
    c.rect(x,y,pw,340,WHITE,24,LINE)
    c.brand(x+28,y+26)
    c.text(x+28,y+105,'Your practice, in one place.',28,INK,True)
    c.wrap(x+28,y+146,'First, check your saved college sign-in. You can then choose your practice days, times and rooms.',pw-56,16)
    c.button(x+28,y+226,pw-56,'Check saved sign-in','run',True)
    with c.link('recovery'): c.text(x+28,y+310,'Sign-in needs attention? Repair connection',14,BLUE)
    c.notice(x,y+368,pw,'Secure setup on this PC','If credentials are missing, open the masked Windows setup prompt. Passwords are never entered in this page.','info')
    c.button(x,y+499,192,'Open secure setup')

def assistant(c):
    x,y,w=c.x,c.y,c.cw; rw=252 if c.option=='c' else 0; cw=w-rw-(24 if rw else 0)
    c.button(x+cw-112,y-4,112,'New chat','assistant',small=True)
    bubble=min(cw-40,545)
    c.rect(x+cw-bubble,y+62,bubble,82,PALE,18)
    c.wrap(x+cw-bubble+20,y+93,'Can you find me three hours of practice tomorrow afternoon?',bubble-40,16,INK)
    c.icon('chat',x,y+184,25,BLUE)
    c.text(x+40,y+203,'Checking your practice plan',18,INK,True)
    c.text(x+40,y+239,'✓  Refreshed your agenda',14,MUTED)
    c.text(x+40,y+274,'Checking suitable afternoon sessions…',14,BLUE)
    c.button(x+40,y+303,106,'Stop','recovery',small=True)
    c.rect(x,y+410,cw,111,WHITE,18,LINE)
    c.text(x+18,y+446,'Ask about your practice…',15,MUTED)
    c.text(x+18,y+493,'Your draft stays here when you switch pages.',12,MUTED)
    c.button(x+cw-95,y+461,76,'Send','run',True,small=True)
    if rw:
        rx=x+cw+24
        c.text(rx,y+87,'Tomorrow',19,INK,True)
        c.text(rx,y+118,'Friday 11 September',13,MUTED)
        c.row(rx,y+150,rw,'Daily target','3 hours','dates')
        c.row(rx,y+235,rw,'Preferred time','12:00–20:00','practice')
        c.button(rx,y+345,rw,'Open calendar','calendar')

def cancel(c):
    x,y,w=c.x,c.y,c.cw; pw=min(w,690)
    c.rect(x,y,pw,376,WHITE,22,LINE)
    c.text(x+24,y+43,'Room A1.06',27,INK,True)
    c.text(x+24,y+83,'Thursday 10 September · 12:00–14:00',18)
    c.text(x+24,y+116,'2 hours of booked practice',14,MUTED)
    c.line(x+24,y+141,pw-48)
    c.wrap(x+24,y+180,'This reservation will be removed. This time will stay protected from automatic rebooking until you reopen it.',pw-48,16)
    c.text(x+24,y+269,'The current booking is checked before cancellation.',13,MUTED)
    c.button(x+24,y+307,(pw-60)/2,'Keep booking','booking')
    c.button(x+36+(pw-60)/2,y+307,(pw-60)/2,'Cancel booking','cancelling',danger=True)

def cancelling(c):
    x,y,w=c.x,c.y,c.cw; pw=min(w,710)
    c.text(x,y+25,'Room A1.06 · Thursday 10 September',22,INK,True)
    c.text(x,y+61,'12:00–14:00 · cancellation in progress',16,MUTED)
    for i,(label,done) in enumerate([('Checked the current booking',True),('Cancellation sent',True),('Verifying removal in Asimut…',False)]):
        yy=y+115+i*62
        c.rect(x,yy-20,30,30,PALE,15)
        if done: c.icon('check',x+5,yy-15,20,BLUE)
        else: c.icon('refresh',x+5,yy-15,20,BLUE)
        c.text(x+48,yy+2,label,18,BLUE if not done else INK,not done)
    c.wrap(x,y+316,'You can leave this page. Verification continues, and the result will be shown when you return.',pw,15)
    c.button(x,y+382,152,'Back to My Week','week')
    c.notice(x,y+442,pw,'If the result cannot be verified','Keep this request for review. Do not send another cancellation.','info',action='Review result in System')

def state_card(c,x,y,w,h,kicker,title,detail,action,key,kind='info'):
    c.rect(x,y,w,h,WHITE,18,LINE)
    c.text(x+20,y+27,kicker,10,MUTED,True)
    color=RED if kind=='error' else AMBER if kind=='warn' else BLUE
    ty=c.wrap(x+20,y+62,title,w-40,19,color,True)
    c.wrap(x+20,ty+13,detail,w-40,14)
    c.button(x+20,y+h-62,w-40,action,key,primary=kind=='info')

def feedback(c):
    x,y,w=c.x,c.y,c.cw; cw=(w-20)/2
    cards=[('LOADING','Checking your bookings…','Your last checked bookings remain visible while this refresh runs.','Cancel refresh','today','info'),
           ('EMPTY · AGENDA AVAILABLE','No upcoming practice','No practice reservation appears in the checked agenda. Your daily target is still 3 hours.','Find a room','assistant','info'),
           ('OFFLINE · CACHED DATA','Could not refresh','Showing bookings checked today at 11:42. They may have changed.','Try refresh again','today','warn'),
           ('UNAVAILABLE · NO CACHE','Agenda unavailable','Booked hours are unknown. Refresh to load your schedule.','Refresh bookings','today','error')]
    for i,(k,t,d,a,key,kind) in enumerate(cards): state_card(c,x+(i%2)*(cw+20),y+(i//2)*270,cw,251,k,t,d,a,key,kind)

def recovery(c):
    x,y,w=c.x,c.y,c.cw; cw=(w-20)/2
    cards=[('INVALID INPUT','Check the daily target','Enter a value from 0.5 to 12 hours. Your other edits are retained.','Return to editor','practice','error'),
           ('STALE EDIT','Settings changed elsewhere','Review the latest values alongside your draft before saving.','Review changes','practice','warn'),
           ('UNCERTAIN DELIVERY','Check the last save','The reply was interrupted. Read the saved values before another Save; your draft stays here.','Check saved result','practice','warn'),
           ('STOP REQUESTED','Finishing verification','No new booking will start. A save already in progress must finish verification.','View run','run','info')]
    for i,(k,t,d,a,key,kind) in enumerate(cards): state_card(c,x+(i%2)*(cw+20),y+(i//2)*270,cw,251,k,t,d,a,key,kind)

def confirmations(c):
    x,y,w=c.x,c.y,c.cw; cw=(w-24)/3
    cards=[('Remove schedule?','Automatic runs will stop. Existing reservations and saved preferences stay in place.','Remove schedule','system'),
           ('Clear saved history?','Saved run history will be removed. This does not cancel any reservations.','Clear history','activity'),
           ('Remove old log files?','3 selected inactive logs, older than 48 hours, will be removed. Active and recent logs stay.','Remove 3 files','tools')]
    for i,(title,detail,label,key) in enumerate(cards):
        xx=x+i*(cw+12)
        c.rect(xx,y,cw,350,WHITE,18,LINE)
        end=c.wrap(xx+20,y+40,title,cw-40,20,INK,True)
        c.wrap(xx+20,end+24,detail,cw-40,15)
        c.button(xx+20,y+236,cw-40,'Keep unchanged',key)
        c.button(xx+20,y+292,cw-40,label,key,danger=True)
    c.notice(x,y+389,w,'Other explicit decisions','Ignoring an event permits overlapping practice. Reopening protected time permits future bookings; it never restores a cancelled booking.','info')

DRAW = {'today':today,'week':week,'calendar':calendar,'timeline':timeline,'fortnight':calendar,
        'three-day':timeline,'plan':plan,'dates':dates,'booking':booking,'settings':settings,
        'practice':practice,'rooms':rooms,'strategy':strategy,'system':system,'run':run,'scan':scan,
        'activity':activity,'events':events,'tools':tools,'setup':setup,'assistant':assistant,
        'cancel':cancel,'cancelling':cancelling,'feedback':feedback,'recovery':recovery,
        'confirmations':confirmations,'narrow':practice,'narrow-settings':settings,
        'minimum-settings':settings}

def gallery():
    style='''*{box-sizing:border-box}body{margin:0;background:#f8fafc;color:#1d2430;font:16px/1.5 "Segoe UI",sans-serif}main{max-width:1440px;margin:auto;padding:36px}h1{font-size:34px;margin:0 0 10px}p{color:#667080;max-width:850px}nav{display:flex;flex-wrap:wrap;gap:12px;margin:28px 0}a{color:#0868d9}nav a{padding:12px 18px;border:1px solid #e1e6ee;border-radius:12px;background:white;text-decoration:none}section{margin:48px 0}h2{font-size:26px}img{display:block;width:100%;height:auto;border:1px solid #e1e6ee;border-radius:16px;background:white}details{background:white;border:1px solid #e1e6ee;border-radius:14px;padding:16px;margin:16px 0}summary{cursor:pointer;font-weight:600}.links{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;margin-top:18px}.caption{font-size:14px}.compare{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}.compare div{padding:20px;background:white;border:1px solid #e1e6ee;border-radius:16px}a:focus-visible,summary:focus-visible{outline:3px solid #0868d9;outline-offset:4px}@media(max-width:760px){main{padding:20px}.compare{grid-template-columns:1fr}h1{font-size:28px}}'''
    html=f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Asimut desktop design options</title><style>{style}</style><main><h1>A lighter Asimut for your PC</h1><p>Three editable SVG directions using the phone app’s colours, rounded controls and simple hierarchy. Design review only. Every booking and status value is an invented example.</p><nav>'
    for o,(name,desc) in OPTIONS.items(): html+=f'<a href="#option-{o}">{o.upper()} · {name}</a>'
    html+='</nav><div class="compare">'
    for o,(name,desc) in OPTIONS.items(): html+=f'<div><strong>{o.upper()} · {name}</strong><p>{desc}</p><span class="caption">'+{'a':'Recommended · closest to your phone','b':'Top navigation · quieter pages','c':'Calendar home · context beside the week'}[o]+'</span></div>'
    html+='</div>'
    for o,(name,desc) in OPTIONS.items():
        html+=f'<section id="option-{o}"><h2>{o.upper()} · {name}</h2><p>{desc}</p><a href="option-{o}.svg"><img src="option-{o}.png" alt="{escape(name)} desktop home mockup"></a><p class="caption"><a href="option-{o}.svg">Open editable main SVG</a> · <a href="screens/{o}-settings.svg">Settings</a> · <a href="screens/{o}-assistant.svg">Assistant</a> · <a href="screens/{o}-narrow.svg">Narrow window and help</a></p><details><summary>All {len(DRAW)} screens and states</summary><div class="links">'
        for k in DRAW: html+=f'<a href="screens/{o}-{k}.svg">{escape(TITLES[k])} <span class="caption">({k})</span></a>'
        html+='</div></details></section>'
    html+='<p>Navigation links inside SVGs open static example frames. Controls do not change the application. Hover help and form editing are specified in the design notes, not executed here. <a href="README.md">Read the complete surface and interaction map</a>.</p></main></html>'
    (ROOT/'index.html').write_text(html,encoding='utf-8')

def comparison(svgs):
    parts=['<svg xmlns="http://www.w3.org/2000/svg" width="1620" height="1230" viewBox="0 0 1620 1230" font-family="Segoe UI, sans-serif"><title>Three phone-inspired desktop options</title><rect width="1620" height="1230" fill="#F8FAFC"/><text x="40" y="49" font-size="28" font-weight="600" fill="#1D2430">A lighter Asimut for your PC</text><text x="40" y="79" font-size="14" fill="#667080">Same phone palette. Three ways to arrange your desktop. All data is an invented example.</text>']
    for i,o in enumerate(OPTIONS):
        xx=40+(i%2)*790; yy=112+(i//2)*552
        parts.append(f'<text x="{xx}" y="{yy+20}" font-size="21" font-weight="600" fill="{INK}">{o.upper()} · {OPTIONS[o][0]}</text>')
        # Nested SVG keeps editable text; no screenshot is baked into the board.
        fragment=svgs[o].split('>',1)[1].rsplit('</svg>',1)[0]
        parts.append(f'<svg x="{xx}" y="{yy+40}" width="750" height="500" viewBox="0 0 1200 800">{fragment}</svg>')
    parts.append(f'<text x="830" y="735" font-size="26" font-weight="600" fill="{INK}">Choose the layout that feels right.</text>')
    for j,line in enumerate(['A · Familiar sidebar and a calm overview.','B · Top navigation and one open page.','C · A weekly schedule with details beside it.','','Each includes settings, editors, system tools,','loading, errors, cancellation and a narrow view.','','Start with A for the closest match to the phone.']):
        parts.append(f'<text x="830" y="{784+j*36}" font-size="18" fill="{MUTED}">{escape(line)}</text>')
    parts.append('</svg>'); (ROOT/'comparison.svg').write_text(''.join(parts),encoding='utf-8')

def main():
    screens=ROOT/'screens'; screens.mkdir(exist_ok=True)
    errors=[]; count=0; svgs={}
    for o in OPTIONS:
        for key,draw in DRAW.items():
            c=Canvas(o,key,760 if key.startswith('narrow') else 1040 if key=='minimum-settings' else 1200,
                     740 if key=='minimum-settings' else 800)
            c.shell(); draw(c)
            svg=c.save(screens/f'{o}-{key}.svg'); count+=1
            errors.extend(f'{o}-{key}: {e}' for e in c.errors)
            if key==('timeline' if o=='c' else 'today'):
                # Relocate relative frame links for the top-level review file.
                svgs[o]=svg.replace(f'href="{o}-',f'href="screens/{o}-')
                (ROOT/f'option-{o}.svg').write_text(svgs[o],encoding='utf-8')
    gallery(); comparison(svgs)
    for path in ROOT.glob('**/*.svg'):
        allowed={BLUE,PALE,INK,MUTED,BG,LINE,WHITE,GREEN,RED,AMBER,WARN,BAD,'#F1F4F8'}
        for color in re.findall(r'#[0-9a-fA-F]{6}',path.read_text(encoding='utf-8')):
            if color.upper() not in allowed: errors.append(f'{path.name}: unapproved palette value {color}')
        tree=ET.parse(path)
        for elem in tree.iter():
            href=elem.attrib.get('href')
            if href and not (path.parent/href).is_file(): errors.append(f'{path.name}: broken link {href}')
    report={'screens':count,'options':len(OPTIONS),'frames_per_option':len(DRAW),'errors':errors,
            'scope':'Static artwork geometry, local links and screen coverage only; no runtime or browser interaction.'}
    (ROOT/'validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    if errors: raise SystemExit(1)

if __name__=='__main__': main()
