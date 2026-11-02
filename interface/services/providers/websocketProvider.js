/**
 * WebSocket LIVE Provider for ULTRON Interface
 * Connects to a real ULTRON substrate backend over WebSocket.
 *
 * Provenance integrity:
 *  - Before any validated connection exists: NOT_INSTRUMENTED
 *  - On successful open (validated SUBSCRIBE handshake): LIVE
 *  - On close/error: OFFLINE
 * The UI marks every metric with this provenance; the provider never claims
 * LIVE for synthetic client-side data.
 */
(function (root) {
  'use strict';

  var DEFAULT_ENDPOINT = 'ws://127.0.0.1:8081/ultron/stream';
  var SUBSCRIBE_CHANNELS = [
    'cognitiveState',
    'vitalMatrix',
    'device',
    'command',
    'operation',
    'runtimeEvents',
    'world',
    'omega',
    'trading'
  ];

  function validateAudioEvidence(evidence) {
    var statuses = ['VERIFIED', 'UNVERIFIABLE', 'MISSING', 'INTEGRITY_FAILURE'];
    var bindings = ['VERIFIED', 'FAILED', 'UNVERIFIED'];
    if (
      !evidence ||
      statuses.indexOf(evidence.status) < 0 ||
      typeof evidence.legacy !== 'boolean' ||
      !Number.isFinite(evidence.clippingThreshold) ||
      evidence.clippingThreshold <= 0 ||
      !bindings.includes(evidence.sourceBinding) ||
      !bindings.includes(evidence.scriptBinding) ||
      !bindings.includes(evidence.artifactBinding) ||
      (evidence.evidenceId !== null && !/^[a-f0-9]{64}$/.test(evidence.evidenceId)) ||
      (evidence.sourceWavSha256 !== null && !/^[a-f0-9]{64}$/.test(evidence.sourceWavSha256)) ||
      ['createdAt', 'lastCheckedAt', 'lastVerifiedAt', 'failureReason', 'nextAction']
        .some(function (key) {
          return evidence[key] !== null && typeof evidence[key] !== 'string';
        })
    ) {
      return false;
    }
    if (evidence.status === 'VERIFIED') {
      return (
        evidence.legacy === false &&
        /^[a-f0-9]{64}$/.test(evidence.evidenceId) &&
        typeof evidence.algorithmVersion === 'string' &&
        /^[a-f0-9]{64}$/.test(evidence.sourceWavSha256) &&
        Number.isInteger(evidence.sourceBytes) &&
        evidence.sourceBytes >= 44 &&
        Number.isInteger(evidence.sampleCount) &&
        evidence.sampleCount > 0 &&
        Number.isInteger(evidence.clippedSamples) &&
        evidence.clippedSamples >= 0 &&
        evidence.clippedSamples <= evidence.sampleCount &&
        Number.isFinite(evidence.clippedSampleRatio) &&
        Math.abs(evidence.clippedSampleRatio - evidence.clippedSamples / evidence.sampleCount) <= 1e-7 &&
        evidence.clippedSampleRatio <= evidence.clippingThreshold &&
        evidence.sourceBinding === 'VERIFIED' &&
        evidence.scriptBinding === 'VERIFIED' &&
        evidence.artifactBinding === 'VERIFIED' &&
        typeof evidence.createdAt === 'string' &&
        typeof evidence.lastCheckedAt === 'string' &&
        typeof evidence.lastVerifiedAt === 'string' &&
        evidence.failureReason === null
      );
    }
    return (
      evidence.sampleCount === null &&
      evidence.clippedSamples === null &&
      evidence.clippedSampleRatio === null &&
      evidence.lastCheckedAt !== null &&
      typeof evidence.nextAction === 'string'
    );
  }

  function validateSceneVisualAsset(asset, scene, payload, manifest) {
    var base64Length = asset && asset.dataBase64;
    var expectedComposition = [
      'COMPARISON_LAYOUT',
      'TIMELINE_LAYOUT',
      'QUESTION_ANSWER_LAYOUT',
      'CONCLUSION_LAYOUT',
      'CAUSE_EFFECT_DIAGRAM',
      'NUMERICAL_CALLOUT',
      'PROCESS_DIAGRAM',
      'EXPLANATORY_CONCEPT_MAP'
    ];
    return Boolean(
      asset &&
      /^[a-f0-9]{64}$/.test(asset.assetId) &&
      asset.format === 'PNG' &&
      asset.width === 720 &&
      asset.height === 1280 &&
      Number.isInteger(asset.byteSize) &&
      asset.byteSize > 0 &&
      asset.byteSize <= 512 * 1024 &&
      /^[a-f0-9]{64}$/.test(asset.sha256) &&
      asset.rendererVersion === manifest.sceneRendererVersion &&
      typeof asset.generatedAt === 'string' &&
      asset.validationStatus === 'TECHNICAL_CHECKS_PASSED' &&
      asset.sourceScriptSha256 === payload.scriptSha256 &&
      asset.sceneContractSha256 === manifest.sceneContractSha256 &&
      asset.excerptSha256 === scene.excerptSha256 &&
      asset.visualStrategy === scene.sceneContract?.visualStrategy &&
      asset.composition === scene.renderedComposition &&
      expectedComposition.includes(asset.composition) &&
      asset.safeArea?.leftPx === 42 &&
      asset.safeArea?.topPx === 222 &&
      asset.safeArea?.rightPx === 678 &&
      asset.safeArea?.bottomPx === 908 &&
      asset.safeArea?.captionBoundaryPx === 1060 &&
      typeof base64Length === 'string' &&
      base64Length.length === 4 * Math.ceil(asset.byteSize / 3) &&
      base64Length.length <= 4 * Math.ceil((512 * 1024) / 3) &&
      /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(base64Length)
    );
  }

  function WebSocketUltronProvider(endpointUrl, requestCredential) {
    this.name = 'WebSocketUltronProvider (' + (endpointUrl || DEFAULT_ENDPOINT) + ')';
    this.endpointUrl = endpointUrl || DEFAULT_ENDPOINT;
    this.provenance = 'NOT_INSTRUMENTED';
    this.bridge = null;
    this.socket = null;
    this.requestCredential = requestCredential;
    this.reconnectInterval = 5000;
    this.shouldReconnect = true;
    this.authenticationFailed = false;
    this.authenticationError = null;
    this.authenticationStatus = null;
    this.authenticated = false;
    this.authenticationRequired = null;
    this.transportConnected = false;
    this.keepaliveTimer = null;
    this.creStatusListeners = [];
    this.creStatusSnapshot = null;
    this.creStatusState = { state: 'LOADING', data: null, message: null };
    this.systemIntelligenceListeners = [];
    this.systemIntelligenceSnapshot = null;
    this.systemIntelligenceState = { state: 'LOADING', data: null, message: null };
  }

  WebSocketUltronProvider.prototype.connect = function (bridge) {
    this.bridge = bridge;
    bridge.setProvenance('NOT_INSTRUMENTED');
    this.initSocket();
  };

  WebSocketUltronProvider.prototype.initSocket = function () {
    var self = this;
    try {
      var WS = this._getWebSocketCtor();
      this.socket = new WS(this.endpointUrl);

      this.socket.onopen = function () {
        self.transportConnected = true;
        console.log('[WebSocketProvider] Connected to ULTRON substrate at ' + self.endpointUrl);
        self.bridge.setProvenance('NOT_INSTRUMENTED');
      };

      this.socket.onmessage = function (event) {
        try {
          var message = JSON.parse(event.data);
          if (message.type === 'connected') {
            self.authenticationRequired = message.authenticationRequired === true;
            self.authenticated = false;
            if (message.authenticationRequired) {
              if (typeof self.requestCredential !== 'function') {
                self.authenticationFailed = true;
                self.bridge.setProvenance('OFFLINE');
                self.socket.close();
                return;
              }
              Promise.resolve(self.requestCredential()).then(function (credential) {
                if (typeof credential !== 'string' || credential.length === 0) {
                  throw new Error('An operator credential is required');
                }
                self.socket.send(JSON.stringify({
                  action: 'AUTHENTICATE',
                  correlationId: 'auth-' + Date.now(),
                  credential: credential
                }));
              }).catch(function (error) {
                self.authenticationFailed = true;
                var cancelled = error && error.code === 'AUTHENTICATION_CANCELLED';
                self.authenticationStatus = cancelled
                  ? 'AUTHENTICATION_CANCELLED'
                  : 'AUTHENTICATION_FAILED';
                self.authenticationError = cancelled
                  ? 'Authentication was cancelled before a credential was sent.'
                  : error && error.message
                    ? error.message
                    : 'Operator authentication failed.';
                self.bridge.setProvenance('OFFLINE');
                self.socket.close();
              });
            } else {
              self._subscribe();
            }
            return;
          }
          if (message.action === 'AUTHENTICATION_RESULT') {
            if (message.status === 'AUTHENTICATED') {
              self.authenticationFailed = false;
              self.authenticationError = null;
              self.authenticationStatus = null;
              self.authenticated = true;
              self._subscribe();
            } else {
              self.authenticationFailed = true;
              self.authenticationStatus = message.status || 'AUTHENTICATION_FAILED';
              self.authenticationError = message.error || 'Authentication failed.';
              if (Number.isInteger(message.retryAfterSeconds) && message.retryAfterSeconds > 0) {
                self.authenticationError += ' Retry after ' + message.retryAfterSeconds + ' seconds.';
              }
              self.authenticated = false;
              self._setCreStatus('UNAUTHORIZED', self.creStatusSnapshot, self.authenticationError);
              self.bridge.setProvenance('OFFLINE');
              self.socket.close();
            }
            return;
          }
          if (message.action === 'SUBSCRIBE_ACK') {
            self.bridge.setProvenance('LIVE');
            self._startKeepalive();
            if (self.authenticated) {
              self.requestCreStatus().catch(function () {});
              self.requestSystemIntelligence().catch(function () {});
            }
            return;
          }
          if (message.channel && message.payload && message.correlationId === undefined) {
            // Observable telemetry frame -> validate & forward via the bridge
            self.bridge.publish(message.channel, message.payload);
          }
        } catch (parseErr) {
          console.error('[WebSocketProvider] Malformed JSON received:', parseErr);
        }
      };

      this.socket.onerror = function (err) {
        console.warn('[WebSocketProvider] WebSocket connection error:', err);
        self.bridge.setProvenance('OFFLINE');
      };

      this.socket.onclose = function () {
        console.log('[WebSocketProvider] Socket closed.');
        self.transportConnected = false;
        self.authenticated = false;
        self._setCreStatus(
          self.authenticationFailed
            ? 'UNAUTHORIZED'
            : self.creStatusSnapshot ? 'STALE' : 'DISCONNECTED',
          self.creStatusSnapshot,
          self.authenticationError
        );
        self._setSystemIntelligence(
          self.systemIntelligenceSnapshot ? 'STALE' : 'DISCONNECTED',
          self.systemIntelligenceSnapshot,
          'WebSocket connection is offline; retained inventory is historical.'
        );
        self.bridge.setProvenance('OFFLINE');
        self._stopKeepalive();
        if (self.shouldReconnect && !self.authenticationFailed) {
          setTimeout(function () { self.initSocket(); }, self.reconnectInterval);
        }
      };
    } catch (e) {
      console.warn('[WebSocketProvider] Unable to initialize WebSocket (no WebSocket available):', e.message);
      this.bridge.setProvenance('OFFLINE');
    }
  };

  WebSocketUltronProvider.prototype._subscribe = function () {
    if (!this.socket || this.socket.readyState !== 1 /* WebSocket.OPEN */) return;
    var channels = SUBSCRIBE_CHANNELS.filter(function (channel) {
      return !['omega', 'trading'].includes(channel) || this.authenticated;
    }, this);
    this.socket.send(JSON.stringify({
      action: 'SUBSCRIBE',
      channels: channels
    }));
  };

  WebSocketUltronProvider.prototype._startKeepalive = function () {
    var self = this;
    this._stopKeepalive();
    this.keepaliveTimer = setInterval(function () {
      if (self.socket && self.socket.readyState === 1 /* WebSocket.OPEN */) {
        self.socket.send(JSON.stringify({ action: 'PING' }));
      }
    }, 30000);
  };

  WebSocketUltronProvider.prototype._stopKeepalive = function () {
    if (this.keepaliveTimer !== null) {
      clearInterval(this.keepaliveTimer);
      this.keepaliveTimer = null;
    }
  };

  WebSocketUltronProvider.prototype._getWebSocketCtor = function () {
    if (typeof WebSocket !== 'undefined') return WebSocket;
    if (root.WebSocket) return root.WebSocket;
    throw new Error('WebSocket is not available in this environment');
  };

  WebSocketUltronProvider.prototype.disconnect = function () {
    this.shouldReconnect = false;
    this.authenticated = false;
    this.transportConnected = false;
    this._stopKeepalive();
    if (this.socket && typeof this.socket.close === 'function') this.socket.close();
    this.socket = null;
  };

  WebSocketUltronProvider.prototype.reconnect = function () {
    this.shouldReconnect = false;
    this.authenticationFailed = false;
    this.authenticationError = null;
    this.authenticationStatus = null;
    this.authenticated = false;
    this.transportConnected = false;
    this._stopKeepalive();
    if (this.socket) {
      this.socket.onopen = null;
      this.socket.onmessage = null;
      this.socket.onerror = null;
      this.socket.onclose = null;
      if (typeof this.socket.close === 'function') this.socket.close();
      this.socket = null;
    }
    this.shouldReconnect = true;
    this.initSocket();
  };

  WebSocketUltronProvider.prototype._request = function (action, payload, timeoutMs) {
    var self = this;
    if (!this.socket || this.socket.readyState !== 1 /* WebSocket.OPEN */) {
      return Promise.reject(new Error('Cannot dispatch: live WebSocket is not connected'));
    }
    var socket = this.socket;
    return new Promise(function (resolve, reject) {
      var correlationId = 'req-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);
      var settled = false;
      var timeout;
      function cleanup() {
        clearTimeout(timeout);
        socket.removeEventListener('message', responseHandler);
        socket.removeEventListener('close', closeHandler);
      }
      function finish(callback, value) {
        if (settled) return;
        settled = true;
        cleanup();
        callback(value);
      }
      var responseHandler = function (event) {
        try {
          var res = JSON.parse(event.data);
          if (res.correlationId === correlationId) {
            if (res.success) finish(resolve, res);
            else {
              var error = new Error(res.errorMessage || res.error || res.status || 'Request failed on substrate');
              error.status = res.status || 'REQUEST_FAILED';
              error.response = res;
              finish(reject, error);
            }
          }
        } catch (_) { /* ignore non-correlated frames */ }
      };
      var closeHandler = function (event) {
        var closeCode = event && Number.isInteger(event.code) ? event.code : null;
        var closeReason = event && typeof event.reason === 'string' ? event.reason : '';
        var message = closeCode === 1009
          ? 'WebSocket closed because the request exceeded the transport message-size limit'
          : 'Live WebSocket connection closed before the request completed';
        var error = new Error(message);
        error.transportClosed = true;
        if (closeCode !== null) error.closeCode = closeCode;
        if (closeReason) error.closeReason = closeReason;
        finish(reject, error);
      };
      socket.addEventListener('message', responseHandler);
      socket.addEventListener('close', closeHandler);
      timeout = setTimeout(function () {
        finish(reject, new Error('Request timed out (no host response)'));
      }, timeoutMs || 5000);
      try {
        socket.send(JSON.stringify({ action: action, correlationId: correlationId, payload: payload }));
      } catch (error) {
        finish(reject, error);
      }
    });
  };

  WebSocketUltronProvider.prototype.handleChat = function (text) {
    if (!this.socket || this.socket.readyState !== 1 /* WebSocket.OPEN */) {
      return this._request('CHAT_MESSAGE', { text: text }, 130000);
    }
    if (!this.authenticated) {
      return Promise.reject(new Error('Chat requires an authenticated operator session'));
    }
    return this._request('CHAT_MESSAGE', { text: text }, 130000);
  };

  WebSocketUltronProvider.prototype.handleCommand = function (commandPayload) {
    return this._request('DISPATCH_COMMAND', commandPayload);
  };

  WebSocketUltronProvider.prototype.handleApprove = function (operation) {
    return this._request('APPROVE_OPERATION', operation);
  };

  WebSocketUltronProvider.prototype.handleReconcile = function (operation) {
    return this._request('RECONCILE_OPERATION', {
      operationId: operation.operationId,
      requestFingerprint: operation.requestFingerprint
    });
  };

  WebSocketUltronProvider.prototype._setCreStatus = function (state, data, message) {
    this.creStatusState = { state: state, data: data || null, message: message || null };
    this.creStatusListeners.slice().forEach(function (listener) {
      try { listener(this.creStatusState); } catch (error) {
        console.error('[WebSocketProvider] CRE status listener failed:', error);
      }
    }, this);
  };

  WebSocketUltronProvider.prototype.subscribeCreStatus = function (listener) {
    if (typeof listener !== 'function') throw new TypeError('CRE status listener must be a function');
    this.creStatusListeners.push(listener);
    listener(this.creStatusState);
    var self = this;
    return function () {
      self.creStatusListeners = self.creStatusListeners.filter(function (item) {
        return item !== listener;
      });
    };
  };

  WebSocketUltronProvider.prototype.requestCreStatus = function () {
    var self = this;
    this._setCreStatus(
      this.creStatusSnapshot ? 'STALE' : 'LOADING',
      this.creStatusSnapshot
    );
    return this._request('CRE_STATUS_REQUEST', { limit: 50 }).then(function (response) {
      var validate = root.ULTRON && root.ULTRON.contracts &&
        root.ULTRON.contracts.validators &&
        root.ULTRON.contracts.validators.validateCreStatus;
      if (typeof validate !== 'function' || !response.payload) {
        throw new Error('CRE status contract validator is unavailable');
      }
      validate(response.payload);
      self.creStatusSnapshot = response.payload;
      self._setCreStatus('AVAILABLE', self.creStatusSnapshot);
      return self.creStatusSnapshot;
    }).catch(function (error) {
      var state = error.status === 'AUTHORIZATION_DENIED' ||
        error.status === 'AUTHENTICATION_REQUIRED' ||
        error.status === 'AUTHENTICATION_UNAVAILABLE'
        ? 'UNAUTHORIZED'
        : self.creStatusSnapshot ? 'STALE' : 'UNAVAILABLE';
      self._setCreStatus(state, self.creStatusSnapshot, error.message);
      throw error;
    });
  };

  WebSocketUltronProvider.prototype.requestSystemIntelligence = function () {
    var self = this;
    this._setSystemIntelligence(
      this.systemIntelligenceSnapshot ? 'STALE' : 'LOADING',
      this.systemIntelligenceSnapshot
    );
    return this._request('SYSTEM_INTELLIGENCE_REQUEST', {}, 15000).then(function (response) {
      if (!response.payload || !Array.isArray(response.payload.observations)) {
        throw new Error('System intelligence response is invalid');
      }
      self.systemIntelligenceSnapshot = response.payload;
      self._setSystemIntelligence('AVAILABLE', self.systemIntelligenceSnapshot);
      return response.payload;
    }).catch(function (error) {
      self._setSystemIntelligence(
        self.systemIntelligenceSnapshot ? 'STALE' : 'UNAVAILABLE',
        self.systemIntelligenceSnapshot,
        error.message
      );
      throw error;
    });
  };

  WebSocketUltronProvider.prototype._setSystemIntelligence = function (state, data, message) {
    this.systemIntelligenceState = {
      state: state,
      data: data || null,
      message: message || null
    };
    this.systemIntelligenceListeners.slice().forEach(function (listener) {
      try { listener(this.systemIntelligenceState); } catch (error) {
        console.error('[WebSocketProvider] System intelligence listener failed:', error);
      }
    }, this);
  };

  WebSocketUltronProvider.prototype.subscribeSystemIntelligence = function (listener) {
    if (typeof listener !== 'function') throw new TypeError('System intelligence listener must be a function');
    this.systemIntelligenceListeners.push(listener);
    listener(this.systemIntelligenceState);
    var self = this;
    return function () {
      self.systemIntelligenceListeners = self.systemIntelligenceListeners.filter(function (item) {
        return item !== listener;
      });
    };
  };

  WebSocketUltronProvider.prototype.restartAuthenticationHelper = function () {
    if (!this.authenticated) {
      return Promise.reject(new Error('An authenticated operator session is required'));
    }
    var operationId = root.crypto && typeof root.crypto.randomUUID === 'function'
      ? root.crypto.randomUUID()
      : 'restart-auth-' + Date.now() + '-' + Math.random().toString(36).slice(2);
    return this._request(
      'SYSTEM_RESTART_AUTH_REQUEST',
      { operationId: operationId },
      65000
    );
  };

  WebSocketUltronProvider.prototype.requestCreSourceVideo = function (payload) {
    if (!this.authenticated || !payload || typeof payload.videoBase64 !== 'string') {
      return Promise.reject(new Error('An authenticated source-video upload is required'));
    }
    var encodedLength = payload.videoBase64.length;
    if (!payload.videoBase64 || encodedLength > Math.ceil(32 * 1024 * 1024 * 4 / 3)) {
      return Promise.reject(new Error('Source video must be non-empty and no larger than 32 MiB'));
    }
    if (typeof payload.fileName === 'string') {
      var extension = String(payload.fileName).toLowerCase().match(/\.[^.]+$/)?.[0] || '';
      if (!['.mp4', '.webm', '.mov', '.mkv'].includes(extension)) {
        return Promise.reject(new Error('Unsupported source-video file type'));
      }
    }
    return this._request('CRE_SOURCE_VIDEO_REQUEST', payload, 300000);
  };

  WebSocketUltronProvider.prototype.scanCreImportFolder = function (directory) {
    if (!this.authenticated) {
      return Promise.reject(new Error('An authenticated operator session is required to scan the CRE import folder'));
    }
    if (typeof directory !== 'string' || !directory.trim()) {
      return Promise.reject(new Error('An authorized CRE import directory is required'));
    }
    if (directory.trim() !== '/home/ultron/Videos/CRE_Import') {
      return Promise.reject(new Error('Only /home/ultron/Videos/CRE_Import is authorized for CRE folder scanning'));
    }
    return this._request('CRE_FOLDER_IMPORT_SCAN_REQUEST', {}, 300000);
  };

  WebSocketUltronProvider.prototype.registerCreImportVideo = function (payload) {
    if (
      !this.authenticated ||
      !payload ||
      typeof payload.fileId !== 'string' ||
      !/^[a-f0-9]{64}$/.test(payload.fileId) ||
      typeof payload.targetContext !== 'string' ||
      typeof payload.accountId !== 'string' ||
      typeof payload.territory !== 'string'
    ) {
      return Promise.reject(new Error('An authenticated, validated CRE import-folder selection is required'));
    }
    return this._request('CRE_FOLDER_IMPORT_REGISTER_REQUEST', payload, 300000);
  };

  WebSocketUltronProvider.prototype.createCreProject = function (payload) {
    if (!this.authenticated || !payload || typeof payload.projectId !== 'string' || typeof payload.script !== 'string' || !Array.isArray(payload.sourceAssetIds)) {
      return Promise.reject(new Error('An authenticated CRE project and source assets are required'));
    }
    return this._request('CRE_PROJECT_CREATE_REQUEST', payload, 300000);
  };

  WebSocketUltronProvider.prototype.saveCreProjectTimeline = function (payload) {
    if (!this.authenticated || !payload || typeof payload.projectId !== 'string' || !Array.isArray(payload.segments)) {
      return Promise.reject(new Error('An authenticated CRE project timeline is required'));
    }
    return this._request('CRE_PROJECT_TIMELINE_REQUEST', payload, 300000);
  };

  WebSocketUltronProvider.prototype.renderCreProject = function (payload) {
    if (!this.authenticated || !payload || typeof payload.projectId !== 'string') {
      return Promise.reject(new Error('An authenticated CRE project is required for rendering'));
    }
    return this._request('CRE_PROJECT_RENDER_REQUEST', payload, 300000);
  };

  WebSocketUltronProvider.prototype.autoplanCreProject = function (payload) {
    if (!this.authenticated || !payload || typeof payload.projectId !== 'string') {
      return Promise.reject(new Error('An authenticated CRE project is required for autonomous editing'));
    }
    return this._request('CRE_PROJECT_AUTOPLAN_REQUEST', payload, 300000);
  };

  WebSocketUltronProvider.prototype.renderPrivateCreProject = function (payload) {
    if (
      !this.authenticated ||
      !payload ||
      typeof payload.operationId !== 'string' ||
      typeof payload.projectId !== 'string' ||
      typeof payload.vertical !== 'boolean'
    ) {
      return Promise.reject(new Error('An authenticated project and stable render operation ID are required'));
    }
    return this._request('CRE_PRIVATE_PROJECT_RENDER_REQUEST', payload, 300000);
  };

  WebSocketUltronProvider.prototype.getPrivateCreRenderStatus = function (operationId) {
    if (!this.authenticated || typeof operationId !== 'string') {
      return Promise.reject(new Error('An authenticated private render operation is required'));
    }
    return this._request(
      'CRE_PRIVATE_RENDER_STATUS_REQUEST',
      { operationId: operationId },
      30000
    );
  };

  WebSocketUltronProvider.prototype.getPrivateCreRenderArtifact = function (operationId) {
    if (!this.authenticated || typeof operationId !== 'string') {
      return Promise.reject(new Error('An authenticated private render operation is required'));
    }
    return this._request(
      'CRE_PRIVATE_RENDER_ARTIFACT_REQUEST',
      { operationId: operationId },
      300000
    );
  };

  WebSocketUltronProvider.prototype.resumeCreSourceVideo = function (payload) {
    if (
      !this.authenticated ||
      !payload ||
      typeof payload.assetId !== 'string' ||
      typeof payload.targetContext !== 'string' ||
      typeof payload.accountId !== 'string' ||
      typeof payload.territory !== 'string'
    ) {
      return Promise.reject(new Error('A registered source and authenticated rights scope are required'));
    }
    return this._request('CRE_SOURCE_VIDEO_RESUME_REQUEST', payload, 300000);
  };

  WebSocketUltronProvider.prototype.requestCreSourceRights = function (payload) {
    if (!this.authenticated || !payload || typeof payload.step !== 'string') {
      return Promise.reject(new Error('An authenticated CRE rights workflow step is required'));
    }
    return this._request('CRE_SOURCE_RIGHTS_REQUEST', payload, 300000);
  };

  WebSocketUltronProvider.prototype.checkCreSourceAssetStatus = function (payload) {
    if (
      !this.authenticated ||
      !payload ||
      typeof payload.assetId !== 'string' ||
      typeof payload.targetContext !== 'string' ||
      typeof payload.accountId !== 'string' ||
      typeof payload.territory !== 'string'
    ) {
      return Promise.reject(new Error('A registered source and authenticated rights scope are required'));
    }
    return this._request('CRE_SOURCE_ASSET_STATUS_REQUEST', payload, 300000);
  };

  WebSocketUltronProvider.prototype.requestCreSourceClip = function (payload) {
    if (
      !this.authenticated ||
      !payload ||
      typeof payload.candidateId !== 'string' ||
      typeof payload.highlightRunId !== 'string'
    ) {
      return Promise.reject(new Error('Select an authenticated transcript-linked candidate'));
    }
    return this._request('CRE_SOURCE_CLIP_REQUEST', payload, 300000).then(function (response) {
      var result = response.payload;
      if (
        response.status !== 'CLIP_READY' ||
        !result ||
        typeof result.base64 !== 'string' ||
        result.mimeType !== 'video/mp4' ||
        !/^[a-f0-9]{64}$/.test(result.sha256) ||
        !Number.isInteger(result.width) ||
        !Number.isInteger(result.height) ||
        !Number.isFinite(result.durationSeconds) ||
        result.verification !== 'PASSED'
      ) {
        throw new Error('The rendered clip did not pass its artifact verification contract');
      }
      return result;
    });
  };

  WebSocketUltronProvider.prototype.requestCreScriptPreview = function (script, references) {
    if (typeof script !== 'string' || !Array.isArray(references)) {
      return Promise.reject(new TypeError('CRE script preview input is invalid'));
    }
    if (Array.from(script).length > 20000) {
      return Promise.reject(new Error('Script must be no more than 20,000 Unicode characters. Nothing was submitted.'));
    }
    if (!script.trim() || Array.from(script).some((character) => {
      const codePoint = character.codePointAt(0);
      return codePoint < 32 && character !== '\t' && character !== '\n' && character !== '\r';
    })) {
      return Promise.reject(new Error('Script must contain 1 to 20,000 Unicode characters with no control characters.'));
    }
    return this._request(
      'CRE_SCRIPT_PREVIEW_REQUEST',
      { script: script, references: references },
      240000
    ).then(function (response) {
      var payload = response.payload;
      var sceneContractVersion = payload?.productionManifest?.sceneContractVersion;
      var sceneVisualEvidence = payload?.productionManifest?.sceneVisualEvidence;
      var sceneVisualEvidenceVersionDeclared = Object.prototype.hasOwnProperty.call(
        payload?.productionManifest || {},
        'sceneVisualEvidenceVersion'
      );
      var legacyVisualEvidenceMissing =
        sceneVisualEvidence?.state === 'MISSING' &&
        !sceneVisualEvidenceVersionDeclared;
      var supportedVisualStrategies = [
        'COMPARISON_LAYOUT',
        'PROCESS_DIAGRAM',
        'NUMERICAL_CALLOUT',
        'CONCEPT_MAP_DIAGRAM'
      ];
      if (
        !payload ||
        typeof payload.scriptId !== 'string' ||
        typeof payload.script !== 'string' ||
        !/^[a-f0-9]{64}$/.test(payload.scriptSha256) ||
        typeof payload.preview?.base64 !== 'string' ||
        payload.preview.mimeType !== 'video/mp4' ||
        payload.preview.audioIncluded !== true ||
        !/^[a-f0-9]{64}$/.test(payload.preview.sha256) ||
        !Number.isInteger(payload.preview.sizeBytes) ||
        payload.preview.sizeBytes <= 0 ||
        payload.preview.sizeBytes > 4 * 1024 * 1024 ||
        payload.productionManifest?.video?.artifactSha256 !== payload.preview.sha256 ||
        payload.productionManifest?.video?.artifactBytes !== payload.preview.sizeBytes ||
        payload.productionManifest?.workflow !== 'LOCAL_NARRATED_ORIGINAL_SCRIPT_PREVIEW' ||
        (sceneContractVersion !== undefined && sceneContractVersion !== 2) ||
        (legacyVisualEvidenceMissing
          ? payload.productionManifest?.sceneRendererVersion !== undefined
          : payload.productionManifest?.sceneVisualEvidenceVersion !== 1 ||
            payload.productionManifest?.sceneRendererVersion !== 'pillow-scene-composer-v2') ||
        !sceneVisualEvidence ||
        !['VERIFIED', 'MISSING', 'INTEGRITY_FAILURE'].includes(sceneVisualEvidence.state) ||
        sceneVisualEvidence.statusScope !== 'CURRENT_READBACK' ||
        typeof sceneVisualEvidence.lastCheckedAt !== 'string' ||
        (sceneVisualEvidence.sceneVisualManifestSha256 !== null &&
          !/^[a-f0-9]{64}$/.test(sceneVisualEvidence.sceneVisualManifestSha256)) ||
        (sceneVisualEvidence.state === 'VERIFIED' &&
          sceneVisualEvidence.sceneVisualManifestSha256 !==
            payload.productionManifest?.sceneVisualManifestSha256) ||
        payload.sceneVisualEvidence?.state !== sceneVisualEvidence.state ||
        !Array.isArray(sceneVisualEvidence.scenes) ||
        sceneVisualEvidence.scenes.length !== payload.productionManifest?.scenes?.length ||
        sceneVisualEvidence.scenes.some((scene, index) =>
          scene.order !== index + 1 ||
          !['VERIFIED', 'MISSING', 'INTEGRITY_FAILURE'].includes(scene.state) ||
          scene.sceneId !== payload.productionManifest.scenes[index]?.sceneId ||
          scene.excerptSha256 !== payload.productionManifest.scenes[index]?.excerptSha256
        ) ||
        (sceneVisualEvidence.state === 'VERIFIED' &&
          sceneVisualEvidence.scenes.some((scene) => scene.state !== 'VERIFIED')) ||
        (sceneVisualEvidence.state !== 'VERIFIED' &&
          sceneVisualEvidence.scenes.some(
            (scene) => scene.state !== sceneVisualEvidence.state
          )) ||
        !validateAudioEvidence(payload.audioEvidence) ||
        payload.productionManifest?.audioEvidence?.status !== payload.audioEvidence.status ||
        payload.productionManifest?.audioEvidence?.statusScope !== 'CURRENT_READBACK' ||
        !(
          payload.productionManifest?.audioEvidence?.renderTimeStatus === null ||
          ['VERIFIED', 'UNVERIFIABLE', 'MISSING', 'INTEGRITY_FAILURE'].includes(
            payload.productionManifest?.audioEvidence?.renderTimeStatus
          )
        ) ||
        (payload.productionManifest?.qualityAssurance?.state !==
          (payload.audioEvidence.status !== 'VERIFIED'
            ? `AUDIO_EVIDENCE_${payload.audioEvidence.status}`
            : sceneVisualEvidence.state !== 'VERIFIED'
              ? `SCENE_VISUAL_EVIDENCE_${sceneVisualEvidence.state}`
              : 'TECHNICAL_CHECKS_PASSED')) ||
        !Array.isArray(payload.productionManifest?.scenes) ||
        payload.productionManifest.scenes.length < 1 ||
        payload.productionManifest.scenes.length > 4 ||
        payload.productionManifest.narration?.state !== 'GENERATED_LOCALLY' ||
        payload.productionManifest.narration?.voiceQuality !== 'NOT_HUMAN_REVIEWED' ||
        payload.productionManifest.narration?.intelligibility !== 'NOT_ASSESSED' ||
        payload.productionManifest.narration?.naturalness !== 'NOT_ASSESSED' ||
        !Number.isFinite(payload.productionManifest.narration?.rmsDbfs) ||
        !Number.isFinite(payload.productionManifest.narration?.peakDbfs) ||
        !Number.isInteger(payload.productionManifest.narration?.sampleCount) ||
        payload.productionManifest.narration.sampleCount <= 0 ||
        !Number.isInteger(payload.productionManifest.narration?.clippedSamples) ||
        payload.productionManifest.narration.clippedSamples < 0 ||
        payload.productionManifest.narration.clippedSamples >
          payload.productionManifest.narration.sampleCount ||
        !Number.isFinite(payload.productionManifest.narration?.clippedSampleRatio) ||
        payload.productionManifest.narration.clippedSampleRatio < 0 ||
        payload.productionManifest.narration.clippedSampleRatio > 0.001 ||
        Math.abs(
          payload.productionManifest.narration.clippedSampleRatio -
          payload.productionManifest.narration.clippedSamples /
            payload.productionManifest.narration.sampleCount
        ) > 0.0000001 ||
        !Number.isFinite(payload.productionManifest.narration?.quietWindowRatio) ||
        !Number.isFinite(payload.productionManifest.narration?.encodedAudioRmsDbfs) ||
        payload.productionManifest.narration.encodedAudioRmsDbfs < -45 ||
        !Array.isArray(payload.productionManifest.narration?.sceneBoundaryGaps) ||
        payload.productionManifest.narration.sceneBoundaryGaps.length !==
          payload.productionManifest.scenes.length - 1 ||
        payload.productionManifest.narration.sceneBoundaryGaps.some((gap) =>
          gap?.state !== 'INTENTIONAL_SILENCE_VERIFIED' ||
          gap.windowMilliseconds !== 50
        ) ||
        payload.productionManifest.narration.rmsDbfs < -45 ||
        payload.productionManifest.narration.peakDbfs > -1 ||
        payload.productionManifest.narration.quietWindowRatio < 0 ||
        payload.productionManifest.narration.quietWindowRatio > 1 ||
        payload.productionManifest.video?.width !== 720 ||
        payload.productionManifest.video?.height !== 1280 ||
        payload.productionManifest.video?.videoCodec !== 'h264' ||
        payload.productionManifest.video?.audioCodec !== 'aac' ||
        !Number.isFinite(payload.productionManifest.video?.durationSeconds) ||
        payload.productionManifest.scenes.some((scene, index) =>
          !scene ||
          scene.order !== index + 1 ||
          typeof scene.excerpt !== 'string' ||
          !Number.isInteger(scene.scriptOffsets?.start) ||
          !Number.isInteger(scene.scriptOffsets?.endExclusive) ||
          scene.scriptOffsets.start < 0 ||
          scene.scriptOffsets.endExclusive <= scene.scriptOffsets.start ||
          payload.script.slice(
            scene.scriptOffsets.start,
            scene.scriptOffsets.endExclusive
          ) !== scene.excerpt ||
          !/^[a-f0-9]{64}$/.test(scene.excerptSha256) ||
          scene.caption?.text !== scene.excerpt ||
          scene.caption?.scriptOffsets?.start !== scene.scriptOffsets.start ||
          scene.caption?.scriptOffsets?.endExclusive !== scene.scriptOffsets.endExclusive ||
          (sceneContractVersion === 2 &&
            (
              scene.sceneContract?.sceneId !== scene.sceneId ||
              scene.sceneContract?.order !== scene.order ||
              scene.sceneContract?.excerpt !== scene.excerpt ||
              scene.sceneContract?.excerptSha256 !== scene.excerptSha256 ||
              scene.sceneContract?.scriptOffsets?.start !== scene.scriptOffsets.start ||
              scene.sceneContract?.scriptOffsets?.endExclusive !==
                scene.scriptOffsets.endExclusive ||
              scene.sceneContract?.keyMessage !== scene.excerpt ||
              scene.sceneContract?.keyMessageSource !== 'SCRIPT_EXCERPT' ||
              ![
                'EXPLANATION',
                'PROCESS',
                'COMPARISON',
                'CAUSE_EFFECT',
                'NUMERICAL_FACT',
                'TIMELINE',
                'QUESTION_ANSWER',
                'SUMMARY'
              ].includes(scene.sceneContract?.narrativeRole) ||
              !supportedVisualStrategies.includes(
                scene.sceneContract?.visualStrategy
              ) ||
              scene.sceneContract?.rendererSupport !== 'SUPPORTED' ||
              scene.sceneContract?.visualInstructions?.layout !==
                scene.sceneContract?.visualStrategy ||
              !Array.isArray(scene.sceneContract?.visualInstructions?.labels) ||
              scene.sceneContract?.narrationRef !==
                `${scene.sceneId}:narration` ||
              scene.sceneContract?.captionRef !== `${scene.sceneId}:caption` ||
              scene.narrationRef !== scene.sceneContract?.narrationRef ||
              scene.captionRef !== scene.sceneContract?.captionRef ||
              scene.sceneContract?.timing !== null ||
              scene.sceneContract?.assetState !== 'NOT_GENERATED' ||
              scene.sceneContract?.technicalValidation !== 'NOT_RUN' ||
              scene.sceneContract?.plannerVersion !==
                payload.productionManifest.scenePlannerVersion ||
              (sceneVisualEvidence.scenes[index].state === 'VERIFIED'
                ? ![
                    'COMPARISON_LAYOUT',
                    'TIMELINE_LAYOUT',
                    'QUESTION_ANSWER_LAYOUT',
                    'CONCLUSION_LAYOUT',
                    'CAUSE_EFFECT_DIAGRAM',
                    'NUMERICAL_CALLOUT',
                    'PROCESS_DIAGRAM',
                    'EXPLANATORY_CONCEPT_MAP'
                  ].includes(scene.renderedComposition) ||
                  scene.assetState !== 'GENERATED' ||
                  scene.technicalValidation !== 'TECHNICAL_CHECKS_PASSED' ||
                  !validateSceneVisualAsset(
                    scene.visualAsset,
                    scene,
                    payload,
                    payload.productionManifest
                  )
                : scene.visualAsset !== null ||
                  scene.assetState !== sceneVisualEvidence.scenes[index].state)
            ))
        ) ||
        (sceneContractVersion === 2 &&
          (
            payload.productionManifest.scenePlannerVersion !==
              'deterministic-heuristic-v2' ||
            !/^[a-f0-9]{64}$/.test(
              payload.productionManifest.sceneContractSha256
            )
          )) ||
        payload.review?.humanReviewState !== 'PENDING' ||
        payload.review?.factualReviewState !== 'REQUIRED' ||
        payload.review?.rightsState !== 'NOT_EVALUATED' ||
        payload.review?.approvalState !== 'NOT_GRANTED' ||
        payload.review?.publicationState !== 'NOT_PUBLISHED' ||
        payload.review?.editorial?.state !== 'PENDING' ||
        payload.review.editorial.artifactSha256 !== payload.preview.sha256
      ) {
        throw new Error('CRE script preview response is invalid');
      }
      return payload;
    });
  };

  WebSocketUltronProvider.prototype.requestCreScriptPreviewReview = function (review) {
    if (
      !review ||
      typeof review.scriptId !== 'string' ||
      !/^[a-f0-9]{64}$/.test(review.artifactSha256) ||
      typeof review.editorialVerdict !== 'string' ||
      typeof review.narrationAssessment !== 'string' ||
      typeof review.pacingAssessment !== 'string' ||
      typeof review.captionAssessment !== 'string' ||
      typeof review.sceneAssessment !== 'string' ||
      typeof review.factualReviewAttested !== 'boolean' ||
      typeof review.notes !== 'string'
    ) {
      return Promise.reject(new TypeError('CRE script preview review input is invalid'));
    }
    return this._request(
      'CRE_SCRIPT_PREVIEW_REVIEW_REQUEST',
      review,
      30000
    ).then(function (response) {
      var payload = response.payload;
      if (
        !payload ||
        payload.scriptId !== review.scriptId ||
        payload.previewSha256 !== review.artifactSha256 ||
        payload.review?.humanReviewState !== 'RECORDED' ||
        payload.review?.editorial?.state !== 'RECORDED' ||
        payload.review.editorial.artifactSha256 !== review.artifactSha256 ||
        payload.review?.approvalState !== 'NOT_GRANTED' ||
        payload.review?.publicationState !== 'NOT_PUBLISHED' ||
        payload.review?.rightsState !== 'NOT_EVALUATED'
      ) {
        throw new Error('CRE script preview review response is invalid');
      }
      return payload;
    });
  };

  root.ULTRON.providers = root.ULTRON.providers || {};
  root.ULTRON.providers.WebSocketUltronProvider = WebSocketUltronProvider;
})(window);