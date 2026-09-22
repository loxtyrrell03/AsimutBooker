'use client';
import './room-now.css';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { AgendaEvent } from '../app/page';
import type { SystemJob } from './system-tools';
import { HelpTip } from './help-tip';
import { requestJson } from '../lib/api';

export type RoomNowBooking = { room: string; date: string; start: string; end: string; event_id: number; duration_minutes: number };
const storageKey = 'asimut-room-now';
const pendingKey = 'asimut-room-now-pending';
const durations = [30, 45, 60, 75, 90, 105, 120];
const label = (n: number) => n === 60 ? '1 hour' : n === 120 ? '2 hours' : `${n} min`;

export function RoomNow({ csrf, enabled, job, onJob, onRefresh, onDetails }: {
  csrf: string; enabled: boolean; job: SystemJob | null; onJob: (job: SystemJob | null) => void;
  onRefresh: () => void; onDetails: (event: AgendaEvent) => void;
}) {
  const [mode, setMode] = useState<'preferred' | 'longest'>('preferred');
  const [minutes, setMinutes] = useState(60);
  const [custom, setCustom] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [pending, setPending] = useState('');
  const serial = useRef(false);
  const refreshed = useRef('');
  const own = job?.action === 'room_now' ? job : null;
  const booking = own?.result?.booking;
  const held = Boolean(pending) || own?.state === 'uncertain';
  const locked = working || Boolean(job?.active) || held;

  useEffect(() => {
    const timer = window.setTimeout(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
      if (saved && ['preferred', 'longest'].includes(saved.mode) && durations.includes(saved.minutes)) {
        setMode(saved.mode); setMinutes(saved.minutes); setCustom(![30, 60, 90, 120].includes(saved.minutes));
      }
      setPending(localStorage.getItem(pendingKey) || '');
    } catch { /* Optional local draft. */ }
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);
  function choose(nextMode: 'preferred' | 'longest', nextMinutes: number) {
    setMode(nextMode); setMinutes(nextMinutes);
    try { localStorage.setItem(storageKey, JSON.stringify({ mode: nextMode, minutes: nextMinutes })); } catch { /* Optional local draft. */ }
  }
  useEffect(() => {
    const timer = window.setTimeout(() => {
    if (pending && job?.request_id === pending) {
      setPending(''); try { localStorage.removeItem(pendingKey); } catch { /* Server result remains authoritative. */ }
    }
    const key = own ? `${own.request_id}:${own.state}` : '';
    if (own && !own.active && key !== refreshed.current) { refreshed.current = key; onRefresh(); }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [job, own, pending, onRefresh]);
  const check = useCallback(async () => {
    try {
      if (pending) {
        const { response, data } = await requestJson<{ job: SystemJob | null; not_started: boolean; message?: string }>('/api/v1/system/room-now-delivery', {
          method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf }, body: JSON.stringify({ request_id: pending }),
        });
        if (!response.ok) throw new Error(data.message);
        onJob(data.job);
        if (data.not_started) {
          setPending(''); try { localStorage.removeItem(pendingKey); } catch { /* Optional storage. */ }
          setError('The request was not delivered. No room was booked; you can try again.');
        }
        return;
      }
      const { response, data } = await requestJson<{ job: SystemJob | null }>('/api/v1/system/job', { credentials: 'include', cache: 'no-store' });
      if (!response.ok) throw new Error();
      onJob(data.job); setError('');
    } catch { setError('Could not check this request. Check the connection and try again.'); }
  }, [onJob, pending, csrf]);
  useEffect(() => {
    if (!csrf || !own?.active) return;
    const timer = window.setInterval(() => void check(), 2000);
    return () => window.clearInterval(timer);
  }, [csrf, own?.active, check]);
  async function submit() {
    if (!enabled || locked || serial.current) return;
    serial.current = true; setWorking(true); setError('');
    const requestId = crypto.randomUUID(); setPending(requestId);
    try { localStorage.setItem(pendingKey, requestId); } catch { /* Mounted state protects delivery. */ }
    try {
      const { response, data } = await requestJson<{ job?: SystemJob; message?: string }>('/api/v1/system/jobs', {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf },
        body: JSON.stringify({ request_id: requestId, action: 'room_now', args: { mode, minutes } }),
      });
      if (!response.ok) {
        if ([400, 401, 403, 409].includes(response.status)) { setPending(''); try { localStorage.removeItem(pendingKey); } catch { /* Optional storage. */ } }
        throw new Error(data.message || 'The request was not confirmed. Check booking status.');
      }
      if (data.job) onJob(data.job);
    } catch (e) { setError(e instanceof Error ? e.message : 'Delivery was not confirmed. Check booking status.'); }
    finally { serial.current = false; setWorking(false); }
  }
  async function control(review: boolean) {
    if (serial.current || !own) return;
    serial.current = true; setWorking(true); setError('');
    try {
      const { response, data } = await requestJson<{ job?: SystemJob; message?: string }>(review ? '/api/v1/system/room-now-review' : '/api/v1/system/stop', {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf }, body: JSON.stringify({ request_id: own.request_id }),
      });
      if (!response.ok) throw new Error(data.message || 'The request was not accepted. Check booking status.');
      if (data.job) onJob(data.job);
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not check the request.'); }
    finally { serial.current = false; setWorking(false); }
  }
  return <section className="room-now" aria-labelledby="room-now-title">
    <div className="room-now-intro"><h2 id="room-now-title">Find me a room now</h2><p>Start soonest. Make the most of your time.</p>
      <fieldset className="room-now-modes" aria-label="Duration mode">
        <button type="button" aria-pressed={mode === 'preferred'} disabled={locked} onClick={() => choose('preferred', 60)}>Preferred duration</button>
        <button type="button" aria-pressed={mode === 'longest'} disabled={locked} onClick={() => choose('longest', 120)}>Longest possible</button>
      </fieldset>
    </div>
    <div className="room-now-controls"><div className="room-now-label"><span>{mode === 'longest' ? 'Maximum duration' : 'Preferred duration'}</span><HelpTip label="Duration">{mode === 'longest' ? 'Starts as soon as possible today. Takes the longest available session, up to your maximum.' : 'Starts as soon as possible today. Books as close to this duration as it can, without going over.'}</HelpTip></div>
      <fieldset className="room-now-durations" aria-label={mode === 'longest' ? 'Maximum duration' : 'Preferred duration'}>{[30, 60, 90, 120].map(n => <button key={n} type="button" disabled={locked} aria-pressed={minutes === n} onClick={() => { choose(mode, n); setCustom(false); }}>{label(n)}</button>)}</fieldset>
      <button type="button" className="room-now-custom" aria-expanded={custom} disabled={locked} onClick={() => setCustom(!custom)}>Custom duration</button>
      {custom && <label className="room-now-custom-field">Duration<select aria-label="Custom duration" disabled={locked} value={minutes} onChange={e => choose(mode, Number(e.target.value))}>{durations.map(n => <option key={n} value={n}>{label(n)}</option>)}</select></label>}
      <button type="button" className="quiet-primary" disabled={!enabled || locked} onClick={() => void submit()}>{working && !own?.active ? 'Sending request…' : 'Find me a room now'}</button>
      <p className="room-now-note">Books a room as soon as possible today.</p>
    </div>
    {(own || pending || error || !enabled) && <div className="room-now-result" aria-live="polite">
      {own && <><strong>{own.active ? 'Finding a room…' : booking ? 'Booked' : 'Room search'}</strong><p>{own.text}</p>
        {own.active && <><progress aria-label="Room search progress" /><button type="button" className="quiet-secondary" disabled={working || own.state === 'stopping' || own.state === 'checking'} onClick={() => void control(false)}>{own.state === 'stopping' ? 'Stopping…' : 'Stop'}</button></>}
        {booking && <><p>{booking.date} · {booking.duration_minutes} min booked{own.result?.requested_minutes && booking.duration_minutes < own.result.requested_minutes ? ` · requested ${own.result.requested_minutes} min` : ''}</p><button type="button" className="quiet-secondary" onClick={() => onDetails({ event_id: booking.event_id, room: booking.room, date: booking.date, start_time: booking.start, end_time: booking.end, is_reservation: true, title: 'Reservation' })}>View booking</button></>}
      </>}
      {error && <p role="alert">{error}</p>}
      {!enabled && !own?.active && <p>Connect to the PC and finish any other active operation to book.</p>}
      {(held || error) && <button type="button" className="quiet-secondary" disabled={working || Boolean(own?.active)} onClick={() => void (own?.state === 'uncertain' ? control(true) : check())}>Check booking status</button>}
    </div>}
  </section>;
}
