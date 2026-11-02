import { test } from 'node:test';
import assert from 'node:assert/strict';
import { VoicePlayback, splitSentences } from '../services/voicePlayback.js';

function audioResult() {
  return {
    ok: true,
    status: 'ok',
    audio: new Blob([new Uint8Array(32)], { type: 'audio/wav' }),
    engine: 'test-engine',
    is_clone: false,
  };
}

class TestAudio {
  constructor(url) {
    this.url = url;
    this.listeners = new Map();
    this.paused = 0;
    this.removedSrc = 0;
    this.loaded = 0;
  }

  addEventListener(name, callback) {
    const listeners = this.listeners.get(name) || [];
    listeners.push(callback);
    this.listeners.set(name, listeners);
  }
  async play() { this.emit('playing'); }
  emit(name) {
    for (const callback of this.listeners.get(name) || []) callback();
    this.listeners.delete(name);
  }
  pause() { this.paused += 1; }
  removeAttribute(name) { if (name === 'src') this.removedSrc += 1; }
  load() { this.loaded += 1; }
}

test('repeated playback stops the previous audio and revokes every object URL', async () => {
  const created = [];
  const revoked = [];
  const audios = [];
  let speakCalls = 0;
  const playback = new VoicePlayback({
    client: { async speak() { speakCalls += 1; return audioResult(); } },
    AudioCtor: class extends TestAudio {
      constructor(url) { super(url); audios.push(this); }
    },
    urlApi: {
      createObjectURL() { const url = `blob:test-${created.length + 1}`; created.push(url); return url; },
      revokeObjectURL(url) { revoked.push(url); },
    },
  });

  const first = await playback.play('first response');
  const second = await playback.play('second response');
  assert.equal(first.status, 'playing');
  assert.equal(second.status, 'playing');
  assert.equal('_current' in first, false);
  assert.equal(audios[0].paused, 1);
  assert.equal(audios[0].removedSrc, 1);
  assert.equal(audios[0].loaded, 1);
  assert.deepEqual(revoked, ['blob:test-1']);
  assert.equal(speakCalls, 2);

  assert.equal(playback.stop(), true);
  assert.equal(audios[1].paused, 1);
  assert.equal(audios[1].removedSrc, 1);
  assert.equal(audios[1].loaded, 1);
  assert.deepEqual(revoked, ['blob:test-1', 'blob:test-2']);
  assert.equal(playback.stop(), false);
});

test('stop during speech generation aborts and never creates an audio URL', async () => {
  let resolveSpeech;
  let observedSignal;
  const revoked = [];
  const playback = new VoicePlayback({
    client: {
      speak(_text, _style, { signal }) {
        observedSignal = signal;
        return new Promise((resolve) => { resolveSpeech = resolve; });
      },
    },
    AudioCtor: TestAudio,
    urlApi: {
      createObjectURL() { assert.fail('cancelled generation must not create an audio URL'); },
      revokeObjectURL(url) { revoked.push(url); },
    },
  });

  const pending = playback.play('cancel this');
  await Promise.resolve();
  assert.equal(playback.stop(), true);
  assert.equal(observedSignal.aborted, true);
  resolveSpeech({ ok: false, status: 'CANCELLED', error: 'cancelled' });
  assert.equal((await pending).status, 'CANCELLED');
  assert.deepEqual(revoked, []);
});

test('state callback exceptions do not break generation or playback control', async () => {
  const playback = new VoicePlayback({
    client: { async speak() { return audioResult(); } },
    onState: () => { throw new Error('state callback failed'); },
    AudioCtor: TestAudio,
    urlApi: {
      createObjectURL() { return 'blob:test-safe'; },
      revokeObjectURL() {},
    },
  });

  const result = await playback.play('should keep playing even if the UI callback explodes');
  assert.equal(result.status, 'playing');
  assert.equal(playback.stop(), true);
});

test('voice service failure does not create audio or report playback success', async () => {
  const states = [];
  const playback = new VoicePlayback({
    client: { async speak() { return { ok: false, status: 'OFFLINE', error: 'connection refused' }; } },
    onState: (state) => states.push(state),
    AudioCtor: TestAudio,
    urlApi: {
      createObjectURL() { assert.fail('failed speech generation must not create audio'); },
      revokeObjectURL() {},
    },
  });
  const result = await playback.play('text chat still exists');
  assert.equal(result.status, 'OFFLINE');
  assert.equal(states.at(-1).status, 'error');
});

test('sentence segmentation handles abbreviations, punctuation, and an unfinished final sentence', () => {
  assert.deepEqual(
    splitSentences('Dr. Smith arrived. We met at 3 p.m. tomorrow. Next! Final thought'),
    ['Dr. Smith arrived.', 'We met at 3 p.m. tomorrow.', 'Next!', 'Final thought'],
  );
  assert.deepEqual(splitSentences('One sentence'), ['One sentence']);
  assert.deepEqual(splitSentences('  '), []);
});

test('short unpunctuated text uses one speech request', async () => {
  const requests = [];
  const playback = new VoicePlayback({
    client: {
      async speak(text) { requests.push(text); return audioResult(); },
    },
    AudioCtor: TestAudio,
    urlApi: { createObjectURL: () => 'blob:short', revokeObjectURL() {} },
  });

  const result = await playback.playSequence('A short response without punctuation');
  assert.equal(result.ok, true);
  assert.deepEqual(requests, ['A short response without punctuation']);
  playback.stop(false);
});

test('sentence playback synthesizes and plays in order without overlapping audio', async () => {
  const requests = [];
  const audios = [];
  const states = [];
  const playback = new VoicePlayback({
    client: {
      async speak(text) {
        requests.push(text);
        return audioResult();
      },
    },
    onState: (state) => states.push(state),
    AudioCtor: class extends TestAudio {
      constructor(url) { super(url); audios.push(this); }
    },
    urlApi: { createObjectURL: () => 'blob:queue', revokeObjectURL() {} },
  });

  const result = await playback.playSequence('First sentence. Second sentence! Third');
  assert.equal(result.ok, true);
  assert.deepEqual(requests, ['First sentence.']);
  assert.equal(audios.length, 1);

  audios[0].emit('ended');
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(requests, ['First sentence.', 'Second sentence!']);
  assert.equal(audios.length, 2);
  audios[1].emit('ended');
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(requests, ['First sentence.', 'Second sentence!', 'Third']);
  assert.equal(audios.length, 3);
  audios[2].emit('ended');
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(states.at(-1).status, 'finished');
  assert.equal(playback.current, null);
});

test('cancelling during sentence synthesis prevents queued requests and playback', async () => {
  let resolveSpeech;
  const requests = [];
  const playback = new VoicePlayback({
    client: {
      speak(text) {
        requests.push(text);
        return new Promise((resolve) => { resolveSpeech = resolve; });
      },
    },
    AudioCtor: TestAudio,
    urlApi: {
      createObjectURL() { assert.fail('cancelled synthesis must not create audio'); },
      revokeObjectURL() {},
    },
  });

  const pending = playback.playSequence('First sentence. Second sentence.');
  await Promise.resolve();
  assert.equal(playback.stop(), true);
  resolveSpeech({ ok: false, status: 'CANCELLED', error: 'cancelled' });
  assert.equal((await pending).status, 'CANCELLED');
  assert.deepEqual(requests, ['First sentence.']);
});

test('cancelling during playback prevents the next sentence from being synthesized', async () => {
  const requests = [];
  const audios = [];
  const playback = new VoicePlayback({
    client: {
      async speak(text) { requests.push(text); return audioResult(); },
    },
    AudioCtor: class extends TestAudio {
      constructor(url) { super(url); audios.push(this); }
    },
    urlApi: { createObjectURL: () => 'blob:queue', revokeObjectURL() {} },
  });

  await playback.playSequence('First sentence. Second sentence.');
  assert.equal(playback.stop(), true);
  audios[0].emit('ended');
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(requests, ['First sentence.']);
  assert.equal(audios.length, 1);
});

test('first chunk failure falls back to the full response before any audio starts', async () => {
  const requests = [];
  const states = [];
  const playback = new VoicePlayback({
    client: {
      async speak(text) {
        requests.push(text);
        return requests.length === 1
          ? { ok: false, status: 'OFFLINE', error: 'temporary failure' }
          : audioResult();
      },
    },
    onState: (state) => states.push(state),
    AudioCtor: TestAudio,
    urlApi: { createObjectURL: () => 'blob:fallback', revokeObjectURL() {} },
  });

  const result = await playback.playSequence('First sentence. Second sentence.');
  assert.equal(result.ok, true);
  assert.deepEqual(requests, ['First sentence.', 'First sentence. Second sentence.']);
  assert.equal(states.at(-1).status, 'playing');
});

test('a later chunk failure is visible and never replays already spoken text', async () => {
  const requests = [];
  const audios = [];
  const states = [];
  const playback = new VoicePlayback({
    client: {
      async speak(text) {
        requests.push(text);
        return requests.length === 1
          ? audioResult()
          : { ok: false, status: 'OFFLINE', error: 'second chunk failed' };
      },
    },
    onState: (state) => states.push(state),
    AudioCtor: class extends TestAudio {
      constructor(url) { super(url); audios.push(this); }
    },
    urlApi: { createObjectURL: () => 'blob:partial', revokeObjectURL() {} },
  });

  const result = await playback.playSequence('First sentence. Second sentence.');
  assert.equal(result.ok, true);
  audios[0].emit('ended');
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(requests, ['First sentence.', 'Second sentence.']);
  assert.equal(states.at(-1).status, 'error');
  assert.match(states.at(-1).message, /second chunk failed/);
  assert.equal(playback.current, null);
  assert.equal(playback.stop(), false);
});
