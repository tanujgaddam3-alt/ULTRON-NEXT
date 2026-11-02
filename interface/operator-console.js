import { eventFilterOptions, filterRuntimeEvents, runtimeEventTime } from './services/runtimeEvents.js';
import { renderMarkdownInto } from './services/markdown.js';
import { summarizeCreBusinessStatus } from './services/creBusinessMetrics.js';
import { renderCreAudioEvidence } from './services/renderCreAudioEvidence.js';
import {
  isCrePlanEligibleForRevision,
  isCreRecoveredOperationEligibleForRevision,
} from './services/creRevisionEligibility.js';

const bridge = window.ULTRON?.bridge;
const Provider = window.ULTRON?.providers?.WebSocketUltronProvider;
const AUTH_MODE = new URLSearchParams(window.location.search).get('auth') === 'token' ? 'token' : 'local';
const voiceTimingDiagnostics = new URLSearchParams(window.location.search).get('voice-timing') === '1';
const CHANNELS = [
  ['cognitiveState', 'Runtime'],
  ['vitalMatrix', 'Runtime summary'],
  ['world', 'System facts'],
  ['device', 'Host capabilities'],
  ['command', 'Command center'],
  ['operation', 'Operations'],
  ['runtimeEvents', 'Runtime events'],
  ['omega', 'OMEGA integration'],
  ['trading', 'Financial trading'],
];
const channelData = new Map();
const channelTimes = new Map();
const integrityIssues = [];
const chatHistory = document.getElementById('chatHistory');
const chatInput = document.getElementById('chatInput');
const chatForm = document.getElementById('chatForm');
const voiceEnabled = document.getElementById('voiceEnabled');
const voiceAuto = document.getElementById('voiceAuto');
const connectionDot = document.getElementById('connectionDot');
const connectionLabel = document.getElementById('connectionLabel');
const authLabel = document.getElementById('authLabel');
const inspector = document.getElementById('inspector');
const inspectorButton = document.getElementById('inspectorButton');
const backdrop = document.getElementById('inspectorBackdrop');
const approvalDialog = document.getElementById('approvalDialog');
let voicePlayback = null;
let voiceClient = null;
let currentApproval = null;
let chatPending = false;
let provider = null;
let voiceHealthResult = null;
let voiceHealthCheckedAt = null;
let previousProvenance = null;
let voiceTimingSequence = 0;
let creStatusState = { state: 'LOADING', data: null, message: null };
let systemIntelligenceState = { state: 'LOADING', data: null, message: null };
let creScriptPreviewUrl = null;
let creScriptPreviewResult = null;
let creSourceWorkflowResult = null;
let creSourceAssets = [];
let creSourceClipUrl = null;
let creSourceEvidenceUrl = null;
let crePrivateProjectId = null;
let crePrivateRenderOperationId = null;
let crePrivateRevisionEligible = false;
let crePrivateProjectOutputUrl = null;
let creRecoveryInProgress = false;
let creUploadOutcomeUnknown = false;
let creUploadRetryArmed = false;
const creRegisteredSourceFiles = [];
const creRegisteredSourceAssets = [];
const CRE_PRIVATE_RENDER_SESSION_KEY = 'ultron.cre.private-render.v1';

function readSavedCrePrivateRender() {
  const saved = sessionStorage.getItem(CRE_PRIVATE_RENDER_SESSION_KEY);
  if (!saved) return null;
  const record = JSON.parse(saved);
  if (
    !record ||
    typeof record.projectId !== 'string' ||
    (record.operationId !== null &&
      (typeof record.operationId !== 'string' ||
        !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(record.operationId)))
  ) {
    sessionStorage.removeItem(CRE_PRIVATE_RENDER_SESSION_KEY);
    return null;
  }
  return record;
}

function saveCrePrivateRender(projectId, operationId) {
  sessionStorage.setItem(
    CRE_PRIVATE_RENDER_SESSION_KEY,
    JSON.stringify({ projectId, operationId }),
  );
}

function createCrePrivateRenderOperationId() {
  if (typeof crypto?.randomUUID === 'function') return crypto.randomUUID();
  if (typeof crypto?.getRandomValues !== 'function') {
    throw new Error('Secure random operation IDs are unavailable in this browser.');
  }
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function crePrivateImportContext() {
  const targetInput = document.getElementById('creSourceTarget');
  const accountInput = document.getElementById('creSourceAccount');
  const territoryInput = document.getElementById('creSourceTerritory');
  return {
    targetContext: targetInput?.value || 'youtube_shorts',
    accountId: accountInput?.value.trim() || 'private-local-owner',
    territory: (territoryInput?.value.trim().toUpperCase() || 'US'),
  };
}

function markVoiceTiming(id, event) {
  const mark = `ultron-voice-${id}-${event}`;
  performance.mark(mark);
  if (voiceTimingDiagnostics) {
    console.info('[ULTRON voice timing]', {
      id,
      event,
      monotonicMs: Math.round(performance.now() * 10) / 10,
    });
  }
}

function measureVoiceTiming(id, name, start, end) {
  try {
    performance.measure(
      `ultron-voice-${id}-${name}`,
      `ultron-voice-${id}-${start}`,
      `ultron-voice-${id}-${end}`,
    );
  } catch {
    return;
  }
}

function snapshotStatus(channel, value) {
  if (channelData.has(channel) && bridge?.getProvenance?.() !== 'LIVE') {
    return `STALE · ${value}`;
  }
  return value;
}

function byId(id) {
  return document.getElementById(id);
}

function setText(id, value) {
  const node = byId(id);
  if (node) node.textContent = value == null || value === '' ? 'Unavailable' : String(value);
}

function renderCreScriptPreviewDetails(result) {
  const production = result.productionManifest;
  const editorial = result.review.editorial;
  const audioEvidence = result.audioEvidence;
  const fields = [
    ['Script ID', result.scriptId],
    ['Script SHA-256', result.scriptSha256],
    ['Preview SHA-256', result.preview.sha256],
    ['Preview', `Local narrated MP4 · ${result.preview.sizeBytes} bytes · ${production.video.durationSeconds}s`],
    ['Video', `${production.video.width}×${production.video.height} · ${production.video.videoCodec} + ${production.video.audioCodec}`],
    ['Narration', `${production.narration.engine} · ${production.narration.voice} · ${production.narration.language}`],
    ['Narration signal', `Source RMS ${production.narration.rmsDbfs} dBFS · encoded MP4 RMS ${production.narration.encodedAudioRmsDbfs} dBFS · peak ${production.narration.peakDbfs} dBFS · quiet 100ms windows ${Math.round(production.narration.quietWindowRatio * 100)}% · clipped samples ${production.narration.clippedSamples}`],
    ['Scene audio boundaries', `${production.narration.sceneBoundaryGaps.length} encoded intentional 150ms silence gap(s) measured`],
    ['Speech intelligibility / naturalness', `${production.narration.intelligibility} / ${production.narration.naturalness}`],
    ['Caption timing', production.captions.timingMethod],
    ['Caption safe area', `${production.captions.safeArea.maximumWidthPx}px max width · ${production.captions.safeArea.maximumLines} lines · ${production.captions.safeArea.fontFamily} ${production.captions.safeArea.fontSizePx}px`],
    ['Technical QA', production.qualityAssurance.state],
    ['Scene visual evidence', `${production.sceneVisualEvidence.state} · ${production.sceneVisualEvidence.failureReason || production.sceneVisualEvidence.nextAction}`],
    ['Durable audio evidence', `${audioEvidence.status} · ${audioEvidence.evidenceId || 'ID unavailable'} · ${audioEvidence.algorithmVersion || 'algorithm unavailable'}`],
    ['Evidence verification', `Created ${audioEvidence.createdAt || 'not recorded'} · checked ${audioEvidence.lastCheckedAt || 'not checked'} · last verified ${audioEvidence.lastVerifiedAt || 'never'}`],
    ['Evidence bindings', `Source ${audioEvidence.sourceBinding} · script ${audioEvidence.scriptBinding} · artifact ${audioEvidence.artifactBinding}`],
    ['Recomputed clipping metrics', audioEvidence.sampleCount === null ? 'Unavailable' : `${audioEvidence.sampleCount} samples · ${audioEvidence.clippedSamples} clipped · ratio ${audioEvidence.clippedSampleRatio} · threshold ${audioEvidence.clippingThreshold}`],
    ['Evidence blocker', audioEvidence.failureReason || audioEvidence.nextAction],
    ...production.scenes.flatMap((scene) => [
      [`Scene ${scene.order} · ${scene.visualType}`, scene.excerpt],
      [`Scene ${scene.order} · caption layout`, `${scene.caption.lineCount} lines · ${scene.caption.maximumLineWidthPx}px max · source ${scene.scriptOffsets.start}–${scene.scriptOffsets.endExclusive}`],
    ]),
    ['References', `${result.references.items.length} supplied · ${result.references.state}`],
    ['Editorial review', editorial.state],
    ['Editorial recommendation', editorial.editorialVerdict || 'NOT_REVIEWED'],
    ['Human narration assessment', editorial.narrationAssessment],
    ['Human pacing assessment', editorial.pacingAssessment],
    ['Human caption assessment', editorial.captionAssessment],
    ['Human scene assessment', editorial.sceneAssessment],
    ['Human review record', editorial.selfReview ? 'OWNER SELF-REVIEW; NOT INDEPENDENT APPROVAL' : 'PENDING'],
    ['Reviewer', editorial.reviewerId || 'NOT_RECORDED'],
    ['Review record ID', editorial.reviewId || 'NOT_RECORDED'],
    ['Editorial notes', editorial.notes || 'None recorded'],
    ['Factual review', result.review.factualReviewState],
    ['Rights', result.review.rightsState],
    ['Approval', result.review.approvalState],
    ['Publication', result.review.publicationState],
    ['Technical limitations', production.qualityAssurance.limitations.join(' ')],
  ];
  const manifest = byId('creScriptPreviewManifest');
  manifest.replaceChildren();
  fields.forEach(([label, value]) => {
    const row = document.createElement('div');
    const term = document.createElement('dt');
    const detail = document.createElement('dd');
    term.textContent = label;
    detail.textContent = String(value);
    row.append(term, detail);
    manifest.append(row);
  });
  const gallery = byId('creSceneVisualGallery');
  gallery.replaceChildren();
  production.scenes.forEach((scene) => {
    const card = document.createElement('article');
    card.className = 'trading-record';
    const narrativeRole = scene.narrativeRole || scene.sceneContract?.narrativeRole || 'UNKNOWN';
    const heading = document.createElement('h4');
    heading.textContent = `Scene ${scene.order} · ${narrativeRole} · ${scene.renderedComposition || scene.visualStrategy}`;
    const details = document.createElement('p');
    const asset = scene.visualAsset;
    details.textContent = `${scene.assetState} · ${scene.technicalValidation} · ${asset?.visualTheme || 'theme unavailable'} / ${asset?.visualTreatment || 'treatment unavailable'} · source ${scene.scriptOffsets.start}-${scene.scriptOffsets.endExclusive} · ${scene.excerptSha256} · ${asset?.sha256 || 'visual asset unavailable'} · ${asset ? `${asset.width}×${asset.height} ${asset.format} · ${asset.byteSize} bytes · ${asset.rendererVersion} · ${asset.generatedAt}` : production.sceneVisualEvidence.failureReason || production.sceneVisualEvidence.nextAction}`;
    card.append(heading);
    if (asset?.dataBase64 && asset.validationStatus === 'TECHNICAL_CHECKS_PASSED') {
      const image = document.createElement('img');
      image.className = 'cre-scene-visual';
      image.src = `data:image/png;base64,${asset.dataBase64}`;
      image.alt = `Scene ${scene.order} visual composition: ${narrativeRole}`;
      image.width = asset.width;
      image.height = asset.height;
      image.addEventListener('error', () => {
        image.removeAttribute('src');
        image.alt = 'Persisted scene visual could not be loaded or decoded.';
      }, { once: true });
      card.append(image);
    }

    card.append(details);
    gallery.append(card);
  });
  const reviewFeedback = byId('creScriptReviewFeedback');
  if (editorial.state === 'RECORDED') {
    reviewFeedback.textContent = `Review recorded for artifact ${editorial.artifactSha256}. This is an owner self-review; approval remains NOT_GRANTED and rights remain NOT_EVALUATED.`;
    reviewFeedback.className = 'notice notice-warning';
  } else {
    reviewFeedback.textContent = 'No editorial review recorded.';
    reviewFeedback.className = 'notice';
  }
}

function renderSystemIntelligence(state) {
  systemIntelligenceState = state;
  const data = state.data;
  const status = byId('systemInventoryStatus');
  if (status) {
    status.textContent = state.message
      ? `${state.state}: ${state.message}`
      : `${state.state}: ${data?.status || 'No verified inventory received.'}`;
  }
  setText(
    'systemInventoryTimestamp',
    data?.generatedAt
      ? `Inventory generated ${new Date(data.generatedAt).toLocaleString()}. Retained values are stale when the connection is offline.`
      : 'Current system health is unknown.',
  );
  const renderRecords = (id, rows, title, detail) => {
    const root = byId(id);
    if (!root) return;
    root.replaceChildren();
    if (!rows?.length) {
      const empty = document.createElement('p');
      empty.className = 'empty-state';
      empty.textContent = 'No evidence available.';
      root.append(empty);
      return;
    }
    rows.forEach((item) => {
      const card = document.createElement('article');
      card.className = 'diagnostic-card';
      const heading = document.createElement('div');
      heading.className = 'inventory-heading';
      const name = document.createElement('h3');
      name.textContent = title(item);
      const pill = document.createElement('span');
      const historicalSnapshot = state.state === 'STALE'
        && (id === 'systemObservations' || id === 'systemServices');
      const statusText = historicalSnapshot
        ? 'STALE'
        : String(
          item.status
          || (id === 'systemServices' ? item.readiness : item.processStatus)
          || 'UNKNOWN',
        ).toUpperCase();
      const tone = ['VERIFIED', 'AVAILABLE', 'SUCCEEDED'].includes(statusText)
        ? 'live'
        : ['FAILED', 'DEGRADED'].includes(statusText) ? 'warning' : 'unknown';
      pill.className = `state-pill state-${tone}`;
      pill.textContent = statusText;
      const description = document.createElement('p');
      description.textContent = detail(item);
      const source = document.createElement('small');
      source.className = 'muted';
      source.textContent = `${item.source || 'Source not recorded'} · ${item.observedAt || 'Timestamp unavailable'}`;
      heading.append(name, pill);
      card.append(heading, description, source);
      root.append(card);
    });
  };
  renderRecords(
    'systemObservations',
    data?.observations,
    (item) => item.name,
    (item) => `${item.value === null ? 'Unavailable' : typeof item.value === 'object' ? JSON.stringify(item.value) : String(item.value)}${item.detail ? ` · ${item.detail}` : ''}`,
  );
  renderRecords(
    'systemServices',
    data?.services,
    (item) => `${item.name} · ${item.expectedAddress}:${item.port}`,
    (item) => `Process ${item.processStatus} · listener ${item.listener ? 'observed' : 'not observed'} · application readiness ${item.readiness}.`,
  );
  renderRecords(
    'systemDependencies',
    data?.dependencies,
    (item) => `${item.from} → ${item.to}`,
    (item) => `${item.relationship} Evidence: ${item.evidence}.`,
  );
  renderRecords(
    'systemOperationalEvents',
    data?.operationalEvents,
    (item) => `${item.service || 'service'} · ${item.status || 'UNKNOWN'}`,
    (item) => `${item.detail || 'No detail recorded'} · operation ${item.operationId || 'unknown'} · ${item.at ? new Date(item.at * 1000).toLocaleString() : 'time unavailable'}`,
  );
  renderRecords(
    'systemDiagnosticRecords',
    data?.diagnostics,
    (item) => item.symptom || 'Diagnostic record',
    (item) => [
      `Likely cause: ${item.likelyCause || 'Unknown'} (confidence ${item.confidence || 'unknown'}).`,
      `Evidence: ${JSON.stringify(item.supportingEvidence || {})}.`,
      `Alternatives: ${(item.alternatives || []).join(' ') || 'Not recorded'}.`,
      `Recovery: ${item.proposedRecovery || 'Not proposed'}.`,
      `Permissions: ${(item.requiredPermissions || []).join(', ') || 'Not recorded'}.`,
      `Preconditions/risks: ${(item.preconditions || []).join(' ') || 'Not recorded'} / ${(item.risks || []).join(' ') || 'Not recorded'}.`,
      `Verification: ${(item.verification || []).join(' ') || 'Not recorded'}.`,
      `Rollback: ${item.rollback || 'Not recorded'}. Outcome: ${item.outcome || 'Unknown'}.`,
    ].join(' '),
  );
  const action = data?.actions?.find((item) => item.id === 'restart-auth');
  const restartButton = byId('restartAuthButton');
  if (restartButton) {
    restartButton.disabled = !provider?.authenticated || state.state !== 'AVAILABLE' || action?.status !== 'AVAILABLE';
  }
  setText(
    'systemActionStatus',
    action
      ? `${action.status}: ${action.authorization} ${action.description}`
      : 'The helper-only restart action is unavailable in this inventory.',
  );
  const handoff = data?.managerHandoff;
  setText(
    'systemHandoffStatus',
    handoff
      ? `${handoff.status} · ${handoff.ownershipStatus || 'OWNERSHIP_UNKNOWN'} · ${handoff.phase} · manager PIDs: ${(handoff.managerPids || []).join(', ') || 'none observed'} · observed ${handoff.observedAt || 'time unavailable'} · handoff eligible: ${handoff.eligible === true ? 'yes' : 'no'}: ${handoff.detail}`
      : 'Manager handoff status is unknown; no ownership transfer is asserted.',
  );
}

function receivedAt(channel) {
  const value = channelTimes.get(channel);
  return value ? value.toLocaleString() : 'Not received';
}

function recordChannel(channel, payload) {
  channelData.set(channel, payload);
  channelTimes.set(channel, new Date());
  renderOverview();
  renderEvents();
  renderOperations();
  renderOperationHistory();
  renderAgents();
  renderSkills();
  renderTelemetry();
  renderChannelStatus();
  renderDiagnostics();
  renderTradingViews();
  renderOmegaViews();
  setText('lastUpdate', new Date().toLocaleString());
}

function getRuntime() {
  return channelData.get('cognitiveState');
}

function getCommand() {
  return channelData.get('command');
}

function getEvents() {
  return channelData.get('runtimeEvents');
}

function renderOverview() {
  const provenance = bridge?.getProvenance?.() || 'OFFLINE';
  const runtime = getRuntime();
  const command = getCommand();
  const operations = command?.activeOperations;
  const agents = runtime?.agents;
  setText('overviewConnection', provenance === 'LIVE' ? 'CONNECTED' : provenance);
  setText('overviewConnectionDetail', provenance === 'LIVE'
    ? `Transport: connected · Chat: ${provider?.authenticated ? 'authenticated' : 'authentication required'}`
    : 'No current verified backend subscription.');
  setText('runtimeState', runtime
    ? snapshotStatus('cognitiveState', runtime.behavior || 'UNKNOWN')
    : 'UNAVAILABLE');
  setText('runtimeDetail', runtime
    ? `${runtime.running ? 'Running' : 'Stopped'} · ${runtime.cycleCount ?? 'Unknown'} completed cycles`
    : 'No runtime snapshot received.');
  setText('activeOperationCount', Array.isArray(operations)
    ? snapshotStatus('command', operations.length)
    : 'UNKNOWN');
  setText('agentCount', Array.isArray(agents)
    ? snapshotStatus('cognitiveState', agents.length)
    : 'UNKNOWN');
  const financial = channelData.get('trading')?.financial;
  const tradingAccess = channelData.get('trading')?.access;
  const mode = financial?.execution?.mode;
  setText('overviewTradingMode', mode ? snapshotStatus('trading', formatExecutionMode(mode)) : tradingAccess
    ? 'AUTHENTICATION REQUIRED'
    : bridge?.getProvenance?.() === 'LIVE' && !provider?.authenticated
      ? 'AUTHENTICATION REQUIRED'
      : 'UNKNOWN');
  setText('overviewTradingDetail', financial
    ? `Market data: ${financial.marketData.state} · broker: ${financial.execution.source}`
    : tradingAccess
      ? 'Financial telemetry is withheld until the WebSocket session is authenticated.'
      : bridge?.getProvenance?.() === 'LIVE' && !provider?.authenticated
        ? 'Authenticate the WebSocket session to request financial account telemetry.'
      : 'No financial telemetry snapshot received.');
  const omegaPayload = channelData.get('omega');
  const omega = omegaPayload?.access ? null : omegaPayload;
  const omegaIntegration = omega?.integration || {};
  setText('overviewOmegaHealth', omega?.status?.overall_health
    ? snapshotStatus('omega', omega.status.overall_health)
    : (
    omegaPayload?.access ? 'AUTHENTICATION REQUIRED' : 'UNKNOWN'
  ));
  setText('overviewOmegaHealthDetail', omega?.status?.overall_health
    ? `OMEGA-reported application health · measured ${omega.measuredAt || 'time unavailable'}`
    : omegaPayload?.access
      ? 'OMEGA telemetry is withheld until the WebSocket session is authenticated.'
      : 'No OMEGA-reported health snapshot received.');
  const omegaLinkState = omegaIntegration.linkState || omega?.linkState;
  setText('overviewOmegaLink', omegaLinkState
    ? snapshotStatus('omega', omegaLinkState)
    : (
    omegaPayload?.access ? 'AUTHENTICATION REQUIRED' : 'UNKNOWN'
  ));
  setText('overviewOmegaLinkDetail', omegaIntegration.lastAttemptAt
    ? `Gateway link state · last attempt ${omegaIntegration.lastAttemptAt}`
    : omegaPayload?.access
      ? 'Authenticate the WebSocket session to inspect the gateway link.'
      : 'No gateway-to-OMEGA integration result received.');
  const voiceStatus = voiceHealthResult
    ? voiceHealthResult.ready === true ? 'READY' : 'UNAVAILABLE'
    : 'UNKNOWN';
  setText('overviewVoiceStatus', voiceStatus);
  setText('overviewVoiceDetail', voiceHealthCheckedAt
    ? `Voice API checked by this browser at ${voiceHealthCheckedAt.toLocaleString()}. Playback is verified separately.`
    : voiceHealthResult?.error || 'Voice API health has not been checked.');

  const world = channelData.get('world');
  const hostFacts = byId('hostFacts');
  if (hostFacts) {
    hostFacts.replaceChildren();
    if (!world || !world.system || !world.project || !world.runtime || world.error) {
      const row = document.createElement('div');
      const label = document.createElement('dt');
      const value = document.createElement('dd');
      label.textContent = 'Status';
      value.textContent = world?.error
        ? `Snapshot unavailable: ${world.error}`
        : 'Unavailable: no system snapshot received.';
      row.append(label, value);
      hostFacts.append(row);
    } else {
      const facts = [
        ['Host', world.system.hostname],
        ['Operating system', world.system.os],
        ['Project', world.project.name],
        ['CPU usage', world.system.cpu_usage == null ? null : `${world.system.cpu_usage}%`],
        ['Memory usage', world.system.memory_usage == null ? null : `${world.system.memory_usage}%`],
        ['Source observed at', world.observedAt],
        ['Active applications', Array.isArray(world.runtime.active_applications)
          ? world.runtime.active_applications.length
          : null],
      ];
      facts.forEach(([name, value]) => {
        const row = document.createElement('div');
        const label = document.createElement('dt');
        const detail = document.createElement('dd');
        label.textContent = name;
        detail.textContent = value == null || value === '' ? 'Unknown' : String(value);
        row.append(label, detail);
        hostFacts.append(row);
      });
      if (bridge?.getProvenance?.() !== 'LIVE') {
        const row = document.createElement('div');
        const label = document.createElement('dt');
        const detail = document.createElement('dd');
        label.textContent = 'Snapshot freshness';
        detail.textContent = `STALE · last received ${receivedAt('world')}`;
        row.append(label, detail);
        hostFacts.append(row);
      }

    }
  }
  const notice = byId('disconnectNotice');
  if (notice) {
    notice.hidden = provenance === 'LIVE';
    notice.textContent = provenance === 'LIVE'
      ? ''
      : 'Backend unavailable or disconnected. Any retained snapshots are last-received values, not current health.';
  }
}

function renderCreStatus(state = creStatusState) {
  creStatusState = state || { state: 'UNKNOWN', data: null, message: null };
  const data = creStatusState.data;
  renderCreAudioEvidence(
    byId('creAudioEvidenceList'),
    data,
    creStatusState.state,
  );
  const label = {
    LOADING: 'Loading',
    AVAILABLE: 'Available',
    STALE: 'Stale',
    DISCONNECTED: 'Disconnected',
    UNAUTHORIZED: 'Unauthorized',
    UNAVAILABLE: 'Unavailable',
  }[creStatusState.state] || 'Unknown';
  const pill = byId('creStatusPill');
  if (pill) {
    pill.textContent = label;
    pill.className = `state-pill ${creStatusState.state === 'AVAILABLE' ? 'state-live' : creStatusState.state === 'UNAUTHORIZED' || creStatusState.state === 'UNAVAILABLE' ? 'state-error' : 'state-warning'}`;
  }
  const notice = byId('creStatusNotice');
  if (notice) {
    const details = {
      LOADING: 'Loading owner-scoped CRE status from the authenticated backend.',
      AVAILABLE: 'Current owner-scoped CRE status received from the backend.',
      STALE: 'Showing the last CRE response; it may be stale because the connection or request is unavailable.',
      DISCONNECTED: 'CRE status is unavailable while the WebSocket is disconnected.',
      UNAUTHORIZED: creStatusState.message || 'The authenticated session is not authorized to read CRE status.',
      UNAVAILABLE: creStatusState.message || 'The CRE status service is unavailable.',
    };
    notice.textContent = details[creStatusState.state] || 'CRE status is unknown.';
    notice.className = `notice ${creStatusState.state === 'AVAILABLE' ? '' : 'notice-warning'}`;
  }
  const generatedAt = data?.generatedAt ? new Date(data.generatedAt) : null;
  setText('creUpdatedAt', generatedAt && !Number.isNaN(generatedAt.valueOf())
    ? generatedAt.toLocaleString()
    : 'Not received');
  if (!data) {
    ['creTotalJobs', 'creProcessingCount', 'creRenderingCount', 'crePublishingCount'].forEach((id) => setText(id, 'UNKNOWN'));
    ['creProcessingDetail', 'creRenderingDetail', 'crePublishingDetail'].forEach((id) => setText(id, 'No authoritative CRE counts received.'));
    setText('crePublishingMode', 'Publishing status unavailable.');
    setText('creServiceStatus', 'Service status unavailable until CRE responds.');
    setText('creAnalyticsStatus', 'Unavailable: no instrumented CRE metrics were reported.');
    setText('creProductionJobs', 'UNKNOWN');
    setText('creAwaitingApproval', 'UNKNOWN');
    setText('creFailedUncertain', 'UNKNOWN');
    setText('creBusinessFreshness', 'Business reporting is unavailable until an authenticated CRE response is received.');
    setText('creProductionJobsNote', 'Awaiting owner-scoped CRE status.');
    setText('creFinancialFreshness', 'Not reported');
    ['creJobsList', 'creLifecycleList', 'creEventsList'].forEach((id) => {
      const root = byId(id);
      if (!root) return;
      const empty = document.createElement('p');
      empty.className = 'empty-state';
      empty.textContent = 'No CRE status response received.';
      root.replaceChildren(empty);
    });
    return;
  }

  setText('creTotalJobs', data.counts.total);
  const countFor = (kind) => Object.values(data.counts[kind] || {}).reduce((sum, value) => sum + value, 0);
  setText('creProcessingCount', countFor('processing'));
  setText('creRenderingCount', countFor('rendering'));
  setText('crePublishingCount', countFor('publishing'));
  setText('creProcessingDetail', Object.entries(data.counts.processing || {}).map(([jobState, count]) => `${jobState}: ${count}`).join(' · ') || 'No processing jobs.');
  setText('creRenderingDetail', Object.entries(data.counts.rendering || {}).map(([jobState, count]) => `${jobState}: ${count}`).join(' · ') || 'No rendering jobs.');
  setText('crePublishingDetail', `${data.counts.pendingApprovals} pending approvals`);
  setText('crePublishingMode', `Adapter mode: ${data.publishing.mode} · service: ${data.services.publishing} · kill switch: ${data.publishing.killSwitchEnabled ? 'enabled' : 'disabled'}`);
  setText(
    'creServiceStatus',
    `Store: ${data.services.store} · Rights: ${data.services.rights} · Processing: ${data.services.processing} · Rendering: ${data.services.rendering} · Script preview: ${data.services.scriptPreview || 'NOT_CONFIGURED'} · Publishing: ${data.services.publishing}`,
  );
  setText('creAnalyticsStatus', data.analytics.available
    ? 'Metrics are available from the CRE backend.'
    : 'Unavailable: CRE analytics are not instrumented.');
  const business = summarizeCreBusinessStatus(data);
  setText('creProductionJobs', business.available ? business.productionJobs : 'UNKNOWN');
  setText('creAwaitingApproval', business.available && business.pendingApprovals !== null
    ? business.pendingApprovals
    : 'UNKNOWN');
  setText('creFailedUncertain', business.available ? business.failedOrUncertainJobs : 'UNKNOWN');
  setText('creProductionJobsNote', 'Queued/running processing and rendering jobs; job count, not unique content items.');
  const generatedAtLabel = generatedAt && !Number.isNaN(generatedAt.valueOf())
    ? generatedAt.toLocaleString()
    : 'time not reported';
  const snapshotFreshness = creStatusState.state === 'AVAILABLE'
    ? `Owner-scoped job snapshot generated ${generatedAtLabel}.`
    : `${creStatusState.state}: last owner-scoped job snapshot generated ${generatedAtLabel}; values may be stale.`;
  setText('creBusinessFreshness', snapshotFreshness);
  setText('creFinancialFreshness', `Reporting period not provided · latest CRE status snapshot ${generatedAtLabel}`);

  const renderRows = (id, rows, titleFor, detailFor, emptyText) => {
    const root = byId(id);
    if (!root) return;
    root.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement('p');
      empty.className = 'empty-state';
      empty.textContent = emptyText;
      root.append(empty);
      return;
    }
    rows.forEach((item) => {
      const card = document.createElement('article');
      card.className = 'trading-record';
      const heading = document.createElement('h3');
      heading.textContent = titleFor(item);
      const detail = document.createElement('p');
      detail.textContent = detailFor(item);
      card.append(heading, detail);
      root.append(card);
    });
  };
  renderRows(
    'creJobsList',
    data.jobs,
    (job) => `${job.type} · ${job.state}`,
    (job) => `ID ${job.id} · updated ${job.updatedAt}`,
    'No stored processing, rendering, or publishing jobs.',
  );
  renderRows(
    'creLifecycleList',
    data.lifecycle,
    (item) => item.state,
    (item) => `Intake ${item.id} · updated ${item.updatedAt}`,
    'No stored content lifecycle records.',
  );
  renderRows(
    'creEventsList',
    data.events,
    (event) => `${event.type} · ${event.state}`,
    (event) => `${event.id} · ${event.createdAt}`,
    'No stored CRE events.',
  );
}

function formatExecutionMode(mode) {
  const normalized = String(mode || '').trim();
  if (!normalized) return 'Unknown';
  return /paper|simulation/i.test(normalized)
    ? `PAPER · ${normalized.toUpperCase()}`
    : normalized.toUpperCase();
}

function setFactList(id, facts) {
  const root = byId(id);
  if (!root) return;
  root.replaceChildren();
  facts.forEach(([name, value]) => {
    const row = document.createElement('div');
    const label = document.createElement('dt');
    const detail = document.createElement('dd');
    label.textContent = name;
    detail.textContent = value == null || value === '' ? 'Not reported' : String(value);
    row.append(label, detail);
    root.append(row);
  });
}

function financialMoney(value) {
  return typeof value === 'number'
    ? new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(value)
    : 'Not reported';
}

function renderMarketData(financial) {
  const market = financial?.marketData;
  const state = byId('marketDataState');
  if (!market) {
    setText('marketDataState', 'Unavailable');
    if (state) state.className = 'state-pill state-unknown';
    setFactList('marketDataFacts', [['Status', 'No financial market-data snapshot received.']]);
    return;
  }
  setText('marketDataState', market.state);
  if (state) {
    state.className = `state-pill ${
      market.state === 'VALID'
        ? 'state-live'
        : ['STALE', 'MARKET_CLOSED'].includes(market.state)
          ? 'state-warning'
          : 'state-unknown'
    }`;
  }
  const price = typeof market.latestPrice === 'number' ? String(market.latestPrice) : 'Not reported';
  setFactList('marketDataFacts', [
    ['Instrument', market.symbol],
    [market.state === 'STALE' ? 'Last validated price (stale)' : 'Validated price', price],
    ['Provider', market.provider],
    ['Adapter', market.source],
    ['Source timestamp', market.latestValidatedAt],
    ['Reported freshness', typeof market.freshnessSeconds === 'number' ? `${market.freshnessSeconds} seconds` : null],
    ['Adapter freshness limit', typeof market.freshnessLimitSeconds === 'number' ? `${market.freshnessLimitSeconds} seconds` : null],
    ['Bid', typeof market.bid === 'number' ? market.bid : 'Not provided by this provider snapshot'],
    ['Ask', typeof market.ask === 'number' ? market.ask : 'Not provided by this provider snapshot'],
    ['Volume', typeof market.volume === 'number' ? market.volume : 'Not provided by this provider snapshot'],
    ['Validation/provider error', market.error ? `${market.error.category || 'Error'}: ${market.error.message || 'Details unavailable'}` : 'None reported'],
    ['Browser received snapshot', receivedAt('trading')],
  ]);
}

function renderPortfolio(financial) {
  const portfolio = financial?.portfolio;
  const execution = financial?.execution;
  const mode = execution?.mode;
  setText('portfolioMode', mode ? formatExecutionMode(mode) : 'Unknown mode');
  const modePill = byId('portfolioMode');
  if (modePill) {
    modePill.className = `state-pill ${mode?.toLowerCase() === 'simulation' ? 'state-live' : 'state-unknown'}`;
  }
  if (!portfolio) {
    setFactList('portfolioFacts', [['Status', 'No portfolio snapshot received.']]);
    setText('portfolioPositionCount', 'Unavailable');
    const positions = byId('portfolioPositions');
    positions?.replaceChildren();
    if (positions) {
      const empty = document.createElement('p');
      empty.className = 'empty-state';
      empty.textContent = 'No portfolio snapshot received.';
      positions.append(empty);
    }
    return;
  }
  const rows = Array.isArray(portfolio.positions) ? portfolio.positions : null;
  setFactList('portfolioFacts', [
    ['Account snapshot status', portfolio.state],
    ['Execution mode', mode ? formatExecutionMode(mode) : null],
    ['Broker source', portfolio.source],
    ['Cash balance', financialMoney(portfolio.cashUsd)],
    ['Equity', financialMoney(portfolio.equityUsd)],
    ['Account snapshot issue', portfolio.error?.message],
    ['Backend snapshot time', portfolio.updatedAt],
    ['Browser received snapshot', receivedAt('trading')],
  ]);
  setText('portfolioPositionCount', rows ? `${rows.length} positions` : 'Unavailable');
  const root = byId('portfolioPositions');
  if (!root) return;
  root.replaceChildren();
  if (!rows) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = 'Position list is unavailable.';
    root.append(empty);
    return;
  }
  if (!rows.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = 'The broker snapshot reports no open positions.';
    root.append(empty);
    return;
  }
  rows.forEach((position) => {
    const card = document.createElement('article');
    card.className = 'trading-record';
    const heading = document.createElement('div');
    heading.className = 'inventory-heading';
    const name = document.createElement('h3');
    name.textContent = position.symbol;
    const value = document.createElement('span');
    value.className = 'state-pill state-unknown';
    value.textContent = financialMoney(position.notionalUsd);
    heading.append(name, value);
    const detail = document.createElement('p');
    detail.textContent = `${position.quantity} units · average price ${financialMoney(position.averagePriceUsd)}`;
    card.append(heading, detail);
    root.append(card);
  });
}

function renderTradingHistory(financial) {
  const root = byId('tradingHistoryList');
  if (!root) return;
  const records = financial?.executions?.records;
  setText('tradingHistoryCount', Array.isArray(records) ? `${records.length} ledger records` : 'Unavailable');
  root.replaceChildren();
  if (!Array.isArray(records)) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = 'No broker trade-ledger snapshot received.';
    root.append(empty);
    return;
  }
  if (!records.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = 'The broker snapshot contains no trade-ledger records.';
    root.append(empty);
    return;
  }
  records.slice().reverse().forEach((record) => {
    const card = document.createElement('article');
    card.className = 'trading-record';
    const heading = document.createElement('div');
    heading.className = 'inventory-heading';
    const title = document.createElement('h3');
    title.textContent = `${record.symbol} · ${record.side}`;
    const mode = document.createElement('span');
    mode.className = 'state-pill state-unknown';
    mode.textContent = financial.execution?.mode
      ? formatExecutionMode(financial.execution.mode)
      : 'Mode unavailable';
    heading.append(title, mode);
    const details = document.createElement('p');
    details.textContent = `${record.quantity} units at ${financialMoney(record.priceUsd)} · fees ${financialMoney(record.feesUsd)} · realized P&L ${financialMoney(record.realizedPnlUsd)}`;
    const timestamp = document.createElement('p');
    timestamp.className = 'event-meta';
    timestamp.textContent = `Trade timestamp: ${record.timestamp}`;
    card.append(heading, details, timestamp);
    root.append(card);
  });
}

function renderTradingViews() {
  const payload = channelData.get('trading');
  const financial = payload?.financial;
  if (!financial) {
    const status = payload?.access?.status === 'AUTHENTICATION_REQUIRED'
      ? 'AUTHENTICATION REQUIRED'
      : bridge?.getProvenance?.() === 'LIVE' && !provider?.authenticated
        ? 'AUTHENTICATION REQUIRED'
      : 'Unavailable';
    setText('tradingMode', status);
    setText('tradingAvailability', status);
    setText('tradingAvailabilityDetail', status === 'Unavailable'
      ? 'No financial telemetry snapshot received.'
      : 'Financial account data is withheld until the WebSocket session is authenticated.');
    setText('tradingMarketState', 'Unavailable');
    setText('tradingMarketDetail', 'Market-data fields are withheld.');
    setText('tradingPositionCount', 'Unavailable');
    setText('tradingPortfolioDetail', 'Portfolio fields are withheld.');
    setText('tradingRiskState', 'Unavailable');
    setText('tradingRiskDetail', 'Risk-control fields are withheld.');
    setFactList('tradingOverviewMarket', [['Status', status]]);
    setFactList('tradingOverviewPortfolio', [['Status', status]]);
    const diagnostics = byId('tradingDiagnostics');
    diagnostics?.replaceChildren();
    if (diagnostics) {
      const item = document.createElement('li');
      item.className = 'empty-state';
      item.textContent = status === 'Unavailable'
        ? 'No trading diagnostics received.'
        : 'Authenticate to inspect trading diagnostics.';
      diagnostics.append(item);
    }
    renderMarketData(null);
    renderPortfolio(null);
    renderTradingHistory(null);
    return;
  }
  const market = financial.marketData;
  const portfolio = financial.portfolio;
  const risk = financial.risk;
  const mode = financial.execution.mode;
  const modePill = byId('tradingMode');
  setText('tradingMode', formatExecutionMode(mode));
  if (modePill) modePill.className = `state-pill ${mode.toLowerCase() === 'simulation' ? 'state-live' : 'state-unknown'}`;
  const initialized = financial.initialization.state === 'READY';
  setText('tradingAvailability', initialized ? 'Components configured' : financial.initialization.state);
  setText('tradingAvailabilityDetail', `${financial.execution.source} · snapshot received ${receivedAt('trading')}`);
  setText('tradingMarketState', market.state);
  setText('tradingMarketDetail', market.latestValidatedAt
    ? `${market.symbol} · ${market.provider} · source time ${market.latestValidatedAt}${typeof market.freshnessSeconds === 'number' ? ` · age ${market.freshnessSeconds.toFixed(1)}s` : ''}`
    : `${market.symbol} · no validated source time`);
  const positions = Array.isArray(portfolio.positions) ? portfolio.positions : null;
  setText('tradingPositionCount', positions ? `${positions.length} positions` : 'Unavailable');
  setText('tradingPortfolioDetail', `${financialMoney(portfolio.cashUsd)} cash · ${financialMoney(portfolio.equityUsd)} equity`);
  setText('tradingRiskState', Array.isArray(risk.engines) ? `${risk.engines.length} controls configured` : 'Unknown');
  setText('tradingRiskDetail', `Individual check results: ${risk.rejections.state}`);
  setFactList('tradingOverviewMarket', [
    ['Status', market.state],
    ['Instrument', market.symbol],
    [market.state === 'STALE' ? 'Last validated price (stale)' : 'Price', typeof market.latestPrice === 'number' ? market.latestPrice : null],
    ['Freshness', typeof market.freshnessSeconds === 'number' ? `${market.freshnessSeconds} seconds` : null],
    ['Freshness limit', typeof market.freshnessLimitSeconds === 'number' ? `${market.freshnessLimitSeconds} seconds` : null],
    ['Bid', typeof market.bid === 'number' ? market.bid : null],
    ['Ask', typeof market.ask === 'number' ? market.ask : null],
    ['Volume', typeof market.volume === 'number' ? market.volume : null],
    ['Provider timestamp', market.latestValidatedAt],
  ]);
  setFactList('tradingOverviewPortfolio', [
    ['Broker source', portfolio.source],
    ['Cash', financialMoney(portfolio.cashUsd)],
    ['Equity', financialMoney(portfolio.equityUsd)],
    ['Open positions', positions ? positions.length : null],
  ]);
  const diagnostics = byId('tradingDiagnostics');
  diagnostics?.replaceChildren();
  if (diagnostics) {
    if (!financial.diagnostics.length) {
      const item = document.createElement('li');
      item.className = 'empty-state';
      item.textContent = 'The gateway reports no financial diagnostics for this snapshot.';
      diagnostics.append(item);
    } else {
      financial.diagnostics.forEach((diagnostic) => {
        const item = document.createElement('li');
        item.textContent = `${diagnostic.category || 'Diagnostic'}: ${diagnostic.message || 'Details unavailable'}`;
        diagnostics.append(item);
      });
    }
  }
  renderMarketData(financial);
  renderPortfolio(financial);
  renderTradingHistory(financial);
}

function setJsonBlock(id, value, unavailableText) {
  const node = byId(id);
  if (!node) return;
  node.textContent = value === undefined || value === null
    ? unavailableText
    : JSON.stringify(value, null, 2);
}

function setStatePill(id, value) {
  const pill = byId(id);
  if (!pill) return;
  const state = String(value || 'UNKNOWN').toUpperCase();
  pill.textContent = state.replace(/_/g, ' ');
  pill.className = `state-pill ${
    ['CONNECTED', 'AUTHENTICATED'].includes(state)
      ? 'state-live'
      : ['OFFLINE', 'REJECTED'].includes(state)
        ? 'state-error'
        : ['DEGRADED', 'NOT_CONFIGURED'].includes(state)
          ? 'state-warning'
          : 'state-unknown'
  }`;
}

function renderOmegaViews() {
  const payload = channelData.get('omega');
  const accessRequired = payload?.access?.status === 'AUTHENTICATION_REQUIRED'
    || (!payload && bridge?.getProvenance?.() === 'LIVE' && !provider?.authenticated);
  const omega = payload?.access ? null : payload;
  const integration = omega?.integration || {};
  const linkState = integration.linkState || omega?.linkState || 'UNKNOWN';
  const authState = integration.authenticationState
    || (omega?.linkState === 'CONNECTED'
      ? 'AUTHENTICATED'
      : accessRequired
        ? 'NOT OBSERVABLE (OPERATOR AUTH REQUIRED)'
        : 'UNKNOWN');

  setStatePill('omegaReportedHealth', omega?.status?.overall_health || 'UNREPORTED');
  setText('omegaStatusSummary', omega?.status ? 'RECEIVED' : 'UNAVAILABLE');
  setText('omegaStatusDetail', omega?.status?.overall_health
    ? `OMEGA status reports ${omega.status.overall_health}.`
    : 'OMEGA status has not reported an overall health field.');
  setText('omegaModelSummary', omega?.model?.name || omega?.model?.provider || 'UNAVAILABLE');
  setText('omegaModelDetail', Array.isArray(omega?.model?.available)
    ? `Model names reported by OMEGA: ${omega.model.available.join(', ') || 'none'}. A listing does not prove a model is loaded.`
    : 'No model inventory was reported.');
  setText('omegaEvidenceCount', omega?.evidenceLedger?.total);
  setText('omegaEvidenceDetail', omega?.evidenceLedger?.isArray === true
    ? `Records returned in the current evidence-ledger sample; snapshot received ${receivedAt('omega')}.`
    : 'Evidence-ledger result unavailable.');
  setText('omegaMeasuredAt', omega?.measuredAt || 'Unavailable');
  setText('omegaMeasurementDetail', omega?.provenance
    ? `OMEGA provenance: ${omega.provenance}. Browser received snapshot: ${receivedAt('omega')}.`
    : 'No OMEGA measurement metadata received.');
  setText('omegaEnvironmentState', omega?.environment ? 'PROFILE RECEIVED' : 'UNAVAILABLE');
  setStatePill(
    'omegaEvidenceState',
    omega?.evidenceLedger?.isArray === true ? linkState : 'UNKNOWN'
  );
  setStatePill(
    'omegaDiagnosticsState',
    omega ? linkState : accessRequired ? 'AUTHENTICATION REQUIRED' : 'UNKNOWN'
  );
  setStatePill('omegaLinkState', linkState);
  setStatePill('omegaExchangeCount', Array.isArray(omega?.communicationTrace)
    ? `${omega.communicationTrace.length} RECORDS`
    : 'UNAVAILABLE');

  const accessNotice = byId('omegaAccessNotice');
  if (accessNotice) {
    accessNotice.textContent = accessRequired
      ? 'OMEGA state is withheld until this WebSocket session is authenticated.'
      : omega
        ? linkState === 'DEGRADED'
          ? 'The gateway has a last-known OMEGA snapshot, but its latest integration attempt failed. OMEGA-reported fields may be stale.'
          : linkState === 'CONNECTED'
            ? 'Authenticated OMEGA state received through the ULTRON gateway.'
            : `OMEGA state is not current: integration reports ${linkState}.`
        : 'No authenticated OMEGA snapshot has been received.';
    accessNotice.hidden = false;
    accessNotice.className = `notice ${accessRequired || linkState !== 'CONNECTED' ? 'notice-warning' : ''}`;
  }

  setJsonBlock('omegaStatusData', omega?.status, 'Unavailable: no authenticated OMEGA status report received.');
  setJsonBlock('omegaHealthData', omega?.health, 'Unavailable: no authenticated OMEGA health report received.');
  setJsonBlock('omegaModelData', omega?.model, 'Unavailable: no authenticated OMEGA model report received.');
  setJsonBlock('omegaEnvironmentData', omega?.environment, 'Unavailable: no authenticated OMEGA environment report received.');
  setJsonBlock(
    'omegaCliTrace',
    omega?.communicationTrace,
    accessRequired
      ? 'Authenticate the WebSocket session to inspect OMEGA-reported communication activity.'
      : 'Unavailable: no OMEGA communication trace received.'
  );
  setJsonBlock(
    'omegaDatabaseAnchor',
    omega?.databaseAnchor
      ? {
          present: omega.databaseAnchor.present,
          size: omega.databaseAnchor.size,
          sha256: omega.databaseAnchor.sha256
        }
      : undefined,
    'Unavailable: no authenticated OMEGA database-anchor report received.'
  );
  setJsonBlock(
    'omegaIntegrationFailure',
    integration.failure,
    omega ? 'No current integration failure reported.' : accessRequired
      ? 'Authenticate the WebSocket session to inspect integration state.'
      : 'No integration attempt detail received.'
  );

  setFactList('omegaFacts', [
    ['OMEGA version', omega?.version?.version],
    ['OMEGA provenance', omega?.provenance],
    ['OMEGA measured at', omega?.measuredAt],
    ['Read-only ability state', omega?.abilityState],
    ['Bridge latency', typeof omega?.bridgeLatencyMs === 'number' ? `${omega.bridgeLatencyMs} ms` : null],
    ['Database anchor present', omega?.databaseAnchor?.present],
    ['Database anchor size', typeof omega?.databaseAnchor?.size === 'number' ? `${omega.databaseAnchor.size} bytes` : null],
  ]);
  setFactList('omegaIntegrationFacts', [
    ['Gateway link state', linkState],
    ['Integration authentication', authState],
    ['Gateway token configured', typeof integration.tokenConfigured === 'boolean'
      ? (integration.tokenConfigured ? 'Yes' : 'No')
      : 'Not reported'],
    ['Last gateway attempt', integration.lastAttemptAt],
    ['Last successful request / response', integration.lastSuccessfulExchangeAt],
    ['Reported bridge latency', typeof omega?.bridgeLatencyMs === 'number' ? `${omega.bridgeLatencyMs} ms` : null],
    ['OMEGA state measured at', omega?.measuredAt],
  ]);

  const capabilitiesRoot = byId('omegaCapabilitiesList');
  const capabilities = omega?.capabilities;
  setText('omegaCapabilityCount', capabilities?.available === true
    ? `${capabilities.total ?? capabilities.capabilities?.length ?? 0} REPORTED`
    : capabilities?.available === false
      ? 'UNAVAILABLE'
      : 'UNKNOWN');
  setText('omegaCapabilityNotice', capabilities?.available === true
    ? `Read-only capability registry received${capabilities.lastUpdatedAt ? ` at ${capabilities.lastUpdatedAt}` : ''}.${linkState === 'DEGRADED' ? ' This snapshot may be last-known because the integration is degraded.' : ''}`
    : capabilities?.error?.message || (accessRequired
      ? 'Authenticate the WebSocket session to inspect OMEGA capabilities.'
      : 'No capability-registry result has been reported.'));
  if (capabilitiesRoot) {
    capabilitiesRoot.replaceChildren();
    if (!Array.isArray(capabilities?.capabilities)) {
      const empty = document.createElement('p');
      empty.className = 'empty-state';
      empty.textContent = accessRequired
        ? 'Authenticate the WebSocket session to inspect OMEGA capabilities.'
        : capabilities?.error?.message || 'Capability registry unavailable.';
      capabilitiesRoot.append(empty);
    } else if (!capabilities.capabilities.length) {
      const empty = document.createElement('p');
      empty.className = 'empty-state';
      empty.textContent = 'OMEGA returned an empty capability registry.';
      capabilitiesRoot.append(empty);
    } else {
      capabilities.capabilities.forEach((capability, index) => {
        const card = document.createElement('article');
        card.className = 'trading-record';
        const heading = document.createElement('h3');
        heading.textContent = typeof capability?.name === 'string'
          ? capability.name
          : `Capability ${index + 1}`;
        const details = document.createElement('pre');
        details.className = 'integration-json';
        details.textContent = JSON.stringify(capability, null, 2);
        card.append(heading, details);
        capabilitiesRoot.append(card);
      });
    }
  }

  const evidenceRoot = byId('omegaEvidenceList');
  if (evidenceRoot) {
    evidenceRoot.replaceChildren();
    const evidence = omega?.evidenceLedger;
    if (!Array.isArray(evidence?.sample)) {
      const empty = document.createElement('p');
      empty.className = 'empty-state';
      empty.textContent = accessRequired
        ? 'Authenticate the WebSocket session to inspect OMEGA evidence.'
        : 'No OMEGA evidence-ledger sample was reported.';
      evidenceRoot.append(empty);
    } else if (!evidence.sample.length) {
      const empty = document.createElement('p');
      empty.className = 'empty-state';
      empty.textContent = 'OMEGA returned an empty evidence-ledger sample.';
      evidenceRoot.append(empty);
    } else {
      evidence.sample.forEach((record, index) => {
        const card = document.createElement('article');
        card.className = 'trading-record';
        const title = document.createElement('h3');
        title.textContent = `Evidence record ${index + 1}`;
        const details = document.createElement('pre');
        details.className = 'integration-json';
        details.textContent = JSON.stringify(record, null, 2);
        card.append(title, details);
        evidenceRoot.append(card);
      });
    }
  }
  setText('omegaEvidenceSummary', omega?.evidenceLedger?.isArray === true
    ? `OMEGA returned ${omega.evidenceLedger.total ?? 'an unknown number of'} records in this evidence-ledger sample.`
    : accessRequired
      ? 'Authenticate the WebSocket session to inspect the OMEGA evidence ledger.'
      : 'No evidence ledger snapshot received.');

  const exchangesRoot = byId('omegaExchangeList');
  if (exchangesRoot) {
    exchangesRoot.replaceChildren();
    const trace = integration.exchangeTrace;
    if (!Array.isArray(trace)) {
      const empty = document.createElement('p');
      empty.className = 'empty-state';
      empty.textContent = accessRequired
        ? 'Authenticate the WebSocket session to inspect the integration trace.'
        : 'Gateway HTTP exchange trace unavailable.';
      exchangesRoot.append(empty);
    } else if (!trace.length) {
      const empty = document.createElement('p');
      empty.className = 'empty-state';
      empty.textContent = 'The gateway has not recorded any HTTP exchange attempts.';
      exchangesRoot.append(empty);
    } else {
      trace.forEach((entry, index) => {
        const card = document.createElement('article');
        card.className = 'trading-record';
        const heading = document.createElement('h3');
        heading.textContent = `${entry.method || 'HTTP'} ${entry.endpoint || 'endpoint unavailable'} · ${entry.responseValid === true ? 'Valid response' : entry.success === false ? 'Failed' : 'Response not validated'}`;
        const summary = document.createElement('p');
        summary.textContent = [
          entry.direction || 'Direction unavailable',
          entry.at || 'Timestamp unavailable',
          typeof entry.latencyMs === 'number' ? `${entry.latencyMs} ms` : 'Latency unavailable',
          entry.responseStatus === null || entry.responseStatus === undefined
            ? 'HTTP status unavailable'
            : `HTTP ${entry.responseStatus}`,
          entry.responseValid === true ? 'Response validated' : 'Response not validated'
        ].filter(Boolean).join(' · ');
        const details = document.createElement('pre');
        details.className = 'integration-json';
        details.textContent = JSON.stringify({
          direction: entry.direction,
          method: entry.method,
          endpoint: entry.endpoint,
          at: entry.at,
          latencyMs: entry.latencyMs,
          responseStatus: entry.responseStatus,
          success: entry.success,
          responseValid: entry.responseValid,
          error: entry.error
        }, null, 2);
        card.append(heading, summary, details);
        exchangesRoot.append(card);
      });
    }
  }
}

function formatEventTime(event) {
  const timestamp = runtimeEventTime(event);
  if (!timestamp) return 'Backend event time unavailable';
  return `${timestamp.label}: ${new Date(timestamp.milliseconds).toLocaleString()}`;
}

function eventRow(event) {
  const item = document.createElement('li');
  item.className = 'event-row';
  const time = document.createElement('time');
  time.className = 'event-time';
  const eventTime = formatEventTime(event);
  time.textContent = eventTime;
  const body = document.createElement('div');
  const type = document.createElement('span');
  type.className = 'event-type';
  type.textContent = event.type || 'EVENT';
  const summary = document.createElement('span');
  summary.className = 'event-summary';
  summary.textContent = event.summary || 'Description unavailable';
  const meta = document.createElement('div');
  meta.className = 'event-meta';
  meta.textContent = [
    event.source ? `Source: ${event.source}` : null,
    event.severity ? `Severity: ${event.severity}` : null,
    event.result ? `Result: ${event.result}` : null,
    `Event snapshot received in browser: ${receivedAt('runtimeEvents')}`,
  ].filter(Boolean).join(' · ') || `Provenance: ${event.provenance || 'Unknown'}`;
  body.append(type, summary, meta);
  const details = document.createElement('details');
  details.className = 'event-details';
  const summaryNode = document.createElement('summary');
  summaryNode.textContent = 'Inspect event';
  const fields = document.createElement('dl');
  [
    ['Event ID', event.eventId],
    ['Source', event.source],
    ['Severity', event.severity],
    ['Domain', event.domain],
    ['Provenance', event.provenance],
    ['Backend event time', eventTime],
    ['Runtime-event snapshot received by browser', receivedAt('runtimeEvents')],
    ['Result', event.result],
    ['Correlation ID', event.correlationId],
  ].forEach(([labelText, valueText]) => {
    if (valueText == null || valueText === '') return;
    const label = document.createElement('dt');
    const value = document.createElement('dd');
    label.textContent = labelText;
    value.textContent = String(valueText);
    fields.append(label, value);
  });
  details.append(summaryNode, fields);
  body.append(details);
  item.append(time, body);
  return item;
}

function updateEventFilterOptions(events) {
  [
    ['eventSeverity', 'All severities', 'severity'],
    ['eventSource', 'All sources', 'source'],
    ['eventType', 'All types', 'type'],
  ].forEach(([id, allLabel, field]) => {
    const select = byId(id);
    if (!select) return;
    const selected = select.value;
    select.replaceChildren(new Option(allLabel, ''));
    const options = eventFilterOptions(events, field);
    options.forEach((value) => select.add(new Option(value, value)));
    select.value = options.includes(selected) ? selected : '';
  });
  const timeRange = byId('eventTimeRange');
  if (timeRange) {
    const hasTimestamp = events.some((event) => runtimeEventTime(event) !== null);
    timeRange.disabled = !hasTimestamp;
    if (!hasTimestamp) timeRange.value = '';
  }
}

function renderEventList(list, targetId, emptyText, noMatchesText = 'The runtime reports no events.') {
  const target = byId(targetId);
  if (!target) return;
  target.replaceChildren();
  if (!Array.isArray(list)) {
    const empty = document.createElement('li');
    empty.className = 'empty-state';
    empty.textContent = emptyText;
    target.append(empty);
    return;
  }
  if (!list.length) {
    const empty = document.createElement('li');
    empty.className = 'empty-state';
    empty.textContent = noMatchesText;
    target.append(empty);
    return;
  }
  list.forEach((event) => target.append(eventRow(event)));
}

function renderEvents() {
  const events = getEvents();
  renderEventList(Array.isArray(events) ? events.slice(0, 5) : null, 'overviewEvents', 'No event data received.');
  if (Array.isArray(events)) {
    updateEventFilterOptions(events);
    const filtered = filterRuntimeEvents(events, {
      query: byId('eventSearch')?.value,
      severity: byId('eventSeverity')?.value,
      source: byId('eventSource')?.value,
      type: byId('eventType')?.value,
      timeRange: byId('eventTimeRange')?.value,
    });
    renderEventList(
      filtered,
      'activityEvents',
      'No event data received.',
      events.length ? 'No runtime events match the current filters.' : 'The runtime reports no events.'
    );
    setText('eventFilterSummary', `Showing ${filtered.length} of ${events.length} runtime events. Time filters use backend timestamp or gateway observation time; browser receive time is displayed separately.`);
  } else {
    renderEventList(null, 'activityEvents', 'No event data received.');
    setText('eventFilterSummary', 'Event filters are available when a runtime event snapshot arrives.');
  }
  const count = byId('eventCount');
  if (count) {
    count.textContent = Array.isArray(events) ? `${events.length} runtime events` : 'Unavailable';
    count.className = `state-pill ${Array.isArray(events) ? 'state-live' : 'state-unknown'}`;
  }
}

function renderOperations() {
  const root = byId('operationsList');
  const command = getCommand();
  const operations = command?.activeOperations;
  if (!root) return;
  root.replaceChildren();
  const counter = byId('operationCount');
  if (counter) {
    counter.textContent = Array.isArray(operations)
      ? provider?.authenticated
        ? `${operations.length} active`
        : `${operations.length} exposed · unauthenticated`
      : 'Unavailable';
    counter.className = `state-pill ${Array.isArray(operations) ? 'state-live' : 'state-unknown'}`;
  }
  if (!Array.isArray(operations)) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = 'No operation snapshot received.';
    root.append(empty);
    return;
  }
  if (!operations.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = provider?.authenticated
      ? 'The authenticated runtime reports no active operations.'
      : 'No active operations are exposed to this unauthenticated session.';
    root.append(empty);
    return;
  }
  operations.forEach((operation) => {
    const card = document.createElement('article');
    card.className = 'operation-card';
    const heading = document.createElement('div');
    heading.className = 'operation-top';
    const title = document.createElement('h2');
    title.textContent = operation.title || operation.verb || 'Operation';
    const state = document.createElement('span');
    state.className = 'state-pill';
    state.textContent = operation.lifecycleState || 'UNKNOWN';
    heading.append(title, state);
    const description = document.createElement('p');
    description.textContent = operation.intent || operation.description || 'Intent unavailable';
    const effect = document.createElement('p');
    effect.textContent = `Expected effect: ${operation.expectedEffect || 'Unavailable'}`;
    card.append(heading, description, effect);
    if (operation.lifecycleState === 'SUGGESTED' && provider?.authenticated) {
      const actions = document.createElement('div');
      actions.className = 'operation-actions';
      const approve = document.createElement('button');
      approve.className = 'button button-danger';
      approve.type = 'button';
      approve.textContent = 'Review approval';
      approve.addEventListener('click', () => openApproval(operation));
      actions.append(approve);
      card.append(actions);
    }
    root.append(card);
  });
}

function renderOperationHistory() {
  const root = byId('operationHistoryList');
  if (!root) return;
  const operations = channelData.get('operation');
  const count = byId('operationHistoryCount');
  const notice = byId('operationHistoryNotice');
  root.replaceChildren();
  if (count) {
    count.textContent = !Array.isArray(operations)
      ? 'Unavailable'
      : provider?.authenticated
        ? `${operations.length} records`
        : 'Withheld';
    count.className = 'state-pill state-unknown';
  }
  if (!Array.isArray(operations)) {
    if (notice) notice.textContent = 'No persisted operation snapshot received.';
    const empty = document.createElement('li');
    empty.className = 'empty-state';
    empty.textContent = 'No operation snapshot received.';
    root.append(empty);
    return;
  }
  if (!provider?.authenticated) {
    if (notice) notice.textContent = 'The gateway limits operation records for unauthenticated sessions.';
    const empty = document.createElement('li');
    empty.className = 'empty-state';
    empty.textContent = 'Authenticate to inspect persisted operation records.';
    root.append(empty);
    return;
  }
  if (notice) notice.textContent = 'Lifecycle records are read-only here; state changes only after backend confirmation.';
  if (!operations.length) {
    const empty = document.createElement('li');
    empty.className = 'empty-state';
    empty.textContent = 'The authenticated runtime reports no persisted operation records.';
    root.append(empty);
    return;
  }
  operations.slice().reverse().forEach((operation) => {
    const item = document.createElement('li');
    const heading = document.createElement('div');
    heading.className = 'inventory-heading';
    const title = document.createElement('h3');
    title.textContent = operation.title || operation.operationId || 'Operation';
    const state = document.createElement('span');
    state.className = 'state-pill state-unknown';
    state.textContent = operation.lifecycleState || 'State unavailable';
    heading.append(title, state);
    const description = document.createElement('p');
    description.textContent = operation.intent || operation.expectedEffect || 'Operation details unavailable.';
    const timestamp = operation.updatedAt || operation.createdAt || operation.timestamp;
    item.append(heading, description);
    if (timestamp) {
      const recorded = document.createElement('p');
      recorded.textContent = `Backend record time: ${formatEventTime({ timestamp })}`;
      item.append(recorded);
    }
    root.append(item);
  });
}

function renderAgents() {
  const root = byId('agentList');
  if (!root) return;
  const agents = getRuntime()?.agents;
  root.replaceChildren();
  const count = byId('agentPageCount');
  if (count) {
    count.textContent = Array.isArray(agents) ? `${agents.length} registered` : 'Unavailable';
    count.className = 'state-pill state-unknown';
  }
  if (!Array.isArray(agents)) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = 'No agent snapshot received.';
    root.append(empty);
    return;
  }
  if (!agents.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = 'The runtime reports no registered agents.';
    root.append(empty);
    return;
  }
  agents.forEach((agent) => {
    const card = document.createElement('article');
    card.className = 'inventory-card';
    const heading = document.createElement('div');
    heading.className = 'inventory-heading';
    const name = document.createElement('h2');
    name.textContent = typeof agent?.name === 'string' ? agent.name : 'Agent name unavailable';
    const status = document.createElement('span');
    status.className = 'state-pill state-unknown';
    status.textContent = 'Health not reported';
    heading.append(name, status);
    const role = document.createElement('p');
    role.textContent = `Role: ${typeof agent?.role === 'string' ? agent.role : 'Not reported'}`;
    const note = document.createElement('p');
    note.className = 'muted';
    note.textContent = 'Registration is reported; per-agent status and capabilities are not published.';
    card.append(heading, role, note);
    root.append(card);
  });
}

function renderSkills() {
  const root = byId('skillList');
  if (!root) return;
  const capabilities = channelData.get('device')?.capabilities;
  root.replaceChildren();
  const count = byId('skillCount');
  if (count) {
    count.textContent = Array.isArray(capabilities) ? `${capabilities.length} registered` : 'Unavailable';
    count.className = 'state-pill state-unknown';
  }
  if (!Array.isArray(capabilities)) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = 'No host capability snapshot received.';
    root.append(empty);
    return;
  }
  if (!capabilities.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = 'The runtime reports no registered skills.';
    root.append(empty);
    return;
  }
  capabilities.forEach((capability) => {
    const card = document.createElement('article');
    card.className = 'inventory-card';
    const heading = document.createElement('div');
    heading.className = 'inventory-heading';
    const name = document.createElement('h2');
    name.textContent = typeof capability?.name === 'string' ? capability.name : 'Skill name unavailable';
    const version = document.createElement('span');
    version.className = 'state-pill state-unknown';
    version.textContent = `Version ${capability?.version ?? 'not reported'}`;
    heading.append(name, version);
    const actionsTitle = document.createElement('p');
    actionsTitle.className = 'muted';
    actionsTitle.textContent = 'Registered actions';
    const actions = document.createElement('ul');
    actions.className = 'action-list';
    if (Array.isArray(capability?.actions) && capability.actions.length) {
      capability.actions.forEach((action) => {
        const item = document.createElement('li');
        item.textContent = String(action);
        actions.append(item);
      });
    } else {
      const item = document.createElement('li');
      item.textContent = 'Actions not reported';
      actions.append(item);
    }
    const note = document.createElement('p');
    note.className = 'muted';
    note.textContent = 'Registry information only; registration does not confirm readiness or execution availability.';
    card.append(heading, actionsTitle, actions, note);
    root.append(card);
  });
}

function renderTelemetry() {
  const root = byId('telemetryList');
  if (!root) return;
  root.replaceChildren();
  CHANNELS.forEach(([channel, label]) => {
    const details = document.createElement('details');
    details.className = 'telemetry-item';
    const summary = document.createElement('summary');
    summary.append(document.createTextNode(label));
    const when = document.createElement('span');
    when.textContent = receivedAt(channel);
    summary.append(when);
    const content = document.createElement('pre');
    const value = channelData.get(channel);
    content.textContent = value === undefined
      ? 'Unavailable: no snapshot received.'
      : JSON.stringify(value, null, 2);
    details.append(summary, content);
    root.append(details);
  });
}

function renderChannelStatus() {
  const root = byId('channelStatus');
  if (!root) return;
  root.replaceChildren();
  CHANNELS.forEach(([channel, label]) => {
    const row = document.createElement('li');
    const name = document.createElement('span');
    name.textContent = label;
    const time = document.createElement('small');
    time.textContent = receivedAt(channel);
    row.append(name, time);
    root.append(row);
  });
}

function renderDiagnostics() {
  const root = byId('diagnosticList');
  if (!root) return;
  root.replaceChildren();
  const live = bridge?.getProvenance?.() === 'LIVE';
  const runtime = getRuntime();
  const chatAuthenticated = live && Boolean(provider?.authenticated);
  const chatState = chatAuthenticated
    ? 'Authenticated'
    : live
      ? (provider?.authenticationRequired === true ? 'Authentication required' : 'Not authenticated')
      : 'Unavailable';
  const voiceState = !voiceHealthResult
    ? 'Unknown'
    : voiceHealthResult.ready
      ? 'Ready'
      : 'Unavailable';
  const diagnostics = [
    ['Backend transport', live ? 'Connected' : 'Unavailable',
      live ? 'WebSocket subscription acknowledged; transport does not imply chat authentication.' : 'No verified live subscription.'],
    ['Operator authentication', chatState,
      chatAuthenticated ? 'Chat and approval requests can be submitted for server authorization.' : 'Chat and approval requests are blocked in this browser session until authenticated.'],
    ['Runtime state', runtime ? String(runtime.behavior || 'Unknown') : 'Unknown',
      runtime ? `Backend reports running=${String(runtime.running)}; no separate readiness result is published.` : 'No cognitive-state snapshot received.'],
    ['Host / process telemetry', channelData.has('device') ? 'Host snapshot received' : 'Unknown',
      channelData.has('device') ? 'Host hardware and skill catalog are available; per-process telemetry is not published.' : 'No device capability snapshot received.'],
    ['Voice service', voiceState,
      voiceHealthResult?.ready
        ? `Health API reports active engine ${voiceHealthResult.engine || 'not specified'}. Playback remains a separate check.`
        : voiceHealthResult?.error || 'Health endpoint has not returned a ready result.'],
    ['AI model provider', 'Status not exposed',
      'The gateway publishes no local provider-health status; no model is claimed loaded or ready.'],
    ['Tasks and workflows', 'Operator API unavailable',
      'The gateway exposes no task/workflow listing, lifecycle, cancellation, or retry contract.'],
    ['Configuration changes', 'Unsupported',
      'The gateway exposes no configuration read/write action. No configuration editor is available.'],
  ];
  diagnostics.forEach(([labelText, stateText, detailText]) => {
    const card = document.createElement('article');
    card.className = 'diagnostic-card';
    const heading = document.createElement('div');
    heading.className = 'inventory-heading';
    const title = document.createElement('h2');
    title.textContent = labelText;
    const state = document.createElement('span');
    state.className = `state-pill ${['Connected', 'Authenticated', 'Ready'].includes(stateText) ? 'state-live' : 'state-unknown'}`;
    state.textContent = stateText;
    const detail = document.createElement('p');
    detail.textContent = detailText;
    heading.append(title, state);
    card.append(heading, detail);
    root.append(card);
  });

  const issues = byId('integrityIssues');
  if (!issues) return;
  issues.replaceChildren();
  if (!integrityIssues.length) {
    const empty = document.createElement('li');
    empty.className = 'empty-state';
    empty.textContent = 'No browser contract issues observed.';
    issues.append(empty);
    return;
  }
  integrityIssues.slice(-20).reverse().forEach((issue) => {
    const item = document.createElement('li');
    item.className = 'event-row';
    const time = document.createElement('time');
    time.className = 'event-time';
    time.textContent = `Observed in browser: ${new Date(issue.timestamp).toLocaleString()}`;
    const content = document.createElement('div');
    const title = document.createElement('strong');
    title.textContent = `Contract violation · ${issue.domain || 'unknown domain'}`;
    const detail = document.createElement('p');
    detail.className = 'event-meta';
    detail.textContent = issue.message || 'Validation detail unavailable.';
    content.append(title, detail);
    item.append(time, content);
    issues.append(item);
  });
}

function updateConnection() {
  const state = bridge?.getProvenance?.() || 'OFFLINE';
  const connected = state === 'LIVE';
  const transportConnected = provider?.transportConnected === true;
  if (connected && previousProvenance !== 'LIVE') {
    channelData.clear();
    channelTimes.clear();
    renderEvents();
    renderOperations();
    renderOperationHistory();
    renderAgents();
    renderSkills();
    renderTelemetry();
    renderChannelStatus();
    renderDiagnostics();
    renderTradingViews();
    renderOmegaViews();
    setText('lastUpdate', '—');
  }
  previousProvenance = state;
  connectionDot.dataset.state = connected ? 'LIVE' : state;
  connectionLabel.textContent = connected ? 'Backend connected' : state === 'OFFLINE'
    ? 'Backend offline'
    : 'Connecting to backend';
  const chatAuthenticated = connected && Boolean(provider?.authenticated);
  authLabel.textContent = chatAuthenticated
    ? 'Operator authenticated'
    : provider?.authenticationStatus === 'AUTHENTICATION_LOCKED'
      ? 'Local unlock temporarily locked'
    : provider?.authenticationStatus === 'AUTHENTICATION_CANCELLED'
      ? 'Authentication cancelled'
    : provider?.authenticationStatus === 'AUTHENTICATION_FAILED'
      ? 'Local passphrase not accepted'
    : provider?.authenticationStatus === 'AUTHENTICATION_UNAVAILABLE'
      ? 'Gateway authentication unavailable'
    : provider?.authenticationStatus === 'AUTHENTICATION_TIMEOUT'
      ? 'Gateway authentication timed out'
    : provider?.authenticationStatus === 'AUTHENTICATION_UPSTREAM_REJECTED'
      ? 'Gateway rejected configured authentication'
    : provider?.authenticationStatus === 'AUTHENTICATION_PROTOCOL_ERROR'
      ? 'Gateway authentication protocol error'
    : provider?.authenticationFailed
      ? 'Authentication failed'
    : transportConnected
      ? 'Chat requires operator authentication'
      : state === 'OFFLINE' && AUTH_MODE === 'local'
        ? 'Local auth helper unavailable'
      : 'Chat authentication unavailable';
  const capability = byId('chatCapability');
  if (capability) {
    capability.textContent = chatAuthenticated ? 'Authenticated session' : transportConnected ? 'Authentication required' : 'Backend unavailable';
    capability.className = `state-pill ${chatAuthenticated ? 'state-live' : transportConnected ? 'state-unknown' : 'state-error'}`;
  }
  const chatNotice = byId('chatNotice');
  if (chatNotice) {
    chatNotice.textContent = chatAuthenticated
      ? 'Authenticated backend session. Messages are sent to the configured conversation manager.'
      : provider?.authenticationStatus === 'AUTHENTICATION_CANCELLED'
        ? 'Authentication was cancelled before a credential was sent.'
      : provider?.authenticationStatus === 'AUTHENTICATION_FAILED'
        ? 'The local passphrase was not accepted. Check the exact text or change it locally with the protected helper setup command.'
      : provider?.authenticationStatus === 'AUTHENTICATION_LOCKED'
        ? (provider.authenticationError || 'Unlock is temporarily locked. Wait for the displayed retry time.')
      : provider?.authenticationStatus === 'AUTHENTICATION_UNAVAILABLE'
        ? (provider.authenticationError || 'The protected gateway is unavailable.')
      : provider?.authenticationStatus === 'AUTHENTICATION_TIMEOUT'
        ? (provider.authenticationError || 'Protected gateway authentication timed out. Reconnect to try again.')
      : provider?.authenticationStatus === 'AUTHENTICATION_UPSTREAM_REJECTED'
        ? (provider.authenticationError || 'The protected gateway rejected its configured credential.')
      : provider?.authenticationStatus === 'AUTHENTICATION_PROTOCOL_ERROR'
        ? (provider.authenticationError || 'The protected gateway returned an invalid authentication response.')
      : provider?.authenticationFailed
        ? (provider.authenticationError || 'Authentication failed. Reconnect to try again.')
        : transportConnected
          ? 'The authentication helper is connected. Complete operator unlock to open an authenticated chat session.'
        : state === 'OFFLINE' && AUTH_MODE === 'local'
          ? 'The local unlock helper is offline. Start it with the documented local auth helper command.'
        : connected
          ? 'Telemetry is connected, but authenticated chat is unavailable. Configure the operator authenticator on the backend and reconnect.'
          : 'Chat is unavailable until the authenticated backend connection is established.';
    chatNotice.className = `notice ${chatAuthenticated ? '' : 'notice-warning'}`;
  }
  document.getElementById('chatSend').disabled = !chatAuthenticated || chatPending || !chatInput.value.trim();
  document.getElementById('reconnectButton').disabled = connected && chatAuthenticated;
  const nav = byId('navProvenance');
  if (nav) nav.textContent = connected
    ? `Snapshot subscription verified · chat ${chatAuthenticated ? 'authenticated' : 'unauthenticated'}`
    : 'No verified connection';
  renderOverview();
  renderOperations();
  renderOperationHistory();
  renderDiagnostics();
  renderTradingViews();
  renderOmegaViews();
  renderCreStatus();
}

function requestOperatorCredential() {
  return new Promise((resolve, reject) => {
    const dialog = byId('operatorAuthDialog');
    const form = byId('operatorAuthForm');
    const input = byId('operatorAuthToken');
    const cancel = byId('operatorAuthCancel');
    const tokenMode = AUTH_MODE === 'token';
    byId('operatorAuthTitle').textContent = tokenMode ? 'Authenticate operator' : 'Unlock ULTRON';
    byId('operatorAuthPrompt').textContent = tokenMode
      ? 'Enter the existing operator token. It is sent only to the loopback gateway; this dashboard does not store it.'
      : 'Enter your local unlock passphrase. It is sent only to the loopback helper; the gateway token is never sent to this page.';
    byId('operatorAuthLabel').textContent = tokenMode ? 'Gateway token' : 'Unlock passphrase';
    byId('operatorAuthModeLink').href = tokenMode ? '?auth=local' : '?auth=token';
    byId('operatorAuthModeLink').textContent = tokenMode
      ? 'Use local unlock password'
      : 'Use gateway token instead';
    function cleanup() {
      form.removeEventListener('submit', submit);
      cancel.removeEventListener('click', cancelAuth);
      dialog.removeEventListener('cancel', cancelAuth);
    }
    function submit(event) {
      event.preventDefault();
      const credential = input.value;
      input.value = '';
      cleanup();
      dialog.close();
      resolve(credential);
    }
    function cancelAuth() {
      input.value = '';
      cleanup();
      if (dialog.open) dialog.close();
      const error = new Error('Operator authentication was cancelled');
      error.code = 'AUTHENTICATION_CANCELLED';
      reject(error);
    }
    form.addEventListener('submit', submit);
    cancel.addEventListener('click', cancelAuth);
    dialog.addEventListener('cancel', cancelAuth);
    dialog.showModal();
    input.focus();
  });
}

function appendChat(speaker, text, context = '') {
  byId('chatEmpty')?.remove();
  const item = document.createElement('li');
  item.className = `chat-entry ${speaker}`;
  const label = document.createElement('div');
  label.className = 'chat-entry-label';
  label.textContent = speaker === 'user' ? 'YOU' : 'ULTRON · CONFIGURED MODEL';
  const message = document.createElement('div');
  message.className = speaker === 'assistant' ? 'chat-entry-text' : 'chat-entry-text plain-text';
  if (speaker === 'assistant') renderMarkdownInto(message, text);
  else message.textContent = text;
  item.append(label, message);
  if (speaker === 'assistant') {
    const actions = document.createElement('div');
    actions.className = 'chat-actions';
    const play = document.createElement('button');
    play.className = 'button button-quiet';
    play.type = 'button';
    play.textContent = 'Play response';
    play.disabled = !voiceEnabled.checked;
    play.addEventListener('click', () => playSpeech(text, context));
    actions.append(play);
    item.append(actions);
  }
  chatHistory.append(item);
  chatHistory.scrollTop = chatHistory.scrollHeight;
}

async function submitChat(event) {
  event.preventDefault();
  if (chatPending) return;
  const text = chatInput.value.trim();
  if (!text) {
    setText('chatProgress', 'Enter a message before sending.');
    return;
  }
  if (!provider?.authenticated || bridge.getProvenance() !== 'LIVE') {
    setText('chatProgress', 'Not sent: an authenticated live backend session is required.');
    return;
  }
  appendChat('user', text);
  chatInput.value = '';
  chatPending = true;
  setText('chatProgress', 'Waiting for the configured ULTRON language model…');
  updateConnection();
  try {
    const result = await bridge.sendChat(text);
    const timingId = ++voiceTimingSequence;
    markVoiceTiming(timingId, 'response_received');
    if (!result || result.success !== true || typeof result.response !== 'string' || !result.response.trim()) {
      throw new Error('Backend did not return a valid assistant response.');
    }
    appendChat('assistant', result.response, text);
    markVoiceTiming(timingId, 'response_rendered');
    measureVoiceTiming(timingId, 'response-render', 'response_received', 'response_rendered');
    setText('chatProgress', 'Response received from the configured backend model.');
    if (voiceEnabled.checked && voiceAuto.checked) {
      await playSpeech(result.response, text, { timingId, sentenceSequence: true });
    }
  } catch (error) {
    setText('chatProgress', `Chat failed: ${error.message}`);
  } finally {
    chatPending = false;
    updateConnection();
  }
}

async function loadVoiceModules() {
  try {
    const clientModule = await import('./services/voiceClient.js');
    const playbackModule = await import('./services/voicePlayback.js');
    voiceClient = new clientModule.default();
    voicePlayback = new playbackModule.VoicePlayback({
      client: voiceClient,
      onState: renderVoiceState,
    });
    await checkVoiceHealth();
  } catch (error) {
    setVoiceState(`Voice client unavailable: ${error.message}`, true);
  }
}

function setVoiceState(message, error = false) {
  const status = byId('voiceStatus');
  status.textContent = message;
  status.classList.toggle('error-text', error);
}

function renderVoiceState(state) {
  if (state.status === 'generating') setVoiceState('Generating audio from the configured local speech service…');
  else if (state.status === 'playing') setVoiceState(`Playing generated audio${state.engine ? ` · engine ${state.engine}` : ''}.`);
  else if (state.status === 'finished') setVoiceState('Playback finished.');
  else if (state.status === 'stopped') setVoiceState('Speech generation or playback stopped.');
  else if (state.status === 'error') setVoiceState(`Voice unavailable: ${state.message || 'synthesis or playback failed.'}`, true);
  byId('stopSpeaking').disabled = !voicePlayback?.current;
}

async function checkVoiceHealth() {
  if (!voiceClient) {
    setVoiceState('Voice client is unavailable.', true);
    return;
  }
  setVoiceState('Checking configured voice service…');
  const serviceState = byId('voiceServiceState');
  if (serviceState) {
    serviceState.textContent = 'Checking';
    serviceState.className = 'state-pill state-unknown';
  }
  const [result, engines, styles] = await Promise.all([
    voiceClient.health(),
    voiceClient.engines(),
    voiceClient.styles(),
  ]);
  voiceHealthResult = result;
  voiceHealthCheckedAt = new Date();
  if (result?.ready) {
    setVoiceState(`Speech service ready · active engine ${result.engine || 'not specified'}.`);
    if (serviceState) {
      serviceState.textContent = 'Ready';
      serviceState.className = 'state-pill state-live';
    }
  } else {
    setVoiceState(`Speech service unavailable${result?.error ? `: ${result.error}` : ''}.`, true);
    if (serviceState) {
      serviceState.textContent = 'Unavailable';
      serviceState.className = 'state-pill state-error';
    }
  }
  const catalog = byId('voiceCatalog');
  if (catalog) {
    const engineNames = Array.isArray(engines?.engines) ? engines.engines.join(', ') : 'unavailable';
    const styleNames = Array.isArray(styles?.styles) ? styles.styles.join(', ') : 'unavailable';
    catalog.textContent = `Reported engine catalog: ${engineNames}. Reported delivery styles: ${styleNames}. Catalog entries do not indicate a loaded engine or playback success.`;
  }
  renderDiagnostics();
  renderOverview();
}

async function playSpeech(text, context, { timingId = ++voiceTimingSequence, sentenceSequence = false } = {}) {
  if (!voiceEnabled.checked) {
    setVoiceState('Enable voice output before playing a response.', true);
    return;
  }
  if (!voicePlayback) {
    setVoiceState('Voice playback client is unavailable.', true);
    return;
  }
  markVoiceTiming(timingId, 'speech_sequence_started');
  let requestTimingId = 0;
  let activeRequestTimingId = 0;
  const onTiming = (event) => {
    if (event === 'request_started') {
      activeRequestTimingId = ++requestTimingId;
      markVoiceTiming(timingId, `request-${activeRequestTimingId}-started`);
      measureVoiceTiming(
        timingId,
        'render-to-request',
        'response_rendered',
        `request-${activeRequestTimingId}-started`,
      );
      measureVoiceTiming(
        timingId,
        'response-to-request',
        'response_received',
        `request-${activeRequestTimingId}-started`,
      );
      return;
    }
    if (event === 'audio_received') {
      markVoiceTiming(timingId, `request-${activeRequestTimingId}-audio-received`);
      measureVoiceTiming(
        timingId,
        `request-${activeRequestTimingId}-duration`,
        `request-${activeRequestTimingId}-started`,
        `request-${activeRequestTimingId}-audio-received`,
      );
      return;
    }
    markVoiceTiming(timingId, event);
    if (event === 'playback_started') {
      measureVoiceTiming(timingId, 'response-to-playback', 'response_received', 'playback_started');
    }
  };
  const result = sentenceSequence
    ? await voicePlayback.playSequence(text, context, { onTiming })
    : await voicePlayback.play(text, context, { onTiming });
  if (!result.ok && result.status !== 'CANCELLED') {
    setVoiceState(`Voice unavailable: ${result.error || result.status}`, true);
  }
}

async function playVoiceTest() {
  const text = byId('voiceTestText').value.trim();
  if (!text) {
    setVoiceState('Enter text before generating audio.', true);
    return;
  }
  await playSpeech(text);
}

function openInspector() {
  inspector.hidden = false;
  backdrop.hidden = false;
  document.querySelector('.shell').classList.add('inspector-open');
  inspectorButton.setAttribute('aria-expanded', 'true');
  byId('closeInspector').focus();
}

function closeInspector() {
  inspector.hidden = true;
  backdrop.hidden = true;
  document.querySelector('.shell').classList.remove('inspector-open');
  inspectorButton.setAttribute('aria-expanded', 'false');
  inspectorButton.focus();
}

function openApproval(operation) {
  if (!provider?.authenticated || bridge.getProvenance() !== 'LIVE') return;
  currentApproval = operation;
  byId('approvalDetails').textContent = JSON.stringify(operation, null, 2);
  byId('approvalError').textContent = '';
  byId('approvalConfirm').disabled = false;
  approvalDialog.showModal();
  byId('approvalCancel').focus();
}

async function approveOperation(event) {
  event.preventDefault();
  if (!currentApproval || !provider?.authenticated || bridge.getProvenance() !== 'LIVE') return;
  const confirm = byId('approvalConfirm');
  confirm.disabled = true;
  byId('approvalError').textContent = 'Sending approval to the backend…';
  try {
    const result = await bridge.approveOperation(currentApproval);
    const feedback = byId('operationFeedback');
    feedback.hidden = false;
    feedback.textContent = `Backend approval response: ${result.status || 'accepted'}. Operation state will update only from runtime telemetry.`;
    byId('approvalError').textContent = result.status
      ? `Backend result: ${result.status}`
      : 'Approval request accepted by backend.';
    currentApproval = null;
    approvalDialog.close();
  } catch (error) {
    byId('approvalError').textContent = `Approval failed: ${error.message}`;
  } finally {
    confirm.disabled = false;
  }
}

function selectView(name) {
  const requestedView = name;
  const targetName = name;
  const targetView = document.getElementById(`view-${targetName}`)
    || byId('view-not-implemented');
  const selectedButton = Array.from(document.querySelectorAll('.nav-button'))
    .find((button) => button.dataset.view === requestedView);
  if (!document.getElementById(`view-${targetName}`) && selectedButton) {
    setText('notImplementedTitle', selectedButton.textContent.trim());
    setText(
      'notImplementedWorkspace',
      selectedButton.closest('.nav-workspace')?.querySelector('summary')?.textContent.trim() || 'WORKSPACE',
    );
  }
  document.querySelectorAll('.view').forEach((view) => {
    const active = view === targetView;
    view.hidden = !active;
    view.classList.toggle('active', active);
  });
  document.querySelectorAll('.nav-button').forEach((button) => {
    const active = button.dataset.view === requestedView;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  selectedButton?.closest('.nav-workspace')?.setAttribute('open', '');
  byId('mainContent').focus({ preventScroll: true });
  if (location.hash !== `#${name}`) history.replaceState(null, '', `#${name}`);
}

function initialize() {
  if (!bridge || !Provider) {
    connectionLabel.textContent = 'Integration layer unavailable';
    byId('disconnectNotice').textContent = 'Required backend integration modules did not load.';
    byId('chatSend').disabled = true;
    return;
  }
  provider = new Provider(
    AUTH_MODE === 'token'
      ? 'ws://127.0.0.1:8080/ultron/stream'
      : 'ws://127.0.0.1:8081/ultron/stream',
    requestOperatorCredential
  );
  try {
    const savedRender = readSavedCrePrivateRender();
    if (savedRender) {
      crePrivateProjectId = savedRender.projectId;
      crePrivateRenderOperationId = savedRender.operationId;
      byId('creProjectRender').hidden = !crePrivateRenderOperationId;
      byId('creProjectRender').disabled = !crePrivateRenderOperationId;
      byId('creRevisionRender').hidden = true;
      if (crePrivateRenderOperationId) {
        byId('creProjectFeedback').textContent =
          'A previous private render operation is saved. Authenticate to recover its persisted status without submitting another render.';
        byId('creProjectFeedback').className = 'notice notice-warning';
      }
    }
  } catch (error) {
    byId('creProjectFeedback').textContent =
      `Saved render recovery data could not be read: ${error.message}`;
    byId('creProjectFeedback').className = 'notice notice-warning';
  }
  bridge.subscribe('provenance', () => {
    updateConnection();
    updateCreFileSelection();
    if (provider?.authenticated && bridge?.getProvenance() === 'LIVE') {
      recoverSavedCrePrivateRender();
    }
  });
  provider.subscribeCreStatus((state) => renderCreStatus(state));
  provider.subscribeSystemIntelligence(renderSystemIntelligence);
  bridge.onIntegrity((issue) => {
    if (issue.kind !== 'CONTRACT_VIOLATION') return;
    integrityIssues.push(issue);
    if (integrityIssues.length > 20) integrityIssues.shift();
    renderDiagnostics();
  });
  CHANNELS.forEach(([channel]) => bridge.subscribe(channel, (payload) => recordChannel(channel, payload)));
  bridge.connectProvider(provider);
  updateConnection();
  renderOperationHistory();
  renderAgents();
  renderSkills();
  renderDiagnostics();
  renderChannelStatus();
  renderOmegaViews();
  byId('voiceTestButton').disabled = !voiceEnabled.checked;
  loadVoiceModules();
  setInterval(updateConnection, 1000);

  document.querySelectorAll('.nav-button').forEach((button) => {
    button.addEventListener('click', () => selectView(button.dataset.view));
  });
  document.querySelectorAll('[data-go]').forEach((button) => {
    button.addEventListener('click', () => selectView(button.dataset.go));
  });
  chatForm.addEventListener('submit', submitChat);
  chatInput.addEventListener('input', updateConnection);
  chatInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      chatForm.requestSubmit();
    }
  });
  byId('reconnectButton').addEventListener('click', () => provider.reconnect());
  byId('creRefreshButton').addEventListener('click', () => {
    byId('creRefreshButton').disabled = true;
    provider.requestCreStatus()
      .catch(() => {})
      .finally(() => { byId('creRefreshButton').disabled = false; });
  });
  byId('systemRefreshButton').addEventListener('click', () => {
    byId('systemRefreshButton').disabled = true;
    provider.requestSystemIntelligence()
      .catch(() => {})
      .finally(() => { byId('systemRefreshButton').disabled = false; });
  });
  byId('restartAuthButton').addEventListener('click', async () => {
    const button = byId('restartAuthButton');
    const result = byId('systemActionResult');
    if (!provider?.authenticated || systemIntelligenceState.state !== 'AVAILABLE') return;
    button.disabled = true;
    result.textContent = 'Helper-only restart requested; waiting for the owning manager result.';
    result.className = 'notice';
    try {
      const response = await provider.restartAuthenticationHelper();
      result.textContent = `${response.status}: ${response.payload?.detail || 'Manager returned no detail.'}`;
      result.className = response.success ? 'notice' : 'notice notice-warning';
      await provider.requestSystemIntelligence();
    } catch (error) {
      result.textContent = `Restart not confirmed: ${error.message}. Inventory is not marked healthy by this request.`;
      result.className = 'notice notice-warning';
    } finally {
      renderSystemIntelligence(systemIntelligenceState);
    }
  });
  byId('creScriptPreviewForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = byId('creScriptPreviewButton');
    const feedback = byId('creScriptPreviewFeedback');
    const output = byId('creScriptPreviewOutput');
    const video = byId('creScriptPreviewVideo');
    button.disabled = true;
    output.hidden = true;
    creScriptPreviewResult = null;
    output.removeAttribute('data-script-id');
    output.removeAttribute('data-preview-sha256');
    byId('creScriptPreviewReviewForm').reset();
    byId('creScriptReviewFeedback').textContent = 'No editorial review recorded.';
    feedback.textContent = 'Submitting the script to the authenticated local preview service…';
    feedback.className = 'notice';
    if (creScriptPreviewUrl) {
      URL.revokeObjectURL(creScriptPreviewUrl);
      creScriptPreviewUrl = null;
    }
    video.removeAttribute('src');
    video.load();
    try {
      const script = byId('creScriptInput').value;
      if (Array.from(script).length > 20000) {
        feedback.textContent = 'Script must be no more than 20,000 Unicode characters. Nothing was submitted.';
        feedback.className = 'notice notice-warning';
        button.disabled = false;
        return;
      }
      const references = byId('creScriptReferences').value
        .split(/\r?\n/)
        .map((url) => url.trim())
        .filter(Boolean)
        .map((url, index) => ({ label: `Reference ${index + 1}`, url }));
      const result = await provider.requestCreScriptPreview(script, references);
      const binary = atob(result.preview.base64);
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) {
        bytes[index] = binary.charCodeAt(index);
      }
      creScriptPreviewUrl = URL.createObjectURL(
        new Blob([bytes], { type: result.preview.mimeType }),
      );
      video.src = creScriptPreviewUrl;
      video.load();
      creScriptPreviewResult = result;
      output.dataset.scriptId = result.scriptId;
      output.dataset.previewSha256 = result.preview.sha256;
      renderCreScriptPreviewDetails(result);
      output.hidden = false;
      feedback.textContent = 'Local narrated preview rendered. Signal and layout checks passed, but intelligibility, naturalness, factual accuracy, and rights are not established. Review the artifact before recording any assessment. Nothing was published.';
      feedback.className = 'notice notice-warning';
    } catch (error) {
      feedback.textContent = error?.message || 'Local script preview failed.';
      feedback.className = 'notice notice-warning';
    } finally {
      button.disabled = false;
    }
  });
  byId('creScriptFactualAttestation').addEventListener('change', (event) => {
    byId('creScriptReviewNotes').required = event.target.checked;
  });
  const creScriptInput = byId('creScriptInput');
  const creScriptCharacterCount = byId('creScriptCharacterCount');
  const updateCreScriptCharacterCount = () => {
    const count = Array.from(creScriptInput.value).length;
    creScriptCharacterCount.textContent = `${count.toLocaleString()} / 20,000 characters`;
    creScriptCharacterCount.className = count > 20000 ? 'notice notice-warning' : 'source-note';
  };
  creScriptInput.addEventListener('input', updateCreScriptCharacterCount);
  updateCreScriptCharacterCount();

  const creSourceVideo = byId('creSourceVideo');
  const creSourceSelectedFile = byId('creSourceSelectedFile');
  const creSourceCount = byId('creSourceCount');
  const creFolderFileList = byId('creFolderFileList');
  const creFolderPathInput = byId('creFolderPath');
  const creFolderAvailability = byId('creFolderAvailability');
  const creFolderScanButton = byId('creFolderScan');
  const creFolderRegisterButton = byId('creFolderRegister');
  const creAddMenu = byId('creAddMenu');
  const creAddMenuToggle = byId('creAddMenuToggle');
  const creAddMenuList = byId('creAddMenuList');
  const creClearMenuItem = creAddMenuList.querySelector('[data-cre-file-action="clear"]');
  let selectedCreFiles = [];
  let creFolderFiles = [];
  let selectedCreFolderFileIds = new Set();
  const creImportedFolderAssets = new Map();
  let creFolderScanInProgress = false;
  let creFolderImportInProgress = false;
  let creEditorOperationInProgress = false;
  let creProjectCreationOutcomeUnknown = false;
  const isValidCreFile = (file) => {
    const extension = String(file.name || '').toLowerCase().match(/\.[^.]+$/)?.[0] || '';
    const allowedExtensions = new Set(['.mp4', '.webm', '.mov', '.mkv']);
    const allowedMimeTypes = new Set(['video/mp4', 'video/webm', 'video/quicktime', 'video/x-matroska']);
    return file.size > 0
      && file.size <= 32 * 1024 * 1024
      && allowedExtensions.has(extension)
      && (!file.type || allowedMimeTypes.has(file.type));
  };
  const formatCreFileSize = (size) => {
    if (size === 0) return '0 B';
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KiB`;
    return `${(size / (1024 * 1024)).toFixed(1)} MiB`;
  };
  const addCreFiles = (files) => {
    const invalidFiles = [];
    for (const file of Array.from(files || [])) {
      if (!isValidCreFile(file)) {
        invalidFiles.push(file.name || 'Unnamed file');
        continue;
      }
      if (!selectedCreFiles.some((selected) => selected.name === file.name
        && selected.size === file.size && selected.lastModified === file.lastModified)) {
        selectedCreFiles.push(file);
      }
    }
    updateCreFileSelection();
    const feedback = byId('creSourceFeedback');
    if (invalidFiles.length) {
      feedback.textContent = `Not added: ${invalidFiles.join(', ')}. Choose non-empty MP4, WebM, MOV, or MKV files no larger than 32 MiB each.`;
      feedback.className = 'notice notice-warning';
    } else if (selectedCreFiles.length) {
      feedback.textContent = `${selectedCreFiles.length} video${selectedCreFiles.length === 1 ? '' : 's'} selected. Nothing is uploaded until you click Start editing.`;
      feedback.className = 'notice';
    }
  };
  const updateCreFileSelection = () => {
    creSourceSelectedFile.replaceChildren();
    const selectedImportedAssets = [...creImportedFolderAssets.entries()]
      .filter(([fileId]) => selectedCreFolderFileIds.has(fileId))
      .map(([, asset]) => asset);
    const selectedFolderCount = creFolderFiles.filter((file) => selectedCreFolderFileIds.has(file.fileId)).length;
    const totalSelectedCount = selectedCreFiles.length + selectedFolderCount;
    creSourceCount.textContent = totalSelectedCount
      ? `${totalSelectedCount} ${totalSelectedCount === 1 ? 'video' : 'videos'} selected`
      : 'No videos selected';
    creClearMenuItem.hidden = totalSelectedCount === 0;
    const authenticatedAndLive = Boolean(provider?.authenticated)
      && bridge?.getProvenance() === 'LIVE';
    const configuredImportPath = '/home/ultron/Videos/CRE_Import';
    const importPathIsAuthorized = creFolderPathInput.value.trim() === configuredImportPath;
    const backendIsLive = bridge?.getProvenance() === 'LIVE';
    if (!backendIsLive) {
      const authDetail = provider?.authenticationStatus === 'AUTHENTICATION_CANCELLED'
        ? 'Authentication was cancelled before a credential was sent.'
        : provider?.authenticationError
          || (authLabel.textContent !== 'Operator authenticated' ? authLabel.textContent : '');
      creFolderAvailability.textContent = authDetail
        ? `Backend offline. ${authDetail} Reconnect and authenticate to scan.`
        : 'Backend offline. Reconnect and authenticate to scan the import folder.';
      creFolderAvailability.className = 'source-note notice-warning';
    } else if (!provider?.authenticated) {
      creFolderAvailability.textContent = provider?.authenticationRequired === true
        ? 'Backend connected; operator authentication is required before scanning.'
        : 'Backend connected, but this session is not authenticated. Authenticate before scanning.';
      creFolderAvailability.className = 'source-note notice-warning';
    } else if (!importPathIsAuthorized) {
      creFolderAvailability.textContent = `This directory is not authorized. Only ${configuredImportPath} can be scanned.`;
      creFolderAvailability.className = 'source-note notice-warning';
    } else {
      creFolderAvailability.textContent = `Ready to scan the authorized directory: ${configuredImportPath}`;
      creFolderAvailability.className = 'source-note';
    }
    creFolderScanButton.disabled = !authenticatedAndLive
      || !importPathIsAuthorized
      || creEditorOperationInProgress
      || creFolderScanInProgress
      || creFolderImportInProgress;
    const selectedImportCandidates = creFolderFiles.filter((file) => (
      selectedCreFolderFileIds.has(file.fileId)
      && file.status === 'ELIGIBLE'
      && !creImportedFolderAssets.has(file.fileId)
    ));
    creFolderRegisterButton.disabled = !authenticatedAndLive
      || !importPathIsAuthorized
      || creEditorOperationInProgress
      || creFolderImportInProgress
      || selectedImportCandidates.length === 0;
    byId('creProjectCreate').disabled = creEditorOperationInProgress
      || creFolderImportInProgress
      || creProjectCreationOutcomeUnknown
      || !authenticatedAndLive
      || (selectedCreFiles.length < 1 && selectedImportedAssets.length < 1)
      || selectedCreFiles.some((file) => !isValidCreFile(file));
    if (!selectedCreFiles.length) {
      const empty = document.createElement('li');
      empty.className = 'cre-file-empty';
      empty.textContent = 'No local video selected.';
      creSourceSelectedFile.append(empty);
      return;
    }
    selectedCreFiles.forEach((file, index) => {
      const item = document.createElement('li');
      item.className = 'cre-file-item';
      const details = document.createElement('span');
      details.className = 'cre-file-details';
      const name = document.createElement('strong');
      name.textContent = file.name;
      const metadata = document.createElement('small');
      metadata.textContent = `${formatCreFileSize(file.size)} · ${file.type || 'video type not reported'}`;
      details.append(name, metadata);
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'cre-file-remove';
      remove.setAttribute('aria-label', `Remove ${file.name}`);
      remove.textContent = 'Remove';
      remove.addEventListener('click', () => {
        selectedCreFiles.splice(index, 1);
        updateCreFileSelection();
      });
      item.append(details, remove);
      creSourceSelectedFile.append(item);
    });
  };
  const updateCreFolderFileList = () => {
    creFolderFileList.replaceChildren();
    if (!creFolderFiles.length) {
      const empty = document.createElement('li');
      empty.className = 'cre-file-empty';
      empty.textContent = 'No videos discovered. Scan the configured import folder.';
      creFolderFileList.append(empty);
      return;
    }
    creFolderFiles.forEach((file) => {
      const row = document.createElement('li');
      row.className = 'cre-folder-file-row';
      row.dataset.status = file.status;
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = selectedCreFolderFileIds.has(file.fileId);
      checkbox.disabled = file.status !== 'ELIGIBLE';
      checkbox.setAttribute('aria-label', `Select ${file.fileName}`);
      checkbox.addEventListener('change', () => {
        if (checkbox.checked) selectedCreFolderFileIds.add(file.fileId);
        else selectedCreFolderFileIds.delete(file.fileId);
        updateCreFolderFileList();
        updateCreFileSelection();
      });
      const details = document.createElement('span');
      details.className = 'cre-file-details';
      const name = document.createElement('strong');
      name.textContent = file.fileName;
      const metadata = document.createElement('small');
      const registeredAsset = creImportedFolderAssets.get(file.fileId);
      metadata.dataset.status = file.status;
      metadata.textContent = registeredAsset
        ? `${formatCreFileSize(file.sizeBytes)} · Registered to this owner (${registeredAsset.assetId})`
        : `${formatCreFileSize(file.sizeBytes)} · ${file.status}: ${file.validation}`;
      details.append(name, metadata);
      const rowState = document.createElement('span');
      rowState.className = file.status === 'ELIGIBLE' ? 'state-pill state-live' : 'state-pill state-error';
      rowState.textContent = registeredAsset ? 'Registered' : file.status;
      row.append(checkbox, details, rowState);
      creFolderFileList.append(row);
    });
  };
  const closeCreAddMenu = (returnFocus = false) => {
    creAddMenuList.hidden = true;
    creAddMenuToggle.setAttribute('aria-expanded', 'false');
    if (returnFocus) creAddMenuToggle.focus();
  };
  const creUploadMenuItem = byId('creUploadMenuItem');
  creUploadMenuItem.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    try {
      if (typeof creSourceVideo.showPicker === 'function') creSourceVideo.showPicker();
      else creSourceVideo.click();
    } catch (error) {
      console.error('[CRE] Keyboard video picker invocation failed:', error);
      const feedback = byId('creSourceFeedback');
      feedback.textContent = `The video file picker could not be opened (${error.name || 'browser error'}). Try clicking Upload from device.`;
      feedback.className = 'notice notice-warning';
    }
  });
  ['pointerdown', 'click'].forEach((eventName) => {
    creSourceVideo.addEventListener(eventName, (event) => {
      console.debug(`[CRE] Native file input received ${eventName}.`, {
        trusted: event.isTrusted,
      });
    });
  });
  creAddMenuToggle.addEventListener('click', () => {
    const opening = creAddMenuList.hidden;
    creAddMenuList.hidden = !opening;
    creAddMenuToggle.setAttribute('aria-expanded', String(opening));
    if (opening) creAddMenuList.querySelector('[role="menuitem"]:not(:disabled):not([hidden])').focus();
  });
  creClearMenuItem.addEventListener('click', (event) => {
    event.preventDefault();
    selectedCreFiles = [];
    selectedCreFolderFileIds.clear();
    updateCreFileSelection();
    updateCreFolderFileList();
    byId('creSourceFeedback').textContent = 'Selection cleared. No files were uploaded.';
    byId('creSourceFeedback').className = 'notice';
    closeCreAddMenu(true);
  });
  creAddMenuList.addEventListener('keydown', (event) => {
    const items = Array.from(creAddMenuList.querySelectorAll('[role="menuitem"]:not(:disabled):not([hidden])'));
    const current = items.indexOf(document.activeElement);
    let next = current;
    if (event.key === 'ArrowDown') next = (current + 1) % items.length;
    else if (event.key === 'ArrowUp') next = (current - 1 + items.length) % items.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = items.length - 1;
    else if (event.key === 'Escape') {
      event.preventDefault();
      closeCreAddMenu(true);
      return;
    } else if (event.key === 'Tab') {
      event.preventDefault();
      if (event.shiftKey) closeCreAddMenu(true);
      else {
        closeCreAddMenu();
        byId('creAutonomousInstruction').focus();
      }
      return;
    } else return;
    event.preventDefault();
    items[next]?.focus();
  });
  document.addEventListener('pointerdown', (event) => {
    if (!creAddMenu.contains(event.target)) closeCreAddMenu();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !creAddMenuList.hidden) closeCreAddMenu(true);
  });
  creSourceSelectedFile.addEventListener('dragenter', (event) => {
    event.preventDefault();
    creSourceSelectedFile.classList.add('is-drop-target');
  });
  creSourceSelectedFile.addEventListener('dragover', (event) => {
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
    creSourceSelectedFile.classList.add('is-drop-target');
  });
  creSourceSelectedFile.addEventListener('dragleave', (event) => {
    if (!creSourceSelectedFile.contains(event.relatedTarget)) {
      creSourceSelectedFile.classList.remove('is-drop-target');
    }
  });
  creSourceSelectedFile.addEventListener('drop', (event) => {
    event.preventDefault();
    creSourceSelectedFile.classList.remove('is-drop-target');
    addCreFiles(event.dataTransfer?.files);
  });
  creSourceVideo.addEventListener('change', () => {
    console.debug('[CRE] Native file picker returned a selection.', {
      count: creSourceVideo.files?.length || 0,
    });
    addCreFiles(creSourceVideo.files);
    creSourceVideo.value = '';
    closeCreAddMenu();
    creAddMenuToggle.focus();
  });
  creSourceVideo.addEventListener('cancel', () => {
    console.debug('[CRE] Native file picker was cancelled.');
    closeCreAddMenu();
    creAddMenuToggle.focus();
  });
  creFolderScanButton.addEventListener('click', async () => {
    if (creFolderScanInProgress || creFolderImportInProgress || creEditorOperationInProgress) return;
    if (!provider?.authenticated || bridge?.getProvenance() !== 'LIVE') {
      byId('creFolderFeedback').textContent = 'Authenticate and connect to the backend before scanning the import folder.';
      byId('creFolderFeedback').className = 'notice notice-warning';
      return;
    }
    const directory = creFolderPathInput.value.trim();
    if (directory !== '/home/ultron/Videos/CRE_Import') {
      creFolderFeedback.textContent = 'Scan rejected. Only /home/ultron/Videos/CRE_Import is authorized.';
      creFolderFeedback.className = 'notice notice-warning';
      updateCreFileSelection();
      return;
    }
    creFolderScanInProgress = true;
    creFolderScanButton.disabled = true;
    const feedback = byId('creFolderFeedback');
    feedback.textContent = `Scanning ${directory} on the authenticated backend…`;
    feedback.className = 'notice';
    try {
      const response = await provider.scanCreImportFolder(directory);
      if (response.status !== 'IMPORT_SCAN_READY' || !Array.isArray(response.payload?.files)) {
        throw new Error(response.error || response.status || 'The import folder could not be scanned.');
      }
      creFolderFiles = response.payload.files;
      selectedCreFolderFileIds = new Set(
        [...selectedCreFolderFileIds].filter((fileId) => creFolderFiles.some((file) => file.fileId === fileId)),
      );
      updateCreFolderFileList();
      feedback.textContent = `${creFolderFiles.length} video${creFolderFiles.length === 1 ? '' : 's'} found in ${directory}. Select eligible files to register.`;
      feedback.className = 'notice';
    } catch (error) {
      creFolderFiles = [];
      selectedCreFolderFileIds.clear();
      updateCreFolderFileList();
      feedback.textContent = `Folder scan failed: ${error?.response?.error || error.message || 'The backend returned no error detail.'}`;
      feedback.className = 'notice notice-warning';
    } finally {
      creFolderScanInProgress = false;
      updateCreFileSelection();
    }
  });
  creFolderRegisterButton.addEventListener('click', async () => {
    if (creFolderImportInProgress || creEditorOperationInProgress) return;
    const feedback = byId('creFolderFeedback');
    const selectedFiles = creFolderFiles.filter((file) => (
      selectedCreFolderFileIds.has(file.fileId)
      && file.status === 'ELIGIBLE'
      && !creImportedFolderAssets.has(file.fileId)
    ));
    if (!selectedFiles.length) {
      feedback.textContent = 'Select at least one eligible folder video before registering.';
      feedback.className = 'notice notice-warning';
      creFolderImportInProgress = false;
      return;
    }
    if (!provider?.authenticated || bridge?.getProvenance() !== 'LIVE') {
      feedback.textContent = 'No registration was sent. Authenticate and connect to the backend first.';
      feedback.className = 'notice notice-warning';
      return;
    }
    creFolderImportInProgress = true;
    updateCreFileSelection();
    let registeredCount = 0;
    const context = crePrivateImportContext();
    for (const file of selectedFiles) {
      try {
        const response = await provider.registerCreImportVideo({ fileId: file.fileId, ...context });
        const asset = response.payload?.asset;
        if (response.status !== 'SOURCE_REGISTERED' || !asset?.assetId || !/^[a-f0-9]{64}$/.test(asset.sha256)) {
          throw new Error(response.error || `The backend did not confirm registration for ${file.fileName}.`);
        }
        creImportedFolderAssets.set(file.fileId, asset);
        creRegisteredSourceAssets.push(asset);
        registeredCount += 1;
        updateCreFolderFileList();
      } catch (error) {
        const detail = error?.response?.error || error.message || 'No backend error detail was returned.';
        feedback.textContent = registeredCount
          ? `${registeredCount} video${registeredCount === 1 ? '' : 's'} registered. Registration stopped at ${file.fileName}: ${detail}`
          : `Registration failed for ${file.fileName}: ${detail}`;
        feedback.className = 'notice notice-warning';
        creFolderImportInProgress = false;
        return;
      } finally {
        updateCreFileSelection();
      }
    }
    creFolderImportInProgress = false;
    feedback.textContent = `${registeredCount} selected video${registeredCount === 1 ? '' : 's'} registered through the authenticated CRE source pipeline. Select them for Start Editing.`;
    feedback.className = 'notice';
    updateCreFolderFileList();
    updateCreFileSelection();
  });
  creFolderPathInput.addEventListener('input', updateCreFileSelection);
  updateCreFolderFileList();
  updateCreFileSelection();

  async function uploadSelectedCreVideos(files, feedback) {
    const allowedMimeTypes = new Set(['video/mp4', 'video/webm', 'video/quicktime', 'video/x-matroska']);
    const allowedExtensions = new Set(['.mp4', '.webm', '.mov', '.mkv']);
    if (files.length < 1) {
      throw new Error('Select at least one local video file before starting an edit.');
    }
    const registeredAssets = [];
    const importContext = crePrivateImportContext();
    for (const file of files) {
      const extension = String(file.name || '').toLowerCase().match(/\.[^.]+$/)?.[0] || '';
      if (file.size <= 0 || file.size > 32 * 1024 * 1024) {
        throw new Error(`${file.name} is empty or larger than 32 MiB.`);
      }
      if (!allowedExtensions.has(extension) || (file.type && !allowedMimeTypes.has(file.type))) {
        throw new Error(`Unsupported media type for ${file.name}. Select MP4, WebM, MOV, or MKV videos.`);
      }
    }
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      const registeredIndex = creRegisteredSourceFiles.indexOf(file);
      const alreadyRegistered = registeredIndex >= 0 ? creRegisteredSourceAssets[registeredIndex] : null;
      if (alreadyRegistered) {
        registeredAssets.push(alreadyRegistered);
        continue;
      }
      feedback.textContent = `Uploading and registering ${index + 1} of ${files.length}: ${file.name}…`;
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error(`The selected video ${file.name} could not be read.`));
        reader.onload = () => resolve(reader.result);
        reader.readAsDataURL(file);
      });
      const videoBase64 = String(dataUrl).split(',', 2)[1];
      let response;
      try {
        response = await provider.requestCreSourceVideo({
          videoBase64,
          fileName: file.name,
          mediaType: file.type || '',
          targetContext: importContext.targetContext,
          accountId: importContext.accountId,
          territory: importContext.territory,
        });
      } catch (error) {
        if (error?.transportClosed) {
          creUploadOutcomeUnknown = true;
          creUploadRetryArmed = false;
          feedback.textContent = `Connection closed before ${file.name} registration was confirmed. The backend may have registered it; no automatic retry was made.`;
        }
        throw error;
      }
      const asset = response.payload?.asset;
      if (response.status !== 'SOURCE_REGISTERED' || !asset?.assetId || !/^[a-f0-9]{64}$/.test(asset.sha256)) {
        throw new Error(`Backend did not return a registered, hash-bound source asset for ${file.name}.`);
      }
      creRegisteredSourceFiles.push(file);
      creRegisteredSourceAssets.push(asset);
      registeredAssets.push(asset);
    }
    creSourceAssets = registeredAssets;
    creUploadOutcomeUnknown = false;
    creUploadRetryArmed = false;
    return creSourceAssets;
  }

  async function playPrivateCreRender(response, feedback) {
    const output = response?.payload;
    if (
      response?.status !== 'PRIVATE_RENDER_ARTIFACT_READY' ||
      typeof output?.base64 !== 'string' ||
      !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(output.base64) ||
      !Number.isInteger(output.sizeBytes) ||
      output.sizeBytes < 1 ||
      output.sizeBytes > 16 * 1024 * 1024 ||
      !/^[a-f0-9]{64}$/.test(output.sha256)
    ) {
      throw new Error('Artifact transfer failed: the backend returned invalid render metadata.');
    }
    let binary;
    try {
      binary = atob(output.base64);
    } catch {
      throw new Error('Artifact transfer failed: the returned Base64 payload is invalid.');
    }
    if (
      binary.length !== output.sizeBytes ||
      output.base64.length !== 4 * Math.ceil(output.sizeBytes / 3)
    ) {
      throw new Error('Artifact transfer failed: the returned byte count is inconsistent.');
    }
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    if (!globalThis.crypto?.subtle?.digest) {
      throw new Error('Artifact transfer failed: browser SHA-256 verification is unavailable.');
    }
    const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
    const actualSha256 = Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, '0'))
      .join('');
    if (actualSha256 !== output.sha256) {
      throw new Error('Artifact transfer failed: SHA-256 verification did not match.');
    }

    const candidateUrl = URL.createObjectURL(
      new Blob([bytes], { type: output.mimeType || 'video/mp4' }),
    );
    const video = byId('creProjectOutputVideo');
    const previousUrl = crePrivateProjectOutputUrl;
    try {
      await new Promise((resolve, reject) => {
        let metadataReady = false;
        let frameReady = false;
        let timeout;
        const cleanup = () => {
          clearTimeout(timeout);
          video.removeEventListener('loadedmetadata', onMetadata);
          video.removeEventListener('loadeddata', onFrame);
          video.removeEventListener('error', onError);
        };
        timeout = setTimeout(() => {
          cleanup();
          reject(new Error('video load timed out'));
        }, 30000);
        const finishIfReady = () => {
          if (
            metadataReady &&
            frameReady &&
            video.videoWidth > 0 &&
            video.videoHeight > 0
          ) {
            cleanup();
            resolve();
          }
        };
        const onMetadata = () => {
          metadataReady = true;
          finishIfReady();
        };
        const onFrame = () => {
          frameReady = true;
          finishIfReady();
        };
        const onError = () => {
          cleanup();
          reject(new Error('browser could not decode the rendered video'));
        };
        video.addEventListener('loadedmetadata', onMetadata);
        video.addEventListener('loadeddata', onFrame);
        video.addEventListener('error', onError);
        video.src = candidateUrl;
        video.hidden = false;
        video.load();
      });
    } catch (error) {
      URL.revokeObjectURL(candidateUrl);
      if (previousUrl) {
        video.src = previousUrl;
        video.load();
      } else {
        video.removeAttribute('src');
        video.load();
      }
      throw new Error(`Preview playback failed: ${error.message}`);
    }
    crePrivateProjectOutputUrl = candidateUrl;
    if (previousUrl) URL.revokeObjectURL(previousUrl);
    const download = byId('creProjectOutputDownload');
    download.href = candidateUrl;
    download.hidden = false;
    feedback.textContent = `Private review loaded and decoded: ${video.videoWidth}×${video.videoHeight}, ${Number(output.durationSeconds || 0).toFixed(2)} seconds, SHA-256 ${output.sha256}. No publication occurred.`;
    feedback.className = 'notice';
  }

  async function recoverCrePrivateRender(operationId, feedback) {
    if (!provider?.authenticated || bridge?.getProvenance() !== 'LIVE') {
      feedback.textContent = 'Render recovery is waiting for the authenticated connection. No new render was submitted.';
      feedback.className = 'notice notice-warning';
      return;
    }
    let statusResponse;
    try {
      statusResponse = await provider.getPrivateCreRenderStatus(operationId);
    } catch (error) {
      feedback.textContent = `Render status recovery failed: ${error.message}. The saved operation ID is retained; no new render was submitted.`;
      feedback.className = 'notice notice-warning';
      return;
    }
    const operation = statusResponse.payload;
    if (statusResponse.status !== 'PRIVATE_RENDER_OPERATION_AVAILABLE' || !operation) {
      crePrivateRevisionEligible = false;
      byId('creRevisionRender').hidden = true;
      feedback.textContent = 'Render operation is unavailable to this authenticated owner. No new render was submitted.';
      feedback.className = 'notice notice-warning';
      return;
    }
    crePrivateRevisionEligible = isCreRecoveredOperationEligibleForRevision(
      crePrivateProjectId,
      operationId,
      operation,
    );
    byId('creRevisionRender').hidden = !crePrivateRevisionEligible;
    byId('creProjectRender').textContent =
      operation.state === 'queued' ? 'Start queued render' : 'Recover render';
    if (operation.state === 'failed') {
      feedback.textContent = `Render failed (${operation.failureCode || 'RENDER_FAILED'}). ${operation.failureDetail || 'No artifact is available.'}`;
      feedback.className = 'notice notice-warning';
      return;
    }
    if (operation.state === 'reconciliation_required') {
      feedback.textContent = `Render outcome requires reconciliation (${operation.failureCode || 'OUTCOME_UNKNOWN'}). It will not be rerun automatically.`;
      feedback.className = 'notice notice-warning';
      return;
    }
    if (operation.state !== 'completed') {
      const nextAction = operation.state === 'queued'
        ? 'Start queued render resumes this existing operation.'
        : 'Recover render checks this operation without submitting another render.';
      feedback.textContent = `Persisted render state: ${operation.state}. ${nextAction}`;
      feedback.className = 'notice';
      return;
    }
    feedback.textContent = 'Render completed and persisted. Retrieving the existing artifact…';
    feedback.className = 'notice';
    try {
      const artifact = await provider.getPrivateCreRenderArtifact(operationId);
      await playPrivateCreRender(artifact, feedback);
    } catch (error) {
      feedback.textContent = `${error.message.startsWith('Preview playback failed:')
        ? error.message
        : `Artifact transfer failed: ${error.message}`}. Render remains completed; use Recover render to retry retrieval.`;
      feedback.className = 'notice notice-warning';
    }
  }

  async function resumeSavedCrePrivateRender(operationId, feedback) {
    let response;
    try {
      response = await provider.getPrivateCreRenderStatus(operationId);
    } catch (error) {
      feedback.textContent = `Render status recovery failed: ${error.message}. The saved operation ID is retained; no render was submitted.`;
      feedback.className = 'notice notice-warning';
      return;
    }
    if (
      response.status !== 'PRIVATE_RENDER_OPERATION_AVAILABLE' ||
      response.payload?.state !== 'queued'
    ) {
      await recoverCrePrivateRender(operationId, feedback);
      return;
    }
    feedback.textContent = 'This operation is still queued and has not claimed the renderer. Starting that saved operation once…';
    feedback.className = 'notice';
    try {
      await provider.renderPrivateCreProject({
        operationId,
        projectId: crePrivateProjectId,
        vertical: false,
      });
    } catch (error) {
      feedback.textContent = `Queued operation response was not confirmed: ${error.message}. Checking the same saved operation; no new operation will be submitted.`;
      feedback.className = 'notice notice-warning';
    }
    await recoverCrePrivateRender(operationId, feedback);
  }

  async function recoverSavedCrePrivateRender() {
    if (
      !crePrivateRenderOperationId ||
      creRecoveryInProgress ||
      !provider?.authenticated ||
      bridge?.getProvenance() !== 'LIVE'
    ) {
      return;
    }
    creRecoveryInProgress = true;
    try {
      await recoverCrePrivateRender(
        crePrivateRenderOperationId,
        byId('creProjectFeedback'),
      );
    } finally {
      creRecoveryInProgress = false;
    }
  }

  async function submitCrePrivateRender(projectId, feedback) {
    const operationId = createCrePrivateRenderOperationId();
    crePrivateProjectId = projectId;
    crePrivateRenderOperationId = operationId;
    saveCrePrivateRender(projectId, operationId);
    const recoveryButton = byId('creProjectRender');
    recoveryButton.hidden = false;
    recoveryButton.disabled = false;
    feedback.textContent = `Render operation ${operationId} saved. Submitting once; recovery will reuse this operation ID.`;
    feedback.className = 'notice';
    try {
      const response = await provider.renderPrivateCreProject({
        operationId,
        projectId,
        vertical: false,
      });
      if (response.status !== 'PRIVATE_RENDER_OPERATION_AVAILABLE') {
        throw new Error(response.error || 'Render operation was not accepted.');
      }
      const state = response.payload?.state;
      if (state === 'completed') {
        await recoverCrePrivateRender(operationId, feedback);
      } else if (state === 'failed') {
        feedback.textContent = `Render failed (${response.payload.failureCode || 'RENDER_FAILED'}). ${response.payload.failureDetail || 'No artifact is available.'}`;
        feedback.className = 'notice notice-warning';
      } else if (state === 'reconciliation_required') {
        feedback.textContent = `Render outcome requires reconciliation (${response.payload.failureCode || 'OUTCOME_UNKNOWN'}). It will not be rerun automatically.`;
        feedback.className = 'notice notice-warning';
      } else {
        feedback.textContent = `Render operation is ${state || 'queued'}. Recover render checks this operation without submitting another render.`;
        feedback.className = 'notice';
      }
    } catch (error) {
      feedback.textContent = `Render response was not confirmed: ${error.message}. Recovering operation ${operationId}; no second render will be submitted.`;
      feedback.className = 'notice notice-warning';
      await recoverCrePrivateRender(operationId, feedback);
    }
  }

  byId('creProjectRender').addEventListener('click', async () => {
    if (!crePrivateRenderOperationId || creRecoveryInProgress) return;
    creRecoveryInProgress = true;
    byId('creProjectRender').disabled = true;
    try {
      await resumeSavedCrePrivateRender(
        crePrivateRenderOperationId,
        byId('creProjectFeedback'),
      );
    } finally {
      creRecoveryInProgress = false;
      byId('creProjectRender').disabled = false;
    }
  });

  byId('creProjectCreate').addEventListener('click', async () => {
    const feedback = byId('creProjectFeedback');
    const sourceFeedback = byId('creSourceFeedback');
    const createButton = byId('creProjectCreate');
    const revisionButton = byId('creRevisionRender');
    const files = [...selectedCreFiles];
    const importedAssets = [...creImportedFolderAssets.entries()]
      .filter(([fileId]) => selectedCreFolderFileIds.has(fileId))
      .map(([, asset]) => asset);
    const instruction = byId('creAutonomousInstruction').value.trim();
    if (creEditorOperationInProgress) return;
    if (creProjectCreationOutcomeUnknown) {
      feedback.textContent = 'Project creation was interrupted without a response. Its outcome is unknown; do not retry or create another project until the gateway state is reconciled.';
      feedback.className = 'notice notice-warning';
      return;
    }
    if (creUploadOutcomeUnknown && !creUploadRetryArmed) {
      creUploadRetryArmed = true;
      feedback.textContent = 'The previous upload lost its response, so registration is unknown. Click Start editing again to explicitly retry; if the backend committed it, retrying may create a duplicate source.';
      feedback.className = 'notice notice-warning';
      return;
    }
    if (creUploadOutcomeUnknown) {
      creUploadRetryArmed = false;
    }
    if (files.length < 1 && importedAssets.length < 1) {
      feedback.textContent = 'Select at least one local or registered folder video before starting an edit.';
      feedback.className = 'notice notice-warning';
      return;
    }
    if (Array.from(instruction).length > 20000 || Array.from(instruction).some((character) => {
      const codePoint = character.codePointAt(0);
      return codePoint < 32 && character !== '\t' && character !== '\n' && character !== '\r';
    })) {
      feedback.textContent = 'Creative direction contains invalid characters or exceeds the size limit.';
      feedback.className = 'notice notice-warning';
      return;
    }
    if (!provider?.authenticated || bridge?.getProvenance() !== 'LIVE') {
      feedback.textContent = 'ULTRON is not authenticated and connected. Unlock the dashboard using its normal local flow, then start editing again; your selected videos are still here.';
      feedback.className = 'notice notice-warning';
      sourceFeedback.textContent = 'No upload was sent. Authentication is required before source videos can be registered.';
      sourceFeedback.className = 'notice notice-warning';
      return;
    }
    creEditorOperationInProgress = true;
    updateCreFileSelection();
    revisionButton.disabled = true;
    feedback.textContent = files.length
      ? 'ULTRON is uploading the selected clips…'
      : 'ULTRON is preparing the registered folder sources…';
    feedback.className = 'notice';
    let phase = 'source upload';
    let projectCreated = false;
    let projectRequestDispatched = false;
    let sourcesRegistered = false;
    try {
      const uploadedAssets = files.length
        ? await uploadSelectedCreVideos(files, sourceFeedback)
        : [];
      creSourceAssets = [...importedAssets, ...uploadedAssets];
      if (!creSourceAssets.length) throw new Error('No registered source assets are available for this edit.');
      sourcesRegistered = true;
      sourceFeedback.textContent = `${creSourceAssets.length} source videos are registered and hash-verified. ULTRON is creating the private project…`;
      sourceFeedback.className = 'notice';
      phase = 'project creation';
      crePrivateProjectId = typeof crypto?.randomUUID === 'function'
        ? `cre-${crypto.randomUUID()}`
        : `cre-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      crePrivateRenderOperationId = null;
      crePrivateRevisionEligible = false;
      byId('creProjectRender').hidden = true;
      byId('creProjectRender').disabled = true;
      revisionButton.hidden = true;
      saveCrePrivateRender(crePrivateProjectId, null);
      const script = instruction || 'Autonomous private owner-review edit from the selected source videos.';
      byId('creProjectScript').value = script;
      projectRequestDispatched = true;
      const createResponse = await provider.createCreProject({
        projectId: crePrivateProjectId,
        script,
        sourceAssetIds: creSourceAssets.map(asset => asset.assetId),
      });
      if (createResponse.status !== 'PROJECT_CREATED') {
        throw new Error(createResponse.error || 'The backend did not create the CRE project.');
      }
      projectCreated = true;
      feedback.textContent = `ULTRON registered ${creSourceAssets.length} assets and is analyzing them to plan the edit…`;
      phase = 'autonomous planning';
      const planResponse = await provider.autoplanCreProject({
        projectId: crePrivateProjectId,
        instruction: script,
      });
      if (planResponse.status !== 'AUTOPLAN_READY' || !Array.isArray(planResponse.payload?.segments)) {
        throw new Error(planResponse.error || 'ULTRON could not plan the multi-source edit.');
      }
      if (!isCrePlanEligibleForRevision(crePrivateProjectId, planResponse.payload)) {
        throw new Error('ULTRON returned an incomplete or mismatched edit plan; revision is unavailable.');
      }
      crePrivateRevisionEligible = true;
      revisionButton.hidden = false;
      feedback.textContent = `ULTRON planned ${planResponse.payload.segmentCount} segments and is rendering the private review…`;
      phase = 'private rendering';
      await submitCrePrivateRender(crePrivateProjectId, feedback);
      revisionButton.hidden = !crePrivateRevisionEligible;
      sourceFeedback.textContent = `${creSourceAssets.length} source videos registered successfully. Check the private render status above before treating the preview as ready.`;
      sourceFeedback.className = 'notice';
    } catch (error) {
      if (!projectCreated) {
        const requestOutcomeIsUnknown = phase === 'project creation'
          && projectRequestDispatched
          && (error?.transportClosed || /timed out/i.test(error?.message || ''));
        if (requestOutcomeIsUnknown) {
          creProjectCreationOutcomeUnknown = true;
          sourceFeedback.textContent = 'Source registration is confirmed, but the project response was lost. Do not resubmit; reconcile the project state before retrying.';
          sourceFeedback.className = 'notice notice-warning';
        } else {
          crePrivateProjectId = null;
          if (sourcesRegistered && phase === 'project creation') {
            sourceFeedback.textContent = 'Source registration is confirmed. The backend rejected project creation; reuse these selected files if retrying in this session.';
            sourceFeedback.className = 'notice notice-warning';
          }
        }
        revisionButton.hidden = true;
        crePrivateRevisionEligible = false;
      }
      const detail = error?.response?.error || error?.message || 'No backend error detail was returned.';
      feedback.textContent = `CRE ${phase} failed: ${detail}`;
      feedback.className = 'notice notice-warning';
    } finally {
      creEditorOperationInProgress = false;
      revisionButton.disabled = false;
      updateCreFileSelection();
    }
  });

  byId('creRevisionRender').addEventListener('click', async () => {
    const feedback = byId('creProjectFeedback');
    const revisionButton = byId('creRevisionRender');
    const revision = byId('creRevisionInstruction').value.trim();
    if (creEditorOperationInProgress) return;
    if (!crePrivateProjectId || !crePrivateRevisionEligible) {
      feedback.textContent = 'Recover a completed structured plan before requesting a revision.';
      feedback.className = 'notice notice-warning';
      return;
    }
    creEditorOperationInProgress = true;
    revisionButton.disabled = true;
    byId('creProjectCreate').disabled = true;
    feedback.textContent = 'ULTRON is applying the revision and re-rendering the private review…';
    feedback.className = 'notice';
    try {
      const planResponse = await provider.autoplanCreProject({
        projectId: crePrivateProjectId,
        instruction: revision || byId('creAutonomousInstruction').value.trim() || 'Polish the edit with a coherent default pacing and sequence.',
      });
      if (planResponse.status !== 'AUTOPLAN_READY') {
        throw new Error(planResponse.error || 'ULTRON could not revise the edit timeline.');
      }
      if (!isCrePlanEligibleForRevision(crePrivateProjectId, planResponse.payload)) {
        throw new Error('ULTRON returned an incomplete or mismatched edit plan; revision was not rendered.');
      }
      await submitCrePrivateRender(crePrivateProjectId, feedback);
    } catch (error) {
      feedback.textContent = error?.message || 'The revision could not be rendered.';
      feedback.className = 'notice notice-warning';
    } finally {
      creEditorOperationInProgress = false;
      revisionButton.disabled = false;
      updateCreFileSelection();
    }
  });

  byId('creSourceAssetCheck').addEventListener('click', async () => {
    const feedback = byId('creSourceFeedback');
    const assetId = byId('creSourceAssetId').value.trim();
    if (!assetId) {
      feedback.textContent = 'Enter a registered source asset ID.';
      feedback.className = 'notice notice-warning';
      return;
    }
    const button = byId('creSourceAssetCheck');
    button.disabled = true;
    feedback.textContent = 'Reading the owner-scoped asset and evaluating current ANALYZE rights…';
    feedback.className = 'notice';
    try {
      const importContext = crePrivateImportContext();
      const targetContext = importContext.targetContext;
      const accountId = importContext.accountId;
      const territory = importContext.territory;
      const response = await provider.checkCreSourceAssetStatus({
        assetId,
        targetContext,
        accountId,
        territory
      });
      if (response.status !== 'ASSET_STATUS_AVAILABLE' || response.payload?.assetId !== assetId) {
        throw new Error('Backend response did not confirm the requested registered asset.');
      }
      const status = response.payload;
      creSourceWorkflowResult = {
        asset: {
          assetId: status.assetId,
          sha256: status.sha256,
          durationSeconds: status.durationSeconds,
          width: status.width,
          height: status.height
        },
        targetContext,
        accountId,
        territory
      };
      const cleared = status.copyrightStatus === 'cleared'
        && status.analysisRightsStatus === 'cleared';
      byId('creSourceResume').disabled = !cleared;
      byId('creSourceRightsStatus').textContent =
        `Copyright: ${status.copyrightStatus}; ANALYZE rights: ${status.analysisRightsStatus}; decision ${status.rightsDecisionId}. ${status.reasonCodes.join(', ')}`;
      feedback.textContent = cleared
        ? 'Current ANALYZE rights are cleared. Resume rechecks authorization before processing.'
        : 'Rights are not currently cleared. The asset remains registered; transcription was not started.';
      feedback.className = 'notice notice-warning';
    } catch (error) {
      feedback.textContent = error?.message || 'Registered asset status could not be established.';
      feedback.className = 'notice notice-warning';
    } finally {
      button.disabled = false;
    }
  });

  byId('creSourceResume').addEventListener('click', async () => {
    const feedback = byId('creSourceFeedback');
    const assetId = byId('creSourceAssetId').value.trim();
    if (!creSourceWorkflowResult || !assetId) {
      feedback.textContent = 'Register a source asset before resuming processing.';
      feedback.className = 'notice notice-warning';
      return;
    }
    const button = byId('creSourceResume');
    button.disabled = true;
    feedback.textContent = 'Rechecking current ANALYZE rights and resuming processing for the same asset…';
    feedback.className = 'notice';
    try {
      const response = await provider.resumeCreSourceVideo({
        assetId,
        targetContext: creSourceWorkflowResult.targetContext,
        accountId: creSourceWorkflowResult.accountId,
        territory: creSourceWorkflowResult.territory
      });
      if (response.status !== 'HIGHLIGHTS_READY' || !response.payload?.asset?.assetId) {
        throw new Error('Backend response did not contain transcript-linked highlight results.');
      }
      creSourceWorkflowResult = {
        ...creSourceWorkflowResult,
        ...response.payload
      };
      displayCreSourceHighlights(response.payload);
      feedback.textContent = `${response.payload.candidates.length} candidate(s) generated from the same registered asset. Current rights were rechecked; nothing was published.`;
      feedback.className = 'notice notice-warning';
    } catch (error) {
      feedback.textContent = error?.message || 'Source-video processing could not be resumed.';
      feedback.className = 'notice notice-warning';
    } finally {
      button.disabled = false;
    }
  });

  byId('creSourceRightsSubmit').addEventListener('click', async () => {
    const resultNode = byId('creSourceRightsResult');
    let request;
    try {
      request = JSON.parse(byId('creSourceRightsRequest').value);
      if (!request || typeof request !== 'object' || Array.isArray(request)) {
        throw new Error('Enter one rights workflow request object.');
      }
    } catch (error) {
      resultNode.textContent = error?.message || 'Rights request JSON is invalid.';
      return;
    }
    const button = byId('creSourceRightsSubmit');
    button.disabled = true;
    resultNode.textContent = 'Submitting to the existing CRE rights service…';
    try {
      const evidenceFile = byId('creSourceRightsFile').files?.[0];
      if (request.step === 'submit_claim' || request.step === 'create_agreement') {
        if (!evidenceFile || evidenceFile.size <= 0 || evidenceFile.size > 2 * 1024 * 1024) {
          throw new Error('Select the corresponding non-empty rights document (maximum 2 MB).');
        }
        const dataUrl = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onerror = () => reject(new Error('Rights evidence file could not be read.'));
          reader.onload = () => resolve(reader.result);
          reader.readAsDataURL(evidenceFile);
        });
        const encodedFile = String(dataUrl).split(',', 2)[1];
        if (request.step === 'submit_claim') request.evidenceBase64 = encodedFile;
        if (request.step === 'create_agreement') request.agreementBase64 = encodedFile;
      }
      const response = await provider.requestCreSourceRights(request);
      const output = response.payload || {};
      if (request.step === 'read_evidence' && typeof output.evidenceBase64 === 'string') {
        const binary = atob(output.evidenceBase64);
        const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
        if (creSourceEvidenceUrl) URL.revokeObjectURL(creSourceEvidenceUrl);
        creSourceEvidenceUrl = URL.createObjectURL(
          new Blob([bytes], { type: 'application/octet-stream' })
        );
        const link = document.createElement('a');
        link.href = creSourceEvidenceUrl;
        link.download = `${output.evidenceId}-rights-evidence`;
        link.textContent = 'Download evidence for independent review';
        const metadata = document.createElement('pre');
        const { evidenceBase64, ...reviewMetadata } = output;
        metadata.textContent = JSON.stringify(reviewMetadata, null, 2);
        resultNode.replaceChildren(metadata, link);
        return;
      }
      resultNode.textContent = `${response.status}\n${JSON.stringify(output, null, 2)}`;
      if (request.step === 'record_grant') {
        byId('creSourceRightsStatus').textContent =
          'A rights grant was recorded. Current ANALYZE and CREATE_DERIVATIVE scope will be evaluated again before use.';
      }
    } catch (error) {
      resultNode.textContent = error?.message || 'CRE rights operation failed.';
    } finally {
      button.disabled = false;
    }
  });

  function displayCreSourceHighlights(result) {
    const transcript = byId('creTranscript');
    transcript.replaceChildren();
    const heading = document.createElement('h2');
    heading.textContent = `Transcript · ${result.transcript.language || 'language unknown'}`;
    transcript.append(heading);
    result.transcript.segments.forEach((segment) => {
      const row = document.createElement('p');
      row.textContent = `${Number(segment.startSeconds).toFixed(2)}–${Number(segment.endSeconds).toFixed(2)}s · ${segment.text}`;
      transcript.append(row);
    });
    const candidateList = byId('creCandidates');
    candidateList.replaceChildren();
    const candidateHeading = document.createElement('h2');
    candidateHeading.textContent = 'Ranked highlight candidates';
    candidateList.append(candidateHeading);
    if (result.visualAnalysis?.method === 'ffmpeg_scene_change_detection') {
      const sceneSummary = document.createElement('p');
      const cuts = result.visualAnalysis.sceneChangeSeconds || [];
      sceneSummary.textContent = cuts.length
        ? `Visual timing cue: FFmpeg detected ${cuts.length} scene change(s) at ${cuts.map((time) => `${Number(time).toFixed(2)}s`).join(', ')}. This detects cuts only; it does not identify objects or understand video meaning.`
        : 'Visual timing cue: FFmpeg found no scene changes. This detects cuts only; it does not identify objects or understand video meaning.';
      candidateList.append(sceneSummary);
    }
    result.candidates.forEach((candidate) => {
      const card = document.createElement('article');
      card.className = 'panel';
      const title = document.createElement('h3');
      title.textContent = `#${candidate.rank} · ${Number(candidate.startSeconds).toFixed(2)}–${Number(candidate.endSeconds).toFixed(2)}s`;
      const excerpt = document.createElement('p');
      excerpt.textContent = candidate.excerpt;
      const reason = document.createElement('p');
      reason.textContent = candidate.reason;
      card.append(title, excerpt, reason);
      const visualEvidence = candidate.visualEvidence;
      if (visualEvidence) {
        const visual = document.createElement('p');
        visual.textContent =
          `Visual cut cue: ${visualEvidence.sceneChangeCount} scene change(s) inside; ` +
          `start ${visualEvidence.startAlignedToSceneCut ? 'aligns' : 'does not align'} ` +
          `to a detected cut; end ${visualEvidence.endAlignedToSceneCut ? 'aligns' : 'does not align'} ` +
          `to a detected cut.`;
        card.append(visual);
      }
      const select = document.createElement('button');
      select.type = 'button';
      select.className = 'button button-primary';
      select.textContent = 'Create source-footage clip';
      select.addEventListener('click', () => renderCreSourceCandidate(candidate));
      card.append(select);
      candidateList.append(card);
    });
  }

  async function renderCreSourceCandidate(candidate) {
    const feedback = byId('creClipFeedback');
    feedback.textContent = 'Revalidating derivative rights and rendering the selected source interval…';
    feedback.className = 'notice';
    try {
      const result = await provider.requestCreSourceClip({
        candidateId: candidate.candidateId,
        highlightRunId: creSourceWorkflowResult.highlightRunId,
        targetContext: crePrivateImportContext().targetContext,
        accountId: crePrivateImportContext().accountId,
        territory: crePrivateImportContext().territory,
      });
      const binary = atob(result.base64);
      const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
      const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
      const sha256 = Array.from(digest, (byte) => byte.toString(16).padStart(2, '0')).join('');
      if (sha256 !== result.sha256) {
        throw new Error('Downloaded clip bytes did not match the backend SHA-256.');
      }
      if (creSourceClipUrl) URL.revokeObjectURL(creSourceClipUrl);
      creSourceClipUrl = URL.createObjectURL(new Blob([bytes], { type: result.mimeType }));
      const video = byId('creSourceClipVideo');
      video.src = creSourceClipUrl;
      video.hidden = false;
      video.load();
      const download = byId('creSourceClipDownload');
      download.href = creSourceClipUrl;
      download.download = `${result.renderId}.mp4`;
      download.hidden = false;
      const details = byId('creSourceClipDetails');
      details.replaceChildren();
      const editSegments = result.manifest?.editTimeline?.segments || [];
      [
        ['Artifact ID', result.artifactId],
        ['Render ID', result.renderId],
        ['SHA-256', result.sha256],
        ['Duration', `${Number(result.durationSeconds).toFixed(2)} seconds`],
        ['Dimensions', `${result.width} × ${result.height}`],
        ['Verification', result.verification],
        [
          'Quality inspection',
          result.manifest?.technicalValidation?.qualityInspection
            ? `${result.manifest.technicalValidation.qualityInspection.status} · black=${result.manifest.technicalValidation.qualityInspection.blackFrames ?? 0} · frozen=${result.manifest.technicalValidation.qualityInspection.frozenFrames ?? 0}`
            : 'Not attempted',
        ],
        ['Source interval', `${candidate.startSeconds}–${candidate.endSeconds} seconds`],
        [
          'Executed edit timeline',
          editSegments.length
            ? editSegments.map((segment, index) =>
              `${index + 1}. ${Number(segment.sourceInSeconds).toFixed(2)}–${Number(segment.sourceOutSeconds).toFixed(2)}s source`
            ).join(' · ')
            : 'Single source interval',
        ],
        ['Publication', 'Not attempted'],
      ].forEach(([label, value]) => {
        const wrapper = document.createElement('div');
        const term = document.createElement('dt');
        const description = document.createElement('dd');
        term.textContent = label;
        description.textContent = String(value);
        wrapper.append(term, description);
        details.append(wrapper);
      });
      feedback.textContent = 'Verified local source-footage clip is ready for human review. Nothing was published.';
      feedback.className = 'notice notice-warning';
      selectView('cre-production');
    } catch (error) {
      feedback.textContent = error?.message || 'Clip rendering failed.';
      feedback.className = 'notice notice-warning';
      selectView('cre-production');
    }
  }

  byId('creScriptPreviewReviewForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = byId('creScriptReviewButton');
    const feedback = byId('creScriptReviewFeedback');
    if (!creScriptPreviewResult) {
      feedback.textContent = 'Generate a preview before recording its review.';
      feedback.className = 'notice notice-warning';
      return;
    }
    button.disabled = true;
    feedback.textContent = 'Recording the authenticated owner review…';
    feedback.className = 'notice';
    try {
      const review = await provider.requestCreScriptPreviewReview({
        scriptId: creScriptPreviewResult.scriptId,
        artifactSha256: creScriptPreviewResult.preview.sha256,
        editorialVerdict: byId('creScriptEditorialVerdict').value,
        narrationAssessment: byId('creScriptNarrationAssessment').value,
        pacingAssessment: byId('creScriptPacingAssessment').value,
        captionAssessment: byId('creScriptCaptionAssessment').value,
        sceneAssessment: byId('creScriptSceneAssessment').value,
        factualReviewAttested: byId('creScriptFactualAttestation').checked,
        notes: byId('creScriptReviewNotes').value,
      });
      creScriptPreviewResult.review = review.review;
      renderCreScriptPreviewDetails(creScriptPreviewResult);
    } catch (error) {
      feedback.textContent = error?.message || 'Editorial review could not be recorded.';
      feedback.className = 'notice notice-warning';
    } finally {
      button.disabled = false;
    }
  });
  ['eventSearch', 'eventSeverity', 'eventSource', 'eventType', 'eventTimeRange'].forEach((id) => {
    byId(id).addEventListener('input', renderEvents);
    byId(id).addEventListener('change', renderEvents);
  });
  byId('resetEventFilters').addEventListener('click', () => {
    byId('eventSearch').value = '';
    byId('eventSeverity').value = '';
    byId('eventSource').value = '';
    byId('eventType').value = '';
    byId('eventTimeRange').value = '';
    renderEvents();
  });
  inspectorButton.addEventListener('click', openInspector);
  byId('closeInspector').addEventListener('click', closeInspector);
  backdrop.addEventListener('click', closeInspector);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !inspector.hidden && !approvalDialog.open && !byId('operatorAuthDialog').open) {
      event.preventDefault();
      closeInspector();
    }
  });
  voiceEnabled.addEventListener('change', () => {
    voiceAuto.disabled = !voiceEnabled.checked;
    byId('voiceTestButton').disabled = !voiceEnabled.checked;
    if (!voiceEnabled.checked) voicePlayback?.stop();
    document.querySelectorAll('.chat-actions button').forEach((button) => {
      button.disabled = !voiceEnabled.checked;
    });
  });
  byId('voiceHealthButton').addEventListener('click', checkVoiceHealth);
  byId('voiceTestButton').addEventListener('click', playVoiceTest);
  byId('stopSpeaking').addEventListener('click', () => voicePlayback?.stop());
  byId('approvalForm').addEventListener('submit', approveOperation);
  byId('approvalCancel').addEventListener('click', () => {
    currentApproval = null;
    approvalDialog.close();
  });
  approvalDialog.addEventListener('close', () => { currentApproval = null; });

  const initialView = location.hash.slice(1);
  if (Array.from(document.querySelectorAll('.nav-button'))
    .some((button) => button.dataset.view === initialView)) {
    selectView(initialView);
  }
}

initialize();
