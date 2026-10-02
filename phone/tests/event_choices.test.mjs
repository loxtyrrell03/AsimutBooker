import assert from 'node:assert/strict';
import test from 'node:test';
import { editEventChoices, validEventChoices } from '../lib/event_choices.js';

const rows = [
  { key: 'first', title: 'Example event', eligible: true, ignored: false },
  { key: 'second', title: 'Example event', eligible: true, ignored: false },
  { key: 'booking', eligible: false, ignored: false },
  { key: 'old-server', ignored: false },
];

test('one exact selection never changes an identical-looking neighbor', () => {
  assert.deepEqual(editEventChoices(rows, {}, ['first'], true), { first: true });
});
test('bulk edits exclude reservations and missing eligibility evidence', () => {
  assert.deepEqual(editEventChoices(rows, {}, rows.map(row => row.key), true), { first: true, second: true });
});
test('returning to the opening value removes the draft key', () => {
  assert.deepEqual(editEventChoices(rows, { first: true, second: true }, ['first'], false), { second: true });
});
test('missing or unsupported choices block the complete save rather than get dropped', () => {
  for (const key of ['missing', 'booking', 'old-server']) assert.equal(validEventChoices(rows, { [key]: true }), false);
  assert.equal(validEventChoices(rows, { first: true }), true);
});
test('explicit same-value binding is valid only for an existing resolved older choice', () => {
  const legacy = [{ key: 'one', eligible: true, ignored: true, choice_basis: 'v2' }];
  assert.deepEqual(editEventChoices(legacy, {}, ['one'], true), {});
  assert.equal(validEventChoices(legacy, { one: true }), true);
  assert.equal(validEventChoices([{ ...legacy[0], choice_basis: 'exact' }], { one: true }), false);
});
test('an ambiguous legacy choice can be explicitly kept protected without ignoring its neighbor', () => {
  const ambiguous = [{ key: 'one', eligible: true, ignored: false, unresolved_choice: true }];
  assert.equal(validEventChoices(ambiguous, { one: false }), true);
  assert.equal(validEventChoices([{ ...ambiguous[0], choice_basis: 'exact' }], { one: false }), false);
});
