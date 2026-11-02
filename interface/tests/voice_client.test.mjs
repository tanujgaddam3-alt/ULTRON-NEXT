import { test } from 'node:test';
import assert from 'node:assert/strict';
import { VoiceClient, VALID_STYLES } from '../services/voiceClient.js';

/* ------------------------------------------------------------------ */
/* Fake global fetch — avoids any network call in tests                 */
/* ------------------------------------------------------------------ */
const fakeFetch = (status, body, { blob = false } = {}) =>
  globalThis.fetch = async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    arrayBuffer: async () => (blob ? new ArrayBuffer(9000) : new ArrayBuffer(0)),
    headers: new Headers({ 'X-Engine': 'edge', 'X-Voice-Clone': 'false' }),
  });

const client = new VoiceClient();

test('health() returns off-line when fetch throws', async () => {
  globalThis.fetch = async () => { throw new Error('connection refused'); };
  const r = await client.health();
  assert.equal(r.status, 'OFFLINE');
});

test('unavailable optional voice endpoints are reported without blocking caller workflows', async () => {
  globalThis.fetch = async () => { throw new TypeError('fetch failed'); };
  const results = await Promise.all([
    client.health(),
    client.engines(),
    client.styles(),
  ]);
  assert.deepEqual(results.map((result) => result.status), ['OFFLINE', 'OFFLINE', 'OFFLINE']);
  assert.ok(results.every((result) => typeof result.error === 'string' && result.error.length > 0));
});

test('health() returns ok when server responds 200', async () => {
  fakeFetch(200, { ok: true, status: 'ok', engines: ['xtts', 'edge'] });
  const r = await client.health();
  assert.equal(r.ok, true);
  assert.deepEqual(r.engines, ['xtts', 'edge']);
});

test('speak rejects empty text', async () => {
  fakeFetch(200, {});
  const r = await client.speak('   ');
  assert.equal(r.ok, false);
  assert.equal(r.status, 'INVALID_INPUT');
});

test('speak rejects invalid style', async () => {
  fakeFetch(200, {});
  const r = await client.speak('hello', 'nonexistent');
  assert.equal(r.ok, false);
  assert.equal(r.status, 'INVALID_INPUT');
});

test('speak posts a valid request and returns a playable Blob + engine headers', async () => {
  let request;
  const timingEvents = [];
  globalThis.fetch = async (url, options) => {
    request = { url, options };
    return {
      ok: true,
      status: 200,
      arrayBuffer: async () => new ArrayBuffer(9000),
      headers: new Headers({ 'X-Engine': 'edge', 'X-Voice-Clone': 'false' }),
    };
  };
  const r = await client.speak('Systems online.', 'warning', {
    onTiming: (event) => timingEvents.push(event),
  });
  assert.equal(r.ok, true);
  assert.equal(r.status, 'ok');
  assert.ok(r.audio instanceof Blob, 'audio should be a Blob');
  assert.equal(r.engine, 'edge');
  assert.equal(r.is_clone, false);
  assert.equal(request.url, 'http://127.0.0.1:8765/api/v1/speak');
  assert.equal(request.options.method, 'POST');
  assert.deepEqual(JSON.parse(request.options.body), { text: 'Systems online.', style: 'warning' });
  assert.deepEqual(timingEvents, ['request_started', 'audio_received']);
});

test('speak cancellation aborts the real speech request and reports CANCELLED', async () => {
  const controller = new AbortController();
  globalThis.fetch = async (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
  });
  const pending = client.speak('Stop this generation.', 'normal', { signal: controller.signal });
  controller.abort();
  const result = await pending;
  assert.equal(result.status, 'CANCELLED');
});

test('speak surfaces HTTP error body', async () => {
  fakeFetch(503, { error: 'engine unavailable' });
  const r = await client.speak('Systems online.');
  assert.equal(r.ok, false);
  assert.equal(r.status, 'HTTP_ERROR');
  assert.match(r.error, /engine unavailable/);
});

test('speak returns OFFLINE when voice server is unreachable', async () => {
  globalThis.fetch = async () => { throw new TypeError('fetch failed'); };
  const r = await client.speak('Systems online.');
  assert.equal(r.status, 'OFFLINE');
});

test('engines() is wrapped and returns OFFLINE on fetch failure', async () => {
  globalThis.fetch = async () => { throw new Error('nope'); };
  const r = await client.engines();
  assert.equal(r.status, 'OFFLINE');
});

test('styles() returns VALID_STYLES list on success', async () => {
  fakeFetch(200, { ok: true, styles: VALID_STYLES });
  const r = await client.styles();
  assert.equal(r.ok, true);
  assert.deepEqual(r.styles, VALID_STYLES);
});

test('class VALID_STYLES export matches config list', () => {
  const expected = ['normal','information','warning','critical','confirmation','question','error'];
  assert.deepEqual(VALID_STYLES, expected);
});

test('constructor respects custom host/port', async () => {
  const custom = new VoiceClient({ host: '10.0.0.1', port: 9999 });
  // Verify the url helper produces the right address (no network call).
  assert.equal(custom._url('health'), 'http://10.0.0.1:9999/api/v1/health');
});