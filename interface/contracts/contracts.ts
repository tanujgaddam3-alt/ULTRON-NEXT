/**
 * ULTRON Integration Contracts
 * Formal TypeScript Interface Specifications for Backend/Substrate Integration
 *
 * Every subsystem and observable telemetry stream in the ULTRON interface
 * conforms strictly to these integration contracts.
 *
 * NOTE: This file is the authoritative TYPE reference. The browser runtime
 * schema validators live in contracts/contracts.js (window.ULTRON.contracts).
 */

// ============================================================================
// 0. PROVENANCE CONTRACT
// ============================================================================

/**
 * Telemetry provenance: every value shown by the UI is attributed to exactly
 * one of these states. The interface NEVER labels simulated/reference data as
 * LIVE, and never invents plausible numbers to make a view look complete.
 */
export type TelemetryProvenance =
  | 'LIVE'             // Backend WebSocket connected & validated
  | 'SIMULATED'        // Mock provider reference substrate (dev only)
  | 'OFFLINE'          // Provider exists but transport is disconnected
  | 'NOT_INSTRUMENTED' // No provider / no telemetry for this domain yet

export interface CreJobSummaryContract {
  id: string;
  type: 'processing' | 'rendering' | 'publishing';
  state: string;
  createdAt: string;
  updatedAt: string;
}

export interface CreLifecycleSummaryContract {
  id: string;
  state: string;
  createdAt: string;
  updatedAt: string;
}

export interface CreEventSummaryContract {
  id: string;
  type: 'processing' | 'rendering' | 'publishing' | 'lifecycle';
  state: string;
  createdAt: string;
}

export interface CreStatusContract {
  status: 'AVAILABLE';
  generatedAt: string;
  counts: {
    total: number;
    processing: Record<string, number>;
    rendering: Record<string, number>;
    publishing: Record<string, number>;
    pendingApprovals: number;
  };
  jobs: CreJobSummaryContract[];
  lifecycle: CreLifecycleSummaryContract[];
  events: CreEventSummaryContract[];
  services: {
    store: string;
    rights: string;
    processing: string;
    rendering: string;
    publishing: string;
  };
  publishing: {
    mode: 'MOCK_ONLY';
    serviceConfigured: boolean;
    killSwitchEnabled: boolean;
  };
  analytics: {
    available: false;
    reason: 'NOT_INSTRUMENTED';
  };
}

// ============================================================================
// 1. COGNITIVE STATE CONTRACT (Section 5)
// ============================================================================

export type CognitiveBehaviorMode =
  | 'IDLE'
  | 'THINKING'
  | 'ACTIVE'
  | 'MULTI_AGENT'
  | 'OMEGA_SYNC'
  | 'TRADING_ANALYSIS'
  | 'EXECUTION'
  | 'ALERT'
  | 'DEGRADED'
  | 'OFFLINE';

export interface SatelliteNodeContract {
  id: 'memory' | 'omega' | 'reasoning';
  name: string;
  status: string;
  tag: string;
  coherence: number;
}

export interface AgentNodeContract {
  name: string;
  role: string;
  status: 'ACTIVE' | 'STANDBY' | 'QUORUM';
  channelLatencyMs: number;
}

export interface CognitiveStateContract {
  timestamp: string;
  behavior: CognitiveBehaviorMode;
  headline: string;
  description: string;
  // Motion-grounded telemetry:
  tensorInferenceRateHz: number;
  synapticThroughputTops: number;
  packetRoutingRateBps: number;
  attentionEntropyDelta: number;
  satellites: SatelliteNodeContract[];
  agents: AgentNodeContract[];
}

// ============================================================================
// 2. OVERVIEW VITAL MATRIX CONTRACT (Section 7)
// ============================================================================

export interface VitalMetricContract {
  key: string;
  question: string;
  primaryValue: string;
  secondaryValue: string;
  statusLevel: 'NOMINAL' | 'WARNING' | 'ALERT' | 'GOLD' | 'PURPLE';
  subsystemId: string;
}

export interface VitalMatrixContract {
  timestamp: string;
  health: VitalMetricContract;
  activity: VitalMetricContract;
  focus: VitalMetricContract;
  agents: VitalMetricContract;
  omega: VitalMetricContract;
  currentTask: VitalMetricContract;
  anomalies: VitalMetricContract;
  capability: VitalMetricContract;
  performance: VitalMetricContract;
  attention: VitalMetricContract;
}

// ============================================================================
// 3. COGNITIVE PIPELINE CONTRACT (Section 8)
// Note: Private internal reasoning and hidden CoT are excluded by design.
// ============================================================================

export interface PipelineStageContract {
  id:
    | 'perception'
    | 'understanding'
    | 'context'
    | 'reasoning'
    | 'decision'
    | 'planning'
    | 'execution'
    | 'reflection'
    | 'learning'
    | 'self_improvement';
  order: string;
  title: string;
  assignedAgent: string;
  stageStatus: string;
  reasoningState: string;        // High-level observable reasoning state
  decisionRationale: string;     // Explicit justification for route selection
  inputs: string;                // Grounded incoming data/parameters
  outputs: string;               // Concrete results/directives
  confidence: string;            // Quantified certainty score
  evidence: string;              // Attested proof / historical correlation
  executionState: string;        // Deterministic hardware/process status
}

export interface CognitivePipelineContract {
  pipelineVersion: string;
  activeStageIndex: number;
  stages: PipelineStageContract[];
}

// ============================================================================
// 4. OMEGA TWIN INTELLIGENCE CONTRACT (Section 9)
// ============================================================================

export interface OmegaCommunicationTraceContract {
  stepNumber: number;
  direction: 'ULTRON_TO_OMEGA' | 'OMEGA_INGESTION' | 'SYNCHRONOUS_CHECK' | 'OMEGA_TO_ULTRON';
  title: string;
  description: string;
  payloadSummary: string;
  latencyMs: number;
}

export interface OmegaSubsystemContract {
  entityState: 'PHASE_LOCKED' | 'SYNCHRONIZING' | 'ISOLATED' | 'DEGRADED';
  bridgeLatencyMs: number;
  channelCoherencePct: number;
  busFrequencyTHz: number;
  model: {
    architecture: string;
    parameterCount: string;
    contextTokens: number;
    specialization: string;
  };
  resources: {
    allocatedCores: number;
    vramAllocatedGb: number;
    vramTotalGb: number;
    ringBufferMemoryGb: number;
  };
  activeAgents: string[];
  communicationTrace: OmegaCommunicationTraceContract[];
  safetyLimitation: string;
}

// ============================================================================
// 5. TRADING INTELLIGENCE & EFFICIENCY LOSS CONTRACT (Sections 10, 11, 12)
// ============================================================================

export interface PortfolioPositionContract {
  asset: string;
  positionSize: string;
  notionalUsd: number;
  hedgeOffsetDescription: string;
  netDeltaExposureUsd: number;
  netDeltaPct: number;
}

export interface TradingStrategyContract {
  strategyId: string;
  name: string;
  allocationPct: number;
  allocationUsd: number;
  realizedPnlUsd: number;
  contributionPct: number;
  sharpeRatio: number;
  winRatePct: number;
  status: 'OPTIMAL' | 'ACTIVE' | 'THROTTLED';
}

export interface EfficiencyLossContract {
  potentialReturnUsd: number;
  realizedReturnUsd: number;
  totalLossUsd: number;
  leakagePct: number;
  formula: string;
  breakdown: {
    opportunityLossUsd: number;
    opportunityLossReason: string;
    executionLatencyLossUsd: number;
    executionLatencyLossReason: string;
    transactionCostLossUsd: number;
    transactionCostLossReason: string;
    slippageLossUsd: number;
    slippageLossReason: string;
    signalQualityLossUsd: number;
    signalQualityLossReason: string;
  };
}

export interface BenchmarkComparisonContract {
  benchmarks: Array<{
    name: string;
    returnPct: number;
    maxDrawdownPct: number;
    sharpeRatio: number;
    annualizedVolPct: number;
  }>;
  driversOfDifference: Array<{
    driverName: string;
    impactPct: number;
    explanation: string;
  }>;
  periodOverPeriod: {
    currentPeriodPnl: number;
    previousPeriodPnl: number;
    growthPct: number;
    drawdownReductionPct: number;
    latencyGainNs: number;
    sharpeTrendPct: number;
  };
}

export interface TradingIntelligenceContract {
  performance: {
    netRealizedPnlUsd: number;
    realizedPnlUsd: number;
    unrealizedPnlUsd: number;
    returnMtdPct: number;
    winRatePct: number;
    totalWins: number;
    totalLosses: number;
    sharpeRatio: number;
    sortinoRatio: number;
    profitFactor: number;
    maxDrawdownPct: number;
    annualizedVolPct: number;
  };
  portfolio: {
    totalValueUsd: number;
    netDeltaExposureUsd: number;
    positions: PortfolioPositionContract[];
  };
  executionQuality: {
    meanFillLatencyNs: number;
    meanAdverseSlippagePct: number;
    topOfBookFillPct: number;
    venueFeeSavingsUsd: number;
  };
  marketIntelligence: {
    detectedRegime: string;
    annualizedVolatilityPct: number;
    sentimentScore: number;
    sentimentLabel: string;
    liquidityThicknessUsd: number;
    activeSignalsPerSec: number;
  };
  strategies: TradingStrategyContract[];
  efficiencyLoss: EfficiencyLossContract;
  benchmarkComparison: BenchmarkComparisonContract;
  /**
   * Optional decision-intelligence trail (signal -> analysis -> decision ->
   * outcome). Absent until the backend emits real decisions; the UI renders
   * NOT INSTRUMENTED when missing — never fabricated.
   */
  decisionLog?: Array<{
    signal: string;
    analysis: string;
    decision: string;
    confidence: number;
    outcome: string;
    expectedVsActual: string;
  }>;
  financial?: FinancialTelemetryContract;
}

export interface FinancialTelemetryContract {
  initialization: {
    state: 'READY' | 'UNAVAILABLE';
    controllerConfigured: boolean;
    brokerConfigured: boolean;
    updatedAt: string;
  };
  marketData: {
    state: 'VALID' | 'MARKET_CLOSED' | 'UNAVAILABLE';
    provider: string;
    source: string;
    symbol: string;
    latestPrice: number | null;
    latestValidatedAt: string | null;
    freshnessSeconds: number | null;
    error: { category: string; message: string } | null;
  };
  execution: {
    mode: string;
    source: string;
    orderSubmissionFromDashboard: false;
    liveEnabled: false;
  };
  portfolio: {
    source: string;
    cashUsd: number | null;
    equityUsd: number | null;
    positions: Array<{
      symbol: string;
      quantity: number;
      averagePriceUsd: number;
      notionalUsd: number;
    }>;
    updatedAt: string;
  };
  risk: { state: string; engines: string[]; rejections: { state: string; count: number | null } };
  executions: { source: string; records: Array<Record<string, unknown>>; orderSubmissionFromDashboard: false };
  diagnostics: Array<{ category: string; message: string }>;
}

// ============================================================================
// 6. SYSTEM OBSERVATORY CONTRACT: "HOW GOOD IS ULTRON?" (Section 13)
// ============================================================================

export interface EvaluatedDimensionContract {
  dimensionId: string;
  name: string;
  scoreDisplay: string;
  trendDisplay: string;
  detail: string;
  rawScore: number;
  /** Evidence classification: LIVE | DERIVED | SIMULATED | NOT_INSTRUMENTED */
  provenance?: string;
}

export interface SwotAnalysisContract {
  strengths: string[];
  weaknesses: string[];
  recentImprovements: string[];
  bottlenecksAndActions: string[];
}

export interface SystemObservatoryContract {
  methodology: string;
  dimensions: EvaluatedDimensionContract[];
  swot: SwotAnalysisContract;
  // Optional: self-improvement lifecycle streamed by the runtime.
  improvementLoop?: {
    stageId: string;
    stage?: string;
    state: string;
    observation?: string;
    summary?: string;
    evidence?: string;
    beforeAfter?: { before: string; after: string };
  }[];
  // Optional: comparative/historical intelligence (current vs previous, expected vs actual,
  // ultron vs benchmark, before vs after). Differences must carry an explanation.
  comparisons?: {
    dimension: string;
    beforeLabel?: string;
    afterLabel?: string;
    before: string | number;
    after: string | number;
    delta?: string | number;
    provenance?: string;
    explanation?: string;
  }[];
}

// ============================================================================
// 7. CAPABILITY MATRIX & DEVICE INTELLIGENCE CONTRACT (Sections 14, 15)
// ============================================================================

export type DeviceHostRole = 'production' | 'local' | 'remote';

export interface SystemCapabilityContract {
  capabilityId: string;
  name: string;
  status: 'ACTIVE' | 'CONNECTED' | 'STANDBY' | 'DEGRADED';
  availabilityPct: number;
  maturityLevel: 'Production' | 'Hardened' | 'Experimental';
  performanceMetric: string;
  dependencies: string[];
  limitations: string;
  // Optional: capability explorer grouping + observability evidence.
  category?: string;
  recentActivity?: string;
  evidence?: string;
}

export interface DeviceHardwareContract {
  cpu: {
    physicalCores: number;
    logicalThreads: number;
    frequencyGhz: number;
    loadPct: number;
    tempCelsius: number;
    governor: string;
  };
  memory: {
    totalGb: number;
    usedGb: number;
    availableGb: number;
    pressurePct: number;
    swapUsedKb: number;
  };
  gpu: {
    vramTotalGb: number;
    vramAllocatedGb: number;
    computeUtilityPct: number;
    tempCelsius: number;
    bandwidthTbPerSec: number;
  };
  network: {
    pingToVenuesMs: number;
    bandwidthGbps: number;
    droppedPackets: number;
    interfaceType: string;
    ptpSyncLocked: boolean;
  };
  storage?: {
    totalGb: number;
    usedGb: number;
    driveType: string;
    healthPct: number;
  };
}

export interface DeviceAssessmentContract {
  computeGrade: 'LOW' | 'MEDIUM' | 'HIGH' | 'EXCELLENT';
  memoryGrade: 'LOW' | 'MEDIUM' | 'HIGH' | 'EXCELLENT';
  storageGrade: 'LOW' | 'MEDIUM' | 'HIGH' | 'EXCELLENT';
  networkGrade: 'LOW' | 'MEDIUM' | 'HIGH' | 'EXCELLENT';
  primaryBottleneck: string;
  actionableRecommendation: string;
}

export interface DeviceAndCapabilitiesContract {
  /**
   * Identifies which machine supplied this telemetry so the UI never
   * silently mixes LOCAL DEVELOPMENT HOST data with PRODUCTION HOST data.
   */
  host?: {
    role: DeviceHostRole;
    hostName: string;
    simulated?: boolean;
  };
  assessment: DeviceAssessmentContract;
  hardware: DeviceHardwareContract;
  capabilities: SystemCapabilityContract[];
}

// ============================================================================
// 8. PRIMARY COMMAND CENTER CONTRACT (Section 16)
// Consequential Operations Lifecycle:
// SUGGESTED -> APPROVED -> EXECUTING -> UNVERIFIED -> VERIFIED
// An interrupted or ambiguous execution is UNCERTAIN; a rejected execution
// is FAILED. COMPLETED is reserved for outcomes ULTRON can establish directly.
// ============================================================================

export type OperationLifecycleState =
  | 'SUGGESTED'
  | 'PLANNED'
  | 'APPROVED'
  | 'EXECUTING'
  | 'COMPLETED'
  | 'VERIFIED'
  | 'UNVERIFIED'
  | 'UNCERTAIN'
  | 'FAILED'
  | 'CANCELLED';

export interface ConsequentialOperationContract {
  operationId: string;
  requestFingerprint?: string;
  title: string;
  lifecycleState: OperationLifecycleState;
  intent: string;
  plan: string;
  tools: string[];
  expectedEffect: string;
  approvalRequirement: string;
  executionResult?: {
    success: boolean;
    status?: string;
    [key: string]: unknown;
  };
  verificationAudit?: {
    verified: boolean;
    outcome: 'CONFIRMED_SUCCESS' | 'CONFIRMED_FAILURE' | 'AMBIGUOUS';
    operationId: string;
    requestFingerprint: string;
    verifier: string;
    provenance: string;
    observedAt: string;
    expectedEffect?: string;
    observedState?: Record<string, unknown> | null;
    [key: string]: unknown;
  };
  reconciliationHistory?: Array<Record<string, unknown>>;
  timestamp: string;
}

export interface CommandDispatchContract {
  commandText: string;
  actionVerb:
    | 'ask' | 'analyze' | 'investigate' | 'search'
    | 'create' | 'modify' | 'execute' | 'control'
    | 'monitor' | 'delegate' | 'plan' | 'debug'
    | 'research' | 'automate' | 'trade';
  parameters: Record<string, unknown>;
  origin: 'UI_TEXT' | 'UI_VOICE' | 'API' | 'AGENT_DELEGATION';
}

export interface CommandCenterContract {
  supportedActions: string[];
  activeOperations: ConsequentialOperationContract[];
}

// ============================================================================
// 9. RUNTIME EVENT STREAM CONTRACT (Section 18 - Audit Trail Feed)
// ============================================================================

export interface RuntimeEventContract {
  eventId: string;
  timestamp: string;
  domain: 'TRADING' | 'OMEGA' | 'AGENT' | 'KERNEL' | 'CORE' | 'COMMAND' | 'SYSTEM';
  type: string;
  summary: string;
  evidence?: string;         // Attested proof when available
  provenance: TelemetryProvenance;
}

// ============================================================================
// 10. UNIFIED ULTRON ROOT INTEGRATION CONTRACT
// ============================================================================

export interface UltronRootStateContract {
  systemVersion: string;
  telemetryTimestamp: string;
  provenance?: TelemetryProvenance;
  vitalMatrix: VitalMatrixContract;
  cognitiveState: CognitiveStateContract;
  pipeline: CognitivePipelineContract;
  omega: OmegaSubsystemContract;
  trading: TradingIntelligenceContract;
  observatory: SystemObservatoryContract;
  deviceAndCapabilities: DeviceAndCapabilitiesContract;
  commandCenter: CommandCenterContract;
}