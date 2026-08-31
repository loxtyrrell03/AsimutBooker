import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const pageSource = await readFile(new URL('../app/page.tsx', import.meta.url), 'utf8');

test('ordinary reservations use booked language rather than confirmation language', () => {
  for (const staleCopy of [
    'confirmed bookings',
    'Next confirmed',
    'No upcoming confirmed reservation',
    'Confirmed and planned',
    '> Confirmed<',
    'h confirmed ·',
  ]) {
    assert.equal(pageSource.includes(staleCopy), false, staleCopy);
  }

  assert.match(pageSource, /current reservations/);
  assert.match(pageSource, /Next booked/);
  assert.match(pageSource, /Booked and planned/);
  assert.match(pageSource, /h booked ·/);
});
