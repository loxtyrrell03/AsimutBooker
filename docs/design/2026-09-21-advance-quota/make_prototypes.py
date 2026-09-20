"""Editable SVG proposals in the existing Open canvas/phone visual language."""
from pathlib import Path
from html import escape

ROOT = Path(__file__).parent
def make(code, title, subtitle):
    parts=['<svg xmlns="http://www.w3.org/2000/svg" width="1500" height="1550" viewBox="0 0 1500 1550">',
           '<rect width="1500" height="1550" fill="#f8fafc"/>',
           '<style>text{font-family:Segoe UI,Arial,sans-serif;fill:#1d2430} .muted{fill:#667080} .blue{fill:#0868d9}</style>']
    def text(x,y,s,size=18,cls='',bold=False):
        parts.append(f'<text x="{x}" y="{y}" font-size="{size}" class="{cls}" font-weight="{600 if bold else 400}">{escape(s)}</text>')
    def box(x,y,w,h,fill='#fff'):
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{fill}" stroke="#e1e6ee"/>')
    def button(x,y,w,s,primary=False):
        box(x,y,w,42,'#eaf3ff' if primary else '#fff');text(x+16,y+27,s,16,'blue' if primary else '')
    def field(x,y,w,label,value):
        text(x,y,label,16);box(x,y+12,w,43);text(x+12,y+39,value,16);text(x+w-24,y+39,'⌄',16,'blue')
    text(34,44,f'{code} · {title}',29,bold=True);text(34,75,subtitle,17,'muted')
    text(34,101,'DESIGN PROPOSAL · All bookings, balances and availability below are invented examples.',14,'muted')
    box(28,125,1010,680);text(55,167,'Asimut',22,bold=True)
    text(350,167,'Today      My Week      Calendar      Assistant      Settings',16,'blue')
    text(68,225,'Advance quota',30,bold=True);text(68,255,'Use your rolling allowance for the practice that matters most.',16,'muted')
    if code=='A':
        text(68,295,'Rooms and periods',20,bold=True)
        field(68,328,410,'Rooms to book in advance  ?','Selected rooms: Weston, Corus')
        field(518,328,450,'Time periods  ?','1. Weekdays 12:00–16:00     2. 16:00–18:00')
        text(68,432,'Distribution',20,bold=True)
        field(68,467,410,'How to share the allowance  ?','Spread across days')
        field(518,467,450,'Preferred advance block  ?','1 hour')
        button(68,545,410,'Day priorities and limits                 ›')
        button(518,545,450,'Waiting, reserve and fallback           ›')
        text(68,643,'Example allocation · 6h total',18,bold=True)
        text(68,675,'Mon 1h   Tue 1h   Wed 1h   Thu 45m   Fri 45m   Sat 45m   Sun 45m',16,'muted')
    elif code=='B':
        text(68,299,'1 Rooms  →  2 Periods  →  3 Distribution  →  4 Review',19,'blue',True)
        text(68,355,'How would you like to use the six hours?',22,bold=True)
        for y,name,desc in [(389,'Spread across days','Secure a useful block on as many practice days as possible.'),
                            (470,'Fewer, longer sessions','Concentrate the allowance into fewer days.'),
                            (551,'Custom day priorities','Give selected days a larger share; set individual limits.')]:
            box(68,y,895,66);text(86,y+28,name,18,bold=True);text(86,y+52,desc,15,'muted')
        text(68,670,'All controls stay available when revisiting any step.',16,'muted')
    else:
        text(68,299,'Plan your allowance',20,bold=True)
        for n,(day,amount) in enumerate(zip(('Mon','Tue','Wed','Thu','Fri','Sat','Sun'),('2h','—','2h','—','2h','—','—'))):
            x=68+n*128;box(x,325,116,178);text(x+15,359,day,18,bold=True)
            if amount!='—':
                box(x+9,380,98,67,'#eaf3ff');text(x+22,420,amount,24,'blue',True)
            text(x+11,482,'Set limits ›',14,'blue')
        text(68,554,'Selected day · Monday',21,bold=True)
        field(68,591,270,'Advance maximum  ?','2 hours')
        field(366,591,270,'Priority  ?','High')
        field(664,591,299,'Preferred periods  ?','16:00–18:00')
    button(68,735,180,'Save changes',True);button(266,735,110,'Cancel')
    text(530,761,'Applies to future decisions; keeps existing bookings.',14,'muted')
    # Phone uses the same interaction model, not an unrelated desktop-only form.
    box(1070,125,390,680);text(1090,165,'Asimut',21,bold=True);text(1090,195,'Settings  ›  Advance quota',16,'blue')
    text(1090,243,'Advance quota',27,bold=True)
    if code=='A':
        for y,label,value in [(288,'Rooms','Weston, Corus  ›'),(390,'Periods','12:00–16:00; 16:00–18:00  ›'),(492,'Distribution','Spread across days  ›')]:field(1090,y,350,label,value)
        button(1090,587,350,'Day priorities and limits             ›');button(1090,640,350,'More options                               ›')
    elif code=='B':
        text(1090,284,'Step 3 of 4 · Distribution',17,'blue')
        for y,label in [(320,'Spread across days'),(400,'Fewer, longer sessions'),(480,'Custom day priorities')]:
            box(1090,y,350,64);text(1108,y+39,label,18)
        button(1090,587,350,'Next: review settings',True)
        button(1090,640,350,'Back to periods')
    else:
        for y,label,value in [(300,'Monday','2 hours · high priority  ›'),(380,'Tuesday','No advance allocation  ›'),(460,'Wednesday','2 hours · high priority  ›')]:
            box(1090,y,350,67);text(1105,y+26,label,17,bold=True);text(1105,y+52,value,15,'blue')
        button(1090,562,350,'Rooms and periods                      ›');button(1090,618,350,'Other days                                    ›')
    button(1090,730,220,'Save changes',True);button(1322,730,118,'Cancel')
    # Complete common details and states. Every concept is editable in A/B/C.
    text(34,850,'Details, setup and states · shared by this proposal',25,bold=True)
    panels=[
        ('Room detail / first setup',['Start with current top two rooms, or choose any allowed rooms.','Rank Weston, Corus and your own alternatives with Up / Down.','Choose selected rooms only, or allow other eligible rooms.','Access restrictions and excluded rooms always apply.']),
        ('Periods and day detail',['Add ranked periods, each with weekdays, start and end.','Prefer these periods, or only use them for advance bookings.','Set weekday priority and an advance limit per day.','Spread evenly, weight days, concentrate, or prefer best quality.']),
        ('Waiting and flexibility',['Choose a preferred block and minimum daily anchor.','Wait for shorter-horizon rooms, or use open rooms now.','Optionally keep some advance minutes unallocated.','Choose when last-minute practice may spend held credit.']),
        ('Help / empty / loading',['? explains rolling credit: used sessions return credit.','No custom periods: use existing preferred times.','No selected rooms: show inline error; retain the draft.','Loading settings…  Cancel remains available.']),
        ('Saving / errors / cancellation',['Saving… prevents a duplicate submission.','Invalid period: show the error beside its times.','Changed elsewhere: Reload settings before saving again.','Cancel discards this editor draft; reservations stay intact.']),
        ('Confirmation / live preview',['Saved. Future booking decisions use these settings.','My Week labels intentions separately from confirmed bookings.','Room opens in 5 days: show its actual unlock, not a promise.','Unavailable credit or slots: explain the remaining shortfall.']),
    ]
    for n,(heading,lines) in enumerate(panels):
        x=28+(n%2)*728;y=878+(n//2)*211;box(x,y,710,191);text(x+22,y+34,heading,20,bold=True)
        for i,line in enumerate(lines):text(x+22,y+67+i*28,line,15,'muted')
    parts.append('</svg>');(ROOT/f'option-{code.lower()}.svg').write_text('\n'.join(parts),encoding='utf-8')

make('A','Focused settings','One compact editor with expandable details, matching the existing Settings pages. Recommended.')
make('B','Guided setup','A four-step flow for choosing rooms, periods, distribution and a final review.')
make('C','Week allocation','Start from a seven-day allowance view, then edit each day and its priorities.')
