'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { AgendaEvent, BookerSnapshot } from '../app/page';
import type { Preferences, TimePreference } from '../lib/preferences';
import { requestJson } from '../lib/api';
import { selectedPlanSessions } from '../lib/plan_state';
import { BookingDetails } from './quiet-focus';
import { HelpTip } from './help-tip';

type Mode = 'month' | 'fortnight' | 'week' | '3days' | 'plan';
type Edit = { enabled?: boolean; hours?: string; time?: TimePreference | null };
const parse = (key: string) => new Date(`${key}T12:00:00Z`);
const keyOf = (value: Date) => value.toISOString().slice(0, 10);
const add = (key: string, days: number) => { const value = parse(key); value.setUTCDate(value.getUTCDate() + days); return keyOf(value); };
const label = (key: string, short = false) => parse(key).toLocaleDateString('en-GB', { timeZone: 'UTC', weekday: short ? 'short' : 'long', day: 'numeric', month: short ? 'short' : 'long' });
const minutes = (clock: string) => Number(clock.slice(0, 2)) * 60 + Number(clock.slice(3));
const cleanTime = (time: TimePreference): TimePreference => ({ enabled: time.enabled, start_time: time.start_time, end_time: time.end_time, strict_mode: time.strict_mode });

export function PhoneCalendar({ booker, csrf, active, editable, onSaved, onRefresh, refreshing, onCancel, cancelling }: {
  booker: BookerSnapshot; csrf: string; active: boolean; editable: boolean; onSaved: () => void;
  onRefresh: () => void; refreshing: boolean; onCancel: (event: AgendaEvent) => void; cancelling: boolean;
}) {
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: booker.timezone || 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  const [anchor, setAnchor] = useState(today);
  const [focus, setFocus] = useState(today);
  const [mode, setMode] = useState<Mode>('month');
  const [values, setValues] = useState<Preferences | null>(null);
  const [edits, setEdits] = useState<Record<string, Edit>>({});
  const [selection, setSelection] = useState<string[]>([]);
  const [selecting, setSelecting] = useState(false);
  const [batchHours, setBatchHours] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [working, setWorking] = useState(false);
  const [saving, setSaving] = useState(false);
  const [reloadRequired, setReloadRequired] = useState(false);
  const [detail, setDetail] = useState<AgendaEvent | null>(null);
  const request = useRef<AbortController | null>(null);
  const saveInFlight = useRef(false);
  const initialized = useRef(false);
  const [batchTime, setBatchTime] = useState<TimePreference>({ enabled: true, start_time: '12:00', end_time: '18:00', strict_mode: false });
  const [batchTimeMode, setBatchTimeMode] = useState<'default' | 'custom' | 'any'>('default');
  const load = useCallback(async (discard = false) => {
    request.current?.abort(); const controller = new AbortController(); request.current = controller;
    setWorking(true); setError('');
    try {
      const { response, data } = await requestJson<Preferences>('/api/v1/preferences', { cache: 'no-store', signal: controller.signal });
      if (!response.ok) throw new Error();
      if (controller.signal.aborted) return;
      setValues(data); setReloadRequired(false);
      if (discard) { setEdits({}); setNotice('Saved calendar settings restored.'); }
      else setNotice('');
    } catch { if (!controller.signal.aborted) setError('Calendar settings could not be loaded. Try again.'); }
    finally { if (!controller.signal.aborted) setWorking(false); }
  }, []);
  useEffect(() => {
    if (active && csrf && !initialized.current) { initialized.current = true; void load(); }
  }, [active, csrf, load]);
  useEffect(() => () => request.current?.abort(), []);
  const dates = useMemo(() => {
    if (mode === 'month') {
      const first = `${anchor.slice(0, 7)}-01`, value = parse(first);
      const lead = (value.getUTCDay() + 6) % 7;
      const lastDate = new Date(value); lastDate.setUTCMonth(value.getUTCMonth() + 1, 0);
      const last = lastDate.getUTCDate();
      return Array.from({ length: Math.ceil((lead + last) / 7) * 7 }, (_, i) => add(first, i - lead));
    }
    const start = mode === 'week' || mode === 'fortnight' ? add(anchor, -((parse(anchor).getUTCDay() + 6) % 7)) : anchor;
    return Array.from({ length: mode === 'fortnight' ? 14 : mode === '3days' ? 3 : 7 }, (_, i) => add(start, i));
  }, [anchor, mode]);
  const visibleEditable = dates.filter(date => date >= today && (mode !== 'month' || date.slice(0, 7) === anchor.slice(0, 7)));
  const state = (date: string) => ({ enabled: edits[date]?.enabled ?? !values?.disabled_dates.includes(date), hours: edits[date]?.hours ?? values?.practice_plan.date_overrides[date]?.toString() ?? '', time: edits[date]?.time !== undefined ? edits[date].time : values?.date_time_preferences?.[date] ?? null });
  const edit = (date: string, patch: Edit) => { setEdits(current => ({ ...current, [date]: { ...current[date], ...patch } })); setNotice(''); };
  const applyBatch = (patch: Edit) => { setEdits(current => { const next = { ...current }; for (const date of selection) next[date] = { ...next[date], ...patch }; return next; }); setNotice(''); };
  const navigate = (direction: number) => {
    if (mode === 'month') { const value = parse(`${anchor.slice(0, 7)}-01`); value.setUTCMonth(value.getUTCMonth() + direction); setAnchor(keyOf(value)); }
    else setAnchor(add(anchor, direction * (mode === 'fortnight' ? 14 : mode === '3days' ? 3 : 7)));
  };
  const save = async () => {
    if (!values || !editable || working || saveInFlight.current || reloadRequired || !Object.keys(edits).length) return;
    saveInFlight.current = true; setSaving(true); setWorking(true); setError('');
    const snapshot = edits;
    const changes: Record<string, unknown> = {};
    const days = Object.entries(snapshot).filter(([, value]) => value.enabled !== undefined).map(([date, value]) => ({ date, enabled: value.enabled }));
    const hours = Object.entries(snapshot).filter(([, value]) => value.hours !== undefined).map(([date, value]) => ({ date, hours: value.hours === '' ? null : Number(value.hours) }));
    const times = Object.fromEntries(Object.entries(snapshot).filter(([, value]) => value.time !== undefined).map(([date, value]) => [date, value.time]));
    if (days.length) changes.booking_days = days;
    if (hours.length) changes.practice_plan = { date_overrides: hours };
    if (Object.keys(times).length) changes.date_time_preferences = times;
    try {
      const { response, data } = await requestJson<Preferences & { message?: string }>('/api/v1/preferences', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf }, body: JSON.stringify({ revision: values.revision, changes }) });
      if (!response.ok) { setReloadRequired(response.status === 409 || response.status >= 500); setError(data.message || 'Calendar changes could not be saved.'); return; }
      setValues(data); setEdits({}); setNotice('Calendar changes saved.'); onSaved();
    } catch { setReloadRequired(true); setError('Save result unknown. Reload saved values before saving again; your draft is retained.'); }
    finally { setWorking(false); setSaving(false); saveInFlight.current = false; }
  };
  const events = (date: string) => booker.agenda.events.filter(event => event.date === date).sort((a, b) => a.start_time.localeCompare(b.start_time));
  const closed = (date: string) => booker.agenda.closed_dates?.includes(date);
  const chosen = state(focus);
  const time = chosen.time ?? values?.time_preferences;
  const changeTime = (patch: Partial<TimePreference>) => { if (time) edit(focus, { time: { ...cleanTime(time), ...patch } }); };
  const plans = booker.plan.available && !booker.plan.stale ? booker.plan.days : [];
  const eventList = (date: string) => <>{events(date).map((event, index) => <button type="button" className="calendar-event" key={`${event.event_id}-${index}`} onClick={() => setDetail(event)}><strong>{event.start_time}–{event.end_time}</strong><span>{event.room} · {event.is_reservation ? 'Booked' : event.title}</span></button>)}</>;

  if (detail) return <BookingDetails event={detail} stale={booker.agenda.stale || !booker.agenda.events.some(event => event.event_id === detail.event_id && event.date === detail.date && event.start_time === detail.start_time && event.end_time === detail.end_time)} onClose={() => setDetail(null)} onAsk={() => setDetail(null)} onCancel={() => onCancel(detail)} cancelling={cancelling} />;
  return <section className="view-page phone-calendar" aria-label="Calendar">
    <div className="view-heading"><h2>Calendar</h2><button type="button" className="quiet-secondary" disabled={refreshing} onClick={onRefresh}>{refreshing ? 'Refreshing…' : 'Refresh'}</button></div>
    <div className="calendar-toolbar"><label>View<select aria-label="View" value={mode} onChange={event => setMode(event.target.value as Mode)}>{[['month', 'Month'], ['fortnight', 'Fortnight'], ['week', 'Week'], ['3days', '3 days'], ['plan', 'Plan']].map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label><button type="button" aria-label="Previous period" onClick={() => navigate(-1)}>‹</button><button type="button" onClick={() => { setAnchor(today); setFocus(today); }}>Today</button><button type="button" aria-label="Next period" onClick={() => navigate(1)}>›</button></div>
    <label className="calendar-jump">Go to date<input type="date" value={anchor} onChange={event => { if (event.target.value) { setAnchor(event.target.value); setFocus(event.target.value); } }} /></label>
    <h3>{parse(anchor).toLocaleDateString('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' })}</h3>
    {(!booker.agenda.available || booker.agenda.stale) && <p className="quiet-notice">{booker.agenda.available ? 'Showing the last checked agenda.' : 'Agenda not checked. Refresh to load events.'}</p>}
    {(mode === 'month' || mode === 'fortnight') && <><div className="calendar-weekdays" aria-hidden="true">{['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(day => <span key={day}>{day}</span>)}</div><div className="calendar-month">{dates.map(date => <button type="button" key={date} aria-label={`${label(date)}, ${values ? state(date).enabled ? 'booking on' : 'booking off' : 'settings not loaded'}${closed(date) ? ', practice rooms closed' : ''}${edits[date] ? ', unsaved changes' : ''}`} aria-pressed={selecting ? selection.includes(date) : focus === date} className={`${focus === date ? 'focused' : ''} ${selection.includes(date) && selecting ? 'selected' : ''} ${values && !state(date).enabled ? 'day-off' : ''} ${closed(date) ? 'day-closed' : ''} ${date.slice(0, 7) !== anchor.slice(0, 7) ? 'outside-month' : ''}`} onClick={() => { setFocus(date); if (selecting && date >= today) setSelection(current => current.includes(date) ? current.filter(item => item !== date) : [...current, date]); }}><span>{Number(date.slice(8))}</span><small>{edits[date] ? '✎' : closed(date) ? '×' : events(date).length ? '•' : state(date).time ? '◷' : ''}</small></button>)}</div><p className="calendar-legend">● Event · shaded date: booking off · ◷ custom time</p></>}
    {(mode === 'week' || mode === '3days') && <div className="calendar-timeline" aria-label="Day timelines, scroll horizontally for more dates">{dates.map(date => <div className="timeline-day" key={date}><button type="button" className={closed(date) ? 'closed-key' : ''} onClick={() => setFocus(date)}>{label(date, true)}</button><div className="timeline-track">{Array.from({ length: 9 }, (_, i) => <span key={i} className="timeline-hour" style={{ top: i * 72 }}>{`${7 + i * 2}:00`}</span>)}{events(date).map((event, index) => <button key={index} type="button" className={`timeline-event ${event.is_reservation ? '' : 'class-event'}`} style={{ top: Math.max(0, (minutes(event.start_time) - 420) * .6), minHeight: 30, height: Math.max(30, (minutes(event.end_time) - minutes(event.start_time)) * .6) }} onClick={() => setDetail(event)}>{event.start_time}–{event.end_time}<br />{event.room}<br />{event.is_reservation ? 'Booked' : event.title}</button>)}{plans.filter(day => day.date === date).flatMap(day => selectedPlanSessions(day)).filter(candidate => candidate.potential_minutes > 0).map((candidate, index) => <button key={`potential-${index}`} type="button" className="timeline-potential" style={{ top: Math.max(0, (minutes(candidate.start_time) - 420) * .6), height: Math.max(30, (minutes(candidate.end_time) - minutes(candidate.start_time)) * .6) }} onClick={() => { setFocus(date); setMode('plan'); }}>{candidate.room}<br />{candidate.start_time}–{candidate.end_time}<br />Potential</button>)}</div></div>)}</div>}
    {mode === 'plan' && <div>{!plans.length && <p className="quiet-notice">{booker.plan.stale ? 'Plan needs refreshing.' : 'No checked plan available for this period.'}</p>}{plans.filter(day => dates.includes(day.date)).map(day => <article key={day.date} className="calendar-plan"><h3>{label(day.date, true)}</h3><p>Target {day.target_minutes / 60} hours · booked {day.existing_minutes / 60} hours</p><p>{day.reason}</p>{selectedPlanSessions(day).map((candidate, index) => <div key={index} className="potential-session">{candidate.room} · {candidate.start_time}–{candidate.end_time}<br />{candidate.potential_minutes > 0 ? `${candidate.potential_minutes} potential minutes · not reserved` : 'Already booked'}<p>{candidate.reason}</p></div>)}{day.backups.length > 0 && <details><summary>Other options</summary>{day.backups.map((candidate, index) => <p key={index}>{candidate.room} · {candidate.start_time}–{candidate.end_time} · potential</p>)}</details>}</article>)}</div>}
    <button type="button" className="quiet-secondary" onClick={() => { setSelecting(value => !value); setSelection([]); }} disabled={!values || working}>{selecting ? 'Finish selecting days' : 'Select several days'}</button>
    {selecting && <fieldset className="calendar-editor" disabled={!editable || working || !values}><legend>{selection.length} selected days</legend><div className="calendar-action-row"><button type="button" onClick={() => setSelection(visibleEditable)}>Select all visible days</button><button type="button" onClick={() => setSelection([])}>Clear selection</button></div><div className="calendar-action-row"><button type="button" disabled={!selection.length} onClick={() => applyBatch({ enabled: true })}>Enable selected days</button><button type="button" disabled={!selection.length} onClick={() => applyBatch({ enabled: false })}>Disable selected days</button></div><label>Target hours for selected days<input type="number" min="0.5" max="12" step="0.5" placeholder="Use daily target" value={batchHours} onChange={event => setBatchHours(event.target.value)} /></label><button type="button" disabled={!selection.length} onClick={() => applyBatch({ hours: batchHours })}>Apply target</button><label>Preferred time for selected days<select aria-label="Preferred time for selected days" value={batchTimeMode} onChange={event => setBatchTimeMode(event.target.value as typeof batchTimeMode)}><option value="default">Use default time</option><option value="custom">Custom time</option><option value="any">Any time</option></select></label>{batchTimeMode === 'custom' && <><label>Selected days start<input type="time" step="900" value={batchTime.start_time} onChange={event => setBatchTime({ ...batchTime, start_time: event.target.value })} /></label><label>Selected days end<input type="time" step="900" value={batchTime.end_time} onChange={event => setBatchTime({ ...batchTime, end_time: event.target.value })} /></label><label><input type="checkbox" checked={batchTime.strict_mode} onChange={event => setBatchTime({ ...batchTime, strict_mode: event.target.checked })} />Only within these times</label></>}<button type="button" disabled={!selection.length} onClick={() => applyBatch({ time: batchTimeMode === 'default' ? null : { ...batchTime, enabled: batchTimeMode !== 'any' } })}>Apply preferred time</button></fieldset>}
    <h3 className={closed(focus) ? 'closed-key' : ''}>{label(focus)}</h3>{closed(focus) && <p className="closure-label">Practice rooms closed</p>}
    {eventList(focus)}{booker.agenda.available && !booker.agenda.stale && !events(focus).length && <p className="quiet-muted">No events in the checked agenda for this date.</p>}
    {values && focus >= today && <fieldset className="calendar-editor" disabled={!editable || working}><legend>Practice on this day</legend><label><input type="checkbox" checked={chosen.enabled} onChange={event => edit(focus, { enabled: event.target.checked })} />Book on this day</label><label>Target hours<input type="number" min="0.5" max="12" step="0.5" placeholder={`Default: ${values.practice_plan.default_hours} hours`} value={chosen.hours} onChange={event => edit(focus, { hours: event.target.value })} /></label><label>Preferred time<select aria-label="Preferred time" value={chosen.time === null ? 'default' : chosen.time?.enabled ? 'custom' : 'any'} onChange={event => edit(focus, { time: event.target.value === 'default' ? null : { ...cleanTime(time ?? values.time_preferences), enabled: event.target.value === 'custom' } })}><option value="default">Use default time</option><option value="custom">Custom time for this day</option><option value="any">Any time on this day</option></select></label>{chosen.time?.enabled && <><label>Day start time<input type="time" step="900" value={chosen.time.start_time} onChange={event => changeTime({ start_time: event.target.value })} /></label><label>Day end time<input type="time" step="900" value={chosen.time.end_time} onChange={event => changeTime({ end_time: event.target.value })} /></label><label><input type="checkbox" checked={chosen.time.strict_mode} onChange={event => changeTime({ strict_mode: event.target.checked })} />Only book within this day’s times</label></>}{chosen.time === null && <p className="quiet-muted">Default: {values.time_preferences.enabled ? `${values.time_preferences.start_time}–${values.time_preferences.end_time}${values.time_preferences.strict_mode ? ' only' : ' preferred'}` : 'Any time'}</p>}<p className="quiet-muted">Turning a day off leaves existing reservations in place. <HelpTip label="Future practice dates">Future dates wait for the live booking window. A practice target is not a reservation.</HelpTip></p>{!values.practice_plan.enabled && <output>Daily targets are off in Settings. Enable them to use date-specific hours.</output>}</fieldset>}
    {!editable && <p className="quiet-notice">Connect and wait for the current action to finish before editing.</p>}
    {working && <output>{saving ? 'Saving calendar…' : 'Loading calendar settings…'}</output>}{error && <p role="alert" className="calendar-error">{error}</p>}{notice && <output className="quiet-notice">{notice}</output>}
    {error && <button type="button" className="quiet-secondary" disabled={working} onClick={() => void load()}>Reload saved values; keep draft</button>}
    {working && !saving && <button type="button" className="quiet-secondary" onClick={() => { request.current?.abort(); setWorking(false); setError('Loading cancelled. Reload when ready.'); }}>Cancel loading</button>}
    {Object.keys(edits).length > 0 && <div className="calendar-save"><span>{Object.keys(edits).length} changed days</span><button type="button" className="quiet-secondary" disabled={working} onClick={() => void load(true)}>Discard changes</button><button type="button" className="quiet-primary" disabled={!editable || working || reloadRequired} onClick={() => void save()}>Save changes</button></div>}
  </section>;
}
