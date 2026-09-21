'use client';

import { timeBoundaryLabel } from '../lib/time-boundaries';

const clocks = Array.from({ length: 96 }, (_, index) =>
  `${String(Math.floor(index / 4)).padStart(2, '0')}:${String((index % 4) * 15).padStart(2, '0')}`);

export function TimeBoundarySelect({ label, side, value, onChange }: {
  label: string; side: 'start' | 'end'; value: string; onChange: (value: string) => void;
}) {
  const boundary = side === 'start' ? 'rooms_open' : 'rooms_closed';
  return <label>{label}<select aria-label={label} value={value} onChange={event => onChange(event.target.value)}>
    <option value={boundary}>{timeBoundaryLabel(boundary)}</option>
    {!clocks.includes(value) && value !== boundary && <option value={value}>{value}</option>}
    {clocks.map(clock => <option key={clock} value={clock}>{clock}</option>)}
  </select></label>;
}
