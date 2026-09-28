'use client';
import './fill-range.css';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { AgendaEvent } from '../app/page';
import type { SystemJob } from './system-tools';
import { HelpTip } from './help-tip';
import { requestJson } from '../lib/api';

const pendingKey = 'asimut-fill-pending';
const draftKey = 'asimut-fill-draft';
const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());

export function FillRange({ selectedDate, csrf, enabled, job, onJob, onRefresh, onDetails, onClose }: {
  selectedDate: string | null; csrf: string; enabled: boolean; job: SystemJob | null;
  onJob: (job: SystemJob | null) => void; onRefresh: () => void;
  onDetails: (event: AgendaEvent) => void; onClose: () => void;
}) {
  const [date, setDate] = useState(today);
  const [start, setStart] = useState('11:00');
  const [end, setEnd] = useState('13:00');
  const [pending, setPending] = useState('');
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const serial = useRef(false);
  const refreshed = useRef('');
  const own = job?.action === 'fill_range' ? job : null;
  const result = own?.result;
  const held = Boolean(pending) || own?.state === 'uncertain';
  const locked = working || Boolean(job?.active) || held;
  useEffect(() => {
    const timer = window.setTimeout(() => {
      try {
        setPending(localStorage.getItem(pendingKey) || '');
        const draft = JSON.parse(localStorage.getItem(draftKey) || 'null');
        if (draft && /^\d{2}:\d{2}$/.test(draft.start) && /^\d{2}:\d{2}$/.test(draft.end)) { setStart(draft.start); setEnd(draft.end); }
      } catch { /* Drafts are optional; durable job ownership is on the PC. */ }
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);
  useEffect(() => {
    if (!selectedDate) return;
    const timer = window.setTimeout(() => {
      if (own?.active && own.range) { setDate(own.range.date); setStart(own.range.start_time); setEnd(own.range.end_time); }
      else setDate(selectedDate);
      setError('');
    }, 0);
    return () => window.clearTimeout(timer);
  }, [selectedDate, own?.active, own?.range]);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (pending && job?.request_id === pending) {
        setPending(''); try { localStorage.removeItem(pendingKey); } catch { /* Server owns the request. */ }
      }
      const key = own ? `${own.request_id}:${own.state}` : '';
      if (own && !own.active && key !== refreshed.current) { refreshed.current = key; onRefresh(); }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [job, own, pending, onRefresh]);
  const check = useCallback(async () => {
    try {
      if (pending) {
        const { response, data } = await requestJson<{ job: SystemJob | null; not_started: boolean; message?: string }>('/api/v1/system/fill-range-delivery', {
          method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf }, body: JSON.stringify({ request_id: pending }),
        });
        if (!response.ok) throw new Error(data.message);
        onJob(data.job);
        if (data.not_started) { setPending(''); localStorage.removeItem(pendingKey); setError('This request was not delivered. You can try again.'); }
      } else {
        const { response, data } = await requestJson<{ job: SystemJob | null }>('/api/v1/system/job', { credentials: 'include', cache: 'no-store' });
        if (!response.ok) throw new Error();
        onJob(data.job); setError('');
      }
    } catch { setError('Could not check this request. Reconnect and check booking status.'); }
  }, [onJob, pending, csrf]);
  useEffect(() => {
    if (!csrf || !own?.active) return;
    const timer = window.setInterval(() => void check(), 2000);
    return () => window.clearInterval(timer);
  }, [csrf, own?.active, check]);

  async function submit(range?: { date: string; start_time: string; end_time: string }) {
    if (!enabled || locked || serial.current) return;
    const chosen = range || { date, start_time: start, end_time: end };
    if (!chosen.date || chosen.date < today() || !chosen.start_time || !chosen.end_time || chosen.end_time <= chosen.start_time || [chosen.start_time, chosen.end_time].some(t => Number(t.slice(3)) % 15)) {
      setError('Choose today or a future date, with Until after From in 15-minute steps.'); return;
    }
    if (range) { setDate(range.date); setStart(range.start_time); setEnd(range.end_time); }
    serial.current = true; setWorking(true); setError('');
    const requestId = crypto.randomUUID(); setPending(requestId);
    try {
      // A durable delivery ID is required before sending a booking request.
      localStorage.setItem(pendingKey, requestId);
      localStorage.setItem(draftKey, JSON.stringify({ start: chosen.start_time, end: chosen.end_time }));
    } catch { setPending(''); setError('Allow site storage before booking, so this request can be recovered.'); serial.current = false; setWorking(false); return; }
    try {
      const { response, data } = await requestJson<{ job?: SystemJob; message?: string }>('/api/v1/system/jobs', {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf },
        body: JSON.stringify({ request_id: requestId, action: 'fill_range', args: chosen }),
      });
      if (!response.ok) {
        if ([400, 401, 403, 409].includes(response.status)) { setPending(''); localStorage.removeItem(pendingKey); }
        throw new Error(data.message || 'Delivery is uncertain. Check booking status before trying again.');
      }
      if (data.job) onJob(data.job);
    } catch (e) { setError(e instanceof Error ? e.message : 'Delivery is uncertain. Check booking status.'); }
    finally { serial.current = false; setWorking(false); }
  }
  async function control(review: boolean) {
    if (serial.current || !own) return;
    serial.current = true; setWorking(true); setError('');
    try {
      const { response, data } = await requestJson<{ job?: SystemJob; message?: string }>(review ? '/api/v1/system/fill-range-review' : '/api/v1/system/stop', {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf }, body: JSON.stringify({ request_id: own.request_id }),
      });
      if (!response.ok) throw new Error(data.message || 'Could not check this request.');
      if (data.job) onJob(data.job);
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not check this request.'); }
    finally { serial.current = false; setWorking(false); }
  }
  return <section className="fill-range" aria-labelledby="fill-title">
    <div className="fill-heading"><h2 id="fill-title">Fill time range</h2><HelpTip label="Fill time range">Temporarily overrides your target, preferred times, booking-off dates, breaks, session length and cancelled-time protections. Your eligible rooms, existing events and ASIMUT limits still apply.</HelpTip></div>
    <p>Book the gaps around existing practice.</p>
    <form onSubmit={e => { e.preventDefault(); void submit(); }}>
      <fieldset disabled={locked} className="fill-fields">
        <label>Date<input type="date" value={date} min={today()} required onChange={e => setDate(e.target.value)} /></label>
        <label>From<input type="time" step={900} value={start} required onChange={e => setStart(e.target.value)} /></label>
        <label>Until<input type="time" step={900} value={end} required onChange={e => setEnd(e.target.value)} /></label>
      </fieldset>
      <p className="fill-note">Temporarily overrides practice settings. Keeps existing bookings. ASIMUT limits apply.</p>
      <div className="fill-actions"><button type="submit" className="quiet-primary" disabled={!enabled || locked}>{working && !own?.active ? 'Sending request…' : 'Fill this range'}</button><button type="button" className="quiet-secondary" onClick={onClose}>{own?.active ? 'Back to My Week' : 'Cancel'}</button></div>
    </form>
    {(own || pending || error || !enabled) && <div className="fill-result" aria-live="polite">
      {own && <><h3>{own.active ? 'Filling your practice…' : typeof result?.covered_minutes === 'number' ? `${result.covered_minutes} of ${result.requested_minutes} min covered` : 'Fill result'}</h3>
        {result?.range && <p>{result.range.date} · {result.range.start_time}–{result.range.end_time}</p>}<p>{own.text}</p>
        {own.active && <><progress aria-label="Fill progress" /><button type="button" className="quiet-secondary" disabled={working || ['stopping', 'checking'].includes(own.state)} onClick={() => void control(false)}>{own.state === 'stopping' ? 'Stopping…' : 'Stop'}</button></>}
        {result?.bookings?.map(b => <button className="fill-booking" type="button" key={b.event_id} onClick={() => onDetails({ event_id: b.event_id, room: b.room, date: b.date, start_time: b.start, end_time: b.end, is_reservation: true, title: 'Reservation' })}>{b.start}–{b.end} · {b.room} <span>View booking →</span></button>)}
        {Boolean(result?.remaining?.length) && <><h4>Still unfilled</h4>{result?.remaining?.map(g => <p key={g.start}>{g.start}–{g.end} · {g.minutes} min</p>)}{result?.reasons?.map(r => <p key={r}>{r}</p>)}{result?.advance_minutes === 0 && <p>ASIMUT reports no advance booking credit.</p>}</>}
      </>}
      {error && <p role="alert">{error}</p>}
      {!enabled && !own?.active && <p>Connect to the PC and finish any other active operation to book.</p>}
      {(held || error) && <button type="button" className="quiet-secondary" disabled={working || Boolean(own?.active)} onClick={() => void (own?.state === 'uncertain' ? control(true) : check())}>Check booking status</button>}
      {!locked && enabled && result?.range && Boolean(result.remaining?.length) && <button type="button" className="quiet-secondary" onClick={() => void submit(result.range)}>Try remaining gaps</button>}
    </div>}
  </section>;
}
