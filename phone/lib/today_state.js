/** Display-only agenda selection. This never authorizes a booking operation. */
/** @typedef {{date:string,start_time:string,end_time:string,title:string,room:string,is_reservation:boolean}} AgendaEvent */

/** @param {AgendaEvent[]} events @param {Date} now @param {string} timezone */
export function todaySummary(events, now = new Date(), timezone = 'Europe/London') {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
    timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(now).map(part => [part.type, part.value]));
  const today = `${parts.year}-${parts.month}-${parts.day}`;
  const clock = `${parts.hour}:${parts.minute}`;
  const monday = new Date(`${today}T12:00:00Z`);
  monday.setUTCDate(monday.getUTCDate() - (monday.getUTCDay() + 6) % 7);
  const weekStart = monday.toISOString().slice(0, 10);
  monday.setUTCDate(monday.getUTCDate() + 7);
  const weekEnd = monday.toISOString().slice(0, 10);
  const ordered = [...events].sort((a, b) => `${a.date} ${a.start_time}`.localeCompare(`${b.date} ${b.start_time}`));
  const next = ordered.find(event => event.is_reservation &&
    (event.date > today || event.date === today && event.end_time > clock)) ?? null;
  const alsoToday = ordered.filter(event => event.date === today && event.end_time > clock && event !== next);
  const weekMinutes = ordered.filter(event => event.is_reservation && event.date >= weekStart && event.date < weekEnd)
    .reduce((total, event) => total + durationMinutes(event), 0);
  return { today, next, alsoToday, weekMinutes, inProgress: Boolean(next && next.date === today && next.start_time <= clock) };
}

/** @param {{start_time:string,end_time:string}} event */
export function durationMinutes(event) {
  const minutes = value => { const [h, m] = value.split(':').map(Number); return h * 60 + m; };
  const duration = minutes(event.end_time) - minutes(event.start_time);
  return Number.isFinite(duration) && duration > 0 ? duration : 0;
}

/** @param {number} minutes */
export function hoursLabel(minutes) {
  return `${Math.round(minutes / 6) / 10} ${minutes === 60 ? 'hour' : 'hours'}`;
}
