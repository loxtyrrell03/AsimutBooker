"""Option B desktop primitives. Presentation only; no booking or persistence code."""
import math
import tkinter as tk
from tkinter import ttk

PAGE = '#F8FAFC'
WHITE = '#FFFFFF'
BLUE = '#0868D9'
PALE = '#EAF3FF'
INK = '#1D2430'
MUTED = '#667080'
LINE = '#E1E6EE'
RED = '#B73332'


def _rounded_image(root, fill, outline=None, radius=10):
    """Nine-slice rounded background without an optional imaging dependency."""
    image = tk.PhotoImage(master=root, width=44, height=44)
    for y in range(44):
        distance = max(radius-y-.5, y-(43-radius)-.5, 0)
        inset = math.ceil(radius-math.sqrt(max(0, radius*radius-distance*distance))) if distance else 0
        image.put(outline or fill, to=(inset,y,44-inset,y+1))
        if outline and 0 < y < 43:
            image.put(fill,to=(max(1,inset+1),y,43-max(0,inset),y+1))
    return image


def install_theme(root, family='Segoe UI'):
    style = ttk.Style(root)
    images = []
    specs = {
        'TButton': (WHITE,INK,LINE), 'Primary.TButton':(BLUE,WHITE,None),
        'Danger.TButton':(WHITE,RED,LINE), 'QuietNav.TButton':(WHITE,MUTED,None),
        'QuietLink.TButton':(PAGE,BLUE,None), 'Toolbar.TButton':(WHITE,INK,LINE),
        'Segment.TRadiobutton':(WHITE,MUTED,None),
    }
    for index,(name,(fill,fg,border)) in enumerate(specs.items()):
        normal=_rounded_image(root,fill,border)
        hover=_rounded_image(root,'#075CBE' if name=='Primary.TButton' else PALE,border)
        selected=_rounded_image(root,PALE)
        disabled=_rounded_image(root,'#F1F4F8',LINE)
        focus=_rounded_image(root,fill,BLUE)
        images.extend([normal,hover,selected,disabled,focus])
        element=f'OpenCanvas{index}.background'
        if element not in style.element_names():
            style.element_create(element,'image',normal,('disabled',disabled),('pressed',hover),
                                 ('selected',selected),('focus',focus),('active',hover),
                                 border=12,padding=0,sticky='nsew')
        prefix='Radiobutton' if name.endswith('TRadiobutton') else 'Button'
        style.layout(name,[(element,{'sticky':'nsew','children':[(prefix+'.padding',{
            'sticky':'nsew','children':[(prefix+'.label',{'sticky':'nsew'})]})]})])
        backdrop=WHITE if name=='QuietNav.TButton' else PAGE
        style.configure(name,foreground=fg,background=backdrop,font=(family,-14),
                        padding=(16,10),borderwidth=0,anchor='center')
        style.map(name,foreground=[('disabled',MUTED),('selected',BLUE),('!disabled',fg)],
                  background=[('!disabled',backdrop)])
    root._open_canvas_images=images
    style.configure('Segment.TRadiobutton',padding=(14,8))
    style.configure('QuietNav.TButton',padding=(18,12))
    style.layout('Hub.TButton',style.layout('QuietNav.TButton'))
    style.configure('Hub.TButton',padding=(0,0),font=(family,-16,'bold'),anchor='w',background=WHITE,foreground=INK)
    style.map('Hub.TButton',background=[('!disabled',WHITE)],foreground=[('!disabled',INK)])
    style.configure('Hero.Primary.TButton',background=PALE)
    style.map('Hero.Primary.TButton',background=[('!disabled',PALE)])
    style.layout('Navigation.TNotebook.Tab',[])
    style.configure('Navigation.TNotebook',borderwidth=0,tabmargins=0)
    style.configure('Navigation.TNotebook.Tab',padding=0)
    style.map('Navigation.TNotebook.Tab',padding=[('selected',0)])
    style.configure('TSeparator', background=LINE)
    style.configure('Vertical.TScrollbar', background=LINE, troughcolor=PAGE, borderwidth=0, arrowsize=12)
    style.configure('Horizontal.TScrollbar', background=LINE, troughcolor=PAGE, borderwidth=0, arrowsize=12)
    for kind in ('TEntry','TCombobox','TSpinbox'):
        style.configure(kind,fieldbackground=WHITE,background=WHITE,foreground=INK,
                        bordercolor=LINE,lightcolor=LINE,darkcolor=LINE,padding=(10,8),
                        relief='flat',arrowsize=14)
        style.map(kind,bordercolor=[('focus',BLUE)],fieldbackground=[('readonly',WHITE)])
    for kind in ('TFrame','TLabel','TCheckbutton','TRadiobutton','TLabelframe','TLabelframe.Label'):
        style.configure(kind,background=PAGE)
    style.configure('TLabel',foreground=INK,font=(family,-15))
    style.configure('TLabelframe',borderwidth=0,relief='flat')
    style.configure('TLabelframe.Label',font=(family,-17,'bold'))
    style.configure('TCheckbutton',padding=(0,5))
    style.map('TCheckbutton',background=[('active',PAGE)])
    style.map('TRadiobutton',background=[('active',PAGE)])
    style.configure('Treeview',background=WHITE,fieldbackground=WHITE,foreground=INK,
                    rowheight=36,borderwidth=0,font=(family,-14))
    style.configure('Treeview.Heading',background=PAGE,foreground=MUTED,
                    font=(family,-13,'bold'),padding=(10,10),relief='flat')
    style.map('Treeview',background=[('selected',PALE)],foreground=[('selected',BLUE)])
    style.configure('Settings.Title.TLabel',background=PAGE,font=(family,-32,'bold'))
    style.configure('Settings.Subtitle.TLabel',background=PAGE,foreground=MUTED,font=(family,-14))
    root.option_add('*Listbox.background',WHITE)
    root.option_add('*Listbox.foreground',INK)
    root.option_add('*Listbox.selectBackground',PALE)
    root.option_add('*Listbox.selectForeground',BLUE)
    root.option_add('*Listbox.highlightThickness',0)
    root.option_add('*Listbox.relief','flat')
    root.option_add('*Text.highlightThickness',1)
    root.option_add('*Text.highlightColor',BLUE)
    return style


class CenteredFrame(tk.Frame):
    """Fill available height and center a bounded content column."""
    def __init__(self,parent,maximum=984,**kwargs):
        super().__init__(parent,bg=PAGE,**kwargs)
        self.content=tk.Frame(self,bg=PAGE)
        self.maximum=maximum
        self.bind('<Configure>',self._resize)

    def _resize(self,event):
        width=min(self.maximum,max(1,event.width-32))
        self.content.place(x=(event.width-width)//2,y=0,width=width,height=event.height)


class HelpTip(ttk.Button):
    """Small adjacent help, usable with mouse, keyboard and touch."""
    def __init__(self,parent,text):
        super().__init__(parent,text='?',width=2,style='QuietLink.TButton',takefocus=True)
        self.help_text=text; self.popup=None; self._pinned=False
        self.configure(command=self.toggle)
        self.bind('<Enter>',self.show)
        self.bind('<FocusIn>',self.show)
        self.bind('<Leave>',self._leave)
        self.bind('<FocusOut>',self.hide)
        self.bind('<Escape>',self.hide)
        self.bind('<Destroy>',self.hide)
        self._outside=self.winfo_toplevel().bind('<Button-1>',self._outside_click,add='+')

    def _outside_click(self,event):
        if event.widget is not self and (not self.popup or event.widget.winfo_toplevel()!=self.popup): self.hide()

    def toggle(self):
        if self._pinned: self.hide()
        else:
            self._pinned=True
            self.show()

    def _leave(self,_event=None):
        if not self._pinned and self.focus_get() is not self: self.hide()

    def show(self,_event=None):
        if self.popup or not self.winfo_ismapped(): return
        popup=tk.Toplevel(self); self.popup=popup
        popup.overrideredirect(True); popup.configure(bg=BLUE)
        tk.Label(popup,text=self.help_text,font=('Segoe UI',-14),bg=PALE,fg=INK,
                 wraplength=300,justify='left',padx=14,pady=12).pack(padx=1,pady=1)
        popup.update_idletasks()
        host=self.winfo_toplevel(); left=host.winfo_rootx(); top=host.winfo_rooty()
        right=left+host.winfo_width(); bottom=top+host.winfo_height()
        x=max(left+8,min(self.winfo_rootx(),right-popup.winfo_reqwidth()-8))
        y=self.winfo_rooty()+self.winfo_height()+4
        if y+popup.winfo_reqheight()>bottom-8: y=self.winfo_rooty()-popup.winfo_reqheight()-4
        popup.geometry(f'+{x}+{max(top+8,y)}')

    def hide(self,_event=None):
        self._pinned=False
        if self.popup:
            self.popup.destroy(); self.popup=None

    def destroy(self):
        self.hide()
        self.winfo_toplevel().unbind('<Button-1>',self._outside)
        super().destroy()


def reuse_detail(key):
    """Revisiting an editor retains its existing widgets and unsaved draft."""
    def decorate(method):
        def wrapped(self,*args,**kwargs):
            page=getattr(self,'_detail_pages',{}).get(key)
            if page is not None and page.winfo_exists():
                self.main_notebook.select(page.host); return page
            return method(self,*args,**kwargs)
        return wrapped
    return decorate


class DetailPage(tk.Frame):
    """Embedded editor body retaining the controller's existing close callbacks."""
    def __init__(self,app,key,owner='settings'):
        from quiet_focus import ScrollPage
        self.app,self.key,self.owner=app,key,owner
        self._title=key; self._close=None
        self.host=ttk.Frame(app.main_notebook,style='Page.TFrame')
        app.main_notebook.add(self.host,text=key)
        page=ScrollPage(self.host)
        page.pack(fill='both',expand=True)
        bar=tk.Frame(page.content,bg=PAGE,padx=24,pady=8);bar.pack(fill='x')
        ttk.Button(bar,text='← Back',style='QuietLink.TButton',command=self.back).pack(side='left')
        super().__init__(page.content,bg=PAGE)
        self.pack(fill='both',expand=True)
        app._detail_pages[key]=self
        app.main_notebook.select(self.host)
        self.bind('<Map>',self._prepare_labels)

    def _prepare_labels(self,_event=None):
        def visit(widget):
            if isinstance(widget,ttk.Label) and widget.cget('wraplength') and not getattr(widget,'_open_canvas_wrapped',False):
                maximum=widget.winfo_pixels(widget.cget('wraplength'))
                widget._open_canvas_wrapped=True
                widget.master.bind('<Configure>',lambda e,w=widget,limit=maximum:w.configure(wraplength=max(80,min(limit,e.width-24))) if w.winfo_exists() else None,add='+')
            if isinstance(widget,ttk.Button) and str(widget.cget('text')).startswith('Save'):
                widget.configure(style='Primary.TButton')
            for child in widget.winfo_children(): visit(child)
        visit(self)

    def title(self,value=None):
        if value is not None: self._title=value
        return self._title
    def geometry(self,*_): pass
    def minsize(self,*_): pass
    def transient(self,*_): pass
    def grab_set(self,*_): pass
    def protocol(self,name,callback=None):
        if name=='WM_DELETE_WINDOW': self._close=callback
    def back(self):
        self.app._select_quiet_page(self.owner)
    def destroy(self):
        if getattr(self.app,'_closing',False) or not self.app.topbar.winfo_exists():
            return super().destroy()
        self.app._detail_pages.pop(self.key,None)
        super().destroy()
        self.app.main_notebook.forget(self.host)
        self.app._select_quiet_page(self.owner)
        if self.host.winfo_exists():
            self.host.destroy()
