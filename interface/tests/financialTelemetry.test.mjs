import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadStack } from './helpers.mjs';

const ctx = loadStack();
const validators = ctx.ULTRON.contracts.validators;
const WSProvider = ctx.ULTRON.providers.WebSocketUltronProvider;
const interfaceDirectory = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const html = fs.readFileSync(path.join(interfaceDirectory, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(interfaceDirectory, 'operator-console.js'), 'utf8');
const css = fs.readFileSync(path.join(interfaceDirectory, 'operator-console.css'), 'utf8');
const websocketProvider = fs.readFileSync(
  path.join(interfaceDirectory, 'services', 'providers', 'websocketProvider.js'),
  'utf8'
);

function financialState(overrides = {}) {
  return {
    initialization: {
      state: 'READY',
      controllerConfigured: true,
      brokerConfigured: true,
      updatedAt: new Date().toISOString()
    },
    marketData: {
      state: 'VALID',
      provider: 'alpaca',
      source: 'AlpacaMarketAdapter',
      symbol: 'TEST',
      latestPrice: 101,
      latestValidatedAt: new Date().toISOString(),
      freshnessSeconds: 0.4,
      error: null
    },
    execution: {
      mode: 'simulation',
      source: 'PaperBroker',
      orderSubmissionFromDashboard: false,
      liveEnabled: false
    },
    portfolio: {
      source: 'PaperBroker',
      cashUsd: 9800,
      equityUsd: 10002,
      positions: [],
      updatedAt: new Date().toISOString()
    },
    risk: { state: 'READY', engines: ['price_guard'], rejections: { state: 'NOT_INSTRUMENTED', count: null } },
    executions: { source: 'PaperBroker', records: [], orderSubmissionFromDashboard: false },
    diagnostics: [],
    ...overrides
  };
}

function trading(financial) {
  return {
    performance: {},
    portfolio: {},
    efficiencyLoss: { totalLossUsd: 0, breakdown: {} },
    benchmarkComparison: {},
    financial
  };
}

test('financial telemetry contract accepts valid simulation data', () => {
  assert.doesNotThrow(() => validators.validateTrading(trading(financialState())));
});

test('market-data contract accepts validated freshness and optional quote fields', () => {
  const state = financialState({
    marketData: {
      state: 'VALID',
      provider: 'alpaca',
      source: 'AlpacaMarketAdapter',
      symbol: 'TEST',
      latestPrice: 101,
      latestValidatedAt: new Date().toISOString(),
      freshnessSeconds: 0.4,
      freshnessLimitSeconds: 30,
      bid: 100.9,
      ask: 101.1,
      volume: 2500,
      error: null
    }
  });
  assert.doesNotThrow(() => validators.validateTrading(trading(state)));
  state.marketData.volume = '2500';
  assert.throws(
    () => validators.validateTrading(trading(state)),
    /marketData\.volume/
  );
});

test('financial telemetry contract accepts unavailable data without a price', () => {
  const state = financialState({
    marketData: {
      state: 'UNAVAILABLE',
      provider: 'alpaca',
      source: 'validated MarketDataService unavailable',
      symbol: 'TEST',
      latestPrice: null,
      latestValidatedAt: null,
      freshnessSeconds: null,
      error: { category: 'not_configured', message: 'No validated service.' }
    }
  });
  assert.doesNotThrow(() => validators.validateTrading(trading(state)));
});

test('financial telemetry can explicitly report that account data requires authentication', () => {
  assert.doesNotThrow(() => validators.validateTrading({
    access: { available: false, status: 'AUTHENTICATION_REQUIRED' }
  }));
  assert.throws(
    () => validators.validateTrading({ access: { available: true, status: 'AUTHENTICATION_REQUIRED' } }),
    /Trading\.access/
  );
});

test('financial telemetry validates nested values and trade-ledger records', () => {
  const valid = financialState({
    portfolio: {
      source: 'PaperBroker',
      cashUsd: 9800,
      equityUsd: 10002,
      positions: [{ symbol: 'TEST', quantity: 2, averagePriceUsd: 100, notionalUsd: 200 }],
      updatedAt: new Date().toISOString()
    },
    executions: {
      source: 'PaperBroker',
      records: [{
        symbol: 'TEST',
        side: 'buy',
        quantity: 2,
        priceUsd: 100,
        feesUsd: 0,
        realizedPnlUsd: 0,
        timestamp: new Date().toISOString()
      }],
      orderSubmissionFromDashboard: false
    }
  });
  assert.doesNotThrow(() => validators.validateTrading(trading(valid)));
  assert.throws(
    () => validators.validateTrading(trading(financialState({
      marketData: { state: 'VALID', latestPrice: 'not-a-price' }
    }))),
    /marketData fields/
  );
});

test('malformed financial telemetry is rejected and cannot poison the bridge contract', () => {
  assert.throws(
    () => validators.validateTrading(trading({ initialization: {}, marketData: {} })),
    /financial.*missing/i
  );
});

test('Trading Control Center exposes read-only supported financial views', () => {
  for (const view of ['trading', 'market-data', 'portfolio', 'trading-history']) {
    assert.match(html, new RegExp(`data-view="${view}"`));
    assert.match(html, new RegExp(`id="view-${view}"`));
  }
  assert.match(html, /Read-only trading telemetry/);
  assert.match(html, /does not submit or approve orders/);
  assert.match(html, /not complete order or decision history/);
  assert.match(websocketProvider, /'trading'/);
  assert.doesNotMatch(html, /place order|submit order|paper_trade/i);
});

test('financial telemetry is subscribed only after authentication', () => {
  const provider = new WSProvider();
  const sent = [];
  provider.socket = { readyState: 1, send(raw) { sent.push(JSON.parse(raw)); } };
  provider._subscribe();
  assert.equal(sent[0].channels.includes('trading'), false);
  provider.authenticated = true;
  provider._subscribe();
  assert.equal(sent[1].channels.includes('trading'), true);
});

test('OMEGA views expose only the gateway state contract and remain read-only', () => {
  for (const view of ['omega', 'omega-intelligence', 'omega-evidence', 'omega-diagnostics', 'omega-link', 'omega-exchanges']) {
    assert.match(html, new RegExp(`data-view="${view}"`));
    assert.match(html, new RegExp(`id="view-${view}"`));
  }
  assert.match(html, /id="omegaCapabilitiesList"/);
  assert.match(html, /id="omegaCliTrace"/);
  assert.match(html, /id="overviewOmegaHealth"/);
  assert.match(html, /id="overviewOmegaLink"/);
  assert.match(app, /OMEGA state is withheld until this WebSocket session is authenticated/);
  assert.match(app, /const trace = integration\.exchangeTrace/);
  assert.match(app, /omega\?\.communicationTrace/);
  assert.match(html, /not a durable audit log, and do not establish synchronization/);
  assert.match(app, /renderOmegaViews/);
  assert.match(app, /last-known because the integration is degraded/);
  assert.match(app, /OMEGA returned an empty capability registry/);
  assert.match(app, /Capability registry unavailable/);
  assert.match(websocketProvider, /'omega'/);
  assert.doesNotMatch(html, /apply proposal|modify OMEGA|run learning/i);
});

test('OMEGA contract validates optional capability and HTTP exchange data', () => {
  const payload = {
    bridgeLatencyMs: 2,
    communicationTrace: [],
    model: { provider: 'ollama' },
    integration: {
      exchangeTrace: [{
        direction: 'ULTRON_GATEWAY -> OMEGA_INTEGRATION_SERVICE',
        method: 'GET',
        endpoint: '/state',
        at: new Date().toISOString(),
        latencyMs: 2,
        responseStatus: 200,
        success: true,
        responseValid: true
      }]
    },
    capabilities: {
      available: true,
      total: 1,
      capabilities: [{ name: 'read_only_state' }]
    }
  };
  assert.doesNotThrow(() => validators.validateOmega(payload));
  payload.capabilities.total = '1';
  assert.throws(() => validators.validateOmega(payload), /capabilities registry/);
});

test('market data renderer labels stale quotes and shows only supplied quote values', () => {
  assert.match(app, /Last validated price \(stale\)/);
  assert.match(app, /Adapter freshness limit/);
  assert.match(app, /Not provided by this provider snapshot/);
});

test('gateway withholds financial account snapshots unless authenticated', () => {
  const gateway = fs.readFileSync(
    path.join(interfaceDirectory, '..', 'ultron', 'application', 'runtime', 'interface_gateway.py'),
    'utf8'
  );
  assert.match(gateway, /channel in \{"trading", "omega"\}/);
  assert.match(gateway, /"status": "AUTHENTICATION_REQUIRED"/);
  assert.match(gateway, /channel not in \{"operation", "command", "trading", "omega"\}/);
});

test('inspector is collapsed by default, integrated on desktop, and closable on mobile', () => {
  assert.match(html, /id="inspector"[^>]*hidden/);
  assert.match(html, /id="inspectorBackdrop"[^>]*hidden/);
  assert.match(app, /classList\.add\('inspector-open'\)/);
  assert.match(app, /classList\.remove\('inspector-open'\)/);
  assert.match(app, /event\.key === 'Escape'/);
  assert.match(css, /\.shell\.inspector-open\s*\{[^}]*310px/);
  assert.match(css, /\.backdrop:not\(\[hidden\]\)/);
});

test('console connects only through the existing backend WebSocket provider', () => {
  assert.match(app, /AUTH_MODE === 'token'/);
  assert.match(app, /ws:\/\/127\.0\.0\.1:8081\/ultron\/stream/);
  assert.match(app, /ws:\/\/127\.0\.0\.1:8080\/ultron\/stream/);
  assert.doesNotMatch(html, /MockUltronProvider|srcSimBtn|srcLiveBtn/);
  assert.match(app, /provider\.reconnect\(\)/);
});

test('renderers do not turn missing or failed snapshots into healthy values', () => {
  assert.match(app, /No runtime snapshot received/);
  assert.match(app, /world\?\.error/);
  assert.match(app, /Snapshot unavailable:/);
  assert.match(app, /Unavailable: no system snapshot received/);
  assert.match(html, /UNKNOWN/);
});
