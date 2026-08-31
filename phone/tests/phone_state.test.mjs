import assert from 'node:assert/strict';
import test from 'node:test';

import {
  cancellationInstruction,
  compactProgressText,
  deliveryDisposition,
  isFreshSequence,
  nextReconnectDelay,
  reconcileStreamPosition,
  updateProgressNarrative,
  upsertReasoningPart,
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

test('thinking summaries are compacted without raw markdown noise', () => {
  assert.equal(
    compactProgressText('**Evaluating booking request**\n\n### Checking agenda'),
    'Evaluating booking request Checking agenda',
  );
  assert.equal(compactProgressText('a'.repeat(500), 24), `${'a'.repeat(23)}…`);
});

test('reasoning deltas update bounded indexed parts instead of one endless blob', () => {
  let parts = [];
  parts = upsertReasoningPart(parts, 0, '**Checking');
  parts = upsertReasoningPart(parts, 0, ' agenda**');
  for (let index = 1; index <= 6; index += 1) {
    parts = upsertReasoningPart(parts, index, `Part ${index}`);
  }
  assert.equal(parts.length, 4);
  assert.deepEqual(parts.map((part) => part.index), [3, 4, 5, 6]);
});

test('commentary progress replaces per update and remains bounded', () => {
  assert.equal(updateProgressNarrative('old detail', 'new detail', true), 'new detail');
  assert.equal(updateProgressNarrative('hello', ' world', false), 'hello world');
  assert.equal(updateProgressNarrative('', 'x'.repeat(2_000), false).length, 1_600);
});
