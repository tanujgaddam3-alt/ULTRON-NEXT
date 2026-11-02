/* ULTRON Voice Client
 *
 * Thin wrapper over the local voice server API
 * (http://127.0.0.1:8765/api/v1/*).  Every public method returns a
 * plain object with success/error status — no thrown exceptions — so
 * callers never have to handle transports directly.
 *
 * The client degrades gracefully when the voice server is not running:
 * speak() returns OFFLINE status instead of throwing.
 *
 * Config lives in the singleton or can be overridden per-call.
 */

const DEFAULT_HOST = '127.0.0.1';
const DEFAULT_PORT = 8765;

const VALID_STYLES = [
  'normal',
  'information',
  'warning',
  'critical',
  'confirmation',
  'question',
  'error',
];

class VoiceClient {
  /** @param {{ host?: string, port?: number, basePath?: string }} opts */
  constructor({ host = DEFAULT_HOST, port = DEFAULT_PORT, basePath = '/api/v1' } = {}) {
    this._host = host;
    this._port = port;
    this._basePath = basePath;
  }

  /* ------------------------------------------------------------------ */
  /* Public API                                                          */
  /* ------------------------------------------------------------------ */

  /** Health check. Returns { ok, engines, status }. */
  async health() {
    return this._get('health');
  }

  /**
   * Speak a text in a given style. Returns the generated audio as a Blob
   * (playable via URL.createObjectURL) plus engine metadata headers.
   *
   * @param {string} text
   * @param {string} [style='normal']
   * @returns {Promise<{ ok: boolean, status: string, audio?: Blob, engine?: string, is_clone?: boolean, error?: string }>}
   */
  async speak(text, style = 'normal', { signal, context, onTiming } = {}) {
    if (typeof text !== 'string' || !text.trim()) {
      return { ok: false, status: 'INVALID_INPUT', error: 'text must be a non-empty string.' };
    }
    if (!VALID_STYLES.includes(style)) {
      return { ok: false, status: 'INVALID_INPUT', error: `style must be one of: ${VALID_STYLES.join(', ')}` };
    }
    const url = this._url('speak');
    const timeoutController = new AbortController();
    const timeout = setTimeout(() => timeoutController.abort(), 120000);
    const abortFromCaller = () => timeoutController.abort();
    if (signal) {
      if (signal.aborted) timeoutController.abort();
      else signal.addEventListener('abort', abortFromCaller, { once: true });
    }
    try {
      const payload = { text: text.trim(), style };
      if (typeof context === 'string' && context.trim()) {
        payload.context = context.trim();
      }
      onTiming?.('request_started');
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: timeoutController.signal,
      });
      if (!res.ok) {
        let error = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          if (body && body.error) error = body.error;
        } catch { /* non-JSON error body */ }
        return { ok: false, status: 'HTTP_ERROR', error };
      }
      const bytes = await res.arrayBuffer();
      onTiming?.('audio_received');
      const audio = new Blob([bytes], { type: 'audio/wav' });
      if (!audio.size || audio.size < 4000) {
        return { ok: false, status: 'EMPTY_AUDIO', error: 'server returned no usable audio.' };
      }
      return {
        ok: true,
        status: 'ok',
        audio,
        engine: res.headers.get('X-Engine') || undefined,
        is_clone: res.headers.get('X-Voice-Clone') === 'true',
      };
    } catch (err) {
      if (signal && signal.aborted) {
        return { ok: false, status: 'CANCELLED', error: 'Speech generation was cancelled.' };
      }
      if (timeoutController.signal.aborted) {
        return { ok: false, status: 'TIMEOUT', error: 'Speech generation timed out.' };
      }
      return { ok: false, status: 'OFFLINE', error: String(err && err.message) };
    } finally {
      clearTimeout(timeout);
      if (signal) signal.removeEventListener('abort', abortFromCaller);
    }
  }

  /**
   * List available engines.
   * @returns {Promise<{ ok: boolean, engines?: string[] }>}
   */
  async engines() {
    return this._get('engines');
  }

  /**
   * List available delivery styles.
   * @returns {Promise<{ ok: boolean, styles?: string[] }>}
   */
  async styles() {
    return this._get('styles');
  }

  /* ------------------------------------------------------------------ */
  /* Internals                                                           */
  /* ------------------------------------------------------------------ */

  _url(path) {
    return `http://${this._host}:${this._port}${this._basePath}/${path}`;
  }

  async _get(path) {
    const url = this._url(path);
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(5000) });
      if (!res.ok) {
        return { ok: false, status: 'HTTP_ERROR', error: `HTTP ${res.status}` };
      }
      return await res.json();
    } catch (err) {
      return { ok: false, status: 'OFFLINE', error: String(err && err.message) };
    }
  }
}

export { VoiceClient, VALID_STYLES };
export default VoiceClient;