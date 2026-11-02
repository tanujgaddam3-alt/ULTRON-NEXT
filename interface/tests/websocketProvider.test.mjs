import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { delay, loadStack } from './helpers.mjs';

const ctx = loadStack();
const B = ctx.ULTRON.bridge;
const WSProvider = ctx.ULTRON.providers.WebSocketUltronProvider;

after(() => {
  if (B.currentProvider && typeof B.currentProvider.disconnect === 'function') {
    B.currentProvider.disconnect();
  }
});

test('CRE script and source provider enforce configured limits', async () => {
  const provider = new WSProvider('ws://localhost:9/ultron/test');
  provider.authenticated = true;
  provider._request = async () => ({ payload: { accepted: true } });

  await assert.rejects(
    provider.requestCreScriptPreview('A'.repeat(20001), []),
    /20,000 Unicode characters/
  );
  await assert.rejects(
    provider.requestCreScriptPreview('Valid\u0000script', []),
    /no control characters/
  );
  provider._request = async (_action, payload) => {
    if (payload.script.length === 20000) throw new Error('exact-limit transport reached');
    throw new Error('unicode transport reached');
  };
  await assert.rejects(
    provider.requestCreScriptPreview('A'.repeat(20000), []),
    /exact-limit transport reached/
  );
  await assert.rejects(
    provider.requestCreScriptPreview('Café — 日本語 🌱\nSecond paragraph.', []),
    /unicode transport reached/
  );
  assert.equal('Café — 日本語 🌱\nSecond paragraph.'.length > 0, true);

  provider._request = async () => ({ payload: { accepted: true } });
  await provider.requestCreSourceVideo({
    videoBase64: 'YQ==',
    fileName: 'source.mp4',
    mediaType: 'video/mp4',
  });
  await assert.rejects(
    provider.requestCreSourceVideo({ videoBase64: '', fileName: 'source.mp4' }),
    /non-empty/
  );
  await assert.rejects(
    provider.requestCreSourceVideo({ videoBase64: 'YQ==', fileName: 'source.exe' }),
    /Unsupported source-video/
  );
  const folderRequests = [];
  provider._request = async (action, payload, timeout) => {
    folderRequests.push({ action, payload, timeout });
    return { payload: { accepted: true } };
  };
  await provider.scanCreImportFolder('/home/ultron/Videos/CRE_Import');
  await provider.registerCreImportVideo({
    fileId: 'a'.repeat(64),
    targetContext: 'youtube_shorts',
    accountId: 'channel-1',
    territory: 'US'
  });
  assert.deepEqual(folderRequests.map((request) => request.action), [
    'CRE_FOLDER_IMPORT_SCAN_REQUEST',
    'CRE_FOLDER_IMPORT_REGISTER_REQUEST'
  ]);
  assert.equal(Object.keys(folderRequests[0].payload).length, 0);
  assert.equal(folderRequests[1].payload.fileId, 'a'.repeat(64));
  assert.equal(folderRequests[1].timeout, 300000);
  await assert.rejects(provider.scanCreImportFolder(''), /authorized CRE import directory/);
  await assert.rejects(
    provider.scanCreImportFolder('/tmp/videos'),
    /Only \/home\/ultron\/Videos\/CRE_Import is authorized/
  );
  provider.authenticated = false;
  await assert.rejects(
    provider.scanCreImportFolder('/home/ultron/Videos/CRE_Import'),
    /authenticated operator session/
  );
  provider.authenticated = true;
  await assert.rejects(
    provider.registerCreImportVideo({
      fileId: '../outside',
      targetContext: 'youtube_shorts',
      accountId: 'channel-1',
      territory: 'US'
    }),
    /validated CRE import-folder selection/
  );
});

test('ws provider is never flagged as simulated (LIVE-only transport)', () => {
  const ws = new WSProvider('ws://localhost:9/ultron/test');
  assert.equal(ws.isSimulated, undefined);
  assert.ok(!ws.isSimulated);
});

test('private CRE render provider uses stable operation IDs for recovery', async () => {
  const provider = new WSProvider('ws://localhost:9/ultron/test');
  provider.authenticated = true;
  const requests = [];
  provider._request = async (action, payload, timeout) => {
    requests.push({ action, payload, timeout });
    return { status: 'PRIVATE_RENDER_OPERATION_AVAILABLE', payload: { state: 'queued' } };
  };

  await assert.rejects(
    provider.renderPrivateCreProject({ projectId: 'project', vertical: false }),
    /stable render operation ID/
  );
  await provider.renderPrivateCreProject({
    operationId: '123e4567-e89b-42d3-a456-426614174000',
    projectId: 'project',
    vertical: false
  });
  await provider.getPrivateCreRenderStatus('123e4567-e89b-42d3-a456-426614174000');
  await provider.getPrivateCreRenderArtifact('123e4567-e89b-42d3-a456-426614174000');

  assert.deepEqual(requests.map(({ action }) => action), [
    'CRE_PRIVATE_PROJECT_RENDER_REQUEST',
    'CRE_PRIVATE_RENDER_STATUS_REQUEST',
    'CRE_PRIVATE_RENDER_ARTIFACT_REQUEST'
  ]);
  assert.equal(requests[0].payload.operationId, '123e4567-e89b-42d3-a456-426614174000');
  assert.equal(requests[1].payload.operationId, requests[0].payload.operationId);
  assert.equal(requests[2].payload.operationId, requests[0].payload.operationId);
});

test('default provider connects to the local authentication helper', () => {
  const provider = new WSProvider();
  assert.equal(provider.endpointUrl, 'ws://127.0.0.1:8081/ultron/stream');
});

test('correlated requests classify oversized transport closes', async () => {
  const provider = new WSProvider('ws://127.0.0.1:8081/ultron/stream');
  provider.authenticated = true;
  const listeners = new Map();
  provider.socket = {
    readyState: 1,
    addEventListener(name, listener) {
      listeners.set(name, listener);
    },
    removeEventListener(name) {
      listeners.delete(name);
    },
    send() {},
  };

  const request = provider._request('CRE_SOURCE_VIDEO_REQUEST', { videoBase64: 'large' }, 1000);
  listeners.get('close')({ code: 1009, reason: 'message too big' });
  await assert.rejects(request, (error) => {
    assert.match(error.message, /exceeded the transport message-size limit/);
    assert.equal(error.transportClosed, true);
    assert.equal(error.closeCode, 1009);
    assert.equal(error.closeReason, 'message too big');
    return true;
  });
});

test('source-video and clip production use correlated authenticated CRE requests', async () => {
  const provider = new WSProvider('ws://127.0.0.1:8080/ultron/stream');
  provider.authenticated = true;
  const listeners = new Map();
  const sent = [];
  provider.socket = {
    readyState: 1,
    addEventListener(name, listener) {
      listeners.set(name, listener);
    },
    removeEventListener(name) {
      listeners.delete(name);
    },
    send(raw) {
      const request = JSON.parse(raw);
      sent.push(request);
      listeners.get('message')({
        data: JSON.stringify({
          action: `${request.action}_RESULT`,
          correlationId: request.correlationId,
          success: true,
          status: request.action === 'CRE_SOURCE_CLIP_REQUEST'
            ? 'CLIP_READY'
            : request.action === 'CRE_SOURCE_VIDEO_REQUEST'
              ? 'SOURCE_REGISTERED'
              : request.action === 'CRE_SOURCE_ASSET_STATUS_REQUEST'
                ? 'ASSET_STATUS_AVAILABLE'
                : request.action === 'CRE_SOURCE_RIGHTS_REQUEST'
                  ? 'RIGHTS_STEP_COMPLETED'
                  : 'HIGHLIGHTS_READY',
          payload: request.action === 'CRE_SOURCE_CLIP_REQUEST'
            ? {
              base64: 'Zm9v',
              mimeType: 'video/mp4',
              sha256: 'a'.repeat(64),
              width: 720,
              height: 1280,
              durationSeconds: 8,
              verification: 'PASSED'
            }
            : request.action === 'CRE_SOURCE_VIDEO_REQUEST'
              ? { asset: { assetId: 'asset-1', sha256: 'a'.repeat(64) } }
              : request.action === 'CRE_SOURCE_ASSET_STATUS_REQUEST'
                ? { assetId: 'asset-1', copyrightStatus: 'review_required', analysisRightsStatus: 'review_required' }
                : request.action === 'CRE_SOURCE_RIGHTS_REQUEST'
                  ? { step: 'read_evidence' }
                  : { asset: { assetId: 'asset-1' }, candidates: [] }
        })
      });
    }
  };

  const source = await provider.requestCreSourceVideo({ videoBase64: 'YQ==' });
  const resumed = await provider.resumeCreSourceVideo({
    assetId: 'asset-1',
    targetContext: 'youtube_shorts',
    accountId: 'channel-1',
    territory: 'US'
  });
  const rights = await provider.requestCreSourceRights({ step: 'read_evidence', evidenceId: 'evidence-1' });
  const assetStatus = await provider.checkCreSourceAssetStatus({
    assetId: 'asset-1',
    targetContext: 'youtube_shorts',
    accountId: 'channel-1',
    territory: 'US'
  });
  const clip = await provider.requestCreSourceClip({
    candidateId: 'candidate-1',
    highlightRunId: 'run-1'
  });

  assert.equal(source.status, 'SOURCE_REGISTERED');
  assert.equal(resumed.status, 'HIGHLIGHTS_READY');
  assert.equal(rights.status, 'RIGHTS_STEP_COMPLETED');
  assert.equal(assetStatus.status, 'ASSET_STATUS_AVAILABLE');
  assert.equal(clip.verification, 'PASSED');
  assert.equal(sent[0].action, 'CRE_SOURCE_VIDEO_REQUEST');
  assert.equal(sent[1].action, 'CRE_SOURCE_VIDEO_RESUME_REQUEST');
  assert.equal(sent[2].action, 'CRE_SOURCE_RIGHTS_REQUEST');
  assert.equal(sent[3].action, 'CRE_SOURCE_ASSET_STATUS_REQUEST');
  assert.equal(sent[4].action, 'CRE_SOURCE_CLIP_REQUEST');
  assert.ok(sent.every((request) => request.correlationId));
});

test('failed inventory refresh retains the last snapshot as stale, not current', async () => {
  const provider = new WSProvider('ws://127.0.0.1:8080/ultron/stream');
  const states = [];
  const listeners = new Set();
  let requestCount = 0;
  provider.subscribeSystemIntelligence((state) => states.push(state));
  provider.socket = {
    readyState: 1,
    addEventListener(name, listener) {
      if (name === 'message') listeners.add(listener);
    },
    removeEventListener(name, listener) {
      if (name === 'message') listeners.delete(listener);
    },
    send(raw) {
      const request = JSON.parse(raw);
      requestCount += 1;
      const response = requestCount === 1
        ? {
          action: 'SYSTEM_INTELLIGENCE_RESULT',
          correlationId: request.correlationId,
          success: true,
          payload: { generatedAt: '2026-10-27T00:00:00Z', observations: [] }
        }
        : {
          action: 'SYSTEM_INTELLIGENCE_RESULT',
          correlationId: request.correlationId,
          success: false,
          status: 'FAILED',
          error: 'Inventory refresh failed.'
        };
      const event = { data: JSON.stringify(response) };
      listeners.forEach((listener) => listener(event));
    }
  };

  const current = await provider.requestSystemIntelligence();
  assert.equal(provider.systemIntelligenceState.state, 'AVAILABLE');
  assert.equal(provider.systemIntelligenceState.data, current);
  await assert.rejects(provider.requestSystemIntelligence(), /Inventory refresh failed/);

  assert.equal(provider.systemIntelligenceState.state, 'STALE');
  assert.equal(provider.systemIntelligenceState.data, current);
  assert.equal(provider.systemIntelligenceState.message, 'Inventory refresh failed.');
  assert.ok(states.some((state) => state.state === 'STALE'));
});

test('without a WebSocket transport the provider degrades to OFFLINE, never LIVE', () => {
  const ws = new WSProvider('ws://localhost:9/ultron/test');
  B.connectProvider(ws);
  // The VM shim has no WebSocket ctor, so the guard flips straight to OFFLINE.
  assert.equal(B.getProvenance(), 'OFFLINE');
  assert.ok(B.getProvenance() !== 'LIVE');
});

test('dispatch through a disconnected ws provider is rejected', async () => {
  const ws = new WSProvider('ws://localhost:9/ultron/test');
  B.connectProvider(ws);
  await assert.rejects(
    () => B.dispatchCommand({ commandText: 'anything', actionVerb: 'analyze', parameters: {} }),
    /Cannot dispatch: live WebSocket is not connected/
  );
});

test('approve through a ws provider without an open socket is rejected', async () => {
  const ws = new WSProvider('ws://localhost:9/ultron/test');
  B.connectProvider(ws);
  await assert.rejects(
    () => B.approveOperation({ operationId: 'op-x' }),
    /Cannot dispatch: live WebSocket is not connected/
  );
});

test('reconcile through a ws provider without an open socket is rejected', async () => {
  const ws = new WSProvider('ws://localhost:9/ultron/test');
  B.connectProvider(ws);
  await assert.rejects(
    () => B.reconcileOperation({ operationId: 'op-x', requestFingerprint: 'fp-x' }),
    /Cannot dispatch: live WebSocket is not connected/
  );
});

test('script preview uses the correlated authenticated CRE request contract', async () => {
  const provider = new WSProvider('ws://127.0.0.1:8080/ultron/stream');
  const listeners = new Map();
  const sent = [];
  let manifestEvidenceStatus = 'VERIFIED';
  let sceneAssetState = 'GENERATED';
  let legacyVisualEvidenceMissing = false;
  provider.socket = {
    readyState: 1,
    send(raw) {
      const request = JSON.parse(raw);
      sent.push(request);
      listeners.get('message')({
        data: JSON.stringify({
          action: 'CRE_SCRIPT_PREVIEW_RESULT',
          correlationId: request.correlationId,
          success: true,
          payload: {
            scriptId: 'script-id',
            script: 'Original script.',
            scriptSha256: 'a'.repeat(64),
            preview: {
              base64: 'ZnR5cA==',
              mimeType: 'video/mp4',
              audioIncluded: true,
              sha256: 'b'.repeat(64),
              sizeBytes: 4
            },
            productionManifest: {
              workflow: 'LOCAL_NARRATED_ORIGINAL_SCRIPT_PREVIEW',
              sceneContractVersion: 2,
              scenePlannerVersion: 'deterministic-heuristic-v2',
              sceneContractSha256: 'f'.repeat(64),
              sceneVisualEvidenceVersion: legacyVisualEvidenceMissing ? undefined : 1,
              sceneRendererVersion: legacyVisualEvidenceMissing
                ? undefined
                : 'pillow-scene-composer-v2',
              sceneVisualManifestSha256: legacyVisualEvidenceMissing
                ? null
                : '7'.repeat(64),
              sceneVisualEvidence: {
                state: legacyVisualEvidenceMissing ? 'MISSING' : 'VERIFIED',
                statusScope: 'CURRENT_READBACK',
                lastCheckedAt: new Date().toISOString(),
                sceneContractSha256: 'f'.repeat(64),
                sceneVisualManifestSha256: legacyVisualEvidenceMissing
                  ? null
                  : '7'.repeat(64),
                rendererVersion: legacyVisualEvidenceMissing
                  ? null
                  : 'pillow-scene-composer-v2',
                failureReason: legacyVisualEvidenceMissing
                  ? 'VISUAL_EVIDENCE_NOT_RETAINED'
                  : null,
                nextAction: legacyVisualEvidenceMissing
                  ? 'Generate a new preview with durable scene visual evidence.'
                  : 'Review visuals editorially.',
                scenes: [{
                  sceneId: 'scene-01',
                  order: 1,
                  narrativeRole: 'EXPLANATION',
                  visualStrategy: 'CONCEPT_MAP_DIAGRAM',
                  sourceOffsets: { start: 0, endExclusive: 16 },
                  excerpt: 'Original script.',
                  excerptSha256: 'c'.repeat(64),
                  renderedComposition: legacyVisualEvidenceMissing
                    ? null
                    : 'EXPLANATORY_CONCEPT_MAP',
                  state: legacyVisualEvidenceMissing ? 'MISSING' : 'VERIFIED',
                  failureReason: legacyVisualEvidenceMissing
                    ? 'VISUAL_EVIDENCE_NOT_RETAINED'
                    : null,
                  asset: legacyVisualEvidenceMissing ? null : {
                    assetId: '9'.repeat(64),
                    format: 'PNG',
                    width: 720,
                    height: 1280,
                    byteSize: 4,
                    sha256: '8'.repeat(64),
                    rendererVersion: 'pillow-scene-composer-v2',
                    generatedAt: new Date().toISOString(),
                    validationStatus: 'TECHNICAL_CHECKS_PASSED'
                  }
                }]
              },
              qualityAssurance: {
                state: legacyVisualEvidenceMissing
                  ? 'SCENE_VISUAL_EVIDENCE_MISSING'
                  : 'TECHNICAL_CHECKS_PASSED'
              },
              audioEvidence: {
                status: manifestEvidenceStatus,
                renderTimeStatus: 'VERIFIED',
                statusScope: 'CURRENT_READBACK',
              },
              narration: {
                state: 'GENERATED_LOCALLY',
                voiceQuality: 'NOT_HUMAN_REVIEWED',
                intelligibility: 'NOT_ASSESSED',
                naturalness: 'NOT_ASSESSED',
                rmsDbfs: -20,
                peakDbfs: -6,
                sampleCount: 1000,
                clippedSamples: 0,
                clippedSampleRatio: 0,
                quietWindowRatio: 0.2,
                encodedAudioRmsDbfs: -20,
                sceneBoundaryGaps: []
              },
              video: {
                artifactSha256: 'b'.repeat(64),
                artifactBytes: 4,
                width: 720,
                height: 1280,
                videoCodec: 'h264',
                audioCodec: 'aac',
                durationSeconds: 12
              },
              scenes: [{
                sceneId: 'scene-01',
                order: 1,
                excerpt: 'Original script.',
                excerptSha256: 'c'.repeat(64),
                scriptOffsets: { start: 0, endExclusive: 16 },
                narrationRef: 'scene-01:narration',
                captionRef: 'scene-01:caption',
                assetState: legacyVisualEvidenceMissing
                  ? 'MISSING'
                  : sceneAssetState,
                technicalValidation: 'TECHNICAL_CHECKS_PASSED',
                renderedComposition: legacyVisualEvidenceMissing
                  ? null
                  : 'EXPLANATORY_CONCEPT_MAP',
                visualAsset: legacyVisualEvidenceMissing ? null : {
                  assetId: '9'.repeat(64),
                  format: 'PNG',
                  width: 720,
                  height: 1280,
                  byteSize: 4,
                  sha256: '8'.repeat(64),
                  rendererVersion: 'pillow-scene-composer-v2',
                  generatedAt: new Date().toISOString(),
                  validationStatus: 'TECHNICAL_CHECKS_PASSED',
                  sourceScriptSha256: 'a'.repeat(64),
                  sceneContractSha256: 'f'.repeat(64),
                  excerptSha256: 'c'.repeat(64),
                  visualStrategy: 'CONCEPT_MAP_DIAGRAM',
                  composition: 'EXPLANATORY_CONCEPT_MAP',
                  safeArea: {
                    leftPx: 42,
                    topPx: 222,
                    rightPx: 678,
                    bottomPx: 908,
                    captionBoundaryPx: 1060
                  },
                  dataBase64: 'iVBORw=='
                },
                sceneContract: {
                  sceneId: 'scene-01',
                  order: 1,
                  excerpt: 'Original script.',
                  excerptSha256: 'c'.repeat(64),
                  scriptOffsets: { start: 0, endExclusive: 16 },
                  narrativeRole: 'EXPLANATION',
                  keyMessage: 'Original script.',
                  keyMessageSource: 'SCRIPT_EXCERPT',
                  visualStrategy: 'CONCEPT_MAP_DIAGRAM',
                  rendererSupport: 'SUPPORTED',
                  visualInstructions: {
                    layout: 'CONCEPT_MAP_DIAGRAM',
                    labels: ['Original', 'script'],
                    relationship: 'EXTRACTED_SCRIPT_TERMS_ONLY',
                    sourceTextScope: 'EXACT_SCENE_EXCERPT'
                  },
                  narrationRef: 'scene-01:narration',
                  captionRef: 'scene-01:caption',
                  timing: null,
                  assetState: 'NOT_GENERATED',
                  technicalValidation: 'NOT_RUN',
                  plannerVersion: 'deterministic-heuristic-v2'
                },
                caption: {
                  text: 'Original script.',
                  scriptOffsets: { start: 0, endExclusive: 16 }
                }
              }]
            },
            review: {
              humanReviewState: 'PENDING',
              factualReviewState: 'REQUIRED',
              rightsState: 'NOT_EVALUATED',
              approvalState: 'NOT_GRANTED',
              publicationState: 'NOT_PUBLISHED',
              editorial: {
                state: 'PENDING',
                artifactSha256: 'b'.repeat(64)
              }
            },
            sceneVisualEvidence: {
              state: legacyVisualEvidenceMissing ? 'MISSING' : 'VERIFIED'
            },
            audioEvidence: {
              status: 'VERIFIED',
              evidenceId: 'd'.repeat(64),
              algorithmVersion: 'PCM_S16LE_MONO_24000_CLIP_32760_V1',
              createdAt: new Date().toISOString(),
              lastCheckedAt: new Date().toISOString(),
              lastVerifiedAt: new Date().toISOString(),
              sourceWavSha256: 'e'.repeat(64),
              sourceBytes: 2044,
              sourceBinding: 'VERIFIED',
              scriptBinding: 'VERIFIED',
              artifactBinding: 'VERIFIED',
              sampleCount: 1000,
              clippedSamples: 0,
              clippedSampleRatio: 0,
              clippingThreshold: 0.001,
              legacy: false,
              failureReason: null,
              nextAction: 'No evidence action required.'
            }
          }
        })
      });
    },
    addEventListener(name, listener) { listeners.set(name, listener); },
    removeEventListener(name) { listeners.delete(name); }
  };

  const references = [{ label: 'Reference 1', url: 'https://example.org/source' }];
  const result = await provider.requestCreScriptPreview('Original script.', references);
  assert.equal(sent[0].action, 'CRE_SCRIPT_PREVIEW_REQUEST');
  assert.deepEqual(sent[0].payload, {
    script: 'Original script.',
    references
  });
  assert.equal(result.review.humanReviewState, 'PENDING');
  assert.equal(result.preview.audioIncluded, true);
  legacyVisualEvidenceMissing = true;
  const legacyResult = await provider.requestCreScriptPreview(
    'Original script.',
    references
  );
  assert.equal(
    legacyResult.productionManifest.sceneVisualEvidence.state,
    'MISSING'
  );
  legacyVisualEvidenceMissing = false;
  sceneAssetState = 'NOT_GENERATED';
  await assert.rejects(
    provider.requestCreScriptPreview('Original script.', references),
    /response is invalid/i,
  );
  sceneAssetState = 'GENERATED';
  manifestEvidenceStatus = 'INTEGRITY_FAILURE';
  await assert.rejects(
    provider.requestCreScriptPreview('Original script.', references),
    /response is invalid/i,
  );
  assert.equal(listeners.size, 0);
});

test('script preview review uses the correlated authenticated CRE action', async () => {
  const provider = new WSProvider('ws://127.0.0.1:8080/ultron/stream');
  const listeners = new Map();
  const sent = [];
  const reviewInput = {
    scriptId: 'script-id',
    artifactSha256: 'b'.repeat(64),
    editorialVerdict: 'READY_FOR_FACTUAL_REVIEW',
    narrationAssessment: 'INTELLIGIBLE',
    pacingAssessment: 'APPROPRIATE',
    captionAssessment: 'SYNCHRONIZED',
    sceneAssessment: 'COHERENT',
    factualReviewAttested: false,
    notes: 'Reviewed the local preview.'
  };
  provider.socket = {
    readyState: 1,
    send(raw) {
      const request = JSON.parse(raw);
      sent.push(request);
      listeners.get('message')({
        data: JSON.stringify({
          action: 'CRE_SCRIPT_PREVIEW_REVIEW_RESULT',
          correlationId: request.correlationId,
          success: true,
          payload: {
            scriptId: 'script-id',
            previewSha256: 'b'.repeat(64),
            review: {
              humanReviewState: 'RECORDED',
              rightsState: 'NOT_EVALUATED',
              approvalState: 'NOT_GRANTED',
              publicationState: 'NOT_PUBLISHED',
              editorial: {
                state: 'RECORDED',
                artifactSha256: 'b'.repeat(64)
              }
            }
          }
        })
      });
    },
    addEventListener(name, listener) { listeners.set(name, listener); },
    removeEventListener(name) { listeners.delete(name); }
  };

  const result = await provider.requestCreScriptPreviewReview(reviewInput);
  assert.equal(sent[0].action, 'CRE_SCRIPT_PREVIEW_REVIEW_REQUEST');
  assert.deepEqual(sent[0].payload, reviewInput);
  assert.equal(result.review.approvalState, 'NOT_GRANTED');
  assert.equal(result.review.rightsState, 'NOT_EVALUATED');
  assert.equal(listeners.size, 0);
});

test('chat request uses the authenticated WebSocket action and returns its correlated result', async () => {
  const provider = new WSProvider('ws://127.0.0.1:8080/ultron/stream');
  provider.authenticated = true;
  const sent = [];
  const listeners = new Map();
  provider.socket = {
    readyState: 1,
    send(raw) {
      const request = JSON.parse(raw);
      sent.push(request);
      const result = {
        action: 'CHAT_RESULT',
        correlationId: request.correlationId,
        success: true,
        response: 'Test-only response from the gateway'
      };
      listeners.get('message')({ data: JSON.stringify(result) });
    },
    addEventListener(name, listener) { listeners.set(name, listener); },
    removeEventListener(name) { listeners.delete(name); }
  };

  const result = await provider.handleChat('Hello ULTRON.');
  assert.equal(sent[0].action, 'CHAT_MESSAGE');
  assert.deepEqual(sent[0].payload, { text: 'Hello ULTRON.' });
  assert.equal(result.response, 'Test-only response from the gateway');
  assert.equal(listeners.size, 0, 'correlation listeners must be removed after completion');
});

test('chat request rejects when the WebSocket is not connected', async () => {
  const provider = new WSProvider('ws://127.0.0.1:8080/ultron/stream');
  await assert.rejects(() => provider.handleChat('hello'), /not connected/);
});

test('disconnect() is idempotent and safe without a socket', () => {
  const ws = new WSProvider('ws://localhost:9/ultron/test');
  B.connectProvider(ws);
  assert.doesNotThrow(() => ws.disconnect());
  assert.doesNotThrow(() => ws.disconnect());
});

test('authenticated transport sends credentials before subscription and LIVE status', async () => {
  const sent = [];
  const provenance = [];
  class AuthenticatedSocket {
    constructor() {
      this.readyState = 0;
      this.listeners = {};
      setTimeout(() => {
        this.readyState = 1;
        this.onopen();
        this.onmessage({
          data: JSON.stringify({
            type: 'connected',
            authenticationRequired: true
          })
        });
      }, 0);
    }
    send(raw) {
      const message = JSON.parse(raw);
      sent.push(message);
      if (message.action === 'AUTHENTICATE') {
        this.onmessage({
          data: JSON.stringify({
            action: 'AUTHENTICATION_RESULT',
            status: 'AUTHENTICATED',
            success: true
          })
        });
      } else if (message.action === 'SUBSCRIBE') {
        this.onmessage({
          data: JSON.stringify({ action: 'SUBSCRIBE_ACK', success: true })
        });
      } else if (message.action === 'CRE_STATUS_REQUEST') {
        const response = {
          data: JSON.stringify({
            action: 'CRE_STATUS_RESULT',
            correlationId: message.correlationId,
            success: true,
            status: 'AVAILABLE',
            payload: {
              status: 'AVAILABLE',
              generatedAt: new Date().toISOString(),
              counts: {
                total: 0,
                processing: {},
                rendering: {},
                publishing: {},
                pendingApprovals: 0
              },
              jobs: [],
              lifecycle: [],
              events: [],
              scriptPreviews: {
                state: 'AVAILABLE',
                generatedAt: new Date().toISOString(),
                items: []
              },
              services: {
                store: 'AVAILABLE',
                rights: 'AVAILABLE',
                processing: 'NOT_CONFIGURED',
                rendering: 'NOT_CONFIGURED',
                publishing: 'MOCK_ONLY_NOT_CONFIGURED'
              },
              publishing: {
                mode: 'MOCK_ONLY',
                serviceConfigured: false,
                killSwitchEnabled: false
              },
              analytics: { available: false, reason: 'NOT_INSTRUMENTED' }
            }
          })
        };
        this.onmessage(response);
        if (this.listeners.message) this.listeners.message(response);
      } else if (message.action === 'SYSTEM_INTELLIGENCE_REQUEST') {
        const response = {
          data: JSON.stringify({
            action: 'SYSTEM_INTELLIGENCE_RESULT',
            correlationId: message.correlationId,
            success: true,
            status: 'VERIFIED',
            payload: { observations: [], generatedAt: new Date().toISOString() }
          })
        };
        this.onmessage(response);
        if (this.listeners.message) this.listeners.message(response);
      }
    }
    addEventListener(name, listener) {
      this.listeners[name] = listener;
    }
    removeEventListener(name) {
      delete this.listeners[name];
    }
    close() {
      this.readyState = 3;
      if (this.onclose) this.onclose();
    }
  }

  ctx.WebSocket = AuthenticatedSocket;
  const provider = new WSProvider(
    'ws://127.0.0.1:8080/ultron/stream',
    async () => 'test-only-token'
  );
  provider.connect({
    setProvenance(value) { provenance.push(value); },
    publish() {}
  });
  await delay(20);

  assert.deepEqual(
    sent.map((message) => message.action),
    ['AUTHENTICATE', 'SUBSCRIBE', 'CRE_STATUS_REQUEST', 'SYSTEM_INTELLIGENCE_REQUEST']
  );
  assert.equal(provenance.at(-1), 'LIVE');
  assert.equal(provenance.filter((value) => value === 'LIVE').length, 1);
  assert.equal(provider.transportConnected, true);
  assert.equal(provider.authenticated, true);
  assert.equal(provider.creStatusState.state, 'AVAILABLE');
  assert.equal(provider.systemIntelligenceState.state, 'AVAILABLE');
  assert.equal(provider.authenticationError, null);
  assert.equal(provider.authToken, undefined);
  provider.disconnect();
});

test('provider preserves the helper distinction between wrong password and lockout', async () => {
  class LockedSocket {
    constructor() {
      this.readyState = 0;
      setTimeout(() => {
        this.readyState = 1;
        this.onopen();
        this.onmessage({ data: JSON.stringify({ type: 'connected', authenticationRequired: true }) });
      }, 0);
    }
    send() {
      this.onmessage({ data: JSON.stringify({
        action: 'AUTHENTICATION_RESULT',
        success: false,
        status: 'AUTHENTICATION_LOCKED',
        error: 'Local unlock is temporarily locked.',
        retryAfterSeconds: 30
      }) });
    }
    close() { this.readyState = 3; }
  }

  ctx.WebSocket = LockedSocket;
  const provider = new WSProvider('ws://127.0.0.1:8081/ultron/stream', async () => 'test-only-passphrase');
  provider.connect({ setProvenance() {}, publish() {} });
  await delay(20);

  assert.equal(provider.authenticationStatus, 'AUTHENTICATION_LOCKED');
  assert.match(provider.authenticationError, /30 seconds/);
  provider.disconnect();
});

test('cancelling operator authentication is not reported as a rejected passphrase', async () => {
  const sent = [];
  class CancelledSocket {
    constructor() {
      this.readyState = 0;
      setTimeout(() => {
        this.readyState = 1;
        this.onopen();
        this.onmessage({ data: JSON.stringify({ type: 'connected', authenticationRequired: true }) });
      }, 0);
    }
    send(raw) { sent.push(JSON.parse(raw)); }
    close() { this.readyState = 3; }
  }

  ctx.WebSocket = CancelledSocket;
  const provider = new WSProvider('ws://127.0.0.1:8081/ultron/stream', () => {
    const error = new Error('Operator authentication was cancelled');
    error.code = 'AUTHENTICATION_CANCELLED';
    return Promise.reject(error);
  });
  provider.connect({ setProvenance() {}, publish() {} });
  await delay(20);

  assert.equal(provider.authenticationStatus, 'AUTHENTICATION_CANCELLED');
  assert.match(provider.authenticationError, /before a credential was sent/);
  assert.equal(provider.authenticationFailed, true);
  assert.deepEqual(sent, []);
  provider.disconnect();
});

test('a telemetry subscription without operator authentication does not enable chat', async () => {
  const sent = [];
  const provenance = [];
  class UnauthenticatedSocket {
    constructor() {
      this.readyState = 0;
      setTimeout(() => {
        this.readyState = 1;
        this.onopen();
        this.onmessage({
          data: JSON.stringify({ type: 'connected', authenticationRequired: false })
        });
      }, 0);
    }
    send(raw) {
      const message = JSON.parse(raw);
      sent.push(message);
      if (message.action === 'SUBSCRIBE') {
        this.onmessage({ data: JSON.stringify({ action: 'SUBSCRIBE_ACK', success: true }) });
      }
    }
    addEventListener() {}
    removeEventListener() {}
    close() { this.readyState = 3; }
  }

  ctx.WebSocket = UnauthenticatedSocket;
  const provider = new WSProvider('ws://127.0.0.1:8080/ultron/stream');
  provider.connect({
    setProvenance(value) { provenance.push(value); },
    publish() {}
  });
  await delay(20);

  assert.deepEqual(sent.map((message) => message.action), ['SUBSCRIBE']);
  assert.equal(provenance.at(-1), 'LIVE');
  assert.equal(provider.authenticated, false);
  await assert.rejects(
    () => provider.handleChat('This must not be sent'),
    /authenticated operator session/
  );
  assert.deepEqual(sent.map((message) => message.action), ['SUBSCRIBE']);
  provider.disconnect();
});

test('the console requests financial telemetry only for an authenticated session', () => {
  const provider = new WSProvider();
  const sent = [];
  provider.socket = {
    readyState: 1,
    send(raw) { sent.push(JSON.parse(raw)); }
  };
  provider._subscribe();
  const publicChannels = [
    'cognitiveState',
    'vitalMatrix',
    'device',
    'command',
    'operation',
    'runtimeEvents',
    'world'
  ];
  assert.deepEqual(sent[0].channels, publicChannels);

  provider.authenticated = true;
  provider._subscribe();
  assert.deepEqual(sent[1].channels, [...publicChannels, 'omega', 'trading']);
});