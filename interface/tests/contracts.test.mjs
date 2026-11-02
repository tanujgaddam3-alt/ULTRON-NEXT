import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadStack } from './helpers.mjs';

const ctx = loadStack();
const validators = ctx.ULTRON.contracts.validators;
const { VALID_PROVENANCE, VALID_LIFECYCLE, VALID_MODES } = ctx.ULTRON.contracts;

// Fixtures built by the mock provider's own payload constructors, so the test
// proves the emitted production shapes are contract-compliant without
// duplicating fixture code.
const fixtureProvider = new ctx.ULTRON.providers.MockUltronProvider();
const fixtures = {
  vitalMatrix: fixtureProvider._vitalMatrix(),
  cognitiveState: fixtureProvider._cognitiveState(),
  pipeline: fixtureProvider._pipeline(),
  omega: fixtureProvider._omega(),
  trading: fixtureProvider._trading(),
  observatory: fixtureProvider._observatory(),
  device: fixtureProvider._device(),
  commandCenter: fixtureProvider._commandCenter(),
  runtimeEvents: fixtureProvider._seedEvents()
};

test('validators accept every mock-provider payload shape', () => {
  assert.doesNotThrow(() => validators.validateVitalMatrix(fixtures.vitalMatrix));
  assert.doesNotThrow(() => validators.validateCognitiveState(fixtures.cognitiveState));
  assert.doesNotThrow(() => validators.validatePipeline(fixtures.pipeline));
  assert.doesNotThrow(() => validators.validateOmega(fixtures.omega));
  assert.doesNotThrow(() => validators.validateTrading(fixtures.trading));
  assert.doesNotThrow(() => validators.validateObservatory(fixtures.observatory));
  assert.doesNotThrow(() => validators.validateDeviceAndCapabilities(fixtures.device));
  assert.doesNotThrow(() => validators.validateCommandCenter(fixtures.commandCenter));
  fixtures.runtimeEvents.forEach((event) => {
    assert.doesNotThrow(() => validators.validateRuntimeEvent(event));
  });
  assert.doesNotThrow(() => validators.validateRuntimeEventStream(fixtures.runtimeEvents));
});

test('OMEGA authentication-required response is a valid withheld snapshot', () => {
  assert.doesNotThrow(() => validators.validateOmega({
    access: {
      available: false,
      status: 'AUTHENTICATION_REQUIRED'
    }
  }));
  assert.throws(
    () => validators.validateOmega({ access: { available: true, status: 'AUTHENTICATION_REQUIRED' } }),
    /Invalid Omega\.access payload/
  );
});

test('VALID_* constants expose the authorized state spaces', () => {
  // Spread into the current realm so cross-realm prototype identity does not fail.
  assert.deepEqual([...VALID_PROVENANCE], ['LIVE', 'SIMULATED', 'OFFLINE', 'NOT_INSTRUMENTED']);
  assert.ok([...VALID_LIFECYCLE].includes('VERIFIED'));
  assert.ok([...VALID_LIFECYCLE].includes('UNVERIFIED'));
  assert.ok([...VALID_LIFECYCLE].includes('UNCERTAIN'));
  assert.ok([...VALID_MODES].includes('DEGRADED'));
  assert.ok([...VALID_MODES].includes('OFFLINE'));
});

test('validateRuntimeEventStream requires an array and rejects invalid members', () => {
  assert.throws(() => validators.validateRuntimeEventStream(fixtures.runtimeEvents[0]), /array/i);
  const bad = [{ ...fixtures.runtimeEvents[0], provenance: 'MAYBE' }];
  assert.throws(() => validators.validateRuntimeEventStream(bad), /provenance/);
});

test('validateCognitiveState rejects invalid behavior modes', () => {
  assert.throws(() => validators.validateCognitiveState({ behavior: 'DREAMING', tensorInferenceRateHz: 1, agents: [] }), /behavior/);
  assert.doesNotThrow(() => validators.validateCognitiveState({ behavior: 'IDLE', agents: [] }));
  assert.doesNotThrow(() => validators.validateCognitiveState({ behavior: 'IDLE', agents: null }));
  assert.throws(() => validators.validateCognitiveState({ behavior: 'IDLE', agents: 'unknown' }), /agents/);
  assert.throws(() => validators.validateCognitiveState({
    behavior: 'IDLE',
    tensorInferenceRateHz: 'unknown',
    agents: []
  }), /must be numeric when reported/);
});

test('validateWorld accepts explicit unavailable snapshots but rejects false zero fallbacks', () => {
  assert.doesNotThrow(() => validators.validateWorld({
    system: {},
    project: {},
    runtime: {},
    entity_count: null,
    entities: null,
    error: 'world observer unavailable'
  }));
  assert.throws(() => validators.validateWorld({
    system: {},
    project: {},
    runtime: {},
    entity_count: 0,
    entities: [],
    error: 'world observer unavailable'
  }), /failed snapshots/);
});

test('validateVitalMatrix requires every metric block with primaryValue and question', () => {
  const broken = JSON.parse(JSON.stringify(fixtures.vitalMatrix));
  delete broken.currentTask;
  assert.throws(() => validators.validateVitalMatrix(broken), /currentTask/);
});

test('telemetry contracts accept explicit unavailable values without treating them as zero', () => {
  assert.doesNotThrow(() => validators.validateOmega({
    bridgeLatencyMs: null,
    communicationTrace: null,
    model: { provider: null, name: null, available: null },
    integration: { exchangeTrace: [] }
  }));
  assert.doesNotThrow(() => validators.validateDeviceAndCapabilities({
    assessment: { status: 'NOT_ASSESSED' },
    hardware: {
      cpu: { cores: null, load1: null },
      memory: { totalGb: null, usedPct: null },
      storage: { totalGb: null }
    },
    capabilities: null
  }));
  assert.throws(() => validators.validateDeviceAndCapabilities({
    assessment: { status: 'NOT_ASSESSED' },
    hardware: {
      cpu: { cores: 'unknown' },
      memory: { totalGb: null, usedPct: null }
    },
    capabilities: null
  }), /hardware\.cpu\.cores/);
});

test('trading contracts allow unavailable account values and metrics', () => {
  const base = fixtureProvider._trading();
  const trading = {
    ...base,
    efficiencyLoss: { totalLossUsd: null, breakdown: {} },
    financial: {
      initialization: {
        state: 'UNAVAILABLE',
        controllerConfigured: false,
        brokerConfigured: false,
        updatedAt: new Date().toISOString()
      },
      marketData: {
        state: 'UNAVAILABLE',
        provider: 'unknown',
        source: 'unavailable',
        symbol: 'UNKNOWN',
        latestPrice: null,
        latestValidatedAt: null,
        freshnessSeconds: null,
        error: null
      },
      execution: {
        mode: 'unavailable',
        source: 'unavailable',
        orderSubmissionFromDashboard: false,
        liveEnabled: false
      },
      portfolio: {
        source: 'unavailable',
        state: 'UNAVAILABLE',
        cashUsd: null,
        equityUsd: null,
        positions: null,
        updatedAt: new Date().toISOString(),
        error: { category: 'not_configured' }
      },
      risk: {
        state: 'UNAVAILABLE',
        engines: [],
        rejections: { state: 'NOT_INSTRUMENTED', count: null }
      },
      executions: {
        source: 'unavailable',
        records: null,
        orderSubmissionFromDashboard: false
      },
      diagnostics: []
    }
  };

  assert.doesNotThrow(() => validators.validateTrading(trading));
});

test('validateOperation enforces completion/verification telemetry (data-integrity rule)', () => {
  const base = (lifecycleState) => ({
    operationId: 'op-x',
    title: 'T',
    intent: 'I',
    expectedEffect: 'E',
    lifecycleState
  });

  const fingerprint = 'request-fingerprint-op-x';
  const successfulReceipt = { success: true, status: 'COMPLETED' };
  const audit = {
    verified: true,
    outcome: 'CONFIRMED_SUCCESS',
    operationId: 'op-x',
    requestFingerprint: fingerprint,
    verifier: 'independent-fixture',
    provenance: 'SIMULATED',
    observedAt: new Date().toISOString()
  };

  assert.throws(() => validators.validateOperation(base('COMPLETED')), /successful execution evidence/);
  assert.doesNotThrow(() => validators.validateOperation({
    ...base('COMPLETED'),
    executionResult: successfulReceipt
  }));
  assert.throws(() => validators.validateOperation({
    ...base('VERIFIED'),
    requestFingerprint: fingerprint,
    executionResult: successfulReceipt
  }), /identity-bound independent verification evidence/);
  assert.doesNotThrow(() => validators.validateOperation({
    ...base('VERIFIED'),
    requestFingerprint: fingerprint,
    executionResult: successfulReceipt,
    verificationAudit: audit
  }));
  assert.throws(() => validators.validateOperation({
    ...base('VERIFIED'),
    requestFingerprint: fingerprint,
    executionResult: successfulReceipt,
    verificationAudit: { ...audit, operationId: 'other-operation' }
  }), /identity-bound independent verification evidence/);
  assert.doesNotThrow(() => validators.validateOperation({
    ...base('UNVERIFIED'),
    executionResult: { success: true, status: 'UNVERIFIED' }
  }));
  assert.doesNotThrow(() => validators.validateOperation({
    ...base('UNCERTAIN'),
    executionResult: { success: false, status: 'UNCERTAIN' }
  }));
  assert.throws(() => validators.validateOperation(base('UNVERIFIED')), /execution evidence/);
  assert.throws(() => validators.validateOperation(base('UNCERTAIN')), /execution evidence/);
});

test('validateOperation accepts every lifecycle state and rejects unknown values', () => {
  const states = ['SUGGESTED', 'PLANNED', 'APPROVED', 'EXECUTING', 'COMPLETED', 'VERIFIED', 'UNVERIFIED', 'UNCERTAIN', 'FAILED', 'CANCELLED'];
  states.forEach((lifecycleState) => {
    const operation = {
      operationId: 'op-' + lifecycleState,
      title: 'T',
      intent: 'I',
      expectedEffect: 'E',
      lifecycleState
    };
    if (lifecycleState === 'COMPLETED' || lifecycleState === 'VERIFIED' || lifecycleState === 'UNVERIFIED' || lifecycleState === 'UNCERTAIN') {
      operation.executionResult = { success: lifecycleState !== 'UNCERTAIN', status: lifecycleState };
    }
    if (lifecycleState === 'VERIFIED') {
      operation.requestFingerprint = 'fp-' + operation.operationId;
      operation.verificationAudit = {
        verified: true,
        outcome: 'CONFIRMED_SUCCESS',
        operationId: operation.operationId,
        requestFingerprint: operation.requestFingerprint,
        verifier: 'test-fixture',
        provenance: 'SIMULATED',
        observedAt: new Date().toISOString()
      };
    }
    assert.doesNotThrow(() => validators.validateOperation(operation));
  });
  assert.throws(() => validators.validateOperation({
    operationId: 'op-invalid', title: 'T', intent: 'I', expectedEffect: 'E', lifecycleState: 'completed'
  }), /lifecycleState/);
});

test('operation channel validates the gateway operation snapshot list, including an empty list', () => {
  assert.doesNotThrow(() => validators.validateOperationStream([]));
  assert.doesNotThrow(() => validators.validateOperationStream([fixtures.commandCenter.activeOperations[0]]));
  assert.throws(() => validators.validateOperationStream([{}]), /lifecycleState/);
  assert.equal(ctx.ULTRON.bridge.publish('operation', []), true);
});

test('validateCommandCenter cascades operation validation', () => {
  const illegal = { supportedActions: ['ask'], activeOperations: [fixtures.commandCenter.activeOperations[2]] };
  // op-102 is VERIFIED and carries full telemetry; sanity check it passes alone.
  assert.doesNotThrow(() => validators.validateOperation(fixtures.commandCenter.activeOperations[2]));

  const bad = JSON.parse(JSON.stringify(fixtures.commandCenter));
  delete bad.activeOperations[0].lifecycleState;
  assert.throws(() => validators.validateCommandCenter(bad), /lifecycleState/);
  assert.doesNotThrow(() => validators.validateCommandCenter(illegal));
});

test('validateCommandDispatch constrains action verbs and requires parameters', () => {
  assert.doesNotThrow(() => validators.validateCommandDispatch({ commandText: 'analyze the market', actionVerb: 'analyze', parameters: {} }));
  assert.throws(() => validators.validateCommandDispatch({ commandText: 'x', actionVerb: 'teleport', parameters: {} }), /actionVerb/);
  assert.throws(() => validators.validateCommandDispatch({ commandText: 'x', actionVerb: 'ask' }), /parameters/);
});

test('validateRuntimeEvent requires provenance attribution', () => {
  const evt = { eventId: 'e-1', type: 'T', summary: 'S', provenance: 'SIMULATED' };
  assert.doesNotThrow(() => validators.validateRuntimeEvent(evt));
  assert.throws(() => validators.validateRuntimeEvent({ ...evt, provenance: 'FAKE' }), /provenance/);
});

test('validateTrading: optional decisionLog is accepted when well-formed and rejected when mis-shaped', () => {
  const base = fixtureProvider._trading();
  const good = {
    ...base,
    decisionLog: [
      { signal: 's1', analysis: 'a1', decision: 'd1', confidence: 0.8, outcome: 'o1', expectedVsActual: 'expected X vs actual Y' }
    ]
  };
  assert.doesNotThrow(() => validators.validateTrading(good));
  assert.throws(
    () => validators.validateTrading({ ...base, decisionLog: 'not-an-array' }),
    /decisionLog/
  );
  assert.throws(
    () => validators.validateTrading({ ...base, decisionLog: [{ signal: 's1', confidence: 'high' }] }),
    /confidence/
  );
  // absence of decisionLog must remain valid (UI renders NOT INSTRUMENTED)
  assert.doesNotThrow(() => validators.validateTrading(base));
});

test('validateDeviceAndCapabilities: optional storage block is validated when present', () => {
  const base = fixtureProvider._device();
  const good = {
    ...base,
    hardware: { ...base.hardware, storage: { totalGb: 1024, usedGb: 512, driveType: 'NVMe', healthPct: 96 } }
  };
  assert.doesNotThrow(() => validators.validateDeviceAndCapabilities(good));
  assert.throws(
    () => validators.validateDeviceAndCapabilities({ ...base, hardware: { ...base.hardware, storage: { usedGb: 512 } } }),
    /storage/
  );
  // absence of storage must remain valid (abstractly NOT INSTRUMENTED)
  assert.doesNotThrow(() => validators.validateDeviceAndCapabilities(base));
});

test('runtime events accept optional severity/source/correlation metadata', () => {
  const evt = {
    eventId: 'e-sev-1',
    type: 'LIFECYCLE',
    summary: 'S',
    severity: 'WARNING',
    source: 'omega-bridge',
    correlationId: 'corr-1',
    provenance: 'LIVE'
  };
  assert.doesNotThrow(() => validators.validateRuntimeEvent(evt));
  assert.doesNotThrow(() => validators.validateRuntimeEventStream([evt]));
  // severity must never be mandatory: minimal events stay valid
  assert.doesNotThrow(() => validators.validateRuntimeEvent({ eventId: 'e-min', type: 'T', summary: 'S', provenance: 'SIMULATED' }));
});

test('observatory allows optional improvementLoop and comparisons evidence blocks', () => {
  const base = fixtureProvider._observatory();
  const good = {
    ...base,
    improvementLoop: [
      {
        stageId: 'S1', stage: 'OBSERVATION', state: 'ACTIVE',
        observation: 'Latency spike observed on venue A.',
        evidence: 'Measured mean latency 180ms vs 120ms baseline.'
      }
    ],
    comparisons: [
      {
        dimension: 'Execution Latency', beforeLabel: 'BEFORE', afterLabel: 'AFTER',
        before: '180ms', after: '120ms', delta: '-60ms', provenance: 'DERIVED',
        explanation: 'Latency loss fell after model change.'
      }
    ]
  };
  assert.doesNotThrow(() => validators.validateObservatory(good));
  // these blocks must remain optional (UI defaults to NOT INSTRUMENTED)
  assert.doesNotThrow(() => validators.validateObservatory(base));
});

test('capability entries accept category/recentActivity/evidence explorer fields', () => {
  const base = fixtureProvider._device();
  const good = {
    ...base,
    capabilities: base.capabilities.map((c) => ({
      ...c,
      category: 'COGNITION',
      recentActivity: 'None reported',
      evidence: 'Awaiting live substrate'
    }))
  };
  assert.doesNotThrow(() => validators.validateDeviceAndCapabilities(good));
});