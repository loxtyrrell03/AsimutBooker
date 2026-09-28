"""Render local SVGs with an isolated browser; no live application requests."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent


def main():
    findings=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--disable-gpu'])
        page=browser.new_page(device_scale_factor=1)
        for name in ('option-a','option-b','option-c','states','narrow'):
            page.set_content('<html><body style="margin:0">'+(ROOT/f'{name}.svg').read_text(encoding='utf-8')+'</body></html>')
            dimensions=page.locator('svg').evaluate('(s)=>({width:s.width.baseVal.value,height:s.height.baseVal.value})')
            page.set_viewport_size(dimensions)
            overflow=page.locator('svg').evaluate('''s => [...s.querySelectorAll('text')].flatMap(t => {
                const b=t.getBBox(), max=Number(t.dataset.maxWidth||0), w=s.width.baseVal.value, h=s.height.baseVal.value;
                return b.x<0 || b.y<0 || b.x+b.width>w || b.y+b.height>h || (max&&b.width>max+1)
                    ? [{text:t.textContent,box:{x:b.x,y:b.y,width:b.width,height:b.height},max}] : [];
            })''')
            page.screenshot(path=str(ROOT/f'{name}.png'),full_page=True)
            findings.append({'file':name+'.svg','text_overflow':overflow})
        for width in (390,1040):
            page.set_viewport_size({'width':width,'height':850})
            page.goto((ROOT/'index.html').as_uri())
            result=page.evaluate('({scrollWidth:document.documentElement.scrollWidth,width:innerWidth,images:[...document.images].every(i=>i.complete&&i.naturalWidth>0)})')
            findings.append({'gallery_width':width,**result})
        browser.close()
    (ROOT/'verification.json').write_text(json.dumps(findings,indent=2),encoding='utf-8')
    print(json.dumps(findings,indent=2))
    assert all(not f.get('text_overflow') for f in findings),'SVG text overflow'
    assert all(f.get('scrollWidth',0)<=f.get('width',0) and f.get('images',True) for f in findings),'Gallery layout'


if __name__=='__main__':
    main()
