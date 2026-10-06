'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { requestJson } from '../lib/api';
import { HelpTip } from './help-tip';

type PauseState = { paused: boolean; revision: string; last_request_id?: string; message: string };

export function BookingPause({ csrf, enabled, onState }: { csrf: string; enabled: boolean; onState: (paused: boolean | null) => void }) {
  const [state, setState] = useState<PauseState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const holding = useRef(false);
  const working = useRef(false);
  const reading = useRef(false);
  const sequence = useRef(0);
  const read = useCallback(async (review = false) => {
    if (!enabled || working.current || reading.current || holding.current && !review) return;
    reading.current = true;
    const version = sequence.current;
    try {
      const { response, data } = await requestJson<PauseState>('/api/v1/booking-pause', { credentials: 'include', cache: 'no-store' });
      if (version !== sequence.current) return;
      if (!response.ok || typeof data.paused !== 'boolean' || !data.revision) throw new Error();
      setState(data); onState(data.paused); setError(''); holding.current = false;
    } catch { if (version === sequence.current) { setError('Booking pause could not be checked. Refresh its status.'); onState(null); } }
    finally { reading.current = false; }
  }, [enabled, onState]);
  useEffect(() => {
    const initial = window.setTimeout(() => { void read(); }, 0);
    const poll = window.setInterval(() => { if (!document.hidden) void read(); }, 3000);
    return () => { window.clearTimeout(initial); window.clearInterval(poll); };
  }, [read]);
  const save = async () => {
    if (!state || !enabled || !csrf || working.current || holding.current || error) return;
    sequence.current += 1;
    working.current = true; setBusy(true);
    try {
      const { response, data } = await requestJson<PauseState>('/api/v1/booking-pause', {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf },
        body: JSON.stringify({ revision: state.revision, paused: !state.paused, request_id: crypto.randomUUID() }),
      });
      if (!response.ok || typeof data.paused !== 'boolean' || !data.revision) throw new Error();
      setState(data); onState(data.paused); setError('');
    } catch {
      holding.current = true; onState(null);
      setError('The change was not confirmed. Refresh booking status before choosing again.');
    } finally { working.current = false; setBusy(false); }
  };
  return <section className="booking-pause" aria-label="Booking pause">
    <div className="booking-pause-row"><strong>{state?.paused ? 'Bookings paused' : 'Room bookings'} </strong>
      <HelpTip label="Booking pause">Pause blocks new reservations and booking edits until you unpause. Your reservations and Automatic On/Off setting are kept.</HelpTip>
      <button type="button" className="quiet-secondary" disabled={!enabled || !csrf || !state || busy || Boolean(error)} onClick={() => void save()}>{busy ? 'Saving…' : state?.paused ? 'Unpause bookings' : 'Pause bookings'}</button>
    </div>
    {state?.paused && !error && <output>Existing reservations are kept.</output>}
    {error && <div role="alert"><p>{error}</p><button type="button" className="quiet-link" disabled={!enabled || busy} onClick={() => void read(true)}>Refresh booking status</button></div>}
  </section>;
}
