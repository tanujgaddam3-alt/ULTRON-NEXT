import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { loadStack } from './helpers.mjs';

const ctx = loadStack();
const B = ctx.ULTRON.bridge;
const validators = ctx.ULTRON.contracts.validators;
const MockProvider = ctx.ULTRON.providers.MockUltronProvider;

const fixture = (() => new MockProvider()._vitalMatrix())();

after(() => {
  if (B.currentProvider && typeof B.currentProvider.disconnect === 'function') {
    B.currentProvider.disconnect();
  }
});

test('subscribe: delivered immediately from cache when data exists', () => {
  const received = [];
  assert.equal(B.publish('vitalMatrix', fixture), true);
  const unsub = B.subscribe('vitalMatrix', (payload) => received.push(payload));
  assert.equal(received.length, 1);
  unsub();
});

test('subscribe: unsubscribe() is callable standalone and stops delivery (bridge this-binding)', () => {
  let calls = 0;
  B.publish('vitalMatrix', fixture);
  const unsub = B.subscribe('vitalMatrix', (payload) => { calls += 1; });
  assert.equal(calls, 1); // initial cache delivery
  assert.doesNotThrow(() => unsub()); // must not throw (regression: unbound `this`)
  B.publish('vitalMatrix', fixture);
  assert.equal(calls, 1); // no delivery after unsubscribe
});

test('publish: contract-violating payload is rejected and cache is not poisoned', () => {
  B.publish('vitalMatrix', fixture);
  const before = B.getState('vitalMatrix');
  assert.equal(B.publish('vitalMatrix', { health: 42 }), false);
  assert.equal(B.getState('vitalMatrix'), before);
});

test('provenance: set/get round-trip and invalid states are rejected', () => {
  B.setProvenance('SIMULATED');
  assert.equal(B.getProvenance(), 'SIMULATED');
  B.setProvenance('NOPE');
  assert.equal(B.getProvenance(), 'SIMULATED');
});

test('provenance subscribers are notified on change', () => {
  const seen = [];
  const unsub = B.subscribe('provenance', (state) => seen.push(state));
  B.setProvenance('OFFLINE');
  assert.deepEqual(seen, ['OFFLINE']);
  unsub();
});

test('dispatchCommand rejects when no provider is attached', async () => {
  assert.equal(B.currentProvider, null);
  await assert.rejects(
    () => B.dispatchCommand({ commandText: 'scan', actionVerb: 'analyze', parameters: {} }),
    /No active backend provider/
  );
});

test('dispatchCommand rejects malformed directives before they reach the provider', async () => {
  B.connectProvider(new MockProvider());
  try {
    await assert.rejects(
      () => B.dispatchCommand({ commandText: 'do it', actionVerb: 'teleport', parameters: {} }),
      /actionVerb/
    );
  } finally {
    B.currentProvider.disconnect();
  }
});

test('sendChat requires a live provider and never uses command dispatch', async () => {
  const fresh = loadStack();
  const bridge = fresh.ULTRON.bridge;
  let dispatched = false;
  let received = '';
  const provider = {
    name: 'TestChatProvider',
    provenance: 'NOT_INSTRUMENTED',
    connect() {},
    disconnect() {},
    handleCommand() { dispatched = true; },
    handleChat(text) { received = text; return Promise.resolve({ success: true, response: 'test-only reply' }); }
  };
  bridge.connectProvider(provider);
  await assert.rejects(() => bridge.sendChat('hello'), /validated live backend connection/);
  bridge.setProvenance('LIVE');
  const result = await bridge.sendChat('  hello  ');
  assert.equal(received, 'hello');
  assert.equal(result.response, 'test-only reply');
  assert.equal(dispatched, false);
  await assert.rejects(() => bridge.sendChat('  '), /non-empty text/);
});

test('approveOperation rejects when no provider supports approval', async () => {
  // A minimal provider that has no handleApprove.
  const bareProvider = {
    name: 'BareProvider',
    provenance: 'SIMULATED',
    isSimulated: true,
    connect: () => {},
    disconnect: () => {},
    handleCommand: () => Promise.resolve({ success: true })
  };
  B.connectProvider(bareProvider);
  try {
    await assert.rejects(() => B.approveOperation({ operationId: 'op-x' }), /does not support operation approval/);
  } finally {
    B.currentProvider.disconnect();
  }
  assert.equal(B.currentProvider, bareProvider);
  await assert.rejects(() => B.approveOperation({ operationId: 'op-x' }), /does not support operation approval/);
});

test('validators referenced by the bridge exist for every domain', () => {
  const domains = ['cognitiveState', 'vitalMatrix', 'pipeline', 'omega', 'trading', 'observatory', 'device', 'operation', 'command', 'runtimeEvents'];
  domains.forEach((domain) => {
    // Reconstruct the bridge's domain->validator mapping expectations.
    const names = ['validateCognitiveState', 'validateVitalMatrix', 'validatePipeline', 'validateOmega', 'validateTrading', 'validateObservatory', 'validateDeviceAndCapabilities', 'validateOperation', 'validateCommandCenter', 'validateRuntimeEventStream'];
    assert.equal(typeof validators[names[domains.indexOf(domain)]], 'function');
  });
});

test('integrity bus: CONTRACT_VIOLATION is broadcast and unsubscribable', () => {
  const events = [];
  const seen = [];
  const off = B.onIntegrity((e) => { seen.push(1); });
  const off2 = B.onIntegrity((e) => events.push(e));
  assert.equal(B.publish('vitalMatrix', { health: 5 }), false);
  assert.equal(events.length, 1);
  assert.equal(events[0].kind, 'CONTRACT_VIOLATION');
  assert.equal(events[0].domain, 'vitalMatrix');
  assert.ok(/health/.test(events[0].message));
  assert.ok(events[0].timestamp);

  off();
  off2();
  const before = events.length;
  B.publish('vitalMatrix', { health: 5 });
  assert.equal(events.length, before, 'no integrity events after unsubscribe');
  assert.ok(B.currentProvider && B.currentProvider.name === 'BareProvider');
});

test('integrity bus: PROVIDER_ATTACHED fires on connectProvider', () => {
  const events = [];
  const off = B.onIntegrity((e) => events.push(e));
  const m = new MockProvider();
  B.connectProvider(m);
  const attaches = events.filter((e) => e.kind === 'PROVIDER_ATTACHED');
  assert.equal(attaches.length, 1);
  assert.ok(/MockUltronProvider/.test(attaches[0].message));
  assert.ok(/SIMULATED/.test(attaches[0].message));
  off();
  B.currentProvider.disconnect();
});

test('integrity bus: DISPATCH_BLOCKED fires when no provider is attached', async () => {
  const fresh = loadStack();
  const bridge = fresh.ULTRON.bridge;
  const events = [];
  bridge.onIntegrity((e) => events.push(e));
  await assert.rejects(
    () => bridge.dispatchCommand({ commandText: 'scan', actionVerb: 'analyze', parameters: {} }),
    /No active backend provider/
  );
  assert.equal(events.filter((e) => e.kind === 'DISPATCH_BLOCKED').length, 1);
});

test('integrity bus: DISPATCH_BLOCKED fires when transport is not validated', async () => {
  const fresh = loadStack();
  const bridge = fresh.ULTRON.bridge;
  const events = [];
  bridge.onIntegrity((e) => events.push(e));
  const noTransport = { name: 'NoTransportProvider', provenance: 'NOT_INSTRUMENTED', connect() {}, disconnect() {} };
  bridge.connectProvider(noTransport);
  await assert.rejects(
    () => bridge.dispatchCommand({ commandText: 'scan', actionVerb: 'analyze', parameters: {} }),
    /no validated transport/
  );
  assert.equal(events.filter((e) => e.kind === 'DISPATCH_BLOCKED').length, 1);
});