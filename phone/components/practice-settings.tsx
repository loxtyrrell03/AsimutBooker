'use client';

import { useState } from 'react';

type Section = 'goal' | 'days' | 'times' | 'rooms';
type Preferences = {
  revision: string;
  practice_plan: { enabled: boolean; default_hours: number; date_overrides: Record<string, number> };
  time_preferences: { enabled: boolean; start_time: string; end_time: string; strict_mode: boolean };
  disabled_dates: string[];
  room_preferences: { ordered_rooms: string[]; excluded_rooms: string[] };
};

const labels: Record<Section, string> = { goal: 'Daily goal', days: 'Practice days', times: 'Preferred times', rooms: 'Favourite rooms' };

export function PracticeSettings({ csrf, enabled, onSaved }: { csrf: string; enabled: boolean; onSaved: () => void }) {
  const [section, setSection] = useState<Section | null>(null);
  const [values, setValues] = useState<Preferences | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [date, setDate] = useState('');
  const [dateEnabled, setDateEnabled] = useState(true);
  const [dateHours, setDateHours] = useState('');

  async function open(next: Section) {
    setSection(next); setValues(null); setError(''); setNotice(''); setWorking(true); setDate('');
    try {
      const response = await fetch('/api/v1/preferences', { credentials: 'include', cache: 'no-store' });
      if (!response.ok) throw new Error('Settings could not be loaded. Try again.');
      setValues(await response.json() as Preferences);
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Settings could not be loaded.'); }
    finally { setWorking(false); }
  }

  async function save(event: { preventDefault: () => void }) {
    event.preventDefault();
    if (!values || !section || working) return;
    let changes: Record<string, unknown>;
    if (section === 'goal') changes = { practice_plan: { enabled: values.practice_plan.enabled, default_hours: values.practice_plan.default_hours } };
    else if (section === 'times') changes = { time_preferences: values.time_preferences };
    else if (section === 'rooms') changes = { room_preferences: { ordered_rooms: values.room_preferences.ordered_rooms, excluded_rooms: values.room_preferences.excluded_rooms } };
    else changes = { booking_days: [{ date, enabled: dateEnabled }], practice_plan: { date_overrides: [{ date, hours: dateHours === '' ? null : Number(dateHours) }] } };
    setWorking(true); setError(''); setNotice('');
    try {
      const response = await fetch('/api/v1/preferences', {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf },
        body: JSON.stringify({ revision: values.revision, changes }),
      });
      const result = await response.json() as Preferences & { message?: string; detail?: string };
      if (!response.ok) throw new Error(result.message || result.detail || 'Settings could not be saved. Reload settings and try again.');
      setValues(result as Preferences); setSection(null); setNotice('Preferences saved. Future booking runs will use your changes.'); onSaved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The save result could not be checked. Reload settings before trying again.');
    } finally { setWorking(false); }
  }

  function pickDate(day: string) {
    setDate(day); setDateEnabled(!values?.disabled_dates.includes(day));
    setDateHours(values?.practice_plan.date_overrides[day]?.toString() ?? '');
  }

  function moveRoom(index: number, direction: number) {
    if (!values) return;
    const rooms = [...values.room_preferences.ordered_rooms];
    [rooms[index], rooms[index + direction]] = [rooms[index + direction], rooms[index]];
    setValues({ ...values, room_preferences: { ...values.room_preferences, ordered_rooms: rooms } });
  }

  return <div className="practice-settings">
    {!section && <div className="preference-actions">{(Object.keys(labels) as Section[]).map(key =>
      <button type="button" className="quiet-secondary" key={key} onClick={() => void open(key)} disabled={!enabled}>{labels[key]}</button>)}</div>}
    {!enabled && <p className="quiet-muted">Connect to Booker and wait for the assistant to finish to edit preferences.</p>}
    {notice && <output className="quiet-notice">{notice}</output>}
    {section && <form className="preference-editor" onSubmit={event => void save(event)} aria-label={labels[section]}>
      <h4>{labels[section]}</h4>
      {working && <output>{values ? 'Saving…' : 'Loading settings…'}</output>}
      {error && <p role="alert">{error}</p>}
      {values && <fieldset disabled={working || !enabled}>
        {section === 'goal' && <>
          <label><input type="checkbox" checked={values.practice_plan.enabled} onChange={event => setValues({ ...values, practice_plan: { ...values.practice_plan, enabled: event.target.checked } })} /> Use a daily practice goal</label>
          <label>Hours per day<input type="number" required min="0.5" max="12" step="0.5" value={values.practice_plan.default_hours} onChange={event => setValues({ ...values, practice_plan: { ...values.practice_plan, default_hours: Number(event.target.value) } })} /></label>
        </>}
        {section === 'days' && <>
          <label>Practice date<input type="date" required value={date} onChange={event => pickDate(event.target.value)} /></label>
          {date && <><label><input type="checkbox" checked={dateEnabled} onChange={event => setDateEnabled(event.target.checked)} /> Allow automatic bookings on this date</label>
            <label>Hours for this date (blank uses daily goal)<input type="number" min="0.5" max="12" step="0.5" value={dateHours} onChange={event => setDateHours(event.target.value)} /></label></>}
          {values.disabled_dates.length > 0 && <p>Dates off: {values.disabled_dates.join(', ')}</p>}
          <p>Turning a date off leaves existing reservations in place.</p>
          {!values.practice_plan.enabled && <p>Daily goals are off. Enable them under Daily goal to use date-specific hours.</p>}
        </>}
        {section === 'times' && <>
          <label><input type="checkbox" checked={values.time_preferences.enabled} onChange={event => setValues({ ...values, time_preferences: { ...values.time_preferences, enabled: event.target.checked } })} /> Use preferred times</label>
          <label>Start time<input type="time" required step="900" value={values.time_preferences.start_time} onChange={event => setValues({ ...values, time_preferences: { ...values.time_preferences, start_time: event.target.value } })} /></label>
          <label>End time<input type="time" required step="900" value={values.time_preferences.end_time} onChange={event => setValues({ ...values, time_preferences: { ...values.time_preferences, end_time: event.target.value } })} /></label>
          <label><input type="checkbox" checked={values.time_preferences.strict_mode} onChange={event => setValues({ ...values, time_preferences: { ...values.time_preferences, strict_mode: event.target.checked } })} /> Only book within these times</label>
        </>}
        {section === 'rooms' && <><p>Higher rooms are preferred. Untick a room to exclude it.</p>
          <ol className="room-preference-list">{values.room_preferences.ordered_rooms.map((room, index) => <li key={room}>
            <label><input type="checkbox" checked={!values.room_preferences.excluded_rooms.includes(room)} onChange={event => setValues({ ...values, room_preferences: { ...values.room_preferences, excluded_rooms: event.target.checked ? values.room_preferences.excluded_rooms.filter(name => name !== room) : [...values.room_preferences.excluded_rooms, room] } })} />{room}</label>
            <button type="button" aria-label={`Move ${room} up`} disabled={index === 0} onClick={() => moveRoom(index, -1)}>↑</button>
            <button type="button" aria-label={`Move ${room} down`} disabled={index === values.room_preferences.ordered_rooms.length - 1} onClick={() => moveRoom(index, 1)}>↓</button>
          </li>)}</ol></>}
      </fieldset>}
      <div className="preference-actions">
        {values && <button className="quiet-primary" type="submit" disabled={working || !enabled}>Save changes</button>}
        <button className="quiet-secondary" type="button" disabled={working} onClick={() => { setSection(null); setError(''); }}>Cancel</button>
        {error && <button className="quiet-secondary" type="button" disabled={working} onClick={() => void open(section)}>Reload settings</button>}
      </div>
    </form>}
  </div>;
}
