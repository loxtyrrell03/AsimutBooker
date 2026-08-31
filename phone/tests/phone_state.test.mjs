import assert from 'node:assert/strict';
import test from 'node:test';

import {
  cancellationInstruction,
  deliveryDisposition,
  isFreshSequence,
  nextReconnectDelay,
  reconcileStreamPosition,
} from '../lib/phone_state.js';

test('accepted-response loss remains ambiguous unless the stream confirmed it', () => {
  assert.equal(deliveryDisposition(502, false), 'ambiguous');
  assert.equal(deliveryDisposition(504, false), 'ambiguous');
  assert.equal(deliveryDisposition(502, true), 'confirmed');
  assert.equal(deliveryDisposition(409, false), 'rejected');
  assert.equal(deliveryDisposition(409, false, 'delivery_uncertain'), 'ambiguous');
  assert.equal(deliveryDisposition(202, false), 'accepted');
});

test('event replay applies each positive sequence only once', () => {
  assert.equal(isFreshSequence(8, 8), false);
  assert.equal(isFreshSequence(8, 7), false);
  assert.equal(isFreshSequence(8, 9), true);
});

test('bootstrap cursor is max-only within one server generation', () => {
  assert.deepEqual(
    reconcileStreamPosition('process-a', 12, 'process-a', 4),
    { generation: 'process-a', cursor: 12, reset: false },
  );
  assert.deepEqual(
    reconcileStreamPosition('process-a', 12, 'process-a', 18),
    { generation: 'process-a', cursor: 18, reset: false },
  );
});

test('new server generation authoritatively resets a stale high cursor', () => {
  assert.deepEqual(
    reconcileStreamPosition('process-a', 91, 'process-b', 2),
    { generation: 'process-b', cursor: 2, reset: true },
  );
});

test('live reconnect uses a bounded three-step backoff', () => {
  assert.deepEqual([0, 1, 2, 3].map(nextReconnectDelay), [800, 2_000, 5_000, null]);
});

test('cancellation prefill includes canonical date and exact interval', () => {
  assert.equal(
    cancellationInstruction({
      date: '2026-09-01',
      start_time: '16:00',
      end_time: '17:30',
      room: 'B0.14',
    }),
    'Cancel my reservation on 2026-09-01 from 16:00 to 17:30 in B0.14.',
  );
});
