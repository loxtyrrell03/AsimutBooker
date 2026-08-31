/** Small pure state decisions shared by the phone UI and its Node tests. */

export const RECONNECT_DELAYS_MS = Object.freeze([800, 2_000, 5_000]);

/** @param {number} attempt */
export function nextReconnectDelay(attempt) {
  return Number.isInteger(attempt) && attempt >= 0
    ? (RECONNECT_DELAYS_MS[attempt] ?? null)
    : null;
}

/** @param {number} current @param {number | undefined} incoming */
export function isFreshSequence(current, incoming) {
  return !incoming || incoming > current;
}

/**
 * Reconcile a trusted bootstrap replay boundary with the current stream.
 * A different per-process generation is authoritative and may move the
 * cursor backwards; within one generation the cursor is strictly max-only.
 *
 * @param {string | null} currentGeneration
 * @param {number} currentCursor
 * @param {string} incomingGeneration
 * @param {number} incomingCursor
 */
export function reconcileStreamPosition(
  currentGeneration,
  currentCursor,
  incomingGeneration,
  incomingCursor,
) {
  const nextCursor = Number.isInteger(incomingCursor) && incomingCursor >= 0
    ? incomingCursor
    : 0;
  if (!incomingGeneration) {
    throw new TypeError('A stream generation is required');
  }
  if (currentGeneration !== incomingGeneration) {
    return { generation: incomingGeneration, cursor: nextCursor, reset: true };
  }
  return {
    generation: currentGeneration,
    cursor: Math.max(currentCursor, nextCursor),
    reset: false,
  };
}

/**
 * @param {number} status
 * @param {boolean} confirmedByStream
 * @param {string} [errorCode]
 * @returns {'confirmed' | 'accepted' | 'rejected' | 'ambiguous'}
 */
export function deliveryDisposition(status, confirmedByStream, errorCode = '') {
  if (confirmedByStream) return 'confirmed';
  if (errorCode === 'delivery_uncertain') return 'ambiguous';
  if (status >= 200 && status < 300) return 'accepted';
  if (status >= 400 && status < 500) return 'rejected';
  return 'ambiguous';
}

/** @param {{date: string, start_time: string, end_time: string, room: string}} event */
export function cancellationInstruction(event) {
  return `Cancel my reservation on ${event.date} from ${event.start_time} to ${event.end_time} in ${event.room}.`;
}

/**
 * Turn markdown-ish streamed summaries into one calm, bounded status line.
 * This is presentation cleanup only; raw model reasoning is never received.
 *
 * @param {string} value
 * @param {number} [maximum]
 */
export function compactProgressText(value, maximum = 360) {
  if (typeof value !== 'string' || !value) return '';
  const cleaned = value
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/[*_`#~]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (cleaned.length <= maximum) return cleaned;
  return `${cleaned.slice(0, Math.max(0, maximum - 1)).trimEnd()}…`;
}

/**
 * Keep only the newest bounded reasoning-summary parts while coalescing deltas.
 *
 * @param {Array<{index: number, text: string}>} current
 * @param {number | undefined} index
 * @param {string | undefined} delta
 */
export function upsertReasoningPart(current, index, delta) {
  const safeIndex = typeof index === 'number' && Number.isInteger(index) && index >= 0
    ? index
    : 0;
  const safeDelta = typeof delta === 'string' ? delta : '';
  if (!safeDelta) return current;
  const existing = current.find((item) => item.index === safeIndex);
  const nextText = `${existing?.text ?? ''}${safeDelta}`.slice(-1_200);
  return [
    ...current.filter((item) => item.index !== safeIndex),
    { index: safeIndex, text: nextText },
  ].sort((left, right) => left.index - right.index).slice(-4);
}

/** @param {string} current @param {string | undefined} delta @param {boolean} replace */
export function updateProgressNarrative(current, delta, replace) {
  const safeDelta = typeof delta === 'string' ? delta : '';
  if (!safeDelta) return current;
  return `${replace ? '' : current}${safeDelta}`.slice(-1_600);
}
