'use client';

import { useEffect, useRef, useState } from 'react';
import { Settings2 } from 'lucide-react';
import { requestJson } from '../lib/api';

import type { Preferences, DailyPlanning } from '../lib/preferences';
import { HelpTip } from './help-tip';

type Section = 'goal' | 'days' | 'times' | 'rooms' | 'strategy' | 'requirements' | 'all';

const labels: Record<Section, string> = { goal: 'Daily goal', days: 'Practice days', times: 'Preferred times', rooms: 'Favourite rooms', all: 'Edit practice settings', strategy: 'Booking strategy', requirements: 'Room requirements' };

export function PracticeSettings({ csrf, enabled, onSaved, targetLabel, timeLabel, onEditing }: { onEditing?: (editing: boolean) => void; csrf: string; enabled: boolean; onSaved: () => void; targetLabel: string; timeLabel: string }) {
  const heading = useRef<HTMLHeadingElement>(null);
  const [hours, setHours] = useState('');
  const [section, setSectionState] = useState<Section | null>(null);
  function setSection(next: Section | null) { setSectionState(next); onEditing?.(next !== null); }
  const [values, setValues] = useState<Preferences | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [date, setDate] = useState('');
  const [dateEdits, setDateEdits] = useState<Record<string, { enabled: boolean; hours: string }>>({});
  const [reloadRequired, setReloadRequired] = useState(false);
  const [roomQuery, setRoomQuery] = useState('');
  const [terms, setTerms] = useState<Record<string, string>>({});
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
      setTerms(Object.fromEntries(['acceptable_instrument_tags', 'acceptable_room_type_tags', 'required_feature_terms'].map(key => [key, (loaded.room_preferences[key as keyof Preferences['room_preferences']] as string[] ?? []).join(', ')])));
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
    else if (section === 'strategy') changes = { booking_strategy: values.booking_strategy };
    else if (section === 'requirements') changes = { room_preferences: { acceptable_instrument_tags: values.room_preferences.acceptable_instrument_tags, acceptable_room_type_tags: values.room_preferences.acceptable_room_type_tags, required_feature_terms: values.room_preferences.required_feature_terms, minimum_block_minutes: values.room_preferences.minimum_block_minutes, allow_fragmented_sessions: values.room_preferences.allow_fragmented_sessions } };
    else if (section === 'rooms') changes = { room_preferences: { ordered_rooms: values.room_preferences.ordered_rooms, excluded_rooms: values.room_preferences.excluded_rooms } };
    else {
      if (!days.length) { setNotice('No date changes to save.'); return; }
      changes = { booking_days: bookingDays, practice_plan: { date_overrides: overrides } };
    }
    if (section === 'requirements') changes.room_preferences = { ...(changes.room_preferences as object), ...Object.fromEntries(Object.entries(terms).map(([key, value]) => [key, value.split(',').map(term => term.trim()).filter(Boolean)])) };
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
    {!section && <div className="preference-actions">{(['goal', 'days', 'times', 'rooms', 'requirements', 'strategy'] as Section[]).map(key =>
      <button type="button" className="quiet-secondary" key={key} onClick={() => void open(key)} disabled={!enabled} aria-label={labels[key]}><span>{labels[key]}</span>{key === 'goal' ? <small>{targetLabel}</small> : key === 'times' ? <small>{timeLabel}</small> : <span aria-hidden="true">›</span>}</button>)}</div>}
    {!enabled && <p className="quiet-muted">Connect to Booker and wait for the current operation to finish to edit preferences.</p>}
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
          <label>Time preset<select aria-label="Time preset" value={values.time_preferences.preset ?? 'custom'} onChange={event => { const times: Record<string, [string, string]> = { morning: ['07:00', '12:00'], peak_afternoon: ['12:00', '16:00'], afternoon: ['12:00', '18:00'], evening: ['18:00', '22:00'], afternoon_evening: ['14:00', '22:00'] }; const pair = times[event.target.value]; setValues({ ...values, time_preferences: { ...values.time_preferences, preset: event.target.value, ...(pair ? { start_time: pair[0], end_time: pair[1] } : {}) } }); }}><option value="morning">Morning</option><option value="peak_afternoon">Peak afternoon</option><option value="afternoon">Afternoon</option><option value="evening">Evening</option><option value="afternoon_evening">Afternoon and evening</option><option value="custom">Custom</option></select></label>
          <label><input type="checkbox" checked={values.time_preferences.enabled} onChange={event => setValues({ ...values, time_preferences: { ...values.time_preferences, enabled: event.target.checked } })} /> Use preferred times</label>
          <label>Start time<input type="time" required step="900" value={values.time_preferences.start_time} onChange={event => setValues({ ...values, time_preferences: { ...values.time_preferences, start_time: event.target.value, preset: 'custom' } })} /></label>
          <label>End time<input type="time" required step="900" value={values.time_preferences.end_time} onChange={event => setValues({ ...values, time_preferences: { ...values.time_preferences, end_time: event.target.value, preset: 'custom' } })} /></label>
          <label><input type="checkbox" checked={values.time_preferences.strict_mode} onChange={event => setValues({ ...values, time_preferences: { ...values.time_preferences, strict_mode: event.target.checked } })} /> Only book within these times</label>
        </>}
        {(section === 'rooms' || section === 'all') && <><label>Find a room<input type="search" value={roomQuery} onChange={event => setRoomQuery(event.target.value)} /></label><p>Higher rooms are preferred. Untick a room to exclude it.</p>
          <ol className="room-preference-list">{values.room_preferences.ordered_rooms.map((room, index) => <li key={room} hidden={!room.toLowerCase().includes(roomQuery.toLowerCase())}>
            <label><input type="checkbox" checked={!values.room_preferences.excluded_rooms.includes(room)} onChange={event => setValues({ ...values, room_preferences: { ...values.room_preferences, excluded_rooms: event.target.checked ? values.room_preferences.excluded_rooms.filter(name => name !== room) : [...values.room_preferences.excluded_rooms, room] } })} />{room}</label>
            <button type="button" aria-label={`Move ${room} up`} disabled={index === 0} onClick={() => moveRoom(index, -1)}>↑</button>
            <button type="button" aria-label={`Move ${room} down`} disabled={index === values.room_preferences.ordered_rooms.length - 1} onClick={() => moveRoom(index, 1)}>↓</button>
          </li>)}</ol></>}
        {section === 'requirements' && <>
          {([['acceptable_instrument_tags', 'Acceptable instruments', 'Any matching instrument is enough. Leave blank for any instrument.'], ['acceptable_room_type_tags', 'Acceptable room types', 'Any matching room type is enough. Leave blank for any type.'], ['required_feature_terms', 'Required features', 'Every comma-separated feature must be present.']] as const).map(([key, title, help]) => <div key={key}><label>{title}<input value={terms[key] ?? ''} onChange={event => setTerms({ ...terms, [key]: event.target.value })} /></label><HelpTip label={title}>{help}</HelpTip></div>)}
          <label>Minimum useful block (minutes)<select aria-label="Minimum useful block (minutes)" value={values.room_preferences.minimum_block_minutes} onChange={event => setValues({ ...values, room_preferences: { ...values.room_preferences, minimum_block_minutes: Number(event.target.value) } })}>{[30,45,60,75,90,105,120].map(minutes => <option key={minutes} value={minutes}>{minutes} minutes</option>)}</select></label>
          <label><input type="checkbox" checked={values.room_preferences.allow_fragmented_sessions} onChange={event => setValues({ ...values, room_preferences: { ...values.room_preferences, allow_fragmented_sessions: event.target.checked } })} />Allow split sessions</label><HelpTip label="Split sessions">Allow the daily target to be filled by more than one session.</HelpTip>
        </>}
        {section === 'strategy' && values.booking_strategy && <>
          <label><input type="checkbox" checked={values.booking_strategy.reverse_date_order} onChange={event => setValues({ ...values, booking_strategy: { ...values.booking_strategy, reverse_date_order: event.target.checked } })} />Book furthest dates first</label>
          <StrategyFields value={values.booking_strategy.daily_planning} onChange={daily_planning => setValues({ ...values, booking_strategy: { ...values.booking_strategy, daily_planning } })} />
        </>}
      </fieldset>}
      <div className="preference-actions">
        {values && <button className="quiet-primary" type="submit" disabled={working || !enabled || reloadRequired}>Save changes</button>}
        <button className="quiet-secondary" type="button" disabled={working && values !== null} onClick={() => { loadRequest.current?.abort(); setWorking(false); setSection(null); setError(''); }}>Cancel</button>
        {error && <button className="quiet-secondary" type="button" disabled={working} onClick={() => void open(section)}>Reload settings</button>}
      </div>
    </form>}
  </div>;
}

function StrategyFields({ value, onChange }: { value: DailyPlanning; onChange: (value: DailyPlanning) => void }) {
  return <>
    <label><input type="checkbox" checked={value.enabled} onChange={event => onChange({ ...value, enabled: event.target.checked })} />Plan before booking</label>
    <label>Preferred peak start<input type="time" step="900" value={value.preferred_peak_start} onChange={event => onChange({ ...value, preferred_peak_start: event.target.value })} /></label>
    <label>Preferred peak end<input type="time" step="900" value={value.preferred_peak_end} onChange={event => onChange({ ...value, preferred_peak_end: event.target.value })} /></label>
    <label><input type="checkbox" checked={value.hold_early_peak_edges} onChange={event => onChange({ ...value, hold_early_peak_edges: event.target.checked })} />Wait for better later rooms</label>
    {([['desired_peak_block_minutes', 'Desired peak session (minutes)', 30, 120, 15, 'Preferred continuous length within peak hours.'], ['foresight_minutes', 'Look ahead (minutes)', 0, 1440, 15, 'How far ahead to consider rooms becoming bookable.'], ['minimum_later_options', 'Better later rooms required', 1, 5, 1, 'Distinct better rooms required before waiting. This is a count, not a probability.'], ['fallback_lead_minutes', 'Stop waiting before peak ends (minutes)', 0, 240, 15, 'Leave this much time before the peak window ends to find a useful fallback.']] as const).map(([key, title, min, max, step, help]) => <div key={key}><label>{title}<select aria-label={title} value={value[key]} onChange={event => onChange({ ...value, [key]: Number(event.target.value) })}>{Array.from({ length: (max - min) / step + 1 }, (_, i) => min + i * step).map(n => <option key={n} value={n}>{n}</option>)}</select></label><HelpTip label={title}>{help}</HelpTip></div>)}
    <label>After peak hours, prefer<select aria-label="After peak hours, prefer" value={value.after_peak_mode} onChange={event => onChange({ ...value, after_peak_mode: event.target.value as DailyPlanning['after_peak_mode'] })}><option value="longest_first">Longest session</option><option value="earliest_first">Earliest start</option><option value="room_first">Room priority</option></select></label>
    <label>Main priority<select aria-label="Main priority" value={value.priority_mode} onChange={event => onChange({ ...value, priority_mode: event.target.value as DailyPlanning['priority_mode'] })}><option value="time_first">Time before room rank</option><option value="room_first">Room rank before time</option></select></label>
  </>;
}
