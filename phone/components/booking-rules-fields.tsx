import type { BookingRules } from '../lib/preferences';
import { HelpTip } from './help-tip';

export function BookingRulesFields({ value, onChange }: { value: BookingRules; onChange: (value: BookingRules) => void }) {
  const custom = value.preset === 'custom';
  const clock = (n: number) => `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(n % 60).padStart(2, '0')}`;
  return <>
    <label>Booking rules preset<select aria-label="Booking rules preset" value={value.preset} onChange={event => {
      const preset = event.target.value as BookingRules['preset'];
      onChange(preset === 'custom' ? { ...value, preset } : { preset, free_horizon_overrides_peak: value.free_horizon_overrides_peak ?? false, rolling_quota_hours: preset === 'legacy' ? 28 : 6,
        peak_quota_minutes: preset === 'legacy' ? 120 : 60, free_horizon_minutes: 300, peak_start_minutes: 540, peak_end_minutes: 960 });
    }}><option value="legacy">Previous quotas (28h / 2h peak)</option><option value="new">New quotas (6h / 1h peak)</option><option value="custom">Custom quotas</option></select></label>
    <p>ASIMUT must approve each booking. Its current limits may be lower than this preset.</p>
    {([['rolling_quota_hours', 'Advance quota (hours)', .5, 168, .25], ['peak_quota_minutes', 'Weekday peak quota (minutes)', 0, 1440, 15], ['free_horizon_minutes', 'Free horizon (minutes)', 0, 1440, 15]] as const).map(([key,label,min,max,step]) =>
      <div key={key}><label>{label}<input type="number" required min={min} max={max} step={step} readOnly={!custom} value={value[key]} onChange={e => onChange({ ...value, [key]:Number(e.target.value) })} /></label>
        {key === 'free_horizon_minutes' && <HelpTip label="Free horizon">Both ends of a short-notice booking must fit inside this window. Available quota is still used normally.</HelpTip>}</div>)}
    {(['peak_start_minutes', 'peak_end_minutes'] as const).map((key, i) => <label key={key}>{i ? 'Weekday peak end' : 'Weekday peak start'}<select aria-label={i ? 'Weekday peak end' : 'Weekday peak start'} disabled={!custom} value={value[key]} onChange={e => onChange({ ...value, [key]:Number(e.target.value) })}>
      {Array.from({length:97}, (_,n) => n*15).map(n => <option key={n} value={n}>{clock(n)}</option>)}
    </select></label>)}
    <div><label><input type="checkbox" checked={value.free_horizon_overrides_peak ?? false} onChange={e => onChange({ ...value, free_horizon_overrides_peak: e.target.checked })} />Allow extra peak time within the free horizon</label><HelpTip label="Extra peak time">Only complete bookings inside the free window qualify. ASIMUT must approve each booking and extension.</HelpTip></div>
    <p>Room access, booking horizons and permitted durations are checked live. Existing reservations stay in place.</p>
  </>;
}
