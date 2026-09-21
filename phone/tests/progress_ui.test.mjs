import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const pageSource = await readFile(new URL('../app/page.tsx', import.meta.url), 'utf8');

test('commentary is routed into progress instead of the answer bubble', () => {
  assert.match(pageSource, /event\.kind === 'progress\.delta'/);
  assert.match(pageSource, /narrative=\{progressNarrative\}/);
  assert.doesNotMatch(pageSource, /open=\{busy\}/);
  assert.match(pageSource, /open=\{expanded\}/);
});

// Scrolling, clipping and automatic follow behavior are verified against the
// rendered UI in tools/check_phone_assistant_scroll_ui.py (Chromium + WebKit).
