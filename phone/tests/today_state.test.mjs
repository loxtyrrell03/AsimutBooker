import assert from 'node:assert/strict';
import test from 'node:test';
import { todaySummary } from '../lib/today_state.js';

const booking = (date, start_time, end_time, is_reservation = true) => ({ date, start_time, end_time, is_reservation, room: 'B0.29', title: 'Practice' });
test('Today chooses practice, includes active sessions, and excludes ended sessions', () => {
  const events = [booking('2026-09-08','08:00','09:00'),booking('2026-09-08','09:00','10:00',false),booking('2026-09-08','09:00','11:00'),booking('2026-09-09','14:00','16:00')];
  const view = todaySummary(events,new Date('2026-09-08T08:41:00Z'));
  assert.equal(view.next,events[2]); assert.equal(view.inProgress,true);
  assert.deepEqual(view.alsoToday,[events[1]]);
});
test('weekly totals exclude classes and dates outside the local calendar week', () => {
  const events = [booking('2026-09-06','14:00','16:00'),booking('2026-09-07','14:00','16:00'),booking('2026-09-08','10:00','11:00',false),booking('2026-09-13','14:00','16:00'),booking('2026-09-14','14:00','16:00')];
  assert.equal(todaySummary(events,new Date('2026-09-08T08:41:00Z')).weekMinutes,240);
});
test('Today uses London date across UTC midnight and excludes sessions ending now', () => {
  const events = [booking('2026-09-08','00:00','00:30'),booking('2026-09-08','08:00','10:00')];
  const view=todaySummary(events,new Date('2026-09-07T23:30:00Z'));
  assert.equal(view.today,'2026-09-08'); assert.equal(view.next,events[1]);
});
