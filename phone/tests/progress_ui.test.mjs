import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const pageSource = await readFile(new URL('../app/page.tsx', import.meta.url), 'utf8');
const styles = await readFile(new URL('../app/globals.css', import.meta.url), 'utf8');

test('commentary is routed into progress instead of the answer bubble', () => {
  assert.match(pageSource, /event\.kind === 'progress\.delta'/);
  assert.match(pageSource, /narrative=\{progressNarrative\}/);
  assert.doesNotMatch(pageSource, /open=\{busy\}/);
  assert.match(pageSource, /open=\{expanded\}/);
});

test('progress stays bounded and scrolls internally', () => {
  const bodyRule = styles.match(/\.progress-body\s*\{[^}]+\}/s)?.[0] ?? '';
  const cardRule = styles.match(/\.progress-card\s*\{[^}]+\}/s)?.[0] ?? '';
  assert.match(cardRule, /max-height:/);
  assert.match(cardRule, /overflow:\s*hidden/);
  assert.match(bodyRule, /max-height:/);
  assert.match(bodyRule, /overflow-y:\s*auto/);
  assert.match(bodyRule, /overscroll-behavior:\s*contain/);
});

test('transcript no longer starts repeated smooth scroll animations during thinking', () => {
  assert.doesNotMatch(pageSource, /behavior:\s*busy\s*\?\s*'smooth'/);
  assert.match(pageSource, /scrollIntoView\(\{ behavior: 'auto'/);
});
