"""Shared live SVG geometry for availability and room targeting.

Read the rendered grid and axis labels; never infer a room from a fixed row
height or scroll the detached room legend instead of the booking surface.
"""

SVG_GEOMETRY_JS = r"""
const svgGeometry = (root, configuredRooms) => {
    const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
    const roomName = text => {
        const matches = configuredRooms.filter(name => {
            const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            return new RegExp(`(^|[^A-Za-z0-9.])${escaped}(?=$|[^A-Za-z0-9.])`, 'i').test(clean(text));
        }).sort((a, b) => b.length - a.length);
        return matches.length && (matches.length === 1 || matches[0].length > matches[1].length) ? matches[0] : null;
    };
    const labels = [...root.querySelectorAll('a[data-location-id]')]
        .filter(el => el.getBoundingClientRect().height > 0)
        .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
    if (!labels.length || new Set(labels.map(el => el.getAttribute('data-location-id'))).size !== labels.length) return null;
    // Locate the grid by its complete row boundaries, not DOM order, fixed
    // dimensions, generated framework classes, or an assumed 30-unit row.
    const grids = [];
    const number = '(-?\\d+(?:\\.\\d+)?)';
    const segments = new RegExp('M\\s*' + number + '[ ,]+' + number + '\\s*([HV])\\s*' + number, 'gi');
    for (const svg of root.querySelectorAll('svg')) {
        if (!svg.getScreenCTM() || !svg.getBoundingClientRect().width) continue;
        for (const path of svg.querySelectorAll('path')) {
            if (path.closest('defs')) continue;
            const lines = [...(path.getAttribute('d') || '').matchAll(segments)];
            const horizontal = lines.filter(m => m[3].toUpperCase() === 'H');
            const vertical = lines.filter(m => m[3].toUpperCase() === 'V');
            const ys = [...new Set(horizontal.map(m => +m[2]))].sort((a,b) => a-b);
            const xs = [...new Set(vertical.map(m => +m[1]))].sort((a,b) => a-b);
            if (ys.length !== labels.length + 1 || xs.length < 3) continue;
            if (!horizontal.every(m => +m[4] > +m[1]) || !vertical.every(m => +m[4] > +m[2])) continue;
            // Transformed grid paths need a different coordinate basis. Fail
            // closed rather than treating a changed renderer as free space.
            const sm = svg.getScreenCTM(), pm = path.getScreenCTM();
            if (!['a','b','c','d','e','f'].every(k => Math.abs(sm[k]-pm[k]) < 0.01)) continue;
            grids.push({svg, ys, xs});
        }
    }
    if (grids.length !== 1) return null;
    const {svg, ys, xs} = grids[0];
    const axis = root.querySelector('#legend-x-inner');
    if (!axis) return null;
    const anchors = [...axis.children].map(el => {
        const match = clean(el.textContent).match(/^(\d{1,2})(?::(\d{2}))?$/);
        if (!match || !el.style.left.endsWith('%')) return null;
        const hour = +match[1] + +(match[2] || 0)/60;
        if (hour < 0 || hour > 24) return null;
        const estimate = xs[0] + parseFloat(el.style.left)/100 * (xs.at(-1)-xs[0]);
        const x = xs.reduce((best, v) => Math.abs(v-estimate) < Math.abs(best-estimate) ? v : best);
        if (Math.abs(x-estimate) > (xs[1]-xs[0])/20) return null;
        return {hour, x};
    }).filter(Boolean).sort((a,b) => a.hour-b.hour);
    if (anchors.length < 3 || anchors[1].hour === anchors[0].hour) return null;
    const perHour = (anchors[1].x-anchors[0].x)/(anchors[1].hour-anchors[0].hour);
    const atHour = hour => anchors[0].x + (hour-anchors[0].hour)*perHour;
    if (!(perHour > 0) || !anchors.every(a => Math.abs(atHour(a.hour)-a.x) < 0.01)) return null;
    const toScreen = (x,y) => new DOMPoint(x,y).matrixTransform(svg.getScreenCTM());
    const fromScreen = (x,y) => new DOMPoint(x,y).matrixTransform(svg.getScreenCTM().inverse());
    const rows = labels.map((label,i) => ({label, room:roomName(label.textContent),
        id:label.getAttribute('data-location-id'), top:ys[i], bottom:ys[i+1]}));
    if (rows.some(row => !row.room) || new Set(rows.map(row => row.room)).size !== rows.length) return null;
    const blockers = [...svg.querySelectorAll('rect.closed-hours, rect.event-overlay')].map(el => {
        const box = el.getBoundingClientRect();
        const first = fromScreen(box.left,box.top), last = fromScreen(box.right,box.bottom);
        return {el, left:first.x, right:last.x, top:first.y, bottom:last.y,
            closed:el.classList.contains('closed-hours')};
    });
    // Event location IDs independently confirm that legend order and grid
    // rows agree. Never click another room after a renderer reorder.
    for (const block of blockers) {
        const id = block.el.getAttribute('data-location-id');
        if (id && !rows.some(row => row.id === id && block.top >= row.top-0.1 && block.bottom <= row.bottom+0.1)) return null;
    }
    return {svg, rows, blockers, atHour, perHour, toScreen, fromScreen};
};
"""

SVG_SNAPSHOT_JS = """(configuredRooms) => {
""" + SVG_GEOMETRY_JS + """
    const roots = [...document.querySelectorAll('app-overview-svg')]
        .filter(root => [...root.querySelectorAll('svg')].some(svg => svg.getBoundingClientRect().width > 0));
    if (roots.length !== 1) return {renderer:'svg', rooms:[]};
    const grid = svgGeometry(roots[0], configuredRooms);
    if (!grid) return {renderer:'svg', rooms:[]};
    return {renderer:'svg', rooms:grid.rows.map(row => {
        const origin = grid.toScreen(grid.atHour(7), (row.top+row.bottom)/2);
        const next = grid.toScreen(grid.atHour(8), (row.top+row.bottom)/2);
        return {room:row.room, clickOriginX:origin.x, clickPixelsPerHour:next.x-origin.x, clickY:origin.y,
            blockedRanges:grid.blockers.filter(b => b.bottom > row.top+0.1 && b.top < row.bottom-0.1 && b.right > b.left)
                .map(b => ({startHour:7+(b.left-grid.atHour(7))/grid.perHour,
                    endHour:7+(b.right-grid.atHour(7))/grid.perHour, closed:b.closed}))};
    })};
} """

ROOM_COORDINATES_JS = """async ([roomName, startHour, endHour, configuredRooms]) => {
""" + SVG_GEOMETRY_JS + r"""
    const frame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const locate = () => {
        const roots = [...document.querySelectorAll('app-overview-svg')]
            .filter(root => [...root.querySelectorAll('svg')].some(svg => svg.getBoundingClientRect().width > 0));
        if (roots.length) {
            if (roots.length !== 1) return null;
            const grid = svgGeometry(roots[0], configuredRooms);
            const row = grid?.rows.find(row => row.room === roomName);
            if (!row) return null;
            const start = grid.atHour(startHour), end = grid.atHour(endHour);
            if (grid.blockers.some(b => b.bottom > row.top+0.1 && b.top < row.bottom-0.1 && b.right > start+0.1 && b.left < end-0.1)) return null;
            const point = grid.toScreen((start+end)/2,(row.top+row.bottom)/2);
            return {x:point.x,y:point.y,surface:grid.svg,renderer:'svg'};
        }
        const rows = [...document.querySelectorAll("[data-cy='overview-location-row'], .location-name-container .location-row")]
            .filter(el => el.getBoundingClientRect().height > 0 && el.textContent.trim().split(' (')[0].trim() === roomName);
        if (rows.length !== 1) return null;
        const row = rows[0].getBoundingClientRect();
        const y = (row.top+row.bottom)/2;
        const days = [...document.querySelectorAll('.location-day')].filter(el => {
            const box=el.getBoundingClientRect();
            return box.width > 0 && Math.abs((box.top+box.bottom)/2-y) < Math.min(3,row.height/4);
        });
        if (days.length !== 1) return null;
        const box=days[0].getBoundingClientRect();
        return {x:box.left+(((startHour+endHour)/2-7)/16)*box.width,y,surface:days[0],renderer:'legacy'};
    };
    // Scroll ancestors of the actual grid. Its legend is a separate clipped
    // panel, so scrolling the label does not expose the target slot.
    for (let pass=0; pass<3; pass++) {
        let target=locate();
        if (!target || !Number.isFinite(target.x) || !Number.isFinite(target.y)) return null;
        for (let el=target.surface.parentElement; el; el=el.parentElement) {
            const style=getComputedStyle(el), box=el.getBoundingClientRect();
            if (/(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight) {
                el.scrollTop += target.y - (box.top + el.clientTop + el.clientHeight/2);
            }
            if (/(auto|scroll)/.test(style.overflowX) && el.scrollWidth > el.clientWidth) {
                el.scrollLeft += target.x - (box.left + el.clientLeft + el.clientWidth/2);
            }
            target=locate();
            if (!target) return null;
        }
        if (target.x < 0 || target.x >= innerWidth || target.y < 0 || target.y >= innerHeight) {
            window.scrollBy(target.x-innerWidth/2,target.y-innerHeight/2);
        }
        await frame();
        target=locate();
        if (!target) return null;
        const hit=document.elementFromPoint(target.x,target.y);
        if (hit && (hit === target.surface || target.surface.contains(hit)) &&
            !hit.closest('.event-overlay, .closed-hours, .location-event, .location-closed')) {
            return {x:target.x,y:target.y,renderer:target.renderer};
        }
    }
    return null;
}
"""
