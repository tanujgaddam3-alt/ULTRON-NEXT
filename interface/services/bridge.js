/**
 * ULTRON Integration Bridge
 * The event-driven decoupling layer between the ULTRON UI and backend data sources.
 * Authoritative integration boundary: the UI never calls backend private methods
 * directly. All payloads are validated against the integration contracts before
 * being delivered to UI subscribers.
 *
 * Classic-script namespace (window.ULTRON.bridge) — single authoritative bridge,
 * there is intentionally no second bridge embedded inside index.html.
 */
(function (root) {
  'use strict';

  var validators = root.ULTRON.contracts.validators;

  // Map of publish domains -> validator method names
  var VALIDATION_DOMAINS = {
    cognitiveState: 'validateCognitiveState',
    vitalMatrix: 'validateVitalMatrix',
    pipeline: 'validatePipeline',
    omega: 'validateOmega',
    trading: 'validateTrading',
    observatory: 'validateObservatory',
    device: 'validateDeviceAndCapabilities',
    operation: 'validateOperationStream',
    command: 'validateCommandCenter',
    runtimeEvents: 'validateRuntimeEventStream',
    world: 'validateWorld'
  };

  function UltronIntegrationBridge() {
    this.subscribers = new Map();
    this.currentProvider = null;
    this.stateCache = {};
    this._provenance = 'NOT_INSTRUMENTED';
    this._integrityListeners = [];
  }

  /**
   * Subscribe a UI component callback to a specific contract domain.
   * Domains: 'provenance', 'cognitiveState', 'vitalMatrix', 'pipeline', 'omega',
   *          'trading', 'observatory', 'device', 'command', 'operation', 'runtimeEvents', 'world'
   */
  UltronIntegrationBridge.prototype.subscribe = function (contractDomain, callback) {
    var bridge = this;
    if (!this.subscribers.has(contractDomain)) {
      this.subscribers.set(contractDomain, new Set());
    }
    this.subscribers.get(contractDomain).add(callback);

    // If cached data already exists for this contract, deliver it immediately
    if (this.stateCache[contractDomain]) {
      try {
        callback(this.stateCache[contractDomain]);
      } catch (err) {
        console.error('[UltronBridge] Initial delivery failed for ' + contractDomain + ':', err);
      }
    }
    return function () {
      var set = bridge.subscribers.get(contractDomain);
      if (set) set.delete(callback);
    };
  };

  /**
   * Subscribe to integrity events (contract violations, provider attach,
   * blocked dispatches). These are client-side observability signals, NOT
   * backend telemetry; they carry no production provenance.
   */
  UltronIntegrationBridge.prototype.onIntegrity = function (callback) {
    var bridge = this;
    this._integrityListeners.push(callback);
    return function () {
      var idx = bridge._integrityListeners.indexOf(callback);
      if (idx !== -1) bridge._integrityListeners.splice(idx, 1);
    };
  };

  UltronIntegrationBridge.prototype._emitIntegrity = function (event) {
    var timestamp = new Date().toISOString();
    this._integrityListeners.forEach(function (cb) {
      try {
        cb({ timestamp: timestamp, kind: event.kind, message: event.message || '', domain: event.domain || '' });
      } catch (e) {
        console.error('[UltronBridge] integrity listener error:', e);
      }
    });
  };

  /**
   * Set the telemetry provenance state and notify provenance subscribers.
   */
  UltronIntegrationBridge.prototype.setProvenance = function (state) {
    if (root.ULTRON.contracts.VALID_PROVENANCE.indexOf(state) === -1) {
      console.error('[UltronBridge] Invalid provenance state:', state);
      return;
    }
    if (this._provenance === state) return;
    this._provenance = state;
    var listeners = this.subscribers.get('provenance');
    if (listeners) {
      listeners.forEach(function (cb) {
        try { cb(state); } catch (e) { console.error('[UltronBridge] provenance subscriber error:', e); }
      });
    }
  };

  UltronIntegrationBridge.prototype.getProvenance = function () {
    return this._provenance;
  };

  UltronIntegrationBridge.prototype.getState = function (contractDomain) {
    return this.stateCache[contractDomain];
  };

  /**
   * Push incoming payload from provider, validate against the contract,
   * cache it, and broadcast to active UI subscribers.
   */
  UltronIntegrationBridge.prototype.publish = function (contractDomain, payload) {
    try {
      var validatorName = VALIDATION_DOMAINS[contractDomain];
      if (validatorName) {
        validators[validatorName](payload);
      }
      this.stateCache[contractDomain] = payload;

      var listeners = this.subscribers.get(contractDomain);
      if (listeners) {
        listeners.forEach(function (cb) {
          try { cb(payload); } catch (e) { console.error('[UltronBridge] Error in subscriber callback for ' + contractDomain + ':', e); }
        });
      }
      return true;
    } catch (validationErr) {
      console.error("[UltronBridge Contract Violation] Domain '" + contractDomain + "':", validationErr.message, payload);
      this._emitIntegrity({ kind: 'CONTRACT_VIOLATION', domain: contractDomain, message: validationErr.message });
      return false;
    }
  };

  /**
   * Connect a data provider (Mock, WebSocket, IPC, REST).
   * The provider declares its provenance; the bridge adopts it.
   */
  UltronIntegrationBridge.prototype.connectProvider = function (provider) {
    if (!this.currentProvider && !provider) {
      throw new Error('Provider is required');
    }

    if (this.currentProvider && typeof this.currentProvider.disconnect === 'function') {
      try { this.currentProvider.disconnect(); } catch (e) { /* ignore */ }
    }

    // Provider boundaries must never leak cached telemetry across provenance.
    // In particular, LIVE must not inherit SIMULATED state.
    this.stateCache = {};

    this.currentProvider = provider;
    var provenance = provider.provenance || 'NOT_INSTRUMENTED';
    this.setProvenance(provenance);
    provider.connect(this);

    console.log('[UltronBridge] Connected provider: ' + provider.name + ' (provenance=' + provenance + ')');
    this._emitIntegrity({
      kind: 'PROVIDER_ATTACHED',
      message: 'Provider attached: ' + provider.name + ' (provenance=' + provenance + ')'
    });
  };

  /**
   * Dispatch a directive from the UI to the active backend provider.
   * Validates the dispatch contract before anything leaves the client.
   */
  UltronIntegrationBridge.prototype.dispatchCommand = function (commandPayload) {
    try {
      validators.validateCommandDispatch(commandPayload);
    } catch (e) {
      return Promise.reject(e);
    }
    if (!this.currentProvider) {
      this._emitIntegrity({ kind: 'DISPATCH_BLOCKED', message: 'Command blocked: no active backend provider connected' });
      return Promise.reject(new Error('No active backend provider connected to ULTRON bridge'));
    }
    if (this._provenance === 'NOT_INSTRUMENTED' && !this.currentProvider.isSimulated) {
      this._emitIntegrity({ kind: 'DISPATCH_BLOCKED', message: 'Command blocked: transport not validated (' + this._provenance + ')' });
      return Promise.reject(new Error('Provider has no validated transport; command not dispatched'));
    }
    return Promise.resolve(this.currentProvider.handleCommand(commandPayload));
  };

  UltronIntegrationBridge.prototype.sendChat = function (text) {
    if (typeof text !== 'string' || !text.trim()) {
      return Promise.reject(new Error('Chat message must be non-empty text'));
    }
    if (!this.currentProvider || this._provenance !== 'LIVE') {
      this._emitIntegrity({ kind: 'CHAT_BLOCKED', message: 'Chat blocked: authenticated live transport is unavailable' });
      return Promise.reject(new Error('Chat requires a validated live backend connection'));
    }
    if (typeof this.currentProvider.handleChat !== 'function') {
      return Promise.reject(new Error('Active backend provider does not support chat'));
    }
    return Promise.resolve(this.currentProvider.handleChat(text.trim()));
  };

  /**
   * Authorize (approve) a consequential operation. Lifecycle transitions are
   * streamed back by the provider over the 'operation' channel; the UI may
   * never declare completion itself without verification telemetry.
   */
  UltronIntegrationBridge.prototype.approveOperation = function (operation) {
    if (!this.currentProvider) {
      return Promise.reject(new Error('No active backend provider connected to ULTRON bridge'));
    }
    if (typeof this.currentProvider.handleApprove !== 'function') {
      return Promise.reject(new Error('Active provider does not support operation approval'));
    }
    return Promise.resolve(this.currentProvider.handleApprove(operation));
  };

  UltronIntegrationBridge.prototype.reconcileOperation = function (operation) {
    if (!this.currentProvider) {
      return Promise.reject(new Error('No active backend provider connected to ULTRON bridge'));
    }
    if (typeof this.currentProvider.handleReconcile !== 'function') {
      return Promise.reject(new Error('Active provider does not support operation reconciliation'));
    }
    return Promise.resolve(this.currentProvider.handleReconcile(operation));
  };

  root.ULTRON.bridge = new UltronIntegrationBridge();
})(window);