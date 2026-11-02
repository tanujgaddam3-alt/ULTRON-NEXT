/**
 * ULTRON Integration Contracts - Runtime Schema & Contract Validators
 * Validates payload structures to ensure clean decoupled integration
 * between the ULTRON UI and backend data services.
 *
 * Classic-script namespace (window.ULTRON.contracts) so the interface
 * continues to load from file:// with a single authoritative integration path.
 */
(function (root) {
  'use strict';

  var VALID_MODES = ['IDLE', 'THINKING', 'ACTIVE', 'MULTI_AGENT', 'OMEGA_SYNC', 'TRADING_ANALYSIS', 'EXECUTION', 'ALERT', 'DEGRADED', 'OFFLINE'];
  var VALID_LIFECYCLE = ['SUGGESTED', 'PLANNED', 'APPROVED', 'EXECUTING', 'COMPLETED', 'VERIFIED', 'UNVERIFIED', 'UNCERTAIN', 'FAILED', 'CANCELLED'];
  var VALID_ACTIONS = ['ask', 'analyze', 'investigate', 'search', 'create', 'modify', 'execute', 'control', 'monitor', 'delegate', 'plan', 'debug', 'research', 'automate', 'trade'];
  var VALID_PROVENANCE = ['LIVE', 'SIMULATED', 'OFFLINE', 'NOT_INSTRUMENTED'];

  function isObject(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }
  function isNumber(v) { return typeof v === 'number' && isFinite(v); }
  function isString(v) { return typeof v === 'string'; }

  var UltronContractValidators = {
    /**
     * Validates a cognitive state payload
     */
    validateCognitiveState: function (data) {
      if (!isObject(data)) throw new Error('Invalid CognitiveState: payload must be an object');
      if (VALID_MODES.indexOf(data.behavior) === -1) {
        throw new Error("Invalid CognitiveState.behavior: received '" + data.behavior + "', expected one of " + VALID_MODES.join(', '));
      }
      if (data.tensorInferenceRateHz !== undefined && !isNumber(data.tensorInferenceRateHz)) {
        throw new Error('Invalid CognitiveState: tensorInferenceRateHz must be numeric when reported');
      }
      if (!(data.agents === null || Array.isArray(data.agents))) {
        throw new Error('Invalid CognitiveState: agents must be an array or null when unavailable');
      }
      return true;
    },

    /**
     * Validates vital matrix overview payload
     */
    validateVitalMatrix: function (data) {
      if (!isObject(data)) throw new Error('Invalid VitalMatrix: payload must be an object');
      var requiredKeys = ['health', 'activity', 'focus', 'agents', 'omega', 'currentTask', 'anomalies', 'capability', 'performance', 'attention'];
      for (var i = 0; i < requiredKeys.length; i++) {
        var key = requiredKeys[i];
        if (!isObject(data[key])) {
          throw new Error("Invalid VitalMatrix: missing required metric block '" + key + "'");
        }
        if (!isString(data[key].primaryValue) || !isString(data[key].question)) {
          throw new Error('Invalid VitalMatrix.' + key + ': primaryValue and question are required');
        }
      }
      return true;
    },

    /**
     * Validates cognitive pipeline stage payload
     */
    validatePipeline: function (data) {
      if (!isObject(data) || !Array.isArray(data.stages)) throw new Error('Invalid CognitivePipeline: stages array required');
      var requiredFields = ['id', 'title', 'reasoningState', 'decisionRationale', 'inputs', 'outputs', 'confidence', 'evidence', 'executionState'];
      for (var idx = 0; idx < data.stages.length; idx++) {
        var stage = data.stages[idx];
        for (var f = 0; f < requiredFields.length; f++) {
          if (stage[requiredFields[f]] === undefined) {
            throw new Error('Invalid Pipeline stage at index ' + idx + ": missing required field '" + requiredFields[f] + "'");
          }
        }
      }
      return true;
    },

    /**
     * Validates OMEGA subsystem payload
     */
    validateOmega: function (data) {
      if (!isObject(data)) throw new Error('Invalid Omega payload');
      if (data.access !== undefined) {
        if (!isObject(data.access) || data.access.available !== false ||
            data.access.status !== 'AUTHENTICATION_REQUIRED') {
          throw new Error('Invalid Omega.access payload');
        }
        return true;
      }
      if (!(data.bridgeLatencyMs === null || isNumber(data.bridgeLatencyMs))) {
        throw new Error('Invalid Omega: bridgeLatencyMs must be numeric or null when not measured');
      }
      if (!(data.communicationTrace === null || Array.isArray(data.communicationTrace))) {
        throw new Error('Invalid Omega: communicationTrace must be an array or null when not reported');
      }
      if (!data.model) throw new Error('Invalid Omega: model block required');
      if (data.integration !== undefined) {
        if (!isObject(data.integration) || !Array.isArray(data.integration.exchangeTrace)) {
          throw new Error('Invalid Omega.integration payload');
        }
        data.integration.exchangeTrace.forEach(function (exchange) {
          if (!isObject(exchange) || !isString(exchange.method) ||
              !isString(exchange.endpoint) || !isString(exchange.direction) ||
              !isString(exchange.at) || !isNumber(exchange.latencyMs) ||
              !(exchange.responseStatus === null || isNumber(exchange.responseStatus)) ||
              typeof exchange.success !== 'boolean' ||
              typeof exchange.responseValid !== 'boolean') {
            throw new Error('Invalid Omega.integration.exchangeTrace entry');
          }
        });
      }
      if (data.capabilities !== undefined) {
        var capabilities = data.capabilities;
        if (!isObject(capabilities) || typeof capabilities.available !== 'boolean') {
          throw new Error('Invalid Omega.capabilities payload');
        }
        if (capabilities.available &&
            (!Array.isArray(capabilities.capabilities) ||
             !isNumber(capabilities.total) ||
             Math.floor(capabilities.total) !== capabilities.total)) {
          throw new Error('Invalid Omega.capabilities registry');
        }
        if (!capabilities.available && capabilities.error !== undefined &&
            !isObject(capabilities.error)) {
          throw new Error('Invalid Omega.capabilities error');
        }
      }
      return true;
    },

    /**
     * Validates Trading intelligence payload
     */
    validateTrading: function (data) {
      if (!isObject(data)) throw new Error('Invalid Trading payload');
      if (data.access !== undefined) {
        if (!isObject(data.access) || data.access.available !== false ||
            data.access.status !== 'AUTHENTICATION_REQUIRED') {
          throw new Error('Invalid Trading.access payload');
        }
        return true;
      }
      if (!data.performance || !data.portfolio || !data.efficiencyLoss || !data.benchmarkComparison) {
        throw new Error('Invalid Trading payload: performance, portfolio, efficiencyLoss, and benchmarkComparison blocks are required');
      }
      var loss = data.efficiencyLoss;
      if (!(loss.totalLossUsd === null || isNumber(loss.totalLossUsd)) || !isObject(loss.breakdown)) {
        throw new Error('Invalid EfficiencyLoss: totalLossUsd must be numeric or null and breakdown is required');
      }
      if (data.decisionLog !== undefined) {
        if (!Array.isArray(data.decisionLog)) {
          throw new Error('Invalid Trading: decisionLog (if present) must be an array');
        }
        for (var di = 0; di < data.decisionLog.length; di++) {
          if (!isObject(data.decisionLog[di])) {
            throw new Error('Invalid Trading: decisionLog entries must be objects');
          }
          if (data.decisionLog[di].confidence !== undefined && !isNumber(data.decisionLog[di].confidence)) {
            throw new Error('Invalid Trading: decisionLog.confidence must be numeric');
          }
        }
      }
      if (data.financial !== undefined) {
        if (!isObject(data.financial)) throw new Error('Invalid Trading.financial payload');
        var financialKeys = ['initialization', 'marketData', 'execution', 'portfolio', 'risk', 'executions', 'diagnostics'];
        for (var fi = 0; fi < financialKeys.length; fi++) {
          if (data.financial[financialKeys[fi]] === undefined) {
            throw new Error('Invalid Trading.financial: missing ' + financialKeys[fi]);
          }
        }
        var financial = data.financial;
        ['initialization', 'marketData', 'execution', 'portfolio', 'risk', 'executions'].forEach(function (key) {
          if (!isObject(financial[key])) throw new Error('Invalid Trading.financial.' + key + ': object required');
        });
        if (!Array.isArray(financial.diagnostics)) throw new Error('Invalid Trading.financial.diagnostics: array required');
        if (!isString(financial.initialization.state) ||
            typeof financial.initialization.controllerConfigured !== 'boolean' ||
            typeof financial.initialization.brokerConfigured !== 'boolean' ||
            !isString(financial.initialization.updatedAt)) {
          throw new Error('Invalid Trading.financial.initialization fields');
        }
        var market = financial.marketData;
        if (!isString(market.state) || !isString(market.provider) ||
            !isString(market.source) || !isString(market.symbol) ||
            !(market.latestPrice === null || isNumber(market.latestPrice)) ||
            !(market.latestValidatedAt === null || isString(market.latestValidatedAt)) ||
            !(market.freshnessSeconds === null || isNumber(market.freshnessSeconds)) ||
            !(market.error === null || isObject(market.error))) {
          throw new Error('Invalid Trading.financial.marketData fields');
        }
        ['freshnessLimitSeconds', 'bid', 'ask', 'volume'].forEach(function (key) {
          if (market[key] !== undefined && market[key] !== null && !isNumber(market[key])) {
            throw new Error('Invalid Trading.financial.marketData.' + key);
          }
        });
        var execution = financial.execution;
        if (!isString(execution.mode) || !isString(execution.source) ||
            typeof execution.orderSubmissionFromDashboard !== 'boolean' ||
            typeof execution.liveEnabled !== 'boolean') {
          throw new Error('Invalid Trading.financial.execution fields');
        }
        var portfolio = financial.portfolio;
        if (!isString(portfolio.source) ||
            !(portfolio.cashUsd === null || isNumber(portfolio.cashUsd)) ||
            !(portfolio.equityUsd === null || isNumber(portfolio.equityUsd)) ||
            !(portfolio.positions === null || Array.isArray(portfolio.positions)) ||
            !isString(portfolio.updatedAt) ||
            (portfolio.state !== undefined && !isString(portfolio.state)) ||
            (portfolio.error !== undefined &&
             !(portfolio.error === null || isObject(portfolio.error)))) {
          throw new Error('Invalid Trading.financial.portfolio fields');
        }
        (portfolio.positions || []).forEach(function (position) {
          if (!isObject(position) || !isString(position.symbol) ||
              !isNumber(position.quantity) || !isNumber(position.averagePriceUsd) ||
              !isNumber(position.notionalUsd)) {
            throw new Error('Invalid Trading.financial.portfolio.positions entry');
          }
        });
        var risk = financial.risk;
        if (!isString(risk.state) || !Array.isArray(risk.engines) ||
            !risk.engines.every(isString) || !isObject(risk.rejections) ||
            !isString(risk.rejections.state) ||
            !(risk.rejections.count === null || isNumber(risk.rejections.count))) {
          throw new Error('Invalid Trading.financial.risk fields');
        }
        var executions = financial.executions;
        if (!isString(executions.source) ||
            !(executions.records === null || Array.isArray(executions.records)) ||
            typeof executions.orderSubmissionFromDashboard !== 'boolean') {
          throw new Error('Invalid Trading.financial.executions fields');
        }
        (executions.records || []).forEach(function (record) {
          if (!isObject(record) || !isString(record.symbol) ||
              !isString(record.side) || !isNumber(record.quantity) ||
              !isNumber(record.priceUsd) || !isNumber(record.feesUsd) ||
              !isNumber(record.realizedPnlUsd) || !isString(record.timestamp)) {
            throw new Error('Invalid Trading.financial.executions record');
          }
        });
      }
      return true;
    },

    /**
     * Validates System Observatory payload ("How good is ULTRON?")
     */
    validateObservatory: function (data) {
      if (!isObject(data)) throw new Error('Invalid Observatory payload');
      if (!Array.isArray(data.dimensions)) throw new Error('Invalid Observatory: dimensions array required');
      if (!isObject(data.swot)) throw new Error('Invalid Observatory: swot block required');
      return true;
    },

    /**
     * Validates Device & Capabilities payload (production-host telemetry)
     */
    validateDeviceAndCapabilities: function (data) {
      if (!isObject(data)) throw new Error('Invalid Device payload');
      if (!isObject(data.assessment)) throw new Error('Invalid Device: assessment block required');
      if (!isObject(data.hardware) || !isObject(data.hardware.cpu) || !isObject(data.hardware.memory)) {
        throw new Error('Invalid Device: hardware block (cpu/memory) required');
      }
      ['cores', 'load1'].forEach(function (key) {
        if (data.hardware.cpu[key] !== undefined &&
            data.hardware.cpu[key] !== null && !isNumber(data.hardware.cpu[key])) {
          throw new Error('Invalid Device: hardware.cpu.' + key);
        }
      });
      ['totalGb', 'usedPct'].forEach(function (key) {
        if (data.hardware.memory[key] !== undefined &&
            data.hardware.memory[key] !== null && !isNumber(data.hardware.memory[key])) {
          throw new Error('Invalid Device: hardware.memory.' + key);
        }
      });
      if (data.hardware.storage !== undefined) {
        if (!isObject(data.hardware.storage) ||
            !(data.hardware.storage.totalGb === null || isNumber(data.hardware.storage.totalGb))) {
          throw new Error('Invalid Device: hardware.storage (if present) must include a numeric or null totalGb');
        }
      }
      if (!(data.capabilities === null || Array.isArray(data.capabilities))) {
        throw new Error('Invalid Device: capabilities must be an array or null when unavailable');
      }
      return true;
    },

    /**
     * Validates Consequential Operation payload
     */
    validateOperation: function (op) {
      if (VALID_LIFECYCLE.indexOf(op.lifecycleState) === -1) {
        throw new Error('Invalid Operation: lifecycleState must be one of ' + VALID_LIFECYCLE.join(', '));
      }
      if (!isString(op.operationId) && !isString(op.id)) {
        throw new Error('Invalid Operation: operationId required');
      }
      if (!op.title || !op.intent || !op.expectedEffect) {
        throw new Error('Invalid Operation: title, intent, and expectedEffect are mandatory for consequential operations');
      }
      if (op.lifecycleState === 'COMPLETED' &&
          (!isObject(op.executionResult) || op.executionResult.success !== true)) {
        throw new Error('Invalid Operation: COMPLETED operations require successful execution evidence');
      }
      if ((op.lifecycleState === 'UNVERIFIED' || op.lifecycleState === 'UNCERTAIN') &&
          !isObject(op.executionResult)) {
        throw new Error('Invalid Operation: UNVERIFIED and UNCERTAIN operations must include structured execution evidence');
      }
      if (op.lifecycleState === 'VERIFIED') {
        var audit = op.verificationAudit;
        if (!isObject(op.executionResult) || op.executionResult.success !== true) {
          throw new Error('Invalid Operation: VERIFIED operations require successful execution evidence');
        }
        if (!isObject(audit) || audit.verified !== true ||
            audit.outcome !== 'CONFIRMED_SUCCESS' ||
            audit.operationId !== (op.operationId || op.id) ||
            !isString(op.requestFingerprint) ||
            audit.requestFingerprint !== op.requestFingerprint ||
            !isString(audit.verifier) || !isString(audit.provenance) ||
            !isString(audit.observedAt)) {
          throw new Error('Invalid Operation: VERIFIED requires identity-bound independent verification evidence');
        }
      }
      return true;
    },

    /**
     * Validates the operation snapshot stream published by the gateway.
     */
    validateOperationStream: function (operations) {
      if (!Array.isArray(operations)) throw new Error('Invalid OperationStream: array of operations required');
      for (var i = 0; i < operations.length; i++) {
        this.validateOperation(operations[i]);
      }
      return true;
    },

    /**
     * Validates CommandCenter payload (supported actions + active operations)
     */
    validateCommandCenter: function (data) {
      if (!isObject(data)) throw new Error('Invalid CommandCenter payload');
      if (!Array.isArray(data.supportedActions)) throw new Error('Invalid CommandCenter: supportedActions array required');
      if (!Array.isArray(data.activeOperations)) throw new Error('Invalid CommandCenter: activeOperations array required');
      for (var i = 0; i < data.activeOperations.length; i++) {
        this.validateOperation(data.activeOperations[i]);
      }
      return true;
    },

    /**
     * Validates a single command dispatch request before it reaches a provider
     */
    validateCommandDispatch: function (payload) {
      if (!isObject(payload)) throw new Error('Invalid CommandDispatch: payload must be an object');
      if (!isString(payload.commandText)) throw new Error('Invalid CommandDispatch: commandText required');
      if (VALID_ACTIONS.indexOf(payload.actionVerb) === -1) {
        throw new Error('Invalid CommandDispatch: actionVerb must be one of ' + VALID_ACTIONS.join(', '));
      }
      if (!isObject(payload.parameters)) throw new Error('Invalid CommandDispatch: parameters block required');
      return true;
    },

    validateCreStatus: function (data) {
      if (!isObject(data) || data.status !== 'AVAILABLE' ||
          !isString(data.generatedAt) || !isObject(data.counts) ||
          !isObject(data.services) || !isObject(data.publishing) ||
          !isObject(data.analytics) || !isObject(data.scriptPreviews) ||
          !Array.isArray(data.jobs) ||
          !Array.isArray(data.lifecycle) || !Array.isArray(data.events)) {
        throw new Error('Invalid CRE status payload');
      }
      var previewEvidence = data.scriptPreviews;
      if (!['AVAILABLE', 'UNAVAILABLE'].includes(previewEvidence.state) ||
          !isString(previewEvidence.generatedAt) ||
          !Array.isArray(previewEvidence.items) || previewEvidence.items.length > 100 ||
          (previewEvidence.state === 'UNAVAILABLE' &&
            (!isString(previewEvidence.reason) || previewEvidence.items.length !== 0))) {
        throw new Error('Invalid CRE script-preview evidence status');
      }
      previewEvidence.items.forEach(function (preview) {
        var evidence = preview && preview.audioEvidence;
        var sceneEvidence = preview && preview.sceneVisualEvidence;
        var validStatus = evidence &&
          ['VERIFIED', 'UNVERIFIABLE', 'MISSING', 'INTEGRITY_FAILURE']
            .includes(evidence.status);
        if (!isObject(preview) || !isString(preview.scriptId) ||
            !/^[a-f0-9]{64}$/.test(preview.scriptSha256) ||
            !(preview.artifactSha256 === null ||
              /^[a-f0-9]{64}$/.test(preview.artifactSha256)) ||
            !isString(preview.previewState) || !isString(preview.createdAt) ||
            !isObject(evidence) || !validStatus ||
            typeof evidence.legacy !== 'boolean' ||
            !isNumber(evidence.clippingThreshold) ||
            evidence.clippingThreshold <= 0 ||
            !['VERIFIED', 'FAILED', 'UNVERIFIED'].includes(evidence.sourceBinding) ||
            !['VERIFIED', 'FAILED', 'UNVERIFIED'].includes(evidence.scriptBinding) ||
            !['VERIFIED', 'FAILED', 'UNVERIFIED'].includes(evidence.artifactBinding) ||
            !(evidence.evidenceId === null ||
              /^[a-f0-9]{64}$/.test(evidence.evidenceId)) ||
            !(evidence.algorithmVersion === null || isString(evidence.algorithmVersion)) ||
            !(evidence.createdAt === null || isString(evidence.createdAt)) ||
            !(evidence.lastCheckedAt === null || isString(evidence.lastCheckedAt)) ||
            !(evidence.lastVerifiedAt === null || isString(evidence.lastVerifiedAt)) ||
            !(evidence.sourceWavSha256 === null ||
              /^[a-f0-9]{64}$/.test(evidence.sourceWavSha256)) ||
            !(evidence.sourceBytes === null ||
              (Number.isInteger(evidence.sourceBytes) && evidence.sourceBytes >= 44)) ||
            !(evidence.failureReason === null || isString(evidence.failureReason)) ||
            !(evidence.nextAction === null || isString(evidence.nextAction)) ||
            !isObject(sceneEvidence) ||
            !['VERIFIED', 'MISSING', 'INTEGRITY_FAILURE'].includes(sceneEvidence.state) ||
            sceneEvidence.statusScope !== 'CURRENT_READBACK' ||
            !isString(sceneEvidence.lastCheckedAt) ||
            !(sceneEvidence.sceneContractSha256 === null ||
              /^[a-f0-9]{64}$/.test(sceneEvidence.sceneContractSha256)) ||
            !(sceneEvidence.sceneVisualManifestSha256 === null ||
              /^[a-f0-9]{64}$/.test(sceneEvidence.sceneVisualManifestSha256)) ||
            !(sceneEvidence.rendererVersion === null ||
              isString(sceneEvidence.rendererVersion)) ||
            !(sceneEvidence.failureReason === null ||
              isString(sceneEvidence.failureReason)) ||
            !isString(sceneEvidence.nextAction) ||
            !Array.isArray(sceneEvidence.scenes) ||
            sceneEvidence.scenes.length < 1 ||
            sceneEvidence.scenes.length > 4) {
          throw new Error('Invalid CRE preview audio-evidence record');
        }
        sceneEvidence.scenes.forEach(function (scene, index) {
          var asset = scene && scene.asset;
          if (!isObject(scene) || !isString(scene.sceneId) ||
              scene.order !== index + 1 ||
              !['EXPLANATION', 'PROCESS', 'COMPARISON', 'CAUSE_EFFECT',
                'NUMERICAL_FACT', 'TIMELINE', 'QUESTION_ANSWER', 'SUMMARY']
                .includes(scene.narrativeRole) ||
              !isString(scene.visualStrategy) ||
              !isObject(scene.sourceOffsets) ||
              !Number.isInteger(scene.sourceOffsets.start) ||
              !Number.isInteger(scene.sourceOffsets.endExclusive) ||
              scene.sourceOffsets.start < 0 ||
              scene.sourceOffsets.endExclusive <= scene.sourceOffsets.start ||
              !isString(scene.excerpt) ||
              !/^[a-f0-9]{64}$/.test(scene.excerptSha256) ||
              !(scene.renderedComposition === null ||
                isString(scene.renderedComposition)) ||
              !['VERIFIED', 'MISSING', 'INTEGRITY_FAILURE'].includes(scene.state) ||
              !(scene.failureReason === null || isString(scene.failureReason))) {
            throw new Error('Invalid CRE scene visual evidence record');
          }
          if (scene.state === 'VERIFIED') {
            if (!isObject(asset) || !/^[a-f0-9]{64}$/.test(asset.assetId) ||
                !/^[a-f0-9]{64}$/.test(asset.sha256) ||
                asset.format !== 'PNG' || asset.width !== 720 ||
                asset.height !== 1280 || !Number.isInteger(asset.byteSize) ||
                asset.byteSize <= 0 || !isString(asset.rendererVersion) ||
                !isString(asset.generatedAt) ||
                asset.validationStatus !== 'TECHNICAL_CHECKS_PASSED') {
              throw new Error('Invalid verified CRE scene visual evidence');
            }
          } else if (asset !== null) {
            throw new Error('Unavailable CRE scene visual evidence includes an asset');
          }
        });
        if (sceneEvidence.state === 'VERIFIED' &&
            sceneEvidence.scenes.some((scene) => scene.state !== 'VERIFIED')) {
          throw new Error('Verified CRE scene evidence contains unavailable scenes');
        }
        if (sceneEvidence.state !== 'VERIFIED' &&
            sceneEvidence.scenes.some((scene) => scene.state !== sceneEvidence.state)) {
          throw new Error('Unavailable CRE scene evidence contains a mismatched state');
        }
        if (evidence.status === 'VERIFIED') {
          if (!Number.isInteger(evidence.sampleCount) || evidence.sampleCount <= 0 ||
              !Number.isInteger(evidence.clippedSamples) ||
              evidence.clippedSamples < 0 ||
              evidence.clippedSamples > evidence.sampleCount ||
              !isNumber(evidence.clippedSampleRatio) ||
              Math.abs(
                evidence.clippedSampleRatio -
                evidence.clippedSamples / evidence.sampleCount
              ) > 1e-7 ||
              evidence.clippedSampleRatio > evidence.clippingThreshold ||
              evidence.sourceBinding !== 'VERIFIED' ||
              evidence.scriptBinding !== 'VERIFIED' ||
              evidence.artifactBinding !== 'VERIFIED' ||
              !isString(evidence.lastCheckedAt) ||
              !isString(evidence.lastVerifiedAt)) {
            throw new Error('Invalid verified CRE preview audio evidence');
          }
        } else if (evidence.sampleCount !== null ||
            evidence.clippedSamples !== null ||
            evidence.clippedSampleRatio !== null ||
            !isString(evidence.lastCheckedAt) ||
            !isString(evidence.nextAction)) {
          throw new Error('Invalid unavailable CRE preview audio evidence');
        }
      });
      if (!isNumber(data.counts.total) || data.counts.total < 0 ||
          Math.floor(data.counts.total) !== data.counts.total ||
          !isNumber(data.counts.pendingApprovals) ||
          data.counts.pendingApprovals < 0 ||
          Math.floor(data.counts.pendingApprovals) !== data.counts.pendingApprovals) {
        throw new Error('Invalid CRE status counts');
      }
      ['store', 'rights', 'processing', 'rendering', 'publishing'].forEach(function (key) {
        if (!isString(data.services[key])) throw new Error('Invalid CRE service status');
      });
      if (data.publishing.mode !== 'MOCK_ONLY' ||
          typeof data.publishing.serviceConfigured !== 'boolean' ||
          typeof data.publishing.killSwitchEnabled !== 'boolean' ||
          data.analytics.available !== false ||
          data.analytics.reason !== 'NOT_INSTRUMENTED') {
        throw new Error('Invalid CRE publishing or analytics status');
      }
      if (data.jobs.length > 100 || data.lifecycle.length > 100 || data.events.length > 100) {
        throw new Error('CRE status result exceeds the bounded result limit');
      }
      data.jobs.forEach(function (job) {
        if (!isObject(job) || !isString(job.id) || !isString(job.type) ||
            !isString(job.state) || !isString(job.createdAt) || !isString(job.updatedAt)) {
          throw new Error('Invalid CRE job summary');
        }
      });
      data.lifecycle.forEach(function (item) {
        if (!isObject(item) || !isString(item.id) || !isString(item.state) ||
            !isString(item.createdAt) || !isString(item.updatedAt)) {
          throw new Error('Invalid CRE lifecycle summary');
        }
      });
      data.events.forEach(function (event) {
        if (!isObject(event) || !isString(event.id) || !isString(event.type) ||
            !isString(event.state) || !isString(event.createdAt)) {
          throw new Error('Invalid CRE event summary');
        }
      });
      return true;
    },

    /**
     * Validates a Runtime Event Stream payload (an array of runtime events)
     */
    validateRuntimeEventStream: function (events) {
      if (!Array.isArray(events)) throw new Error('Invalid RuntimeEventStream: array of runtime events required');
      for (var idx = 0; idx < events.length; idx++) {
        this.validateRuntimeEvent(events[idx]);
      }
      return true;
    },

    /**
     * Validates authoritative world telemetry.
     */
    validateWorld: function (data) {
      if (!isObject(data)) {
        throw new Error('Invalid World: payload must be an object');
      }

      if (!isObject(data.system)) {
        throw new Error('Invalid World: system block required');
      }

      if (!isObject(data.project)) {
        throw new Error('Invalid World: project block required');
      }

      if (!isObject(data.runtime)) {
        throw new Error('Invalid World: runtime block required');
      }

      if (data.error !== undefined) {
        if (!isString(data.error) || data.entity_count !== null || data.entities !== null) {
          throw new Error('Invalid World: failed snapshots must identify the error and mark unavailable collections as null');
        }
      } else {
        if (!isNumber(data.entity_count)) {
          throw new Error('Invalid World: entity_count must be numeric');
        }
        if (!Array.isArray(data.entities)) {
          throw new Error('Invalid World: entities array required');
        }
      }

      if (data.provenance !== undefined &&
          VALID_PROVENANCE.indexOf(data.provenance) === -1) {
        throw new Error('Invalid World: invalid provenance');
      }

      return true;
    },

    /**
     * Validates a Runtime Event Stream record
     */
    validateRuntimeEvent: function (event) {
      if (!isObject(event)) throw new Error('Invalid RuntimeEvent: payload must be an object');
      if (!isString(event.eventId)) throw new Error('Invalid RuntimeEvent: eventId required');
      if (!isString(event.type) || !isString(event.summary)) throw new Error('Invalid RuntimeEvent: type and summary required');
      if (VALID_PROVENANCE.indexOf(event.provenance) === -1) {
        throw new Error('Invalid RuntimeEvent: provenance must be one of ' + VALID_PROVENANCE.join(', '));
      }
      return true;
    }
  };

  root.ULTRON = root.ULTRON || {};
  root.ULTRON.contracts = {
    validators: UltronContractValidators,
    VALID_MODES: VALID_MODES,
    VALID_LIFECYCLE: VALID_LIFECYCLE,
    VALID_ACTIONS: VALID_ACTIONS,
    VALID_PROVENANCE: VALID_PROVENANCE
  };
})(window);