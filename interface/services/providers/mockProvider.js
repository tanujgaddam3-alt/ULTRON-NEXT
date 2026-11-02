/**
 * Mock Provider for ULTRON Interface
 * Reference/SIMULATED substrate used ONLY for local development and demos.
 *
 * Integrity contract:
 *  - provenance is 'SIMULATED' and every runtime event carries the same marker
 *  - the browser UI displays the [SIMULATED SUBSTRATE] provenance badge whenever
 *    this provider is active, so these values can never be mistaken for live
 *    ULTRON telemetry
 */
(function (root) {
  'use strict';

  var PROVENANCE = 'SIMULATED';

  function MockUltronProvider() {
    this.name = 'MockUltronProvider (SIMULATED Reference Substrate)';
    this.provenance = PROVENANCE;
    this.isSimulated = true;
    this.bridge = null;
    this.timers = [];
    this._operations = [];
    this._opSeq = 100;
  }

  MockUltronProvider.prototype.connect = function (bridge) {
    this.bridge = bridge;
    bridge.setProvenance(this.provenance);
    this.emitInitialState();
    this.startAmbientSimulation();
  };

  MockUltronProvider.prototype.disconnect = function () {
    for (var i = 0; i < this.timers.length; i++) clearInterval(this.timers[i]);
    this.timers = [];
  };

  MockUltronProvider.prototype._now = function () {
    return new Date().toISOString();
  };

  /* ==========================================================================
     SIMULATED INITIAL STATE (contract-compliant, reference values only)
     ========================================================================== */
  MockUltronProvider.prototype.emitInitialState = function () {
    this.bridge.publish('vitalMatrix', this._vitalMatrix());
    this.bridge.publish('cognitiveState', this._cognitiveState());
    this.bridge.publish('pipeline', this._pipeline());
    this.bridge.publish('omega', this._omega());
    this.bridge.publish('trading', this._trading());
    this.bridge.publish('observatory', this._observatory());
    this.bridge.publish('device', this._device());
    this.bridge.publish('command', this._commandCenter());
    this.bridge.publish('operation', this._operations.slice(0, 40));
    this.bridge.publish('runtimeEvents', this._seedEvents());
  };

  MockUltronProvider.prototype._vitalMatrix = function () {
    return {
      timestamp: this._now(),
      health: { key: 'health', question: 'Is ULTRON healthy?', primaryValue: 'NOMINAL', secondaryValue: 'Reference coherence sample', statusLevel: 'NOMINAL', subsystemId: 'system' },
      activity: { key: 'activity', question: 'What is ULTRON doing?', primaryValue: 'STANDBY', secondaryValue: 'Awaiting live signal feed', statusLevel: 'NOMINAL', subsystemId: 'system' },
      focus: { key: 'focus', question: 'Thinking about?', primaryValue: '—', secondaryValue: 'Awaiting telemetry', statusLevel: 'NOMINAL', subsystemId: 'reasoning' },
      agents: { key: 'agents', question: 'Active agents?', primaryValue: '0 ACTIVE', secondaryValue: 'Mock substrate idle', statusLevel: 'NOMINAL', subsystemId: 'agents' },
      omega: { key: 'omega', question: 'Is OMEGA active?', primaryValue: 'STANDBY', secondaryValue: 'Awaiting peer connection', statusLevel: 'NOMINAL', subsystemId: 'omega' },
      currentTask: { key: 'currentTask', question: 'Working on currently?', primaryValue: '—', secondaryValue: 'No active task', statusLevel: 'NOMINAL', subsystemId: 'execution' },
      anomalies: { key: 'anomalies', question: 'Anything wrong?', primaryValue: '0 FAULTS', secondaryValue: 'No anomalies reported', statusLevel: 'NOMINAL', subsystemId: 'diagnostics' },
      capability: { key: 'capability', question: 'How capable?', primaryValue: 'AWAITING TELEMETRY', secondaryValue: 'Capabilities depend on live substrate', statusLevel: 'NOMINAL', subsystemId: 'ai_core' },
      performance: { key: 'performance', question: 'How performing?', primaryValue: '—', secondaryValue: 'Awaiting trading telemetry', statusLevel: 'NOMINAL', subsystemId: 'financial' },
      attention: { key: 'attention', question: 'Requires attention?', primaryValue: 'NONE', secondaryValue: 'No attention items', statusLevel: 'NOMINAL', subsystemId: 'system' }
    };
  };

  MockUltronProvider.prototype._cognitiveState = function () {
    return {
      timestamp: this._now(),
      behavior: 'IDLE',
      headline: 'IDLE // SIMULATED SUBSTRATE',
      description: 'Mock reference substrate running in idle background state. No live production telemetry is attached.',
      tensorInferenceRateHz: 0,
      synapticThroughputTops: 0,
      packetRoutingRateBps: 0,
      attentionEntropyDelta: 0,
      satellites: [
        { id: 'memory', name: 'MEMORY', status: 'STANDBY', tag: 'REFERENCE', coherence: 0 },
        { id: 'omega', name: 'OMEGA', status: 'STANDBY', tag: 'REFERENCE', coherence: 0 },
        { id: 'reasoning', name: 'REASONING', status: 'STANDBY', tag: 'REFERENCE', coherence: 0 }
      ],
      agents: [
        { name: 'AGENT-ALPHA', role: 'EXECUTION', status: 'STANDBY', channelLatencyMs: 0 }
      ]
    };
  };

  MockUltronProvider.prototype._pipeline = function () {
    var stage = function (id, order, title, assignedAgent, stageStatus, reasoningState, decisionRationale, inputs, outputs, confidence, evidence, executionState) {
      return { id: id, order: order, title: title, assignedAgent: assignedAgent, stageStatus: stageStatus, reasoningState: reasoningState, decisionRationale: decisionRationale, inputs: inputs, outputs: outputs, confidence: confidence, evidence: evidence, executionState: executionState };
    };
    return {
      pipelineVersion: '5.2.0 (reference)',
      activeStageIndex: 0,
      stages: [
        stage('perception', '01 // STAGE', 'PERCEPTION', 'BETA (INGEST)', 'STANDBY', 'Awaiting live sensory stream.', 'No live feed attached; reference stage placeholders.', 'No inputs attached.', 'No outputs yet.', 'Not instrumented.', 'Not instrumented.', 'Idle.'),
        stage('understanding', '02 // STAGE', 'UNDERSTANDING', 'BETA (SEMANTICS)', 'STANDBY', 'Awaiting telemetry.', 'No live classification running.', 'No inputs attached.', 'No outputs yet.', 'Not instrumented.', 'Not instrumented.', 'Idle.'),
        stage('context', '03 // STAGE', 'CONTEXT', 'MEM-ROUTER', 'STANDBY', 'Awaiting telemetry.', 'No context assembly running.', 'No inputs attached.', 'No outputs yet.', 'Not instrumented.', 'Not instrumented.', 'Idle.'),
        stage('reasoning', '04 // STAGE', 'REASONING', 'ULTRON (CORE)', 'STANDBY', 'Awaiting telemetry.', 'No reasoning graph active.', 'No inputs attached.', 'No outputs yet.', 'Not instrumented.', 'Not instrumented.', 'Idle.'),
        stage('decision', '05 // STAGE', 'DECISION', 'GAMMA (CONSENSUS)', 'STANDBY', 'Awaiting telemetry.', 'No quorum vote active.', 'No inputs attached.', 'No outputs yet.', 'Not instrumented.', 'Not instrumented.', 'Idle.'),
        stage('planning', '06 // STAGE', 'PLANNING', 'ALPHA (ROUTE)', 'STANDBY', 'Awaiting telemetry.', 'No plan active.', 'No inputs attached.', 'No outputs yet.', 'Not instrumented.', 'Not instrumented.', 'Idle.'),
        stage('execution', '07 // STAGE', 'EXECUTION', 'ALPHA (DISPATCH)', 'STANDBY', 'Awaiting telemetry.', 'No dispatch active.', 'No inputs attached.', 'No outputs yet.', 'Not instrumented.', 'Not instrumented.', 'Idle.'),
        stage('reflection', '08 // STAGE', 'REFLECTION', 'DELTA (AUDIT)', 'STANDBY', 'Awaiting telemetry.', 'No audit running.', 'No inputs attached.', 'No outputs yet.', 'Not instrumented.', 'Not instrumented.', 'Idle.'),
        stage('learning', '09 // STAGE', 'LEARNING', 'ULTRON (TUNE)', 'STANDBY', 'Awaiting telemetry.', 'No tuning loop active.', 'No inputs attached.', 'No outputs yet.', 'Not instrumented.', 'Not instrumented.', 'Idle.'),
        stage('self_improvement', '10 // STAGE', 'SELF-IMPROVEMENT', 'AUTONOMY CORE', 'STANDBY', 'Awaiting telemetry.', 'No improvement loop active.', 'No inputs attached.', 'No outputs yet.', 'Not instrumented.', 'Not instrumented.', 'Idle.')
      ]
    };
  };

  MockUltronProvider.prototype._omega = function () {
    return {
      entityState: 'SYNCHRONIZING',
      bridgeLatencyMs: 0,
      channelCoherencePct: 0,
      busFrequencyTHz: 0,
      model: {
        architecture: 'Awaiting OMEGA model telemetry from live substrate',
        parameterCount: '—',
        contextTokens: 0,
        specialization: 'Awaiting telemetry'
      },
      resources: {
        allocatedCores: 0,
        vramAllocatedGb: 0,
        vramTotalGb: 0,
        ringBufferMemoryGb: 0
      },
      activeAgents: [],
      communicationTrace: [],
      safetyLimitation: 'No live OMEGA peer connected. Bridge latency cannot be validated.'
    };
  };

  MockUltronProvider.prototype._trading = function () {
    return {
      performance: {
        netRealizedPnlUsd: 0, realizedPnlUsd: 0, unrealizedPnlUsd: 0,
        returnMtdPct: 0, winRatePct: 0, totalWins: 0, totalLosses: 0,
        sharpeRatio: 0, sortinoRatio: 0, profitFactor: 0, maxDrawdownPct: 0, annualizedVolPct: 0
      },
      portfolio: {
        totalValueUsd: 0,
        netDeltaExposureUsd: 0,
        positions: []
      },
      executionQuality: { meanFillLatencyNs: 0, meanAdverseSlippagePct: 0, topOfBookFillPct: 0, venueFeeSavingsUsd: 0 },
      marketIntelligence: {
        detectedRegime: 'Awaiting telemetry',
        annualizedVolatilityPct: 0,
        sentimentScore: 0,
        sentimentLabel: 'Awaiting telemetry',
        liquidityThicknessUsd: 0,
        activeSignalsPerSec: 0
      },
      strategies: [],
      efficiencyLoss: {
        potentialReturnUsd: 0, realizedReturnUsd: 0, totalLossUsd: 0, leakagePct: 0,
        formula: 'Awaiting live efficiency telemetry.',
        breakdown: {
          opportunityLossUsd: 0, opportunityLossReason: 'Awaiting telemetry',
          executionLatencyLossUsd: 0, executionLatencyLossReason: 'Awaiting telemetry',
          transactionCostLossUsd: 0, transactionCostLossReason: 'Awaiting telemetry',
          slippageLossUsd: 0, slippageLossReason: 'Awaiting telemetry',
          signalQualityLossUsd: 0, signalQualityLossReason: 'Awaiting telemetry'
        }
      },
      benchmarkComparison: {
        benchmarks: [],
        driversOfDifference: [],
        periodOverPeriod: { currentPeriodPnl: 0, previousPeriodPnl: 0, growthPct: 0, drawdownReductionPct: 0, latencyGainNs: 0, sharpeTrendPct: 0 }
      }
    };
  };

  MockUltronProvider.prototype._observatory = function () {
    return {
      methodology: 'Composite evaluation across observable dimensions. Values below are SIMULATED REFERENCE samples intended to exercise the interface; they are not live ULTRON measurements.',
      dimensions: [
        { dimensionId: 'cognitive', name: 'Cognitive Performance', scoreDisplay: 'REFERENCE', trendDisplay: '—', detail: 'Simulated sample only. Not instrumented.', rawScore: 0 },
        { dimensionId: 'task_success', name: 'Task Success Rate', scoreDisplay: 'REFERENCE', trendDisplay: '—', detail: 'Simulated sample only. Not instrumented.', rawScore: 0 },
        { dimensionId: 'reasoning', name: 'Reasoning Performance', scoreDisplay: 'REFERENCE', trendDisplay: '—', detail: 'Simulated sample only. Not instrumented.', rawScore: 0 },
        { dimensionId: 'planning', name: 'Planning Performance', scoreDisplay: 'REFERENCE', trendDisplay: '—', detail: 'Simulated sample only. Not instrumented.', rawScore: 0 },
        { dimensionId: 'decision', name: 'Decision Quality', scoreDisplay: 'REFERENCE', trendDisplay: '—', detail: 'Simulated sample only. Not instrumented.', rawScore: 0 },
        { dimensionId: 'memory', name: 'Memory Performance', scoreDisplay: 'REFERENCE', trendDisplay: '—', detail: 'Simulated sample only. Not instrumented.', rawScore: 0 },
        { dimensionId: 'tool_latency', name: 'Tool Execution Latency', scoreDisplay: 'REFERENCE', trendDisplay: '—', detail: 'Simulated sample only. Not instrumented.', rawScore: 0 },
        { dimensionId: 'reliability', name: 'System Reliability', scoreDisplay: 'REFERENCE', trendDisplay: '—', detail: 'Simulated sample only. Not instrumented.', rawScore: 0 },
        { dimensionId: 'omega', name: 'OMEGA Contribution', scoreDisplay: 'REFERENCE', trendDisplay: '—', detail: 'Simulated sample only. Not instrumented.', rawScore: 0 },
        { dimensionId: 'trading_efficiency', name: 'Trading Efficiency', scoreDisplay: 'REFERENCE', trendDisplay: '—', detail: 'Simulated sample only. Not instrumented.', rawScore: 0 },
        { dimensionId: 'resources', name: 'Resource Efficiency', scoreDisplay: 'REFERENCE', trendDisplay: '—', detail: 'Simulated sample only. Not instrumented.', rawScore: 0 },
        { dimensionId: 'self_improvement', name: 'Self-Improvement', scoreDisplay: 'REFERENCE', trendDisplay: '—', detail: 'Simulated sample only. Not instrumented.', rawScore: 0 }
      ],
      swot: {
        strengths: ['Clean contract boundary isolates simulated data from live telemetry.'],
        weaknesses: ['No live substrate attached; all domains are not instrumented in this mode.'],
        recentImprovements: ['Provenance attribution added to every displayed metric.'],
        bottlenecksAndActions: ['Connect a real ULTRON backend via the LIVE provider to populate observable values.']
      }
    };
  };

  MockUltronProvider.prototype._device = function () {
    return {
      host: { role: 'production', hostName: 'ULTRON PRODUCTION HOST', simulated: true },
      assessment: {
        computeGrade: 'MEDIUM', memoryGrade: 'MEDIUM', storageGrade: 'MEDIUM', networkGrade: 'MEDIUM',
        primaryBottleneck: 'Production host telemetry not instrumented in simulated mode',
        actionableRecommendation: 'Attach the live ULTRON backend to report production host hardware telemetry.'
      },
      hardware: {
        cpu: { physicalCores: 0, logicalThreads: 0, frequencyGhz: 0, loadPct: 0, tempCelsius: 0, governor: '—' },
        memory: { totalGb: 0, usedGb: 0, availableGb: 0, pressurePct: 0, swapUsedKb: 0 },
        gpu: { vramTotalGb: 0, vramAllocatedGb: 0, computeUtilityPct: 0, tempCelsius: 0, bandwidthTbPerSec: 0 },
        network: { pingToVenuesMs: 0, bandwidthGbps: 0, droppedPackets: 0, interfaceType: '—', ptpSyncLocked: false }
      },
      capabilities: [
        { capabilityId: 'mem', name: 'MEMORY', status: 'STANDBY', availabilityPct: 0, maturityLevel: 'Experimental', performanceMetric: 'Awaiting telemetry', dependencies: ['live substrate'], limitations: 'Not instrumented', category: 'MEMORY', recentActivity: 'None reported', evidence: null },
        { capabilityId: 'reason', name: 'REASONING', status: 'STANDBY', availabilityPct: 0, maturityLevel: 'Experimental', performanceMetric: 'Awaiting telemetry', dependencies: ['live substrate'], limitations: 'Not instrumented', category: 'COGNITION', recentActivity: 'None reported', evidence: null },
        { capabilityId: 'agents', name: 'AGENT ORCHESTRATION', status: 'STANDBY', availabilityPct: 0, maturityLevel: 'Experimental', performanceMetric: 'Awaiting telemetry', dependencies: ['live substrate'], limitations: 'Not instrumented', category: 'AGENTS', recentActivity: 'None reported', evidence: null },
        { capabilityId: 'web', name: 'WEB RESEARCH', status: 'STANDBY', availabilityPct: 0, maturityLevel: 'Experimental', performanceMetric: 'Awaiting telemetry', dependencies: ['live substrate'], limitations: 'Not instrumented', category: 'KNOWLEDGE', recentActivity: 'None reported', evidence: null },
        { capabilityId: 'code', name: 'CODE ENGINE', status: 'STANDBY', availabilityPct: 0, maturityLevel: 'Experimental', performanceMetric: 'Awaiting telemetry', dependencies: ['live substrate'], limitations: 'Not instrumented', category: 'EXECUTION', recentActivity: 'None reported', evidence: null },
        { capabilityId: 'financial', name: 'FINANCIAL INTELLIGENCE', status: 'STANDBY', availabilityPct: 0, maturityLevel: 'Experimental', performanceMetric: 'Awaiting telemetry', dependencies: ['live substrate'], limitations: 'Not instrumented', category: 'TRADING', recentActivity: 'None reported', evidence: null },
        { capabilityId: 'system', name: 'SYSTEM CONTROL', status: 'STANDBY', availabilityPct: 0, maturityLevel: 'Experimental', performanceMetric: 'Awaiting telemetry', dependencies: ['live substrate'], limitations: 'Not instrumented', category: 'SYSTEM', recentActivity: 'None reported', evidence: null },
        { capabilityId: 'voice', name: 'VOICE & MULTIMODAL', status: 'STANDBY', availabilityPct: 0, maturityLevel: 'Experimental', performanceMetric: 'Awaiting telemetry', dependencies: ['live substrate'], limitations: 'Not instrumented', category: 'VOICE', recentActivity: 'None reported', evidence: null },
        { capabilityId: 'omega', name: 'OMEGA BRIDGE', status: 'STANDBY', availabilityPct: 0, maturityLevel: 'Experimental', performanceMetric: 'Awaiting telemetry', dependencies: ['live substrate'], limitations: 'Not instrumented', category: 'OMEGA', recentActivity: 'None reported', evidence: null }
      ]
    };
  };

  MockUltronProvider.prototype._commandCenter = function () {
    this._operations = [
      {
        operationId: 'op-104', title: 'Rebalance Delta-Neutral Hedge', lifecycleState: 'SUGGESTED',
        intent: 'Establish full delta neutrality across production venues.',
        plan: 'Draft: adjust hedge offsets after live feed arrival.',
        tools: ['RiskGatewayConfig'], expectedEffect: 'Awaiting live inputs',
        approvalRequirement: 'Human sign-off required', timestamp: this._now()
      },
      {
        operationId: 'op-103', title: 'Attach Live Telemetry Substrate', lifecycleState: 'PLANNED',
        intent: 'Wire production telemetry through the live WebSocket provider.',
        plan: 'Enable LIVE provider and validate all ten contract domains.',
        tools: ['WebSocketUltronProvider'], expectedEffect: 'Transition to LIVE telemetry',
        approvalRequirement: 'Operator confirmation', timestamp: this._now()
      },
      {
        operationId: 'op-102', title: 'Validation Pass: Contract Pipeline', lifecycleState: 'VERIFIED',
        requestFingerprint: 'simulated-request-fingerprint-op-102',
        intent: 'Confirm contract validation path is operational.',
        plan: 'Run all ten domains through bridge validation.',
        tools: ['UltronIntegrationBridge'], expectedEffect: 'Validated integration boundary',
        approvalRequirement: 'None (routine)',
        executionResult: { success: true, status: 'SIMULATED_SUCCESS', summary: 'Validation passed: 10/10 domains' },
        verificationAudit: {
          verified: true,
          outcome: 'CONFIRMED_SUCCESS',
          operationId: 'op-102',
          requestFingerprint: 'simulated-request-fingerprint-op-102',
          verifier: 'mock-provider-contract-fixture',
          provenance: 'SIMULATED',
          observedAt: this._now(),
          expectedEffect: 'Validated integration boundary'
        },
        timestamp: this._now()
      }
    ];
    return {
      supportedActions: ['ask', 'analyze', 'investigate', 'search', 'create', 'modify', 'execute', 'control', 'monitor', 'delegate', 'plan', 'debug', 'research', 'automate', 'trade'],
      activeOperations: this._operations
    };
  };

  MockUltronProvider.prototype._publishOperationSnapshot = function () {
    this.bridge.publish('operation', this._operations.slice(0, 40));
  };

  MockUltronProvider.prototype._seedEvents = function () {
    var seed = [
      { domain: 'SYSTEM', type: 'PROVIDER_ATTACHED', summary: 'Simulated reference substrate attached to integration bridge.' },
      { domain: 'CORE', type: 'PROVENANCE', summary: 'Provenance set to SIMULATED. No values certified as live production telemetry.' },
      { domain: 'SYSTEM', type: 'VALIDATION', summary: 'Contract validation path operational across 10 domains.' },
      { domain: 'COMMAND', type: 'LIFECYCLE', summary: 'Consequential operation lifecycle supports SUGGESTED -> VERIFIED.' }
    ];
    var out = [];
    for (var i = 0; i < seed.length; i++) {
      out.push({
        eventId: 'evt-sim-' + i,
        timestamp: this._now(),
        domain: seed[i].domain,
        type: seed[i].type,
        summary: seed[i].summary,
        severity: 'INFO',
        evidence: null,
        provenance: PROVENANCE
      });
    }
    return out;
  };

  /* ==========================================================================
     AMBIENT SIMULATION (clearly labeled SIMULATED heartbeat events)
     ========================================================================== */
  MockUltronProvider.prototype.startAmbientSimulation = function () {
    var self = this;
    var heartbeats = [
      'Reference substrate heartbeat. No production traffic.',
      'Simulated telemetry clock synchronized with local time.',
      'Bridge validation idle. Awaiting live provider attach.'
    ];
    var hb = 0;
    this.timers.push(setInterval(function () {
      var events = (self.bridge.getState('runtimeEvents') || []).slice(0);
      events.unshift({
        eventId: 'evt-sim-' + Date.now(),
        timestamp: self._now(),
        domain: 'SYSTEM',
        type: 'SIM_HEARTBEAT',
        summary: heartbeats[hb % heartbeats.length],
        severity: 'INFO',
        evidence: null,
        provenance: PROVENANCE
      });
      if (events.length > 30) events.length = 30;
      self.bridge.publish('runtimeEvents', events);
      hb++;
    }, 6000));
  };

  /* ==========================================================================
     COMMAND DISPATCH + CONSEQUENTIAL LIFECYCLE (SIMULATED)
     Lifecycle transitions are streamed to the 'operation'/'command' channels.
     ========================================================================== */
  var CONSEQUENTIAL_VERBS = ['execute', 'control', 'trade', 'create', 'modify', 'delegate', 'automate', 'plan', 'debug'];

  MockUltronProvider.prototype.handleCommand = function (commandPayload) {
    var self = this;
    var isConsequential = CONSEQUENTIAL_VERBS.indexOf(commandPayload.actionVerb) !== -1;

    if (!isConsequential) {
      this._publishRuntimeEvent('COMMAND', commandPayload.actionVerb.toUpperCase(), 'Read-only directive acknowledged (simulated). No system mutation performed.', PROVENANCE);
      return Promise.resolve({
        success: true,
        commandId: 'cmd-sim-' + Date.now(),
        status: 'ACKNOWLEDGED',
        result: 'Read-only directive acknowledged by simulated substrate.'
      });
    }

    var op = {
      operationId: 'op-sim-' + (++this._opSeq),
      requestFingerprint: 'simulated-request-fingerprint-op-sim-' + this._opSeq,
      title: commandPayload.commandText,
      lifecycleState: 'SUGGESTED',
      intent: 'Operator-directed ' + commandPayload.actionVerb + ' directive.',
      plan: 'Awaiting plan elaboration from live substrate; simulated approval flow.',
      tools: ['OperatorDispatch'],
      expectedEffect: 'Determined by backend upon execution',
      approvalRequirement: 'Explicit operator authorization required',
      timestamp: this._now()
    };
    this._operations.unshift(op);
    this._publishOperationSnapshot();
    this._syncCommandCenter();

    return Promise.resolve({
      success: true,
      commandId: 'cmd-sim-' + Date.now(),
      status: 'AWAITING_APPROVAL',
      operation: op
    });
  };

  MockUltronProvider.prototype.handleApprove = function (operation) {
    var self = this;
    var op = operation;
    for (var i = 0; i < this._operations.length; i++) {
      if (this._operations[i].operationId === operation.operationId) { op = this._operations[i]; break; }
    }
    op.lifecycleState = 'APPROVED';
    this._publishOperationSnapshot();
    this._syncCommandCenter();

    // Simulated execution progression (label stays SIMULATED at the UI layer)
    setTimeout(function () {
      op.lifecycleState = 'EXECUTING';
      op.executionResult = { success: false, status: 'EXECUTING', summary: 'Simulated execution in progress.' };
      self._publishOperationSnapshot();
      self._syncCommandCenter();
    }, 900);

    setTimeout(function () {
      op.lifecycleState = 'COMPLETED';
      op.executionResult = { success: true, status: 'COMPLETED', summary: 'Simulated reference execution receipt.' };
      self._publishOperationSnapshot();
      self._syncCommandCenter();
    }, 1800);

    setTimeout(function () {
      op.lifecycleState = 'VERIFIED';
      op.executionResult = { success: true, status: 'COMPLETED', summary: 'Simulated reference execution receipt.' };
      op.verificationAudit = {
        verified: true,
        outcome: 'CONFIRMED_SUCCESS',
        operationId: op.operationId,
        requestFingerprint: op.requestFingerprint,
        verifier: 'mock-provider-reference-verifier',
        provenance: PROVENANCE,
        observedAt: self._now()
      };
      self._publishOperationSnapshot();
      self._syncCommandCenter();
      self._publishRuntimeEvent('COMMAND', 'LIFECYCLE', 'Operation ' + op.operationId + ' verified (reference simulation).', PROVENANCE);
    }, 2600);

    return Promise.resolve({ success: true });
  };

  MockUltronProvider.prototype._publishRuntimeEvent = function (domain, type, summary, provenance) {
    var events = (this.bridge.getState('runtimeEvents') || []).slice(0);
    events.unshift({
      eventId: 'evt-sim-' + Date.now(),
      timestamp: this._now(),
      domain: domain, type: type, summary: summary, severity: 'INFO', evidence: null,
      provenance: provenance || PROVENANCE
    });
    if (events.length > 30) events.length = 30;
    this.bridge.publish('runtimeEvents', events);
  };

  MockUltronProvider.prototype._syncCommandCenter = function () {
    var state = this.bridge.getState('command');
    var cc = state ? state : { supportedActions: ['ask', 'analyze', 'investigate', 'search', 'create', 'modify', 'execute', 'control', 'monitor', 'delegate', 'plan', 'debug', 'research', 'automate', 'trade'] };
    cc.activeOperations = this._operations.slice(0, 10);
    this.bridge.publish('command', cc);
  };

  root.ULTRON.providers = root.ULTRON.providers || {};
  root.ULTRON.providers.MockUltronProvider = MockUltronProvider;
})(window);