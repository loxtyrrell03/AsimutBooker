'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, RefreshCw, X } from 'lucide-react';
import { requestJson } from '../lib/api';
import { HelpTip } from './help-tip';
import type { SystemJob } from './system-tools';
import './room-availability.css';

type Interval = { start: number; end: number; kind: 'booked' | 'closed'; label: string; event_id: string | null };
type Room = { name: string; closes: string | null; closed_all_day: boolean; intervals: Interval[] };
type Day = { date: string; observed_at: string; stale: boolean; rooms: Room[] };
type Grid = { days: Record<string, Day>; message: string; revision?: string | null };
const clock = (m: number) => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
const localDate = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
const shift = (day: string, n: number) => { const d = new Date(`${day}T12:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const monday = (day: string) => shift(day, -((new Date(`${day}T12:00:00Z`).getUTCDay() + 6) % 7));
const labelDate = (day: string, options: Intl.DateTimeFormatOptions) => new Date(`${day}T12:00:00Z`).toLocaleDateString('en-GB', { ...options, timeZone: 'Europe/London' });
function lanes(intervals: Interval[]) {
  const ends: number[] = [];
  const items = intervals.filter(item => item.kind === 'booked').map(item => {
    let lane = ends.findIndex(end => end <= item.start);
    if (lane < 0) lane = ends.length;
    ends[lane] = item.end;
    return { ...item, lane };
  });
  return { items, count: Math.max(1, ends.length) };
}

export function RoomAvailability({ active, csrf, enabled, job, onJob }: { active: boolean; csrf: string; enabled: boolean; job: SystemJob | null; onJob: (job: SystemJob | null) => void }) {
  const [selected, setSelected] = useState(localDate);
  const [week, setWeek] = useState(() => monday(localDate()));
  const [grid, setGrid] = useState<Grid>({ days: {}, message: '' });
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [detail, setDetail] = useState<{ room: string; date: string; item: Interval } | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const serial = useRef(false);
  const autoAttempts = useRef(new Set<string>());
  const scanActive = job?.active && job.action === 'scan';
  const day = grid.days[selected];
  const days = Array.from({ length: 7 }, (_, i) => shift(week, i));
  const today = localDate();
  const refreshKey = days.filter(d => d >= today && d <= shift(today, 7)).join(',');
  const refreshDates = refreshKey ? refreshKey.split(',') : [];
  const rooms = day?.rooms.filter(room => room.name.toLowerCase().includes(query.toLowerCase())) ?? [];
  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const { response, data } = await requestJson<Grid>('/api/v1/room-grid', { credentials: 'include', cache: 'no-store', signal });
      if (!response.ok) throw new Error('Room availability could not be loaded. Try Refresh again.');
      if (!signal?.aborted) { setGrid(data); setError(''); }
    } catch (e) { if (!signal?.aborted) setError(e instanceof Error ? e.message : 'Could not load rooms.'); }
    finally { if (!signal?.aborted) setLoading(false); }
  }, []);
  useEffect(() => {
    if (!active || !csrf) return;
    const controller = new AbortController();
    const start = window.setTimeout(() => void load(controller.signal), 0);
    const poll = window.setInterval(() => void load(controller.signal), scanActive ? 2000 : 30000);
    return () => { controller.abort(); window.clearTimeout(start); window.clearInterval(poll); };
  }, [active, csrf, scanActive, job?.updated_at, load]);
  useEffect(() => { if (detail) dialog.current?.showModal(); else dialog.current?.close(); }, [detail]);
  const attemptKey = `${grid.revision}:${week}`;
  const refresh = useCallback(async () => {
    if (serial.current || !enabled || job?.active || !refreshKey) return;
    autoAttempts.current.add(attemptKey);
    serial.current = true; setWorking(true); setError('');
    try {
      const { response, data } = await requestJson<{ job: SystemJob; message?: string }>('/api/v1/system/jobs', {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf },
        body: JSON.stringify({ request_id: crypto.randomUUID(), action: 'scan', args: { dates: refreshKey.split(',') } }),
      });
      if (!response.ok) throw new Error(data.message || 'The Booker is busy. Try Refresh when it finishes.');
      onJob(data.job);
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not start the refresh.'); }
    finally { serial.current = false; setWorking(false); }
  }, [enabled, job?.active, refreshKey, attemptKey, csrf, onJob]);
  useEffect(() => {
    if (!active || loading || error || !grid.revision || !enabled || job?.active ||
        !refreshKey.split(',').includes(selected) || autoAttempts.current.has(attemptKey)) return;
    if (!refreshKey.split(',').some(d => !grid.days[d] || grid.days[d].stale)) return;
    const start = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(start);
  }, [active, loading, error, grid, enabled, job?.active, selected, attemptKey, refreshKey, refresh]);
  const stop = async () => {
    if (!job || working) return;
    setWorking(true);
    try {
      const { response, data } = await requestJson<{ job: SystemJob }>('/api/v1/system/stop', {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf }, body: JSON.stringify({ request_id: job.request_id }),
      });
      if (!response.ok) throw new Error();
      onJob(data.job);
    } catch { setError('Could not request Stop. Reload status before trying again.'); }
    finally { setWorking(false); }
  };
  const chooseWeek = (offset: number) => { const next = shift(week, offset); setWeek(next); setSelected(next); };
  return <section className="room-availability" aria-labelledby="room-grid-title">
    <div className="room-grid-heading"><div><h2 id="room-grid-title">Room availability</h2><p>{day ? `${day.rooms.length} bookable rooms` : 'Your bookable rooms'} <HelpTip label="Room availability">Rooms follow your saved filters and ASIMUT access checks. Free space still needs a live booking check for your conflicts, quotas and booking window.</HelpTip></p></div>
      <button className="room-grid-refresh" disabled={!enabled || Boolean(job?.active) || working || !refreshDates.length} onClick={() => void refresh()}><RefreshCw size={17} className={scanActive ? 'spin-slow' : ''} />Refresh</button></div>
    <div className="room-grid-week"><button aria-label="Previous week" onClick={() => chooseWeek(-7)}><ChevronLeft /></button><strong>{labelDate(week, { day: 'numeric', month: 'short' })} – {labelDate(shift(week, 6), { day: 'numeric', month: 'short', year: 'numeric' })}</strong><button aria-label="Next week" onClick={() => chooseWeek(7)}><ChevronRight /></button><button className="room-grid-today" onClick={() => { setSelected(today); setWeek(monday(today)); }}>Today</button></div>
    <div className="room-grid-days" aria-label="Choose a day">{days.map(d => <button key={d} aria-pressed={selected === d} aria-label={labelDate(d, { weekday: 'long', day: 'numeric', month: 'long' })} onClick={() => setSelected(d)}><span>{labelDate(d, { weekday: 'short' })}</span><strong>{labelDate(d, { day: 'numeric' })}</strong><i className={grid.days[d] ? 'checked' : ''} /></button>)}</div>
    <div className="room-grid-tools"><label><span className="sr-only">Filter rooms</span><input placeholder="Filter rooms…" value={query} onChange={e => setQuery(e.target.value)} /></label><span>{day ? `${day.stale ? 'Last checked' : 'Checked'} ${new Date(day.observed_at).toLocaleString('en-GB', { timeZone: 'Europe/London', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}` : 'Not checked yet'}</span></div>
    <div aria-live="polite">{scanActive && <div className="room-grid-progress"><span>{job.text}</span><button disabled={working || job.state === 'stopping'} onClick={() => void stop()}>Stop</button></div>}
      {error && <p className="room-grid-error" role="alert">{error}</p>}
      {!scanActive && job?.action === 'scan' && ['failed', 'rejected', 'uncertain', 'stopped'].includes(job.state) && <p className="room-grid-error">{job.text} Use Refresh to try again.</p>}
      {day?.stale && <p className="room-grid-notice">Showing the last check. Availability may have changed.</p>}</div>
    {day && rooms.length > 0 ? <><section className="room-grid-scroll" aria-label={`Room timeline for ${selected}. Scroll horizontally for later times.`}>
      <div className="room-grid-axis"><div className="room-grid-corner">Room / closes</div><div className="room-grid-hours">{Array.from({ length: 17 }, (_, i) => <span key={i} style={{ left: `${i / 16 * 100}%` }}>{clock((i + 7) * 60)}</span>)}</div></div>
      {rooms.map(room => { const stack = lanes(room.intervals); return <div className="room-grid-row" key={room.name} style={{ minHeight: stack.count * 60 + 10 }}><div className="room-grid-name"><strong>{room.name}</strong><small>{room.closed_all_day ? 'Closed all day' : room.closes ? `Closes ${room.closes}` : 'Close not shown'}</small></div><div className="room-grid-track" style={{ height: stack.count * 60 + 10 }}>
        {room.intervals.filter(i => i.kind === 'closed').map((item, i) => <div key={`closed-${i}`} className="room-grid-closed" style={{ left: `${(item.start - 420) / 960 * 100}%`, width: `${(item.end - item.start) / 960 * 100}%` }} title={`Closed ${clock(item.start)}–${clock(item.end)}`}><span>Closed</span></div>)}
        {stack.items.map((item, i) => <button key={i} className="room-grid-booking" style={{ left: `${(item.start - 420) / 960 * 100}%`, width: `${(item.end - item.start) / 960 * 100}%`, top: item.lane * 60 + 5 }} aria-label={`${room.name}: ${item.label}, ${clock(item.start)} to ${clock(item.end)}`} onClick={() => setDetail({ room: room.name, date: selected, item })}><strong>{item.label}</strong><span>{clock(item.start)}–{clock(item.end)}</span></button>)}
      </div></div>; })}
    </section><div className="room-grid-legend"><span><i />Free space</span><span><i className="booked" />Booked</span><span><i className="closed" />Closed</span><span className="room-grid-swipe">Scroll time ↔</span></div></> : <div className="room-grid-empty"><strong>{loading || scanActive ? 'Loading room availability…' : day ? (query ? 'No matching rooms' : 'No eligible rooms') : 'No room grid for this day yet'}</strong><p>{day ? (query ? 'Try a different room name.' : 'Your saved room filters determine which rooms appear here.') : refreshDates.includes(selected) ? (scanActive ? 'This day will appear when its check finishes.' : job?.active || !enabled ? 'Waiting for the Booker to become available.' : error || (job?.action === 'scan' && !job.active ? 'Select Refresh to try again.' : grid.message) || 'Refresh this week to read its room availability.') : 'This date is outside the current live scan window. Previously checked dates remain visible.'}</p></div>}
    <dialog className="room-grid-dialog" ref={dialog} onCancel={() => setDetail(null)} onClose={() => setDetail(null)}>{detail && <><button className="room-grid-dismiss" aria-label="Close booking details" onClick={() => setDetail(null)}><X /></button><h3>{detail.item.label}</h3><p>{detail.room} · {labelDate(detail.date, { weekday: 'long', day: 'numeric', month: 'long' })}</p><strong>{clock(detail.item.start)}–{clock(detail.item.end)}</strong><p>Booking text as shown by ASIMUT.</p>{detail.item.event_id && <a href={`https://rwcmd.asimut.net/arrangement?eventId=${detail.item.event_id}`} target="_blank" rel="noreferrer">Open in ASIMUT ↗</a>}</>}</dialog>
  </section>;
}
