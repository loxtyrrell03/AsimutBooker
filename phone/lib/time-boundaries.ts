export const timeBoundaryLabel = (value: string) =>
  value === 'rooms_open' ? 'Rooms open' : value === 'rooms_closed' ? 'Rooms closed' : value;

export function timeBoundaryMinutes(value: string): number {
  if (value === 'rooms_open') return 0;
  if (value === 'rooms_closed') return 1440;
  return Number(value.slice(0, 2)) * 60 + Number(value.slice(3));
}
