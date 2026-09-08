'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronRight, RefreshCw } from 'lucide-react';
import { requestJson } from '../lib/api';
import { HelpTip } from './help-tip';

type Gap = { date: string; room: string; start: string; end: string; minutes: number };
export type SystemJob = { request_id: string; action: string; active: boolean; state: string; text: string; updated_at?: string; result?: { scan?: { observed_at: string; rows: Gap[]; scanned_dates: string[]; unavailable_dates: string[] } } | null };
type View = 'schedule' | 'run' | 'scan' | 'history' | 'events' | 'logs' | 'config' | 'cleanup' | 'protected' | 'about';
type Rules = { rolling_quota: number; same_room_gap_minutes: number; peak_hours: { start: string; end: string; max_hours: number } };
type ScanResult = { observed_at: string; rows: Gap[]; scanned_dates: string[]; unavailable_dates: string[] };
type PageData = {
  scan?: ScanResult | null;
  revision?: string;
  task?: { task_name: string; state: string; next_run: string; healthy: boolean; problem: string } | null;
  runs?: { time: string; bookings: number | null; outcome: string }[];
  stale?: boolean;
  events?: { key: string; date: string; start: string; end: string; title: string; room: string; ignored: boolean }[];
  rules?: Rules;
  files?: { name: string; bytes?: number; entries?: { time: string; message: string }[] }[];
  windows?: { date: string; start_time: string; end_time: string }[];
};
type Confirmation = { title: string; consequence: string; action: string; args: Record<string, unknown> };
const names: Record<View, string> = { schedule: 'Automatic scheduling', run: 'Run manually', scan: 'Find available rooms', history: 'Booking history', events: 'Event conflicts', logs: 'Activity and logs', config: 'Advanced rules', cleanup: 'Old log cleanup', protected: 'Cancelled times kept free', about: 'About and setup' };
const groups: { title: string; views: View[] }[] = [
  { title: 'PC operations', views: ['schedule', 'run', 'scan'] },
  { title: 'Activity and events', views: ['history', 'events', 'protected', 'logs'] },
  { title: 'Maintenance', views: ['config', 'cleanup', 'about'] },
];
const confirmLabels: Record<string, string> = { run: 'Run Booker now', run_visible: 'Open browser and run', schedule_install: 'Repair automatic schedule', schedule_remove: 'Remove automatic schedule', history_clear: 'Delete run history', events_save: 'Save conflict choices', config_save: 'Save booking rules', cleanup: 'Delete old logs', reopen: 'Allow this time again' };
const localDate = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
const when = (value?: string) => value ? new Date(value).toLocaleString('en-GB', { timeZone: 'Europe/London' }) : 'Not available';

function download(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function csvCell(value: string | number) {
  let text = String(value);
  if (/^[=+@\-\t\r]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

export function SystemTools({ csrf, enabled, active, job, onJob, onSaved, onEditing }: { onEditing?: (editing: boolean) => void; csrf: string; enabled: boolean; active: boolean; job: SystemJob | null; onJob: (job: SystemJob | null) => void; onSaved: () => void }) {
  const [view, setView] = useState<View | null>(null);
  const [pages, setPages] = useState<Partial<Record<View, PageData>>>({});
  const [loading, setLoading] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [hold, setHold] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [dateInput, setDateInput] = useState(localDate);
  const [dates, setDates] = useState<string[]>([]);
  const [filterDate, setFilterDate] = useState('');
  const [room, setRoom] = useState('');
  const [minimum, setMinimum] = useState(30);
  const [changes, setChanges] = useState<Record<string, boolean>>({});
  const [eventQuery, setEventQuery] = useState('');
  const [rules, setRules] = useState<Rules | null>(null);
  const [clearedAt, setClearedAt] = useState('');
  const loadRequest = useRef<AbortController | null>(null);
  const serial = useRef(false);
  const confirmBox = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const current = view ? pages[view] : undefined;
  const disabled = !enabled || working || Boolean(job?.active) || job?.state === 'uncertain' || hold;
  const scan = (job?.action === 'scan' ? job.result?.scan : null) ?? pages.scan?.scan;
  const gaps = (scan?.rows ?? []).filter(gap => (!filterDate || gap.date === filterDate) && gap.room.toLowerCase().includes(room.toLowerCase()) && gap.minutes >= minimum);
  const visibleEvents = (pages.events?.events ?? []).filter(event => `${event.title} ${event.room} ${event.date}`.toLowerCase().includes(eventQuery.toLowerCase()));

  const checkJob = useCallback(async (review = false) => {
    try {
      const { response, data } = await requestJson<{ job: SystemJob | null }>('/api/v1/system/job', { credentials: 'include', cache: 'no-store' });
      if (!response.ok) throw new Error();
      if (data.job?.result?.scan) setPages(previous => ({ ...previous, scan: { scan: data.job!.result!.scan } }));
      onJob(data.job);
      if (review) { setHold(false); setError(''); onSaved(); }
    } catch { if (review) setError('Operation status could not be loaded. Try Reload status again.'); }
  }, [onJob, onSaved]);

  useEffect(() => {
    if (!active || !csrf) return;
    const timer = window.setTimeout(() => void checkJob(), 0);
    return () => window.clearTimeout(timer);
  }, [active, csrf, checkJob]);
  useEffect(() => {
    if (!job?.active || !csrf) return;
    const timer = window.setInterval(() => void checkJob(), 2000);
    return () => window.clearInterval(timer);
  }, [job?.active, csrf, checkJob]);
  useEffect(() => { if (confirmation) { confirmBox.current?.focus(); confirmBox.current?.scrollIntoView({ block: 'center' }); } }, [confirmation]);
  useEffect(() => () => loadRequest.current?.abort(), []);
  useEffect(() => { if (view) { heading.current?.focus({ preventScroll: true }); heading.current?.scrollIntoView({ block: 'start' }); } }, [view]);

  async function load(next: View, force = false) {
    setView(next); onEditing?.(true); setError(''); setConfirmation(null);
    if (['run', 'about'].includes(next) || (pages[next] && !force)) return;
    loadRequest.current?.abort();
    const controller = new AbortController(); loadRequest.current = controller;
    setLoading(true);
    try {
      const { response, data } = await requestJson<PageData>(`/api/v1/system/${next}`, { credentials: 'include', cache: 'no-store', signal: controller.signal });
      if (!response.ok) throw new Error();
      if (controller.signal.aborted) return;
      setPages(previous => ({ ...previous, [next]: data }));
      // Reload updates the revision while retaining the user's unsaved choices.
      if (data.rules) setRules(previous => previous ?? data.rules!);
      setHold(false);
    } catch { if (!controller.signal.aborted) setError('This page could not be loaded. Try Reload page.'); }
    finally { if (!controller.signal.aborted) setLoading(false); }
  }

  async function start(action: string, args: Record<string, unknown> = {}) {
    if (serial.current || disabled) return;
    serial.current = true; setWorking(true); setError(''); setConfirmation(null);
    if (scan) setPages(previous => ({ ...previous, scan: { scan } }));
    try {
      const { response, data } = await requestJson<{ job?: SystemJob; message?: string; duplicate?: boolean }>('/api/v1/system/jobs', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf }, body: JSON.stringify({ request_id: crypto.randomUUID(), action, args }) });
      if (!response.ok) { setHold(true); throw new Error(data.message || 'The operation could not start. Reload its status.'); }
      if (data.job) onJob(data.job);
      if (data.duplicate) { setHold(true); setError('This operation was already submitted. Reload its status.'); }
    } catch (failure) { setHold(true); setError(failure instanceof Error ? failure.message : 'Delivery was not confirmed. Reload status before another action.'); }
    finally { serial.current = false; setWorking(false); }
  }

  async function stop() {
    if (!job?.active || serial.current) return;
    serial.current = true; setWorking(true); setError('');
    try {
      const { response, data } = await requestJson<{ job?: SystemJob; message?: string }>('/api/v1/system/stop', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf }, body: JSON.stringify({ request_id: job.request_id }) });
      if (!response.ok) throw new Error(data.message || 'Stop was not accepted. Reload status.');
      if (data.job) onJob(data.job);
    } catch { setError('Stop was not confirmed. Reload status to check the operation.'); }
    finally { serial.current = false; setWorking(false); }
  }

  function confirm(value: Confirmation) { if (!disabled) setConfirmation(value); }
  return <section className="system-tools" aria-label="PC tools">
    {job && <output className={`system-job ${job.state === 'uncertain' ? 'system-warning' : ''}`} aria-live="polite">
      <strong>{job.active ? 'PC operation in progress' : job.state === 'completed' ? 'PC operation complete' : 'PC operation update'}</strong>
      <p>{job.text}</p>
      {job.active && <progress aria-label="PC operation progress" />}
      <div className="system-actions">
        {job.active && ['run', 'run_visible', 'scan', 'agenda', 'plan', 'login'].includes(job.action) && <button type="button" disabled={working || job.state === 'stopping'} onClick={() => void stop()}>{job.state === 'stopping' ? 'Stopping…' : 'Stop operation'}</button>}
        <button type="button" onClick={() => void checkJob(true)}>Reload status</button>
      </div>
      {job.state === 'uncertain' && <p>Review this page and the latest agenda, then use the outcome review in Assistant before another action.</p>}
    </output>}
    {error && <div className="system-warning" role="alert"><p>{error}</p><button type="button" onClick={() => void checkJob(true)}>Reload status</button></div>}
    {!view ? groups.map(group => <div className="system-group" key={group.title}><h3>{group.title}</h3>{group.views.map(item => <button type="button" className="system-link" key={item} onClick={() => void load(item)}><span>{names[item]}</span><ChevronRight size={17} /></button>)}</div>) : <>
      <div className="system-heading"><button type="button" onClick={() => { loadRequest.current?.abort(); setLoading(false); setView(null); onEditing?.(false); setConfirmation(null); }}>Back to tools</button><h3 ref={heading} tabIndex={-1}>{names[view]}</h3>{!['run', 'scan', 'about'].includes(view) && <button type="button" disabled={loading} aria-label="Reload page" onClick={() => void load(view, true)}><RefreshCw size={17} /></button>}</div>
      {loading && <output>Loading… <button type="button" onClick={() => { loadRequest.current?.abort(); setLoading(false); }}>Cancel loading</button></output>}
      {view === 'schedule' && current && <div className="system-panel">
        <dl><dt>Recurring task</dt><dd>{current.task ? current.task.healthy ? 'Installed and ready' : 'Needs attention' : 'Not installed'}</dd><dt>Next scheduled run</dt><dd>{when(current.task?.next_run)}</dd><dt>Task state</dt><dd>{current.task?.state || 'Not installed'}</dd></dl>
        {current.task?.problem && <p className="system-warning">{current.task.problem}</p>}
        <div className="system-actions"><button type="button" disabled={disabled} onClick={() => confirm({ title: 'Install or repair automatic booking?', consequence: 'This updates the recurring Asimut task and PC wake settings, including allowing the plugged-in PC to stay awake with its lid closed.', action: 'schedule_install', args: {} })}>Install / repair schedule</button><button type="button" disabled={disabled || !current.task} onClick={() => confirm({ title: 'Remove automatic booking?', consequence: 'Future automatic runs will stop. Existing reservations remain booked.', action: 'schedule_remove', args: {} })}>Remove schedule</button></div>
        <p className="system-note">Wake support depends on the PC and Windows. The phone needs the PC running to connect.</p>
      </div>}
      {view === 'run' && <div className="system-panel"><h4>Book using saved preferences</h4><div className="system-actions"><button type="button" disabled={disabled} onClick={() => confirm({ title: 'Run the Booker now?', consequence: 'The PC will create or extend eligible reservations using your saved preferences.', action: 'run', args: {} })}>Run in background</button><button type="button" disabled={disabled} onClick={() => confirm({ title: 'Run with a browser on the PC?', consequence: 'A browser opens on the PC and the Booker may create or extend eligible reservations using your saved preferences.', action: 'run_visible', args: {} })}>Run with PC browser</button></div>
        <h4>Checks and refreshes</h4><div className="system-actions"><button type="button" disabled={disabled} onClick={() => void start('login')}>Check / repair login</button><HelpTip label="Login recovery">Uses the PC’s saved Microsoft session and deterministic recovery. Changing stored credentials requires the masked setup on the PC.</HelpTip><button type="button" disabled={disabled} onClick={() => void start('agenda')}>Refresh agenda</button><button type="button" disabled={disabled} onClick={() => void start('plan')}>Refresh practice plan</button></div>
      </div>}
      {view === 'scan' && <div className="system-panel"><div className="system-fields"><label>Scan date<input type="date" value={dateInput} onChange={e => setDateInput(e.target.value)} /></label><button type="button" disabled={!dateInput || dates.includes(dateInput) || dates.length >= 31} onClick={() => setDates(previous => [...previous, dateInput].sort())}>Add date</button></div>
        <div className="system-actions">{dates.map(date => <button type="button" key={date} aria-label={`Remove scan date ${date}`} onClick={() => setDates(previous => previous.filter(d => d !== date))}>{date} ×</button>)}</div>
        <div className="system-actions"><button type="button" disabled={disabled || !dates.length} onClick={() => void start('scan', { dates })}>Scan selected dates</button><HelpTip label="Availability scan">Reads visible gaps within Asimut’s current booking window. A gap is an observation, not a reservation or a guarantee that you can book it.</HelpTip></div>
        {scan && <><p>Checked {when(scan.observed_at)}</p>{scan.unavailable_dates.length > 0 && <p className="system-warning">Outside the checked booking window: {scan.unavailable_dates.join(', ')}</p>}<div className="system-fields"><label>Filter date<select value={filterDate} onChange={e => setFilterDate(e.target.value)}><option value="">All checked dates</option>{scan.scanned_dates.map(d => <option key={d}>{d}</option>)}</select></label><label>Room search<input value={room} onChange={e => setRoom(e.target.value)} /></label><label>Minimum gap (minutes)<input type="number" min="15" step="15" max="960" value={minimum} onChange={e => setMinimum(Number(e.target.value))} /></label></div>
          <p>{gaps.length} matching gaps</p><button type="button" onClick={() => download('asimut-availability.csv', [['Date', 'Room', 'Start', 'End', 'Gap minutes', 'Checked at'], ...gaps.map(g => [g.date, g.room, g.start, g.end, g.minutes, scan.observed_at])].map(row => row.map(csvCell).join(',')).join('\r\n'), 'text/csv;charset=utf-8')}>Download filtered CSV</button>
          <div className="system-results">{gaps.map((gap, index) => <article key={index}><strong>{gap.room} · {gap.date}</strong><p>{gap.start}–{gap.end} · {gap.minutes} minutes</p></article>)}</div></>}
      </div>}
      {view === 'history' && current && <div className="system-panel"><p>{current.runs?.length ?? 0} recent runs</p><div className="system-results">{current.runs?.map((run, index) => <details key={index}><summary>{when(run.time)} · {run.bookings === null ? 'Booking count unavailable' : `${run.bookings} bookings`}</summary><p>Run result: {run.outcome.replaceAll('_', ' ')}. See My Week for current reservations.</p></details>)}</div><button type="button" disabled={disabled || !current.runs?.length} onClick={() => confirm({ title: 'Clear booking history?', consequence: 'Saved run history will be deleted. Current reservations and preferences remain unchanged.', action: 'history_clear', args: { revision: current.revision } })}>Clear booking history</button></div>}
      {view === 'events' && current && <div className="system-panel"><p className="system-warning">Ignoring an event lets the Booker schedule practice over it. Reservations still count toward your quota.</p>{current.stale && <p className="system-warning">Agenda is out of date. Refresh it before saving changes.</p>}<button type="button" disabled={disabled} onClick={() => void start('agenda')}>Refresh live agenda</button><label>Find event<input value={eventQuery} onChange={e => setEventQuery(e.target.value)} /></label><div className="system-actions"><button type="button" disabled={disabled} onClick={() => setChanges(previous => ({ ...previous, ...Object.fromEntries(visibleEvents.map(e => [e.key, false])) }))}>Respect visible events</button><button type="button" disabled={disabled} onClick={() => setChanges(previous => ({ ...previous, ...Object.fromEntries(visibleEvents.map(e => [e.key, true])) }))}>Ignore visible events</button></div>
        {visibleEvents.map(event => <label className="system-check" key={event.key} aria-label={`Ignore ${event.title} on ${event.date} at ${event.start}`}><input type="checkbox" checked={changes[event.key] ?? event.ignored} disabled={disabled} onChange={e => setChanges(previous => ({ ...previous, [event.key]: e.target.checked }))} /><span><strong>Ignore {event.title}</strong><small>{event.date} · {event.start}–{event.end} {event.room}</small></span></label>)}{!visibleEvents.length && <p>No matching events.</p>}
        <div className="system-actions"><button type="button" disabled={disabled || current.stale || !Object.keys(changes).length} onClick={() => confirm({ title: 'Save event conflict choices?', consequence: 'The Booker may book over events marked Ignore. Review the selected events before saving.', action: 'events_save', args: { revision: current.revision, changes } })}>Save event choices</button><button type="button" onClick={() => setChanges({})}>Discard event edits</button></div>
      </div>}
      {view === 'protected' && current && <div className="system-panel">{!current.windows?.length && <p>No cancelled time windows are protected.</p>}{current.windows?.map(window => <article className="system-item" key={`${window.date}-${window.start_time}`}><strong>{window.date} · {window.start_time}–{window.end_time}</strong><button type="button" disabled={disabled} onClick={() => confirm({ title: 'Allow booking in this time again?', consequence: `The automatic Booker can use ${window.date}, ${window.start_time}–${window.end_time} again. This does not create a reservation immediately.`, action: 'reopen', args: { revision: current.revision, window } })}>Allow booking again</button></article>)}</div>}
      {view === 'logs' && current && <div className="system-panel"><h4>Current operation</h4>{job && (job.updated_at ?? '') > clearedAt ? <p>{job.text}</p> : <p>No new operation activity.</p>}<button type="button" onClick={() => setClearedAt(new Date().toISOString())}>Clear displayed activity</button><h4>Sanitized logs <HelpTip label="Sanitized logs">Shows recognized operation stages and errors. Credentials, browser output and private diagnostics stay on the PC.</HelpTip></h4>{!current.files?.length && <p>No supported logs available.</p>}{current.files?.map(file => <details key={file.name}><summary>{file.name}</summary><button type="button" onClick={() => download(`${file.name}.txt`, (file.entries ?? []).map(row => `${row.time} ${row.message}`).join('\n'), 'text/plain;charset=utf-8')}>Download sanitized log</button>{file.entries?.length ? file.entries.map((row, index) => <p key={index}>{row.time || 'Time unavailable'} · {row.message}</p>) : <p>No recognized stages in the latest log section.</p>}</details>)}</div>}
      {view === 'config' && current && rules && <form className="system-panel" onSubmit={event => { event.preventDefault(); confirm({ title: 'Save advanced booking rules?', consequence: 'These limits apply to new Booker runs. Fresh Asimut restrictions still apply.', action: 'config_save', args: { revision: current.revision, rules } }); }}><p>Limits for new Booker runs <HelpTip label="Advanced rules">These are the supported PC configuration fields. Asimut may impose stricter live limits, which the Booker still checks.</HelpTip></p><div className="system-fields"><label>Rolling weekly quota (hours)<input required type="number" min="0.5" max="168" step="0.5" value={rules.rolling_quota} onChange={e => setRules({ ...rules, rolling_quota: Number(e.target.value) })} /></label><label>Same-room gap (minutes)<input required type="number" min="0" max="240" step="15" value={rules.same_room_gap_minutes} onChange={e => setRules({ ...rules, same_room_gap_minutes: Number(e.target.value) })} /></label><label>Weekday peak starts<input required type="time" step="3600" value={rules.peak_hours.start} onChange={e => setRules({ ...rules, peak_hours: { ...rules.peak_hours, start: e.target.value } })} /></label><label>Weekday peak ends<input required type="time" step="3600" value={rules.peak_hours.end} onChange={e => setRules({ ...rules, peak_hours: { ...rules.peak_hours, end: e.target.value } })} /></label><label>Peak allowance per weekday (hours)<input required type="number" min="0" max="24" step="0.5" value={rules.peak_hours.max_hours} onChange={e => setRules({ ...rules, peak_hours: { ...rules.peak_hours, max_hours: Number(e.target.value) } })} /></label></div><div className="system-actions"><button type="submit" disabled={disabled || rules.peak_hours.end <= rules.peak_hours.start}>Save advanced rules</button><button type="button" onClick={() => setRules(current.rules ?? null)}>Discard rule edits</button></div></form>}
      {view === 'cleanup' && current && <div className="system-panel"><p>{current.files?.length ?? 0} inactive dated Booker logs older than 48 hours</p>{current.files?.map(file => <p key={file.name}>{file.name} · {file.bytes} bytes</p>)}<button type="button" disabled={disabled || !current.files?.length} onClick={() => confirm({ title: 'Delete these old logs?', consequence: 'Only the listed inactive dated Booker logs will be deleted. Active logs, settings and login data are retained.', action: 'cleanup', args: { revision: current.revision } })}>Delete listed old logs</button></div>}
      {view === 'about' && <div className="system-panel"><h4>Asimut Booker</h4><p>Your private companion to the PC app. Practice preferences, Calendar date edits and booking state are shared.</p><p>For Microsoft credential setup, use the masked setup on the PC. The phone never asks for a password or verification code.</p><p>Reconfirm reservations in Asimut on RWCMD Wi-Fi when the action becomes available.</p><a href="https://rwcmd.asimut.net/" target="_blank" rel="noreferrer">Open Asimut</a><p>To install on iPhone, open this private address in Safari, tap Share, then Add to Home Screen.</p></div>}
    </>}
    {confirmation && <div ref={confirmBox} tabIndex={-1} className="system-confirm" role="alertdialog" aria-label={confirmation.title} aria-describedby="system-consequence"><h4>{confirmation.title}</h4><p id="system-consequence">{confirmation.consequence}</p><div className="system-actions"><button type="button" disabled={disabled} onClick={() => void start(confirmation.action, confirmation.args)}>{confirmLabels[confirmation.action] || 'Confirm changes'}</button><button type="button" onClick={() => setConfirmation(null)}>Keep current state</button></div></div>}
  </section>;
}
