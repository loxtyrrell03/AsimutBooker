"""Editable phone parity proposals. Synthetic content; never imports the app."""
from pathlib import Path
from html import escape
import calendar
import json

OUT = Path(__file__).resolve().parent
BLUE, INK, MUTED, LINE = '#0868d9', '#1d2430', '#667080', '#e1e6ee'
BG, PALE, RED, AMBER = '#f8fafc', '#eaf3ff', '#b73332', '#93620c'
OPTIONS = {
    1: ('Desktop companion', 'Keep My Week and Calendar separate.', ['Today', 'My Week', 'Calendar', 'Assistant', 'Settings']),
    2: ('Combined calendar', 'One Calendar for the agenda and date planning.', ['Today', 'Calendar', 'Assistant', 'Settings']),
    3: ('Calendar home', 'Dates first; practice and system tools in Manage.', ['Calendar', 'Assistant', 'Manage']),
}


class SVG:
    def __init__(self, w, h):
        self.w, self.h, self.p = w, h, []

    def rect(self, x, y, w, h, fill='white', r=12, stroke=LINE, dash=False):
        self.p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}"' + (' stroke-dasharray="4 4"' if dash else '') + '/>')

    def text(self, x, y, value, size=14, color=INK, bold=False, anchor='start'):
        self.p.append(f'<text x="{x}" y="{y}" font-family="Segoe UI,Arial,sans-serif" font-size="{size}" fill="{color}" font-weight="{600 if bold else 400}" text-anchor="{anchor}">{escape(str(value))}</text>')

    def line(self, x, y, w):
        self.p.append(f'<path d="M{x} {y}h{w}" stroke="{LINE}"/>')

    def add(self, other, x, y, scale=1):
        self.p.append(f'<g transform="translate({x} {y}) scale({scale})">' + ''.join(other.p) + '</g>')

    def xml(self, title):
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}" role="img"><title>{escape(title)} — invented example data, proposed interface</title>' + ''.join(self.p) + '</svg>'


class Phone(SVG):
    def __init__(self, option, title, tab='Settings', narrow=False, connected=True):
        super().__init__(320 if narrow else 390, 844)
        self.option, self.y = option, 144
        self.rect(0, 0, self.w, self.h, BG, 24)
        self.text(20, 28, '9:41', 12, bold=True)
        self.text(self.w-20, 28, 'EXAMPLE', 11, MUTED, anchor='end')
        self.rect(20, 47, 30, 30, BLUE, 9, BLUE)
        for x, h in [(28, 9), (34, 18), (40, 13)]:
            self.rect(x, 69-h, 3, h, 'white', 1, 'white')
        self.text(60, 67, 'Asimut', 17, bold=True)
        self.text(self.w-20, 67, 'Connected' if connected else 'Offline', 12, MUTED if connected else RED, anchor='end')
        self.text(20, 116, title, 27, bold=True)
        tabs = OPTIONS[option][2]
        if option == 2 and tab == 'My Week': tab = 'Calendar'
        if option == 3 and tab not in tabs: tab = 'Calendar' if tab in ('Today', 'My Week') else 'Manage'
        self.rect(1, 754, self.w-2, 89, 'white', 0, LINE)
        gap = (self.w-16)/len(tabs)
        for i, name in enumerate(tabs):
            cx = 8+gap*(i+.5)
            active = name == tab
            if active: self.rect(8+gap*i, 762, gap, 58, PALE, 12, PALE)
            color = BLUE if active else MUTED
            icons = {
                'Today': '<path d="m2 10 10-8 10 8v11H7V10h10v11"/>',
                'My Week': '<rect x="3" y="3" width="18" height="19" rx="3"/><path d="M7 8h10M7 13h10M7 18h6"/>',
                'Calendar': '<rect x="3" y="5" width="18" height="17" rx="3"/><path d="M7 2v6M17 2v6M3 11h18M7 15h3M14 15h3"/>',
                'Assistant': '<path d="M21 12a9 9 0 0 1-9 9H3l1-5a9 9 0 1 1 17-4Z"/><path d="M7 9h10M7 14h6"/>',
                'Settings': '<path d="M3 7h18M3 17h18"/><circle cx="8" cy="7" r="3" fill="white"/><circle cx="16" cy="17" r="3" fill="white"/>',
                'Manage': '<path d="M3 7h18M3 17h18"/><circle cx="8" cy="7" r="3" fill="white"/><circle cx="16" cy="17" r="3" fill="white"/>',
            }
            self.p.append(f'<g transform="translate({cx-10} 769) scale(.85)" fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{icons[name]}</g>')
            self.text(cx, 808, name, 10.5 if len(tabs)==5 else 12, color, active, 'middle')
        self.rect(self.w/2-50, 832, 100, 4, INK, 2, INK)

    def words(self, value, color=MUTED, size=14, bold=False, x=20, width=None):
        width = width or self.w-x-20
        limit = int(width/(size*.54))
        line = ''
        for word in value.split():
            if len(line)+len(word)+1 > limit:
                self.text(x, self.y+size, line, size, color, bold)
                self.y += size+7
                line = word
            else: line = (line+' '+word).strip()
        if line:
            self.text(x, self.y+size, line, size, color, bold)
            self.y += size+7
        self.y += 5

    def heading(self, value):
        self.y += 8
        self.words(value, INK, 17, True)

    def segments(self, labels, selected=0):
        width=(self.w-40)/len(labels)
        for i,label in enumerate(labels):
            self.rect(20+i*width,self.y,width,44,PALE if i==selected else 'white',10)
            self.text(20+(i+.5)*width,self.y+28,label,14,BLUE if i==selected else MUTED,i==selected,'middle')
        self.y+=60

    def row(self, label, detail='', action='›'):
        self.text(20, self.y+20, label, 15, bold=True)
        self.text(self.w-22, self.y+20, action, 16, BLUE, anchor='end')
        self.y += 27
        if detail: self.words(detail, size=13, width=self.w-58)
        self.line(20, self.y+6, self.w-40)
        self.y += 21

    def field(self, label, value, help=False):
        self.text(20, self.y+14, label, 14, bold=True)
        if help: self.text(self.w-26, self.y+14, '?', 15, BLUE, True)
        self.rect(20, self.y+24, self.w-40, 44)
        self.text(34, self.y+52, value, 16)
        self.y += 83

    def toggle(self, label, on=True):
        self.text(20, self.y+23, label, 14)
        self.rect(self.w-64, self.y+5, 44, 27, BLUE if on else LINE, 14)
        self.rect(self.w-(40 if on else 61), self.y+8, 21, 21, 'white', 11, 'white')
        self.y += 47

    def button(self, label, primary=True, danger=False):
        fill = RED if danger else BLUE if primary else 'white'
        self.rect(20, self.y, self.w-40, 46, fill, 12)
        self.text(self.w/2, self.y+29, label, 14, 'white' if primary or danger else INK, True, 'middle')
        self.y += 58

    def notice(self, title, lines, error=False):
        start = self.y
        # Text follows rectangle so the full message stays visible.
        self.p.append('NOTICE')
        index = len(self.p)-1
        self.y += 12
        self.words(title, RED if error else INK, 14, True, 32, self.w-64)
        self.words(lines, RED if error else MUTED, 13, x=32, width=self.w-64)
        self.y += 8
        height = self.y-start
        self.p[index] = f'<rect x="20" y="{start}" width="{self.w-40}" height="{height}" rx="12" fill="{"#fff1f0" if error else PALE}" stroke="{LINE}"/>'
        self.y += 12

    def save(self, label='Save changes'):
        self.rect(0, 678, self.w, 76, BG, 0, BG)
        self.rect(20, 692, 104, 46, 'white')
        self.text(72, 721, 'Discard', 14, anchor='middle')
        self.rect(136, 692, self.w-156, 46, BLUE, 12, BLUE)
        self.text(136+(self.w-156)/2, 721, label, 14, 'white', True, 'middle')

    def modes(self, active='Month'):
        self.rect(20, self.y, self.w-40, 44, 'white')
        self.text(34, self.y+28, f'{active}  ▾', 14, BLUE, True)
        self.text(self.w-34, self.y+28, '‹    Today    ›', 14, anchor='end')
        self.y += 60

    def month(self, select=False, fortnight=False):
        cell = (self.w-40)/7
        for i, d in enumerate(['M', 'T', 'W', 'T', 'F', 'S', 'S']):
            self.text(20+cell*(i+.5), self.y+12, d, 12, MUTED, anchor='middle')
        self.y += 24
        weeks = calendar.Calendar().monthdayscalendar(2026, 9)
        if fortnight: weeks = weeks[1:3]
        for week in weeks:
            for i, d in enumerate(week):
                if not d: continue
                x = 20+cell*i
                chosen = d in ((8, 9, 10) if select else (8,))
                self.rect(x+2, self.y, cell-4, 43, BLUE if chosen else 'white', 8, BLUE if chosen else LINE)
                self.text(x+cell/2, self.y+24, d, 15, 'white' if chosen else RED if d==13 else INK, chosen, 'middle')
                if d==13:
                    self.p.append(f'<path d="M{x+12} {self.y+19}h{cell-24}" stroke="{RED}"/>')
                if d in (8,10,11):
                    self.p.append(f'<circle cx="{x+cell/2}" cy="{self.y+35}" r="2" fill="{"white" if chosen else BLUE}"/>')
            self.y += 49
        self.y += 8

    def event(self, title='B0.29', time='14:00–16:00', kind='Booked', potential=False):
        self.rect(20, self.y, self.w-40, 83, 'white' if potential else PALE, 14, BLUE if potential else LINE, potential)
        self.text(34, self.y+27, title, 18, BLUE, True)
        self.text(34, self.y+56, time, 15)
        self.text(self.w-34, self.y+56, kind, 12, MUTED, anchor='end')
        self.y += 95


def make(option, number):
    titles = ['Connect to Booker','Today','Calendar','Calendar','My Week','Practice dates','Booking plan','Assistant','Booking details','Settings','Target & times','Room order','Room requirements','Booking strategy','Waiting & priorities','Automatic booking','Run booker','Health & login','Available rooms','Activity & history','Manage events','Connection & loading','Save needs attention','Cancellation','Waiting choices','Confirm changes','Files & configuration','Setup & support','Calendar','Calendar']
    tab = 'Calendar' if number in (3,4,6,7,29,30) else 'Today' if number==2 else 'My Week' if number==5 else 'Assistant' if number==8 else 'Settings'
    title = titles[number-1]
    if number==10 and option==3: title='Manage'
    if number==5 and option==2: title='Calendar'
    s = Phone(option, title, tab, narrow=number==25, connected=number not in (1,22))
    if number==1:
        s.heading('Your private phone app')
        s.words('Connect this phone to your private network, then open Booker.')
        s.button('Open private Booker')
        s.row('PC connection', 'The PC must be on and reachable.')
        s.row('Install on this phone', 'Safari → Share → Add to Home Screen')
        s.notice('Cannot reach the PC', 'Check the PC and private connection. Your request has not been sent.', True)
        s.button('Try connection again', False)
    elif number==2:
        s.words('Tuesday 8 September', size=14)
        s.heading('Next practice')
        s.event()
        s.button('View booking')
        s.row('Also today', '10:00–11:00 · Performance class')
        s.row('This week', '8 hours booked · 2 hours potential')
        s.button('Open calendar', False)
        s.button('Find available rooms', False)
    elif number in (3,6,29):
        if option==3 and number==3:
            s.row('Today · next practice', 'B0.29 · 14:00–16:00 · Booked')
        s.modes('Fortnight' if number==29 else 'Month')
        s.words('September 2026', INK, 18, True)
        s.month(select=number==6, fortnight=number==29)
        if number==6:
            s.words('3 selected dates · 8–10 September', INK, 14, True)
            s.toggle('Book on selected dates')
            s.field('Target per selected day', '3 hours')
            s.save('Save 3 dates')
        elif number==29:
            s.words('7–20 September · tap a day', size=13)
            s.event()
            s.row('Select dates', 'Enable all visible / disable all visible')
            s.words('Future dates wait for their booking window.')
        else:
            s.words('● Booking    13 crossed out: rooms closed', size=12)
            if option!=3: s.event()
            s.button('Select practice dates', False)
    elif number in (4,30):
        s.modes('Week' if number==4 else '3 days')
        s.words('8–14 September' if number==4 else '8–10 September', INK, 17, True)
        s.words('Swipe timeline for more days →', size=12)
        y=s.y
        for i,d in enumerate(['Tue 8','Wed 9','Thu 10']):
            x=63+i*102
            s.text(x+43,y+16,d,13,BLUE,i==0,'middle')
            s.rect(x,y+34,93,250,'white',8)
        for i,t in enumerate(['12:00','13:00','14:00','15:00','16:00']):
            s.text(20,y+65+i*50,t,11,MUTED)
        s.rect(68,y+155,83,94,PALE,8,BLUE)
        s.text(77,y+183,'B0.29',13,BLUE,True)
        s.text(77,y+207,'Booked',12,BLUE)
        s.rect(170,y+90,83,80,'white',8,BLUE,True)
        s.text(179,y+116,'B1.09',13,BLUE,True)
        s.text(179,y+143,'Potential',12,MUTED)
        s.y=y+310
        s.row('Tuesday 8', '2 hours booked · target 3 hours')
        s.button('Edit this day', False)
    elif number==5:
        if option==1: s.words('7–13 September · checked 2 minutes ago',size=13)
        else: s.modes('Agenda')
        s.heading('Tuesday 8')
        s.event('Performance class','10:00–11:00','Class')
        s.event()
        s.heading('Wednesday 9')
        s.event('B1.09','13:00–14:00','Potential',True)
        s.button('Refresh events',False)
    elif number==7:
        s.modes('Plan')
        s.row('Tuesday 8 September','Target 3 hours · 2 hours booked')
        s.event('B1.09','16:00–17:00','Potential',True)
        s.notice('Waiting for booking window','This hour is a possible session. It is not reserved.')
        s.row('Other options','Show alternative rooms and times')
        s.row('Selected day','Edit practice target or turn the day off')
        s.button('Refresh booking plan',False)
    elif number==8:
        s.row('Fresh agenda','Checked before this request')
        s.notice('You','Book my usual practice tomorrow.')
        s.notice('Checking availability','Agenda checked · finding rooms')
        s.button('Stop request',False)
        s.field('Message','Ask Booker…')
        s.button('Send')
        s.words('New chat · existing draft kept when navigating',size=12)
    elif number==9:
        s.words('Tuesday 8 September · Reservation',size=14)
        s.event()
        s.row('Location','Music Practice Rooms · AHC')
        s.notice('Reconfirm at College','Open Asimut on RWCMD Wi-Fi when reconfirmation becomes available.')
        s.button('Open in Asimut')
        s.button('Cancel this booking',False)
        s.words('Cancellation asks you to confirm this exact room and time.',size=13)
    elif number==10:
        if option==1:
            for a,b in [('Practice target','Daily hours and date overrides'),('Preferred times','Presets, custom hours and strict times'),('Rooms & sessions','Order, requirements and minimum length'),('Booking strategy','Waiting and room/time priorities'),('Automatic booking','Schedule, manual runs and health'),('Activity & tools','History, events, scans and files')]: s.row(a,b)
        elif option==2:
            s.row('Your practice','3 hours per enabled day','⌄')
            s.field('Preferred time','12:00–18:00')
            s.button('Edit target and times',False)
            for a,b in [('Rooms & sessions','Order and requirements'),('Booking strategy','Waiting and priorities'),('Automatic booking','Schedule, runs and health'),('Activity & tools','History, events and files')]: s.row(a,b,'›')
        else:
            s.segments(['Practice','System','Activity'])
            s.heading('Your routine')
            s.row('Target & times','3 hours · 12:00–18:00')
            s.row('Rooms & sessions','Room order and requirements')
            s.row('Booking strategy','Waiting and fallback')
            s.button('Edit practice dates',False)
    elif number==11:
        s.toggle('Use a daily practice target')
        s.field('Target per enabled day','3 hours')
        s.toggle('Prefer a time of day')
        s.field('Time preset','Afternoon · 12:00–18:00  ▾')
        s.field('Custom start / end','12:00    to    18:00')
        s.toggle('Only book inside these times',False)
        s.row('Date overrides','Open Calendar to edit individual days')
        s.save()
    elif number==12:
        s.field('Find a room','Search room names')
        s.words('Included rooms · highest priority first',size=13)
        for a,b in [('1  Weston Gallery','Included'),('2  Corus Recital Room','Included'),('3  B0.29','Included'),('4  B1.09','Excluded')]: s.row(a,b,'↑  ↓')
        s.button('Edit room requirements',False)
        s.words('Tap a room to include or exclude it.',size=13)
        s.save()
    elif number==13:
        s.field('Acceptable instruments','Any instrument  ▾',True)
        s.field('Acceptable room types','Practice room  ▾',True)
        s.field('Required features','grand piano, adjustable stool',True)
        s.field('Minimum useful session','30 minutes  ▾',True)
        s.toggle('Allow split sessions')
        s.notice('Required features','Every listed feature must match the room.')
        s.save()
    elif number==14:
        s.toggle('Plan before booking')
        s.field('Preferred peak window','12:00    to    16:00',True)
        s.field('Desired peak session','120 minutes  ▾',True)
        s.toggle('Wait for better later rooms')
        s.field('Look ahead','180 minutes  ▾',True)
        s.row('Waiting & priorities','Later choices, fallback and date order')
        s.save()
    elif number==15:
        s.field('Better later rooms required','2 rooms  ▾',True)
        s.field('Stop waiting before peak ends','60 minutes  ▾',True)
        s.field('After peak hours, prefer','Longest available session  ▾',True)
        s.field('Main priority','Session time before room rank  ▾',True)
        s.toggle('Book furthest dates first')
        s.save()
    elif number==16:
        if option==3: s.segments(['Practice','System','Activity'],1)
        s.notice('Automatic booking is on','Every 15 minutes · 07:13–21:58')
        s.row('Next automatic run','Today at 14:13')
        s.row('Last completed run','Today at 13:58')
        s.button('Install / repair schedule')
        s.button('Remove schedule',False)
        s.row('Run manually','Start a run and follow its progress')
        s.row('Health & login','View checks and recover login')
    elif number==17:
        s.field('Run mode','In background  ▾',True)
        s.words('Also available: show browser on the PC.',size=13)
        s.notice('Book using saved preferences','This can create reservations. Only eligible dates and rooms will be attempted.')
        s.button('Start booking run')
        s.heading('Example: run in progress')
        s.notice('Checking room availability','Agenda checked · scanning selected dates')
        s.button('Stop this run',False)
        s.words('If a save has started, its result is checked before the run finishes.',size=13)
    elif number==18:
        for a,b in [('Automatic schedule','On · next run 14:13'),('Last successful run','Today at 13:58'),('Saved login','Checked today'),('Login recovery','No cooldown'),('Pending actions','None recorded'),('Wake from sleep','Not yet physically verified')]: s.row(a,b)
        s.button('Check / repair login',False)
    elif number==19:
        s.field('Dates to scan','8–10 September · 3 dates  ▾')
        s.button('Scan available rooms')
        s.words('While scanning: progress and Stop scan appear here.',size=13)
        s.heading('Checked availability')
        s.words('Date: All    Room: All    Minimum: 30 min',size=12)
        s.row('B0.29 · Tuesday 8','16:00–17:00 · 60 minutes')
        s.row('B1.09 · Wednesday 9','13:00–15:00 · 120 minutes')
        s.words('Openings can change; these are not bookings.',size=13)
        s.button('Download results CSV',False)
    elif number==20:
        s.segments(['Activity','History','Logs'],1)
        s.row('Today · 13:58','Completed · no new reservations')
        s.row('Today · 13:43','Completed · 1 verified extension')
        s.row('Today · 13:28','Stopped before booking · settings changed')
        s.button('Refresh history',False)
        s.row('Run details','Times, verified results and warnings')
        s.row('Manage events','Choose which events block practice')
        s.row('Clear history','Review permanent deletion first')
    elif number==21:
        s.notice('Ignored events allow conflicts','Booker may reserve practice over an event you choose to ignore.',True)
        s.button('Refresh my agenda',False)
        s.row('Tuesday 8 · 10:00–11:00','Performance class · blocks booking','☑')
        s.row('Thursday 10 · 09:00–10:00','Workshop · ignored','☐')
        s.row('Apply to visible events','Respect all / ignore all')
        s.words('Bookings still count toward booking limits.',size=13)
        s.save()
    elif number==22:
        s.notice('Loading calendar…','Cancel loading keeps the last checked agenda.')
        s.button('Cancel loading',False)
        s.notice('No events on this date','Agenda checked. Select practice dates to plan sessions.')
        s.notice('Connection lost','Showing the last checked agenda. New booking actions need a connection.',True)
        s.button('Retry connection',False)
        s.words('Unavailable data says “Not checked”; it never appears as zero.',size=13)
    elif number==23:
        s.field('Preferred time','18:00    to    12:00')
        s.words('End time must be later than start time.',RED,14)
        s.notice('Settings changed on the PC','Keep your draft and reload saved values before reviewing changes.',True)
        s.button('Reload saved values',False)
        s.notice('Save result unknown','Check saved settings before sending another save. Your draft is retained.',True)
        s.button('Check saved settings',False)
    elif number==24:
        s.notice('Cancel this booking?','B0.29 · Tue 8 Sep · 14:00–16:00. This time stays protected from rebooking.')
        s.button('Confirm cancellation',True,True)
        s.button('Keep booking',False)
        s.heading('After confirmation')
        s.words('Opening booking → cancelling → verifying absence',INK,14)
        s.notice('Result could not be verified','Check the current booking before any further action. Do not repeat cancellation.',True)
        s.row('Cancelled time','Allow booking here again…')
    elif number==25:
        s.field('Better later rooms required','2 rooms  ▾',True)
        s.notice('What does this mean?','Wait only when this many distinct better rooms are available later. This is a count, not a probability.')
        s.button('Close help',False)
        s.field('Stop waiting before peak ends','60 minutes  ▾',True)
        s.words('Help opens beside ?. Tap again or press Escape to close.',size=13)
        s.save()
    elif number==26:
        s.notice('Remove automatic schedule?','Future automatic runs will stop. Existing reservations remain.')
        s.button('Remove schedule',True,True)
        s.heading('History deletion')
        s.words('Permanently delete the displayed history records. Reservations are unchanged.',INK,14)
        s.button('Delete history',True,True)
        s.heading('Discard unsaved edits?')
        s.words('Keep editing to retain your changes.',size=14)
        s.button('Keep editing',False)
    elif number==27:
        s.row('Today’s log','Read sanitized booking activity')
        s.row('Other logs','Choose date · read or download')
        s.row('Advanced configuration','Validated fields · save with revision check')
        s.heading('Clean up old files')
        s.row('Selected old logs','3 files · 420 KB · example selection','☑')
        s.notice('Review before deletion','Only eligible old logs are listed. Active logs, settings and login data are excluded.')
        s.button('Review selected files',False)
    elif number==28:
        s.row('Check / repair login','Run secure recovery on the PC')
        s.notice('Changing stored credentials','Use the masked setup on your PC. Phone controls can check or recover an existing saved login.')
        s.row('Visible browser mode','Opens a browser on the PC')
        s.row('About Asimut Booker','Version, connection and support information')
        s.row('Phone installation','Add to Home Screen instructions')
        s.words('College reconfirmation still needs RWCMD Wi-Fi when Asimut enables it.',size=14)
    return s


def build():
    (OUT/'screens').mkdir(exist_ok=True)
    manifest=[]
    for option,(name,description,_) in OPTIONS.items():
        screens=[]
        for number in range(1,31):
            s=make(option,number)
            title=f'{name} / screen {number:02}'
            path=f'screens/option-{option}-{number:02}.svg'
            (OUT/path).write_text(s.xml(title),encoding='utf-8')
            manifest.append({'path':path,'width':s.w,'height':s.h,'screen':number})
            screens.append(s)
        # A readable three-phone overview, without reducing font sizes in source.
        cover=SVG(1300,1030)
        cover.rect(0,0,1300,1030,'#eef1f5',0,'#eef1f5')
        cover.text(32,43,f'{option:02}  {name}',30,bold=True)
        cover.text(32,75,description,17,MUTED)
        cover.text(32,105,'Design proposal · every value is an example · no live actions',13,MUTED)
        for x,idx,label in [(32,3,'CALENDAR'),(454,10,'SETTINGS'),(876,16,'AUTOMATIC BOOKING')]:
            cover.text(x,143,label,12,MUTED,True)
            cover.add(screens[idx-1],x,162)
        (OUT/f'option-{option}.svg').write_text(cover.xml(name),encoding='utf-8')
        atlas=SVG(1708,7490)
        atlas.rect(0,0,atlas.w,atlas.h,'#eef1f5',0,'#eef1f5')
        atlas.text(32,45,f'{option:02}  {name} — complete surface and state atlas',30,bold=True)
        atlas.text(32,79,'30 editable screens · all values invented · static proposals, not working app controls',16,MUTED)
        for i,s in enumerate(screens):
            x,y=32+(i%4)*418,140+(i//4)*915
            atlas.text(x,y-14,f'{i+1:02} / '+('320px help state' if i==24 else '390px phone'),13,MUTED)
            atlas.add(s,x,y)
        (OUT/f'option-{option}-atlas.svg').write_text(atlas.xml(name+' full atlas'),encoding='utf-8')
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print('Generated 3 overview SVGs, 3 full atlases and 90 individual screens.')


if __name__=='__main__': build()
