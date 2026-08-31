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
