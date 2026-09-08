/** Bound the whole JSON exchange, including a stalled response body. Never retry writes. */
export async function requestJson<T>(url: string, init: RequestInit = {}, timeoutMs = 15_000) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (init.signal?.aborted) abort();
  init.signal?.addEventListener('abort', abort, { once: true });
  const timer = window.setTimeout(abort, timeoutMs);
  try {
    const response = await fetch(url, { ...init, signal: controller.signal });
    const data = await response.json().catch((error: unknown) => {
      if (response.ok || controller.signal.aborted) throw error;
      return {};
    }) as T;
    return { response, data };
  } finally {
    window.clearTimeout(timer);
    init.signal?.removeEventListener('abort', abort);
  }
}
