# CRE Phase 4: Visual Intelligence Implementation Roadmap

## Goal and non-goals

Add genuine, locally executed visual observations as an optional,
evidence-backed input to the existing CRE creative planner.

Preserve the current source/project lifecycle, ownership and rights checks,
structured plan validation and revision history, FFmpeg renderer, durable
private-render operations, artifact storage and verification, authenticated
gateway contracts, and browser preview/recovery. Visual intelligence must not
be a replacement planner/renderer or a reason to reinterpret scene cuts as
semantic understanding.

This roadmap began as a proposal. The current bounded implementation adds
provider-neutral visual contracts, a loopback-only Ollama adapter, verified
timestamped frame sampling, a separate cross-modal highlight ranker, and an
explicit owner-scoped durable analysis lifecycle. The lifecycle is not wired
into the CRE runtime, planner, gateway, or UI.

## Current implementation status

| Stage | Status | Evidence and limits |
|---|---|---|
| Lightweight model feasibility | COMPLETE FOR ONE SYNTHETIC STRUCTURED IMAGE REQUEST | `qwen3-vl:2b-instruct-q4_K_M` completed the real adapter's strict JSON path on a synthetic 320×240 image, returning two objects. The measured device was CPU, not GPU; temporal reasoning was not tested. |
| Model-neutral visual contract | IMPLEMENTED | `content_visual_intelligence.py` defines stable observations, uncertainty, confidence semantics, capabilities, and resource reports. `visual_intelligence.py` defines `VisualUnderstandingProvider` and a loopback-only Ollama adapter. |
| Timestamped video sampling | IMPLEMENTED AND TESTED | `VideoFrameSampler` verifies the source digest, probes duration, extracts bounded PNG frames, and binds timestamps and frame hashes to the source. `VisualIntelligenceService` analyzes frames in timestamp order and explicitly reports that cross-frame reasoning was not performed. |
| Independent highlight analysis | IMPLEMENTED AND TESTED | `content_highlights.py` and `highlights.py` combine typed visual, scene-transition, transcript, audio-event, narrative-context, and event-importance evidence into deterministically ranked candidates. Scores are heuristic, not probabilities; supplied evidence must match the source asset and retain its digest. |
| Durable owner-scoped analysis lifecycle | IMPLEMENTED AND ISOLATED; INTEGRATION READINESS PARTIAL | `ContentStore` has an explicit, idempotent visual-analysis schema extension for schema-v10 databases. Separate request-idempotency and content/config cache fingerprints protect owner/project/asset scoped records; completed exact-match results may be reused with a new operation ID and an auditable source-run reference. Worker startup takes a process-wide SQLite-adjacent file lease and reconciles interrupted analyses before any claim. Retention is dry-run only; no record or evidence deletion is enabled. The migration has not been run on the application database. |
| Runtime, planner, gateway, and UI integration | NOT IMPLEMENTED | The lifecycle is not wired into runtime startup, a worker loop, planner, gateway dispatch, or frontend. No application database was opened or migrated. |

## Proposed component boundaries

| Component | Responsibility | Must not do |
|---|---|---|
| `VideoFrameSampler` (implemented application boundary) | Accept in-memory bytes supplied by an authorized caller; verify their SHA-256; probe a bounded duration; extract requested timestamped frames with FFmpeg; emit dimensions and hashes. | Read arbitrary paths, perform authorization itself, bypass source provenance, process unbounded frames, or write render artifacts. |
| `VisualUnderstandingProvider` protocol (implemented) | Describe a sampled image with a provider-neutral typed result and report capabilities and resource use. | Make gateway/auth decisions, invoke FFmpeg, or claim video semantics from isolated frames. |
| `OllamaVisionProvider` (implemented adapter) | Use the installed `qwen3-vl:2b-instruct-q4_K_M` model through a loopback-only Ollama endpoint, with bounded JSON output and a 2-GiB `MemAvailable` floor. | Download or switch models, use a remote endpoint, or report confidence as calibrated without evaluation. |
| Visual domain contracts (implemented) | Validate source/frame hashes, timestamps, model identity, uncertainty, confidence semantics, capability claims, and inference resource reports. Reject invalid model output explicitly. | Guess malformed timestamps or silently accept mismatched source evidence. |
| `VisualIntelligenceService` (implemented, not runtime-wired) | Sample and analyze frames sequentially, preserve source-time order, and mark cross-frame reasoning as not performed. | Load project media, make authorization decisions, persist analysis, or claim temporal model reasoning. |
| `analyze_highlights` (implemented, not planner-wired) | Rank bounded candidate windows from typed visual, scene, transcript, audio, narrative, and importance evidence; return evidence IDs and reasons. | Treat heuristic scores as probabilities, invent missing evidence, or alter existing planner behavior. |
| Versioned visual analysis persistence | Store durable state/result keyed by source content digest, model ID/digest, prompt/schema version, sampling policy, and output configuration. Record timestamps and retries. | Store frame paths as globally accessible media, cross owners, or reuse results with incompatible model/prompt versions. |
| Planner evidence input | Pass validated observation references and source intervals into the existing autoplan decision path. Select only plan-valid segments and bind selected evidence IDs/version to the resulting plan revision. | Replace existing plan validation or expose model text directly as FFmpeg instructions. |
| Existing FFmpeg renderer and render lifecycle | Continue to render an already validated/pinned structured plan and manage durable operation/artifact recovery. | Perform semantic analysis or own visual-analysis retries. |

### Existing integration points

- Runtime construction and dependency wiring: [`create_content_runtime`](../ultron/application/content/status.py#L184)
  creates the content store, rights service, storage, FFmpeg renderer, and
  `ContentClipRenderingService`.
- Gateway injection: [`serve_interface`](../ultron/application/runtime/interface_gateway.py#L7218)
  assigns the runtime `clip_rendering` service to the gateway container.
- Analysis/planning seam: [`ContentClipRenderingService.autoplan_edit_project`](../ultron/application/content/rendering.py#L531)
  reads verified owner media, probes it, detects scene boundaries, selects
  intervals, and saves the next structured plan.
- Plan source/segment contracts:
  [`ContentEditPlanSource`](../ultron/domain/content.py#L469),
  [`ContentEditPlanSegment`](../ultron/domain/content.py#L509), and
  [`ContentCreativeEditPlanV2`](../ultron/domain/content.py#L807).
- Plan revision persistence:
  [`ContentStore.save_edit_plan_in_transaction`](../ultron/application/content/store.py#L3725)
  and the immutable `content_edit_plan_revisions` table.
- Durable render operation:
  [`ContentPrivateRenderOperation`](../ultron/domain/content.py#L154),
  [`ContentStore` schema-v10 operation tables](../ultron/application/content/store.py#L460),
  and the existing status/artifact methods in
  [`rendering.py`](../ultron/application/content/rendering.py#L1116).

Do not add inference calls to the gateway request handler itself. Keep gateway
dispatch/auth narrow and delegate through the configured content application
service as the existing CRE handlers do.

## Data/evidence contract proposal

Every accepted visual finding should be represented with explicit provenance:

```text
analysis_id
owner_id
source_asset_id
source_asset_sha256
frame_refs[] = {frame_id, timestamp_seconds, frame_sha256, width, height}
start_seconds / end_seconds
observation_text
event_category
model_id / model_digest / runtime_version
prompt_version / schema_version / sampler_version
confidence_score? + confidence_semantics
supporting_frame_refs[]
created_at
analysis_state / failure_code
```

Rules:

- Frame timestamp is the source-media timeline timestamp; make that explicit
  through extraction and tests. Do not mix it with output timeline time.
- An image observation has a point timestamp unless two or more referenced
  samples justify a coarse interval. A sparse sequence does not justify
  frame-accurate event onset/offset or entity tracking.
- Confidence may be omitted. If exposed, label it as model-specific output and
  not a probability unless empirically calibrated.
- Keep original model observation, deterministic validation, and planner
  interpretation as distinct fields.
- Bind a selected plan interval to the exact evidence IDs and versions that
  influenced it. Keep the existing source asset hash and plan digest checks.
- Cache key includes source hash, model digest/tag, model runtime, prompt,
  sampler, preprocessing, and schema versions. A model/policy change yields a
  different key and visible stale status.
- Delete staged frame files in `finally` paths. If retained for explanation,
  retain under the source owner and existing access controls with bounded
  storage/retention.
- Analysis timeout/failure must be explicit. The planner may use its current
  heuristic fallback only under a declared policy and must mark that analysis
  was unavailable; never make a failure look like “no interesting events”.

## Sequenced implementation plan

### Milestone 0 — Feasibility gate (synthetic-only)

**Status: one real structured synthetic-image request completed.** The local
Ollama adapter parsed its strict schema and reported a CPU backend. Earlier
structured attempts were truncated; model quality and repeatability remain
unmeasured. No GPU inference was established.

**Purpose:** establish whether the lightweight local VLM can perform a basic
image-understanding task within resource limits.

The selected model was downloaded with authorization and tested on a disposable
synthetic image. A concise caption completed on CPU while the 2-GiB available
memory floor was maintained. A later one-frame structured adapter request
completed, described the synthetic graphic, and returned two objects. See
[`CRE_PHASE4_1_QWEN_VISION_EXPERIMENT.md`](./CRE_PHASE4_1_QWEN_VISION_EXPERIMENT.md)
for earlier inference and memory evidence. The latest run used 320×240
synthetic pixels in memory, took 41.068 seconds, reported CPU (`size_vram=0`),
and maintained at least 6.31 GB `MemAvailable`. No private media or CRE
database was used.

### Milestone 1 — Frame manifest and sampler

**Status: implemented and tested with generated synthetic video.**

Implement a pure/deterministic frame-selection policy and FFmpeg extraction
wrapper. Source loading and authorization remain outside the sampler; it accepts
in-memory bytes only after the caller has authorized them. Combine a small
number of evenly spaced samples with optional cut-adjacent samples, remove
duplicates, verify the registered source digest, and cap total samples.

**Tests:** empty/corrupt/no-video sources; exact timestamps; duration boundary;
duplicate cut timestamps; repeated scenes; frame count and pixel caps; disk
cleanup on success/failure/timeout; source digest linkage; no media path escape.

### Milestone 2 — Local VLM adapter and validated findings

**Status: provider contract and adapter implemented; one real local structured
inference through the adapter completed on synthetic input.**

Implement an engine protocol and a local Ollama adapter only after Milestone 0.
Return a strict JSON observation schema, validate with application domain
types, and retain model/prompt/schema identifiers. Handle no response, malformed
JSON, content refusal, timeout, and resource exhaustion explicitly.

**Tests:** provider-neutral contracts; malformed/oversized output rejection;
unknown categories; invalid confidence; unsupported model version; local-only
behavior; no secret logging. Do not fabricate model observations to simulate
successful inference; run a real local inference only when the memory guard
and resource checks permit it.

### Milestone 3 — Persistent analysis lifecycle

**Status: isolated lifecycle implementation complete; integration readiness
partial.** An explicit schema-v10 foundation plus visual-extension v1-to-v2
migration stores queued/analyzing/completed/failed/reconciliation-required
states, separate idempotency and cache fingerprints, typed results, source-run
cache provenance, and append-only events. Owner-authorized create/status/event
operations are separate from internal worker transitions. Worker start takes
an exclusive process lease, reconciles interrupted analyses once, and only
then permits claims. Claims remain atomic; completion and failure are bound to
the analysis ID, owner, and expected state. Cached results require exact
owner/project/source/hash, model/provider/version, prompt/schema/sampler,
timestamp, and inference-limit identity. Retention has an explicit owner-scoped
dry-run policy only; deletion is not implemented because lifecycle audit events
and cache references must be retained. No application database was migrated.

Add owner-scoped analysis state/results and content-addressed cache identity.
Use bounded claims and durable status transitions. Keep analysis IDs distinct
from plan revision and render operation IDs. A reload/status request must reuse
the same analysis ID and must not silently initiate a second model run.

**Tests:** duplicate/concurrent claims once; owner isolation including
unknown-ID indistinguishability; interruption/reconciliation without automatic
rerun; deterministic cache hit; model/prompt/sampler version invalidates cache;
storage cleanup/retention; analysis error does not mutate completed render state.

### Milestone 4 — Planner consumes evidence optionally

**Status: not started.** The new highlight ranker and visual observations do
not change the current planner.

At `autoplan_edit_project`, obtain a matching analysis for the authorized
project source assets. For each chosen segment, validate time bounds against
the source duration and record supporting evidence IDs and selection rationale.
Keep existing v2 plan validation and immutable revision writes. Preserve the
existing nonsemantic fallback when no visual findings are available, explicitly
labeling it as heuristic-only.

Avoid changing output constraints or FFmpeg rendering semantics. If a
versioned plan contract must expand, define a backward-compatible plan version
or a linked evidence record; do not silently change the meaning/digest of
existing v1/v2 stored plans.

**Tests:** plan remains valid with no findings; supported finding changes a
bounded selection; finding outside source bounds rejected; correct asset/hash
linkage; revision history increments; same project/source IDs retained;
existing plans still deserialize; deterministic fallback; selected evidence
displayed with source timestamps.

### Milestone 5 — UI explainability and acceptance

**Status: not started.**

Add an owner-scoped plan evidence presentation only after the backend evidence
contract is stable. Clearly distinguish “frame described” from “event
confirmed”; show timestamps, frame references, model/version, and uncertainty.
Do not block preview/recovery on optional visual analysis. The UI must display
analysis failure distinctly from render failure.

**Acceptance:** a synthetic source with known visual events is analyzed with
timestamped evidence; observation-to-source hash/frame linkage is verified;
planner chooses a supported interval and exposes why; plan revision and
render-operation behavior remain unchanged; reload recovers same analysis,
project, and operation; video artifact still validates and plays; owner
isolation and failure paths pass; no unrelated subsystem is contacted.

## Resource, quality, and governance gates

- **Budgets:** define maximum frames, image dimensions, source duration, total
  inference wall time, memory, output token/byte count, concurrency, and
  retained analysis size. Start with one worker.
- **Evaluation:** build a synthetic and then human-labeled validation corpus
  spanning cuts, gradual transitions, static shots, motion, occlusion,
  low-light, captions/text in frame, and ambiguous events. Measure per-category
  precision/recall and timestamp tolerance. Do not optimize against anecdotes.
- **False positives:** require evidence frame references and expose abstention.
  Never invent people identity, intent, or off-screen causality.
- **Confidence:** calibrate against labeled data before probability language or
  numeric score fusion. Keep model confidence separate from current
  transcript-highlight heuristic scores.
- **Licensing:** verify the exact Qwen model card/license, redistribution
  terms, attribution, and any restrictions before committing model references
  or distributing weights. Record third-party notices.
- **Privacy:** keep inference local; do not add cloud calls, external telemetry,
  or data export. Use only registered managed assets and erase temporary
  frames deterministically.
- **Compatibility:** current scene-cut logic and the render path must continue
  to work if the model is missing, disabled, times out, or returns invalid
  output. Do not automatically download or switch models.

## Blockers and dependencies

1. One synthetic image produced a valid structured result through the real
   adapter. Earlier structured attempts were truncated; broader model quality,
   repeatability, and realistic-media performance are unmeasured.
2. No supported GPU inference provider was confirmed for the Radeon iGPU.
   Do not report GPU acceleration.
3. There is no native video-understanding model/runtime. Frame analyses remain
   independent observations, not cross-frame reasoning.
4. CRE has a durable visual-analysis lifecycle and owner-authorized service
   boundary, but no runtime worker wiring, planner contract, gateway endpoint,
   or UI integration. The worker contract uses a single-host exclusive file
   lease; multi-host coordination is not supported.
5. Human-labeled visual data and acceptance thresholds are required to make
   claims about event relevance or model confidence.
6. Qwen license metadata is Apache-2.0; review exact upstream model-card terms,
   attribution, redistribution, and organizational policy before distributing
   weights or model references.
7. The schema gate now distinguishes the base content layer (v7) from the
   runtime foundation (v10); startup preflight requires v10 and remains
   read-only. The configured database was not opened, so its current version
   is intentionally unknown. Visual-analysis migration remains an explicit
   operator action and the lifecycle is not runtime-wired.

## First implementation milestone

Complete Stage 2.1 integration-readiness review and authorize any migration
only against an explicitly selected database after its state is independently
verified. Before runtime wiring, retain the single-host worker constraint or
design a separately authorized multi-host coordination mechanism. Do not
wire the new services into the planner, gateway, or UI until integration
acceptance is defined. Do not change the renderer or render-operation lifecycle.

## Acceptance decision

The current outcome is **PARTIAL PROTOTYPE**. One real structured synthetic
image inference completed; provider contracts, timestamped synthetic video
sampling, evidence-backed highlight ranking, and an isolated owner-scoped
durable lifecycle are implemented and covered by focused tests. Runtime worker
recovery, application-database migration, planner integration, browser
explainability, and production acceptance remain incomplete. The existing CRE
ownership, plan, renderer, and durable render machinery remain the system of
record.
