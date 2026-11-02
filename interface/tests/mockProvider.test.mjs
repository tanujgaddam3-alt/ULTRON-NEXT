import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { loadStack, delay } from './helpers.mjs';

const ctx = loadStack();
const B = ctx.ULTRON.bridge;
const validators = ctx.ULTRON.contracts.validators;
const MockProvider = ctx.ULTRON.providers.MockUltronProvider;

let M;

before(() => {
  M = new MockProvider();
  B.connectProvider(M);
});

after(() => {
  if (B.currentProvider) B.currentProvider.disconnect();
});

test('mock provider declares SIMULATED provenance and isSimulated=true', () => {
  assert.equal(M.provenance, 'SIMULATED');
  assert.equal(M.isSimulated, true);
  assert.equal(B.getProvenance(), 'SIMULATED');
});

test('connecting the mock publishes every contract domain with valid payloads', () => {
  const checks = [
    ['vitalMatrix', 'validateVitalMatrix'],
    ['cognitiveState', 'validateCognitiveState'],
    ['pipeline', 'validatePipeline'],
    ['omega', 'validateOmega'],
    ['trading', 'validateTrading'],
    ['observatory', 'validateObservatory'],
    ['device', 'validateDeviceAndCapabilities'],
    ['command', 'validateCommandCenter']
  ];
  checks.forEach(([domain, validatorName]) => {
    const payload = B.getState(domain);
    assert.ok(payload, 'expected cached state for ' + domain);
    assert.doesNotThrow(() => validators[validatorName](payload), 'for domain ' + domain);
  });
});

test('every baseline runtime event carries SIMULATED provenance (integrity)', () => {
  const events = B.getState('runtimeEvents');
  assert.ok(events.length >= 4);
  events.forEach((event) => assert.equal(event.provenance, 'SIMULATED'));
  assert.doesNotThrow(() => events.forEach((e) => validators.validateRuntimeEvent(e)));
});

test('baseline operation lifecycle is honest: no unverifiable completion', () => {
  const ops = B.getState('command').activeOperations;
  const byId = Object.fromEntries(ops.map((op) => [op.operationId, op]));
  assert.equal(byId['op-104'].lifecycleState, 'SUGGESTED');
  assert.equal(byId['op-103'].lifecycleState, 'PLANNED');
  const verified = byId['op-102'];
  assert.equal(verified.lifecycleState, 'VERIFIED');
  assert.ok(verified.executionResult, 'VERIFIED op must carry executionResult');
  assert.ok(verified.verificationAudit, 'VERIFIED op must carry verificationAudit');
});

test('read-only directives acknowledge without mutating the command center', async () => {
  const before = B.getState('command').activeOperations.length;
  const res = await B.dispatchCommand({ commandText: 'Summarize current observation state', actionVerb: 'analyze', parameters: {} });
  assert.equal(res.status, 'ACKNOWLEDGED');
  assert.equal(B.getState('command').activeOperations.length, before);
});

test('consequential directives create a SUGGESTED operation awaiting approval', async () => {
  const res = await B.dispatchCommand({ commandText: 'Restart trading engine', actionVerb: 'execute', parameters: { scope: 'trading' } });
  assert.equal(res.status, 'AWAITING_APPROVAL');
  assert.equal(res.operation.lifecycleState, 'SUGGESTED');
  assert.equal(B.getState('operation')[0].operationId, res.operation.operationId);
  assert.equal(B.getState('command').activeOperations[0].operationId, res.operation.operationId);
});

test('approval drives lifecycle SUGGESTED -> APPROVED -> EXECUTING -> COMPLETED -> VERIFIED', async () => {
  const res = await B.dispatchCommand({ commandText: 'Rotate risk limits', actionVerb: 'modify', parameters: {} });
  const op = res.operation;
  assert.equal(op.lifecycleState, 'SUGGESTED');

  await B.approveOperation(op);
  assert.equal(op.lifecycleState, 'APPROVED');

  await delay(1000);
  assert.equal(op.lifecycleState, 'EXECUTING');

  await delay(900); // t ~1.9s -> COMPLETED
  assert.equal(op.lifecycleState, 'COMPLETED');
  assert.ok(op.executionResult, 'COMPLETED op must carry an execution result');
  // Not yet verified until the provider streams the audit.
  assert.equal(op.verificationAudit, undefined);

  await delay(800); // t ~2.7s -> VERIFIED
  assert.equal(op.lifecycleState, 'VERIFIED');
  assert.ok(op.executionResult);
  assert.ok(op.verificationAudit);

  const evt = B.getState('runtimeEvents')[0];
  assert.equal(evt.domain, 'COMMAND');
  assert.equal(evt.provenance, 'SIMULATED');
});

test('disconnect stops the ambient heartbeat timers', () => {
  const timersBefore = M.timers.length;
  assert.ok(timersBefore > 0);
  M.disconnect();
  assert.equal(M.timers.length, 0);
});