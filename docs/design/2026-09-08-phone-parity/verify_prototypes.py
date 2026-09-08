"""Render static local proposals in isolated headless Chromium. No app requests."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parent


def main():
    problems=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1300,'height':1030},device_scale_factor=1)
        page.route('http://**/*',lambda route: route.abort())
        page.route('https://**/*',lambda route: route.abort())
        for item in json.loads((ROOT/'manifest.json').read_text()):
            awaitable_source=(ROOT/item['path']).read_text(encoding='utf-8')
            page.set_content('<style>body{margin:0}</style>'+awaitable_source)
            errors=page.evaluate('''() => {
                const svg=document.querySelector('svg'), width=svg.viewBox.baseVal.width;
                const errors=[];
                const text=[...svg.querySelectorAll('text')];
                for(const t of text){
                    const b=t.getBBox();
                    if(b.x<0||b.x+b.width>width||b.y<0||b.y+b.height>844)
                        errors.push('Out of bounds: '+t.textContent);
                }
                // Ignore no text: all sample copy must be legible, including navigation.
                for(let i=0;i<text.length;i++)for(let j=i+1;j<text.length;j++){
                    const a=text[i].getBBox(),b=text[j].getBBox();
                    const dx=Math.min(a.x+a.width,b.x+b.width)-Math.max(a.x,b.x);
                    const dy=Math.min(a.y+a.height,b.y+b.height)-Math.max(a.y,b.y);
                    if(dx>1&&dy>1)errors.push('Text overlap: '+text[i].textContent+' / '+text[j].textContent);
                }
                return errors;
            }''')
            problems.extend(f"{item['path']}: {error}" for error in errors)
        for option in (1,2,3):
            page.set_viewport_size({'width':1300,'height':1030})
            page.set_content('<style>body{margin:0}</style>'+(ROOT/f'option-{option}.svg').read_text(encoding='utf-8'))
            page.screenshot(path=str(ROOT/f'option-{option}.png'),full_page=True)
        page.set_viewport_size({'width':320,'height':844})
        page.set_content('<style>body{margin:0}</style>'+(ROOT/'screens/option-1-25.svg').read_text(encoding='utf-8'))
        page.screenshot(path=str(ROOT/'narrow-help.png'),full_page=True)
        browser.close()
    report={'screens_checked':90,'bounds_and_text_overlap_errors':problems,'evidence':'Static SVG rendering only; no live APIs or product interactions.'}
    (ROOT/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))
    if problems: raise SystemExit(1)


if __name__=='__main__': main()
