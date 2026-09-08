'use client';

import { useEffect, useRef, useState } from 'react';
import { Settings2, Pencil } from 'lucide-react';
import { requestJson } from '../lib/api';

type Section = 'goal' | 'days' | 'times' | 'rooms' | 'all';
type Preferences = {
  revision: string;
  practice_plan: { enabled: boolean; default_hours: number; date_overrides: Record<string, number> };
  time_preferences: { enabled: boolean; start_time: string; end_time: string; strict_mode: boolean };
  disabled_dates: string[];
  room_preferences: { ordered_rooms: string[]; excluded_rooms: string[] };
};

const labels: Record<Section, string> = { goal: 'Daily goal', days: 'Practice days', times: 'Preferred times', rooms: 'Favourite rooms', all: 'Edit practice settings' };

export function PracticeSettings({ csrf, enabled, onSaved, targetLabel, timeLabel }: { csrf: string; enabled: boolean; onSaved: () => void; targetLabel: string; timeLabel: string }) {
  const heading = useRef<HTMLHeadingElement>(null);
  const [hours, setHours] = useState('');
  const [section, setSection] = useState<Section | null>(null);
  const [values, setValues] = useState<Preferences | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [date, setDate] = useState('');
  const [dateEdits, setDateEdits] = useState<Record<string, { enabled: boolean; hours: string }>>({});
  const [reloadRequired, setReloadRequired] = useState(false);
  const loadRequest = useRef<AbortController | null>(null);
  const dateEnabled = dateEdits[date]?.enabled ?? !values?.disabled_dates.includes(date);
  const dateHours = dateEdits[date]?.hours ?? values?.practice_plan.date_overrides[date]?.toString() ?? '';

  const hasValues = values !== null;
  useEffect(() => {
    if (section && hasValues) { heading.current?.focus({ preventScroll: true }); heading.current?.scrollIntoView({ block: 'start' }); }
  }, [section, hasValues]);
  useEffect(() => () => loadRequest.current?.abort(), []);

  async function open(next: Section) {
    loadRequest.current?.abort();
    const request = new AbortController();
    loadRequest.current = request;
    setSection(next); setValues(null); setError(''); setNotice(''); setWorking(true); setDate(''); setDateEdits({}); setReloadRequired(false);
    try {
      const { response, data: loaded } = await requestJson<Preferences>('/api/v1/preferences', { credentials: 'include', cache: 'no-store', signal: request.signal });
      if (!response.ok) throw new Error('Settings could not be loaded. Try again.');
      if (request.signal.aborted) return;
      setValues(loaded); setHours(String(loaded.practice_plan.default_hours));
    } catch { if (!request.signal.aborted) setError('Settings could not be loaded. Try again.'); }
    finally { if (!request.signal.aborted) setWorking(false); }
  }

  async function save(event: { preventDefault: () => void }) {
    event.preventDefault();
    if (!values || !section || working || !enabled || reloadRequired) return;
    if ((section === 'times' || section === 'all') && values.time_preferences.start_time >= values.time_preferences.end_time) {
      setError('End time must be later than start time.'); return;
    }
    const days = Object.entries(dateEdits);
    const overrides = days.map(([date, edit]) => ({ date, hours: edit.hours === '' ? null : Number(edit.hours) }));
    const bookingDays = days.map(([date, edit]) => ({ date, enabled: edit.enabled }));
    let changes: Record<string, unknown>;
    if (section === 'all') {
      changes = { practice_plan: { enabled: values.practice_plan.enabled, default_hours: Number(hours), ...(days.length ? { date_overrides: overrides } : {}) }, time_preferences: values.time_preferences, room_preferences: { ordered_rooms: values.room_preferences.ordered_rooms, excluded_rooms: values.room_preferences.excluded_rooms } };
      if (days.length) changes.booking_days = bookingDays;
    } else if (section === 'goal') changes = { practice_plan: { enabled: values.practice_plan.enabled, default_hours: Number(hours) } };
    else if (section === 'times') changes = { time_preferences: values.time_preferences };
    else if (section === 'rooms') changes = { room_preferences: { ordered_rooms: values.room_preferences.ordered_rooms, excluded_rooms: values.room_preferences.excluded_rooms } };
    else {
      if (!days.length) { setNotice('No date changes to save.'); return; }
      changes = { booking_days: bookingDays, practice_plan: { date_overrides: overrides } };
    }
    setWorking(true); setError(''); setNotice('');
    try {
      const { response, data: result } = await requestJson<Preferences & { message?: string; detail?: string; error?: string }>('/api/v1/preferences', {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf },
        body: JSON.stringify({ revision: values.revision, changes }),
      });
      if (!response.ok) {
        setReloadRequired(response.status >= 500 || result.error === 'preferences_changed' || response.status === 409 && !result.error);
        setError(result.message || result.detail || 'Settings could not be saved. Reload settings and try again.'); return;
      }
      setValues(result as Preferences); setSection(null); setNotice('Preferences saved. Future booking runs will use your changes.'); onSaved();
    } catch {
      setReloadRequired(true);
      setError('The save result could not be checked. Reload settings before trying again.');
    } finally { setWorking(false); }
  }

  function editDate(patch: Partial<{ enabled: boolean; hours: string }>) {
    setDateEdits(current => ({ ...current, [date]: { enabled: dateEnabled, hours: dateHours, ...patch } }));
  }

  function moveRoom(index: number, direction: number) {
    if (!values) return;
    const rooms = [...values.room_preferences.ordered_rooms];
    [rooms[index], rooms[index + direction]] = [rooms[index + direction], rooms[index]];
    setValues({ ...values, room_preferences: { ...values.room_preferences, ordered_rooms: rooms } });
  }

  return <div className="practice-settings">
    <div className="section-heading"><h3>Your practice</h3><button type="button" className="quiet-icon" aria-label="Edit all practice settings" disabled={!enabled || section !== null} onClick={() => void open('all')}><Settings2 /></button></div>
    {!section && <div className="metric-grid editable-metrics">
      <button type="button" aria-label="Edit daily target" disabled={!enabled} onClick={() => void open('goal')}><span>Daily target <Pencil /></span><strong>{targetLabel}</strong></button>
      <button type="button" aria-label="Edit preferred time" disabled={!enabled} onClick={() => void open('times')}><span>Preferred time <Pencil /></span><strong>{timeLabel}</strong></button>
    </div>}
    {!section && <div className="preference-actions">{(['goal', 'days', 'times', 'rooms'] as Section[]).map(key =>
      <button type="button" className="quiet-secondary" key={key} onClick={() => void open(key)} disabled={!enabled}>{labels[key]}</button>)}</div>}
    {!enabled && <p className="quiet-muted">Connect to Booker and wait for the assistant to finish to edit preferences.</p>}
    {notice && <output className="quiet-notice">{notice}</output>}
    {section && <form className="preference-editor" onSubmit={event => void save(event)} aria-label={labels[section]}>
      <h4 ref={heading} tabIndex={-1}>{labels[section]}</h4>
      {working && <output>{values ? 'Saving…' : 'Loading settings…'}</output>}
      {error && <p role="alert">{error}</p>}
      {values && <fieldset disabled={working || !enabled}>
        {(section === 'goal' || section === 'all') && <>
          <label><input type="checkbox" checked={values.practice_plan.enabled} onChange={event => setValues({ ...values, practice_plan: { ...values.practice_plan, enabled: event.target.checked } })} /> Use a daily practice goal</label>
          <label>Hours per day<input type="number" required min="0.5" max="12" step="0.5" value={hours} onChange={event => setHours(event.target.value)} /></label>
        </>}
        {(section === 'days' || section === 'all') && <>
          <label>Practice date<input type="date" required={section === 'days' && !Object.keys(dateEdits).length} value={date} onChange={event => setDate(event.target.value)} /></label>
          {date && <><label><input type="checkbox" checked={dateEnabled} onChange={event => editDate({ enabled: event.target.checked })} /> Allow automatic bookings on this date</label>
            <label>Hours for this date (blank uses daily goal)<input type="number" min="0.5" max="12" step="0.5" value={dateHours} onChange={event => editDate({ hours: event.target.value })} /></label></>}
          {Object.keys(dateEdits).length > 0 && <p>Dates to save: {Object.keys(dateEdits).sort().join(', ')}</p>}
          {values.disabled_dates.length > 0 && <p>Dates off: {values.disabled_dates.join(', ')}</p>}
          <p>Turning a date off leaves existing reservations in place.</p>
          {!values.practice_plan.enabled && <p>Daily goals are off. Enable them under Daily goal to use date-specific hours.</p>}
        </>}
        {(section === 'times' || section === 'all') && <>
          <label><input type="checkbox" checked={values.time_preferences.enabled} onChange={event => setValues({ ...values, time_preferences: { ...values.time_preferences, enabled: event.target.checked } })} /> Use preferred times</label>
          <label>Start time<input type="time" required step="900" value={values.time_preferences.start_time} onChange={event => setValues({ ...values, time_preferences: { ...values.time_preferences, start_time: event.target.value } })} /></label>
          <label>End time<input type="time" required step="900" value={values.time_preferences.end_time} onChange={event => setValues({ ...values, time_preferences: { ...values.time_preferences, end_time: event.target.value } })} /></label>
          <label><input type="checkbox" checked={values.time_preferences.strict_mode} onChange={event => setValues({ ...values, time_preferences: { ...values.time_preferences, strict_mode: event.target.checked } })} /> Only book within these times</label>
        </>}
        {(section === 'rooms' || section === 'all') && <><p>Higher rooms are preferred. Untick a room to exclude it.</p>
          <ol className="room-preference-list">{values.room_preferences.ordered_rooms.map((room, index) => <li key={room}>
            <label><input type="checkbox" checked={!values.room_preferences.excluded_rooms.includes(room)} onChange={event => setValues({ ...values, room_preferences: { ...values.room_preferences, excluded_rooms: event.target.checked ? values.room_preferences.excluded_rooms.filter(name => name !== room) : [...values.room_preferences.excluded_rooms, room] } })} />{room}</label>
            <button type="button" aria-label={`Move ${room} up`} disabled={index === 0} onClick={() => moveRoom(index, -1)}>↑</button>
            <button type="button" aria-label={`Move ${room} down`} disabled={index === values.room_preferences.ordered_rooms.length - 1} onClick={() => moveRoom(index, 1)}>↓</button>
          </li>)}</ol></>}
      </fieldset>}
      <div className="preference-actions">
        {values && <button className="quiet-primary" type="submit" disabled={working || !enabled || reloadRequired}>Save changes</button>}
        <button className="quiet-secondary" type="button" disabled={working && values !== null} onClick={() => { loadRequest.current?.abort(); setWorking(false); setSection(null); setError(''); }}>Cancel</button>
        {error && <button className="quiet-secondary" type="button" disabled={working} onClick={() => void open(section)}>Reload settings</button>}
      </div>
    </form>}
  </div>;
}
