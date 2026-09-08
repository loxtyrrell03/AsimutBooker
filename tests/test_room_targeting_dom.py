import unittest

from playwright.sync_api import sync_playwright

from overview_geometry import ROOM_COORDINATES_JS, SVG_SNAPSHOT_JS


class RoomTargetingDOMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={'width': 1000, 'height': 650})
        self.addCleanup(self.page.close)
        self.rooms = [f'B1.{i:02}' for i in range(29)] + ['Corus Recital Room', 'Weston Gallery']

    def grid(self, *, zoom=1, row_height=30, hour_width=60, reverse=False):
        rooms = list(reversed(self.rooms)) if reverse else self.rooms
        width, height = 16*hour_width, len(rooms)*row_height
        labels = ''.join(f'<a data-location-id="{i+1}" style="position:absolute;top:{100*i/len(rooms)}%;height:{100/len(rooms)}%">{room}</a>' for i,room in enumerate(rooms))
        axis = ''.join(f'<p style="position:absolute;left:{100*i/16}%">{i+7}</p>' for i in range(1,16))
        lines = ''.join(f'M{x} 0V{height}' for x in range(0,width+1,hour_width)) + ''.join(f'M0 {y}H{width}' for y in range(0,height+1,row_height))
        targets = ''.join(f'<rect data-room="{room}" x="0" y="{i*row_height}" width="{width}" height="{row_height}" fill="white"/>' for i,room in enumerate(rooms))
        self.page.set_content(f'''<style>body{{margin:0}} .outer{{height:540px;width:900px;overflow:auto;margin:40px}} .grid-scroll{{height:350px;width:600px;overflow:auto;margin-left:220px}} #legend-y-inner{{position:absolute;top:100px;left:0;width:200px;height:{height*zoom}px}} #legend-y-inner a{{width:200px}} #legend-x-inner{{position:relative;width:{width*zoom}px;height:30px}}</style>
            <div class="outer"><app-overview-svg>
            <div style="position:absolute;overflow:hidden;height:350px;width:200px"><div id="legend-y-inner">{labels}</div></div>
            <div id="legend-x-inner">{axis}</div>
            <div class="grid-scroll"><svg viewBox="0 0 {width} {height}" width="{width*zoom}" height="{height*zoom}">
            {targets}<path d="{lines}" fill="none" stroke="gray" pointer-events="none"/></svg></div>
            </app-overview-svg></div>
            <script>window.clicked=[]; document.querySelector('svg').addEventListener('click',e=>window.clicked.push(e.target.getAttribute('data-room')))</script>''')

    def test_every_room_clicks_in_detached_legend_and_nested_scroll_grid(self):
        for options in ({}, {'zoom':1.65,'row_height':43,'hour_width':75,'reverse':True}, {'zoom':0.8,'row_height':24,'hour_width':45}):
            self.grid(**options)
            for room in self.rooms:
                with self.subTest(options=options, room=room):
                    coords = self.page.evaluate(ROOM_COORDINATES_JS, [room,12,13,self.rooms])
                    self.assertIsNotNone(coords)
                    self.page.mouse.click(coords['x'],coords['y'])
                    self.assertEqual(self.page.evaluate('window.clicked.at(-1)'),room)

    def test_availability_uses_actual_row_height_and_hour_spacing(self):
        self.grid(zoom=1.4,row_height=43,hour_width=75,reverse=True)
        self.page.locator('svg').evaluate("el => el.insertAdjacentHTML('beforeend','<rect class=\"event-overlay\" data-location-id=\"1\" x=\"375\" y=\"0\" width=\"150\" height=\"43\"/>')")
        snapshot = self.page.evaluate(SVG_SNAPSHOT_JS,self.rooms)
        weston = next(row for row in snapshot['rooms'] if row['room']=='Weston Gallery')
        self.assertAlmostEqual(weston['blockedRanges'][0]['startHour'],12, places=5)
        self.assertAlmostEqual(weston['blockedRanges'][0]['endHour'],14, places=5)
        self.assertIsNone(self.page.evaluate(ROOM_COORDINATES_JS,['Weston Gallery',12,13,self.rooms]))
        self.assertIsNotNone(self.page.evaluate(ROOM_COORDINATES_JS,['Corus Recital Room',12,13,self.rooms]))

    def test_unknown_or_ambiguous_geometry_and_overlays_block_clicking(self):
        for change in (
            "document.querySelector('#legend-x-inner').remove()",
            "document.querySelector('a').setAttribute('data-location-id','2')",
            "document.body.insertAdjacentHTML('beforeend','<div style=\"position:fixed;inset:0;background:white;z-index:100\"></div>')",
            "document.querySelector('svg').insertAdjacentHTML('beforeend','<rect class=\"event-overlay\" data-location-id=\"1\" x=\"300\" y=\"30\" width=\"60\" height=\"30\"/>')",
        ):
            with self.subTest(change=change):
                self.grid()
                self.page.evaluate(change)
                self.assertIsNone(self.page.evaluate(ROOM_COORDINATES_JS,['Weston Gallery',12,13,self.rooms]))
                self.assertEqual(self.page.evaluate('window.clicked'),[])

    def test_legacy_grid_scrolls_actual_surface(self):
        self.page.set_content('''<div style="height:350px;width:800px;overflow:auto">
            <div style="height:1600px"></div><div style="display:flex;height:40px">
            <div data-cy="overview-location-row" style="width:200px">Corus Recital Room</div>
            <div class="location-day" style="width:600px;background:lightblue" onclick="window.clicked=true"></div>
            </div></div>''')
        coords=self.page.evaluate(ROOM_COORDINATES_JS,['Corus Recital Room',12,13,self.rooms])
        self.assertIsNotNone(coords)
        self.page.mouse.click(coords['x'],coords['y'])
        self.assertTrue(self.page.evaluate('window.clicked'))


if __name__ == '__main__':
    unittest.main()
