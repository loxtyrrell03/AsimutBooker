import assert from 'node:assert/strict';
import test from 'node:test';

import { selectedPlanMinutes, selectedPlanSessions } from '../lib/plan_state.js';

test('multi-session day exposes primary and every additional session', () => {
  const day = {
    primary: { room: 'B0.29', start_time: '12:00', potential_minutes: 120 },
    additional: [
      { room: 'B1.09', start_time: '16:00', potential_minutes: 60 },
    ],
  };

  assert.deepEqual(
    selectedPlanSessions(day).map((candidate) => candidate.start_time),
    ['12:00', '16:00'],
  );
  assert.equal(selectedPlanMinutes(day), 180);
});

test('day without a primary has no selected plan minutes', () => {
  assert.deepEqual(selectedPlanSessions({ primary: null, additional: [] }), []);
  assert.equal(selectedPlanMinutes({ primary: null, additional: [] }), 0);
});
