import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const sourceRoot = new URL('../', import.meta.url);
const browser = { ULTRON: {} };
const context = vm.createContext({
  window: browser,
  console: { error() {}, warn() {}, log() {} },
  setInterval,
  clearInterval,
  setTimeout,
});
vm.runInContext(
  fs.readFileSync(new URL('contracts/contracts.js', sourceRoot), 'utf8'),
  context,
);
vm.runInContext(
  fs.readFileSync(new URL('services/providers/websocketProvider.js', sourceRoot), 'utf8'),
  context,
);

const status = {
  status: 'AVAILABLE',
  generatedAt: '2026-10-24T10:00:00Z',
  counts: {
    total: 0,
    processing: {},
    rendering: {},
    publishing: {},
    pendingApprovals: 0,
  },
  jobs: [],
  lifecycle: [],
  events: [],
  scriptPreviews: {
    state: 'AVAILABLE',
    generatedAt: '2026-10-24T10:00:00Z',
    items: [],
  },
  services: {
    store: 'AVAILABLE',
    rights: 'AVAILABLE',
    processing: 'NOT_CONFIGURED',
    rendering: 'NOT_CONFIGURED',
    publishing: 'MOCK_ONLY_NOT_CONFIGURED',
  },
  publishing: {
    mode: 'MOCK_ONLY',
    serviceConfigured: false,
    killSwitchEnabled: false,
  },
  analytics: {
    available: false,
    reason: 'NOT_INSTRUMENTED',
  },
};

test('CRE response contract accepts bounded real-status shape and rejects malformed rows', () => {
  const validate = browser.ULTRON.contracts.validators.validateCreStatus;
  assert.equal(validate(status), true);
  assert.throws(() => validate({
    ...status,
    jobs: [{ id: 'job', state: 'done' }],
  }), /Invalid CRE job summary/);
  assert.throws(() => validate({
    ...status,
    jobs: Array.from({ length: 101 }, () => ({
      id: 'job', type: 'processing', state: 'done',
      createdAt: 'now', updatedAt: 'now',
    })),
  }), /bounded result limit/);
  assert.throws(() => validate({
    ...status,
    scriptPreviews: { state: 'AVAILABLE', generatedAt: 'now', items: [{
      scriptId: 'id',
      scriptSha256: 'a'.repeat(64),
      artifactSha256: 'b'.repeat(64),
      previewState: 'PREVIEW_READY',
      createdAt: 'now',
      audioEvidence: { status: 'VERIFIED' },
    }] },
  }), /Invalid CRE preview audio-evidence record/);
});

test('CRE status contract validates persisted scene visual metadata and negative states', () => {
  const validScene = {
    sceneId: 'scene-01',
    order: 1,
    narrativeRole: 'EXPLANATION',
    visualStrategy: 'CONCEPT_MAP_DIAGRAM',
    sourceOffsets: { start: 0, endExclusive: 12 },
    excerpt: 'Source text.',
    excerptSha256: 'b'.repeat(64),
    renderedComposition: 'EXPLANATORY_CONCEPT_MAP',
    state: 'VERIFIED',
    failureReason: null,
    asset: {
      assetId: 'c'.repeat(64),
      format: 'PNG',
      width: 720,
      height: 1280,
      byteSize: 1024,
      sha256: 'd'.repeat(64),
      rendererVersion: 'pillow-scene-composer-v2',
      generatedAt: '2026-10-26T10:00:00Z',
      validationStatus: 'TECHNICAL_CHECKS_PASSED',
    },
  };
  const verified = {
    state: 'VERIFIED',
    statusScope: 'CURRENT_READBACK',
    lastCheckedAt: '2026-10-26T10:00:00Z',
    sceneContractSha256: 'e'.repeat(64),
    sceneVisualManifestSha256: 'f'.repeat(64),
    rendererVersion: 'pillow-scene-composer-v2',
    failureReason: null,
    nextAction: 'Review the visuals.',
    scenes: [validScene],
  };
  const preview = {
    scriptId: 'script-id',
    scriptSha256: '1'.repeat(64),
    artifactSha256: '2'.repeat(64),
    previewState: 'PREVIEW_READY',
    createdAt: '2026-10-26T10:00:00Z',
    audioEvidence: {
      status: 'VERIFIED',
      legacy: false,
      clippingThreshold: 0.001,
      sourceBinding: 'VERIFIED',
      scriptBinding: 'VERIFIED',
      artifactBinding: 'VERIFIED',
      evidenceId: '3'.repeat(64),
      algorithmVersion: 'PCM_S16LE_MONO_24000_CLIP_32760_V1',
      createdAt: '2026-10-26T10:00:00Z',
      lastCheckedAt: '2026-10-26T10:00:00Z',
      lastVerifiedAt: '2026-10-26T10:00:00Z',
      sourceWavSha256: '4'.repeat(64),
      sourceBytes: 2044,
      failureReason: null,
      nextAction: 'No action required.',
      sampleCount: 1000,
      clippedSamples: 0,
      clippedSampleRatio: 0,
    },
    sceneVisualEvidence: verified,
  };
  const validate = browser.ULTRON.contracts.validators.validateCreStatus;
  assert.equal(validate({
    ...status,
    scriptPreviews: {
      state: 'AVAILABLE',
      generatedAt: status.generatedAt,
      items: [preview],
    },
  }), true);

  for (const state of ['MISSING', 'INTEGRITY_FAILURE']) {
    const unavailable = {
      ...verified,
      state,
      statusScope: 'CURRENT_READBACK',
      sceneVisualManifestSha256: null,
      failureReason: `SCENE_${state}`,
      scenes: [{
        ...validScene,
        state,
        failureReason: `SCENE_${state}`,
        asset: null,
      }],
    };
    assert.equal(validate({
      ...status,
      scriptPreviews: {
        state: 'AVAILABLE',
        generatedAt: status.generatedAt,
        items: [{ ...preview, sceneVisualEvidence: unavailable }],
      },
    }), true);
  }

  assert.throws(() => validate({
    ...status,
    scriptPreviews: {
      state: 'AVAILABLE',
      generatedAt: status.generatedAt,
      items: [{
        ...preview,
        sceneVisualEvidence: {
          ...verified,
          scenes: [{
            ...validScene,
            asset: { ...validScene.asset, width: 999 },
          }],
        },
      }],
    },
  }), /Invalid verified CRE scene visual evidence/);
});

test('CRE provider reports disconnected request failure without claiming availability', async () => {
  const Provider = browser.ULTRON.providers.WebSocketUltronProvider;
  const provider = new Provider('ws://127.0.0.1:8080/ultron/stream');
  const observed = [];
  provider.subscribeCreStatus((state) => observed.push(state.state));

  await assert.rejects(provider.requestCreStatus(), /not connected/);

  assert.deepEqual(observed, ['LOADING', 'LOADING', 'UNAVAILABLE']);
  assert.equal(provider.creStatusSnapshot, null);
});

test('CRE provider retains last response as stale and supports listener cleanup', () => {
  const Provider = browser.ULTRON.providers.WebSocketUltronProvider;
  const provider = new Provider('ws://127.0.0.1:8080/ultron/stream');
  const observed = [];
  const unsubscribe = provider.subscribeCreStatus((state) => observed.push(state.state));

  provider._setCreStatus('AVAILABLE', status);
  provider._setCreStatus('STALE', status);
  unsubscribe();
  provider._setCreStatus('DISCONNECTED', status);

  assert.deepEqual(observed, ['LOADING', 'AVAILABLE', 'STALE']);
  assert.equal(provider.creStatusState.data, status);
});
