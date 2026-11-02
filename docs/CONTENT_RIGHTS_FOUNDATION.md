# Content Rights and Provenance Foundation

## Status

This is a local, durable foundation for source provenance, reviewed rights
evidence, platform-scoped rights decisions, immutable content-version
references, and human review records. It is **not** a Content Revenue Engine
end-to-end pipeline and does not make ULTRON production-ready.

The implementation follows ULTRON's Pydantic domain-model, application-service,
signed `AuthorizationContext`, and SQLite transaction/event patterns. The
authenticated gateway and operator console expose local source-video
transcription, highlight review, and source-footage clip rendering when the
local transcription model is configured. No publishing control is exposed.

## Current local source-video workflow

- The operator console submits a local video to authenticated CRE gateway
  actions. FFprobe verifies actual video/audio streams, duration, dimensions,
  frame rate, and audio format; the filename extension is not treated as media
  validation. Source uploads are bounded by `ULTRON_CRE_MAX_SOURCE_BYTES`
  (default 32 MiB; allowed range greater than 1 MiB through 32 MiB).
- Registered footage is stored in the owner-only `data/ultron_cre_content`
  directory and is bound to its SHA-256. FFmpeg extracts mono 16 kHz PCM WAV
  under a bounded output size; the persisted transcript remains linked to the
  original video asset ID and hash. Highlights are deterministic transcript
  windows, limited to five, with source timestamps, actual transcript excerpts,
  and heuristic explanations. Bounded FFmpeg scene-cut timestamps contribute
  an 8% highlight score cue; this is cut detection, not semantic visual
  understanding.
- A local Faster-Whisper model must already be provisioned and selected by
  absolute path with `ULTRON_CRE_FASTER_WHISPER_MODEL`. The application does
  not download models or call external providers. Without the model,
  transcription is reported as `NOT_CONFIGURED`.
- Uploading footage is not rights clearance. The exact existing `ANALYZE`
  grant must authorize transcription, and an exact `CREATE_DERIVATIVE` grant
  is revalidated before rendering. Missing evidence remains denied; the
  workflow does not create or infer grants from an operator upload.
- Selected candidates render original source footage, center-cropped to
  9:16 without geometric distortion, with transcript-timed captions and
  loudness-normalized source audio. The executable edit timeline follows the
  candidate's transcript spans in source order and splits spans at detected
  scene cuts; FFmpeg concatenates those intervals with hard cuts and removes
  gaps between transcript spans. The exact ordered intervals and output
  timings are exposed in the review manifest. This does not reorder candidates
  into a larger story or infer visual meaning. The MP4 is hash-verified and
  served as a local download/preview. Human review remains required;
  publication is not attempted.

## Mission 2: source intake and lifecycle

- The authenticated `ContentRightsService.register_source_reference` operation
  records metadata only for an existing `SourceAsset` whose bytes were already
  hashed at the trusted application boundary. It does not fetch, resolve, or
  follow the supplied URL. References must be bounded, credential-free HTTPS
  URLs; filesystem paths and credential-bearing query parameters are rejected.
  Parsing never performs DNS or network access, and URL paths are never mapped
  to local files.
- Intake records require an explicit acquisition method and provenance linked
  to the same source URL. An optional supplied source digest remains
  `UNVERIFIED`; it is never treated as proof that remote material matches the
  already-registered asset. Access permission is recorded separately and
  defaults to `UNKNOWN`; a public URL, successful parsing, or authenticated
  ULTRON caller does not change that decision or establish copyright rights.
- Each intake is transactionally bound to an asset ID and its verified
  SHA-256, target context, exact `ANALYZE` use, and an initial `REGISTERED`
  lifecycle record/event. Supported progression is
  `REGISTERED -> RIGHTS_PENDING -> RIGHTS_CLEARED -> READY_FOR_ANALYSIS`.
  Unsupported transitions fail. A policy check is run when requesting
  `RIGHTS_CLEARED` and is run again when requesting or revalidating
  `READY_FOR_ANALYSIS`; the check uses the existing rights policy and exact
  platform, account/channel, territory, asset hash, validity window, and
  `ANALYZE` action. Missing/pending rights remain `RIGHTS_PENDING`, rejected
  evidence becomes `REJECTED`, other insufficient scope becomes `BLOCKED`,
  and expired/revoked grants become `EXPIRED`/`REVOKED`.
- Rights/evidence changes and grant revocation atomically return affected
  cleared/ready lifecycle records to `RIGHTS_PENDING`, append lifecycle
  history, and invalidate related approvals. An approval is refused if its
  expiry would outlive any applicable grant. Time passing alone does not run
  a background transition; rights evaluation lazily records expired agreement
  events and invalidates affected readiness. The analysis boundary must still
  request the `READY_FOR_ANALYSIS` transition immediately before work, which
  revalidates rights and records `EXPIRED`/`REVOKED` instead of readiness.
  No analysis entry point exists in this mission.
- YouTube Shorts and Instagram Reels are distinct target contexts and use the
  existing, independent YouTube/Instagram grant adapters. YouTube Kids is
  recorded only as an audience-classification context and still uses ordinary
  YouTube rights; it creates no separate copyright permission. Snapchat
  Spotlight can be recorded as a target context, but no Snapchat policy adapter
  exists and its lifecycle cannot reach rights-cleared or analysis-ready.
- Source-intake, lifecycle current state, lifecycle history, rights decision,
  command/idempotency record, and audit event commit in the existing
  `BEGIN IMMEDIATE` transaction. Lifecycle events and source-intake records
  are append-only; lifecycle identity is immutable. Schema version 2 is an
  additive migration from Mission 1 version 1 and preserves existing tables
  and records. Startup continues to run SQLite integrity and foreign-key
  checks.

## Mission 3: grantor and agreement verification

- Grantor identity claims are account-scoped, immutable records with an
  evidence reference and SHA-256 digest. They begin `UNVERIFIED`; only a
  configured reviewer other than the account owner may append a review
  decision bound to the exact evidence digest. Identity verification does not
  establish authority to license.
- Authority claims are separate, asset-specific records. They require a
  currently verified identity and a separate reviewer decision bound to the
  authority evidence digest. Agreement scope is constrained to the asset
  covered by that authority claim.
- Each rights agreement has a stable ID and sequential version, explicit
  grantor, authority, rights-holder, grantee, asset scope, full platform/account
  and use terms, validity interval, evidence reference/hash, and canonical
  fingerprint. Agreements start `PENDING_REVIEW`. A reviewer other than the
  agreement owner must verify the exact agreement fingerprint and evidence
  digest before an `APPROVED` event is recorded. Self-assertion or a signature
  reference alone is not automatic verification.
- Rights grants must reference the latest approved agreement version. The
  agreement, its reviewer decision, identity review, authority review, rights
  evidence, and grant terms are rechecked transactionally when a grant is
  created and again by the existing downstream rights policy. An approved
  agreement cannot expand the asset, platform, account, territory, action,
  commercial-use, monetization, attribution, duration, or validity scope of
  the underlying rights evidence. YouTube and Instagram remain independent.
- Amendments append a new version and mark the prior version `AMENDED`.
  Suspension, revocation, expiry, reviewer-decision changes, and amendments
  invalidate affected approvals and lifecycle readiness in the same
  transaction. Expiration is detected and recorded as an append-only event on
  the next rights evaluation; there is no background scheduler. Expired,
  suspended, revoked, amended, stale, or inconsistent agreements do not
  authorize use.
- Identity/authority/agreement rows, reviews, and events use SQLite foreign
  keys and append-only triggers for ordinary application operations. Schema
  version 3 adds agreement references to existing rights grants and creates
  the new tables additively; legacy grants without an agreement remain stored
  but fail closed in the policy.

## Combined Mission 4–5: synthetic processing foundation

- `ContentRightsService.create_processing_job` accepts only a signed,
  resource-bound request for one registered asset, its exact SHA-256, a
  supported target context, and a non-commercial `ANALYZE`-only `RightsUse`.
  The existing rights policy is evaluated in the same SQLite transaction
  that creates the queued job and processing event. A prior
  `READY_FOR_ANALYSIS` lifecycle value is not used as authorization.
- A worker claim is separately signed and bound to the job ID. Immediately
  before the processor is called, the claim transaction reloads the asset and
  job linkage, reads and hashes the registered bytes through the injected
  managed store, and reevaluates current rights. Missing bytes, hash mismatch,
  invalid agreement/grant state, expiry, revocation, or scope mismatch prevents
  dispatch. Rights are not held as a lock during processing; a later revocation
  does not claim to stop work already dispatched.
- Schema version 4 adds processing job, output metadata, and append-only event
  tables without rebuilding existing content tables. Job creation, worker
  claim, cancellation, and terminal outcomes use the existing authorization,
  transaction, idempotency, and audit mechanisms. Concurrent workers cannot
  claim a queued job twice. Interrupted `RUNNING` jobs are changed to
  `UNCERTAIN` on database reopen and are never automatically retried.
- Pending jobs can be cancelled to `CANCELLED`. A cancellation while a job is
  `RUNNING` changes it to `UNCERTAIN`; it does not assert that the processor
  stopped. Outputs from that execution are not exposed as successful results.
  Processor failures are `FAILED`; a timeout detected after the bounded
  synthetic processor returns is `UNCERTAIN`, because this implementation
  cannot preempt an in-flight processor.
- `LocalTemporaryContentStore` is an opt-in local/test implementation. It
  accepts only validated opaque asset keys, separates input/output namespaces,
  enforces a 1 MiB input and 64 KiB output limit, stages files under a
  controlled directory, fsyncs files and directories, and verifies SHA-256 on
  every read. The deterministic `SyntheticContentProcessor` transforms only
  marked synthetic test bytes into a digest result; it does not decode media.
  The base content service remains unchanged when this optional store and
  processor are not configured.
- Output files are finalized before their SQLite metadata commits and are
  exposed only after a successful job/output record exists. If database
  finalization fails, the service attempts to remove the unreferenced output;
  cleanup failure is surfaced. A crash between file finalization and database
  commit can leave an inaccessible orphan. `recover_processing_storage`
  removes unreferenced output files and abandoned staging files and must be
  called only while workers are quiescent. Startup database recovery itself
  marks interrupted running jobs uncertain; storage orphan cleanup is an
  explicit host-startup step.
- This is not production media storage or a real media pipeline. It does not
  provide production object-store controls, encryption/key management,
  malware scanning, decoder isolation, durable queue scheduling, process
  preemption, or deployment-level worker coordination. Real media formats,
  acquisition, evidence retention/deletion, legal review, and reviewer
  qualification remain future gates. No gateway, browser, publishing, remote
  acquisition, transcription, editing, or platform integration was added.

## Missions 6–7: transcription and highlight candidates

- `ContentIntelligenceService.create_transcription_job` creates a related
  `content_processing_jobs` row and immutable transcription request in one
  authorized transaction. The request binds the registered asset ID/hash,
  supported target context, exact non-commercial `ANALYZE` use, and bounded
  transcription limits. `run_transcription_job` accepts only those linked
  jobs; the existing synthetic processing worker refuses to claim them.
- Before engine dispatch, the transcription worker rechecks ownership, job and
  asset linkage, the stored bytes' SHA-256, audio structure, media duration,
  and current rights in its atomic claim. The input boundary accepts bytes
  read by the injected managed store for the registered opaque key; it does
  not accept caller filesystem paths or remote URLs. The currently supported
  format is uncompressed PCM WAV only: 1–2 channels, 8–48 kHz, 8/16/24/32-bit
  samples, at most 1 MiB, and at most 15 minutes. No video demuxing or audio
  extraction from video is implemented.
- `FasterWhisperLocalEngine` is a provider-independent-contract adapter for
  Faster-Whisper. It requires an explicitly supplied local model directory
  containing model weights, configuration, and a local tokenizer file, and
  sets `local_files_only=True`; it rejects incomplete models rather than
  allowing Faster-Whisper's tokenizer-download fallback. PCM WAV is decoded
  and resampled to bounded in-memory mono audio before dispatch, without
  passing a path or URL to the engine. The adapter is optional and the base
  requirements do not declare Faster-Whisper. Missions 6–7 were verified
  against the local Faster-Whisper Base model at
  `~/.cache/ultron/models/faster-whisper-base`; standard deterministic tests
  continue to use a test-only fake engine.
- Engine output is bounded and validated before persistence: non-empty
  segments, finite non-negative timestamps, end after start and within media
  duration, bounded segment count and text, and finite confidence in `[0, 1]`
  when a provider actually supplies it. Segments must retain chronological
  engine order; overlapping intervals are allowed and preserved without
  sorting or changing text. Confidence is omitted when the engine does not
  provide it. The durable transcript keeps source identity/hash, job and
  target, engine/version, language when reported, validation version,
  canonical raw-segment digest, and validated segment content.
- Transcription shares the processing job state machine and startup recovery.
  A validated transcript, successful state transition, event, and audit record
  commit together. Engine or validation failures become `FAILED`; a runtime
  limit detected after return becomes `UNCERTAIN` and does not preempt the
  engine. If transcript finalization fails after inference, the service
  records `UNCERTAIN` where possible and never exposes an uncommitted
  transcript. A process interruption leaves `RUNNING` for startup recovery to
  mark `UNCERTAIN`; no automatic retry is permitted. Authorization/audit
  failure rolls back transcript and job-success persistence.
- `generate_highlights` consumes only a persisted validated transcript,
  revalidates current rights in the same transaction, and stores an immutable
  run and candidate records with its audit event. Candidate timestamps are
  exact transcript/source-audio segment boundaries; no transcript text or
  timing is altered. Candidates reference transcript segment IDs, transcript
  fingerprint, source asset/hash, configuration, scoring version, component
  scores and signal evidence. A deterministic run identity makes identical
  transcript/configuration/scoring inputs idempotent. Reads recheck ownership,
  current rights, and transcript fingerprint; revoked or expired scope cannot
  be returned as current candidates.
- The selector evaluates bounded contiguous windows (maximum 20,000 windows;
  default 15–60 seconds; up to 10 results, default minimum score 0.38). Its
  explainable heuristic score is normalized to `[0, 1]` and equals:
  `0.15*hook + 0.20*coherence + 0.20*information_density + 0.15*narrative
  + 0.15*ending + 0.15*duration_fit`. Hook, coherence, density, narrative,
  ending, and duration components use transparent textual/structural signals;
  they are heuristics, not probabilities, engagement predictions, or revenue
  estimates. Candidates with temporal intersection-over-union above 0.65 are
  deduplicated deterministically. A weak/short transcript may produce no
  candidates. This is not semantic understanding, and source timestamps are
  not frame-accurate video edit points.
- Schema version 5 additively creates transcription-request, transcript,
  highlight-run, and candidate tables with foreign keys and append-only
  triggers; existing content tables are not rebuilt.
- Focused synthetic test fixtures cover PCM-WAV validation, authenticated
  job creation and claims, rights revocation, source-hash mismatch, invalid
  timestamps, transcript persistence, uncertainty/restart behavior,
  concurrency, transactional audit failures, deterministic candidate scoring,
  idempotent runs, rights revalidation, and no-candidate output.

## Missions 8–9: local clip production and quality assurance

- `ContentClipRenderingService` accepts only an existing owned highlight
  candidate whose immutable run fingerprint, transcript, successful analysis
  job, source asset ID/hash, target context, and segment-boundary interval all
  agree. It requires a fresh, non-commercial `CREATE_DERIVATIVE` use scoped to
  that exact source hash and target platform. Rights are evaluated at request
  creation and again atomically immediately before a worker claims rendering.
  Analysis clearance by itself does not authorize a derivative.
- Clip render state, source interval, candidate/transcript/asset provenance,
  output key/hash/size, stream flags, durations, and append-only state events
  are durably recorded. The additive schema version 6 migration does not
  rebuild prior content tables. Existing managed storage remains the only
  source boundary: source bytes are retrieved by registered opaque key and
  checked against the immutable SHA-256; callers cannot provide local paths
  or remote URLs.
- This bounded implementation accepts registered sources of at most 1 MiB
  and 300 seconds, clips of at most 60 seconds, one video stream up to
  1920x1080/60 fps and one audio stream up to 48 kHz/stereo, and outputs of
  at most 16 MiB. FFmpeg writes H.264/AAC MP4 into private managed staging;
  FFprobe validates source/output metadata, output stream types and duration
  (within 0.75 seconds), and audio/video duration alignment. FFmpeg separately
  decodes the complete output before it can be committed as successful.
  Renderer entry points accept only expected managed-staging filenames under
  the configured staging directory; caller-supplied filesystem paths are
  rejected. FFmpeg/FFprobe are invoked with argument arrays (no shell),
  bounded runtime and codec probing/allocation options. FFprobe JSON is
  capped at 64 KiB; FFmpeg/FFprobe diagnostic output is discarded rather than
  accumulated in memory. Unsupported, malformed, oversized, or out-of-range
  input fails closed.
- A worker interruption or FFmpeg/decoder timeout is `UNCERTAIN`, not retried;
  startup recovery records interrupted running renders as uncertain. Definite
  failures become `FAILED`; interruption/cancellation also attempts to record
  `UNCERTAIN` before propagating and always attempts to remove both staging
  files. Temporary output is removed on failure, output
  files are committed to managed storage before success metadata, and orphan
  cleanup preserves staging files and output objects for `RUNNING` or
  `UNCERTAIN` renders while cleaning other orphans; the state snapshot and
  cleanup are held within the database write transaction to prevent a local
  worker claim/finalization racing cleanup. Identical candidate/use requests
  return the existing render record rather than making another. This is not an
  exactly-once guarantee.
- Rights are revalidated immediately before the worker claim, but rights
  revoked after rendering begins do not preempt an FFmpeg process already in
  progress. A timeout or interruption is not automatically retried because
  the final outcome may be uncertain.
- The inspected local environment provides FFmpeg 6.1.1, FFprobe, the
  `libx264` encoder, and AAC encoding; no media dependency was installed or
  upgraded. The transcription service itself still accepts validated PCM WAV;
  the authorized gateway extracts that bounded audio from the registered
  source video before invoking it. Scene-cut timing and the ordered source
  edit timeline are also generated and executed locally.
- Real local FFmpeg/FFprobe fixture integration tests exercise source-video
  analysis, ordered timeline rendering, independent output decoding, duration,
  hash and provenance checks, plus audio/video stream validation. This verifies
  the tested local toolchain and bounded fixture path only; it is not a
  production media-safety certification.

## Implemented behavior

- `SourceAsset` records an owner, category, acquisition method, provenance,
  opaque storage key, separate asset state, and SHA-256 digest. Registration
  accepts a binary stream, computes its digest, and rejects a mismatch. It
  never accepts or opens a filesystem path. Stored asset identity and hash
  cannot be updated through SQLite.
- `OwnershipClaim` records the claimant, claimed rights-holder identity, and
  authority description. `RightsEvidence` bytes and their hash are stored
  together. Only a configured reviewer with a signed, action/resource-bound
  authorization can retrieve evidence for review or record a durable
  `VERIFIED`, `REJECTED`, or `AMBIGUOUS` decision. The decision binds both the
  exact evidence hash and (for verification) the fingerprint of the exact
  `RightsTerms`. Verification does not use model-generated assessments.
- A `RightsGrant` is tied to one asset, one claim, one evidence record, exactly
  one platform, an explicit account/channel scope, territory scope, validity
  interval, permitted actions, attribution terms, and an explicit maximum
  duration. A wildcard account or territory is only recognized when the grant
  says `*`. Revocation is a separate append-only event.
- `ContentRightsPolicy` uses separate YouTube and Instagram adapters. They
  evaluate grant scope independently: platform, account/channel, territory,
  time, each requested action, commercial/monetization permission, attribution,
  and duration. Music is represented as its own asset and must have its own
  rights grant.
- Every rights evaluation is persisted with its exact use, result, policy
  version, reasons, and timestamp. Missing rights are `UNKNOWN`; pending
  evidence is `REVIEW_REQUIRED`; rejected, ambiguous, tampered, mismatched, or
  insufficient rights are blocked. Expired and revoked grants have distinct
  states. None of these states is permission.
- Content package versions bind one target platform/account, source-use
  records, metadata hash, and an artifact hash. Registration of a source and
  artifact computes SHA-256 from supplied binary streams; it does not store
  those media bytes. Versions and approval records are immutable. A new
  evidence submission, ownership claim, evidence decision, grant, grant
  revocation, or package version invalidates affected review approvals in the
  same transaction.
- Human review approval binds the authenticated principal and session, exact
  package version, artifact and metadata hashes, platform, account/channel,
  permitted `publish` action, rights snapshot, expiry, and unique nonce. An
  approval is only a review record; it is **not** publishing authorization.
  Publication state is stored separately and starts at `NOT_PUBLISHED`.
- All mutating service calls require a live, signed, action/resource-bound
  authorization context, consume its grant once, and write business state,
  idempotency state, and audit event in one `BEGIN IMMEDIATE` transaction.
  Duplicate command IDs with the same request return the original result;
  reuse with different content is rejected. Audit and decision records are
  append-only at the application/schema level. SQLite quick-check and foreign
  key validation run at startup; unsupported schema versions fail closed.
- The content database is explicitly configured, has SQLite foreign keys,
  WAL, `synchronous=FULL`, a busy timeout, and owner-only initial file mode.
  It is not implicitly pointed at a live application database.
- The agreement and grantor mutations use the same signed
  `AuthorizationContext`, idempotency ledger, `BEGIN IMMEDIATE` transaction,
  and audit-event writer as existing content operations. Review events and
  agreement status events commit atomically with invalidation records.

## Decision separation and platform policy inputs

The result exposes independent fields for:

1. **Copyright evidence/clearance** (`copyright_state`).
2. **Platform-specific grant scope** (`platform_rights_state`).
3. **Platform-policy compliance** (`platform_compliance_state`).
4. **Publishing authorization** (`publishing_authorization_state`).
5. **Monetization permission in a rights grant** (`monetization_permission`).
6. **Platform monetization eligibility** (`monetization_eligibility_state`).

Platform policy rules are versioned input records with reviewer identity,
verification time, and official-source references. The repository ships with
unverified YouTube and Instagram rule inputs and no platform-specific
requirements. Consequently compliance remains `UNKNOWN` by default. Any
future rule set must be checked against current official documentation,
recorded with its source/version, tested, and explicitly enabled by an
authorized policy owner. A configured rule set is only evidence that its
declared checks passed; it does not establish platform approval or eligibility.

YouTube permission never implies Instagram permission. A dated review of
official YouTube upload, OAuth, Shorts, copyright, and monetization sources is
recorded in [CONTENT_PUBLISHING_DESIGN.md](./CONTENT_PUBLISHING_DESIGN.md).
Those sources have not been installed as enabled `PlatformPolicyInputs`, and
no API project, account eligibility, or monetization state has been verified
for this implementation.

## State model

- **Asset:** `ACTIVE`, `QUARANTINED`, `ARCHIVED`.
- **Rights decision:** `UNKNOWN`, `REVIEW_REQUIRED`, `CLEARED`, `BLOCKED`,
  `EXPIRED`, `REVOKED`; always scoped to the exact asset/use/platform/account
  evaluation, never a global promise that an asset is safe for every use.
- **Evidence/claim:** pending until an authenticated reviewer records a
  specific verification decision; later decisions are appended and the latest
  decision governs.
- **Grantor identity/authority:** `UNVERIFIED`, `PENDING_REVIEW`, `VERIFIED`,
  or `REJECTED` from their current append-only review history. Identity and
  asset-specific authority have separate evidence and reviewer records.
- **Rights agreement:** `PENDING_REVIEW`, `APPROVED`, `REJECTED`, `AMENDED`,
  `SUSPENDED`, `EXPIRED`, or `REVOKED`, with append-only versioned review and
  lifecycle events. Expiry is persisted lazily at a rights-evaluation boundary.
- **Content review:** `PENDING`, `APPROVED`, `REJECTED`, `INVALIDATED`,
  `EXPIRED`, recorded independently from rights and publication state.
- **Publication:** `NOT_PUBLISHED`, `QUEUED`, `PUBLISHING`, `PUBLISHED`,
  `UNCERTAIN`, `FAILED`, `TAKEDOWN_REQUIRED`. This mission only initializes
  `NOT_PUBLISHED`; it does not implement publication transitions.
- **Source lifecycle:** `REGISTERED`, `RIGHTS_PENDING`, `RIGHTS_CLEARED`,
  `READY_FOR_ANALYSIS`, `REJECTED`, `BLOCKED`, `EXPIRED`, `REVOKED`,
  `ARCHIVED`. Readiness is a durable last-checked state, not a standing
  authorization; callers must request fresh revalidation before analysis.
- **Source access permission:** `UNKNOWN`, `CLAIMED_AUTHORIZED`, `NOT_REQUIRED`,
  `DENIED`, `EXPIRED`, `REVOKED`, persisted independently from rights,
  platform compliance, publishing authorization, and monetization eligibility.

## Remaining limitations and risks

- No trend discovery, remote media acquisition, semantic visual
  understanding, OAuth onboarding, publishing, analytics, or
  performance-learning integration is implemented. Local video audio
  extraction, PCM-WAV transcription, scene-cut timing, and bounded timeline
  rendering are documented above. The renderer accepts only the established
  managed-store input limits and is not a general-purpose service for
  arbitrary media files.
- Source intake is intentionally limited to metadata references attached to an
  already-registered asset; this mission does not acquire or validate the
  legality of a source URL or its content. `access_permission_state` is
  caller-supplied evidence with an `UNKNOWN` default, not a verified access
  credential or remote-access control. No scheduler automatically updates
  lifecycle state at rights expiry; freshness is enforced at the explicit
  analysis-readiness revalidation boundary.
- The application accepts an already-open binary stream from a trusted caller;
  it does not prove that the caller acquired the stream lawfully. The
  provenance reference, license text, identity/authority documents, and
  reviewer decisions are durable evidence, not a legal opinion or automatic
  proof of rights ownership. The application enforces a reviewer allowlist and
  prevents self-review, but it does not establish reviewer qualifications,
  enforce an external identity provider, or cryptographically prove the
  grantor's identity, ownership, or authority. Evidence references and hashes
  are reviewer-asserted; agreement evidence bytes are not stored or fetched.
- The content database does not store media bytes. Without the optional
  synthetic processing store, the service only hashes caller-supplied
  source/artifact streams. The local synthetic store is not production object
  storage and does not implement encryption/key management, malware scanning,
  decoder isolation, or deployment-level access controls. Evidence documents
  are stored in SQLite (maximum 2 MB each) and may contain personal or
  confidential data.
- SQLite append-only triggers protect ordinary application writes, not a
  privileged operator who can modify the database file or drop triggers. The
  audit log is not cryptographically chained or externally anchored. Database
  backups, encryption, retention, and access monitoring remain deployment
  requirements.
- A review approval expires after at most 24 hours and is invalidated when
  related rights or content records change. No publishing service consumes
  this record, no external effect is authorized, and no exactly-once
  publication guarantee exists.
- Copyright clearance does not establish community-policy compliance,
  account/API authorization, publication approval, music portability, or
  monetization eligibility. Obtain legal review for license terminology,
  publicity/privacy/voice/likeness and minor-consent requirements, territory,
  attribution, platform terms, and commercial use before enabling downstream
  work.

## Required next implementation gate

Before enabling real media processing, the host application must provide an
authorized intake boundary that obtains media lawfully, role-managed
independent rights reviewers, protected evidence/media storage with
retention/deletion controls, supported-format validation, decoder isolation,
resource/time enforcement, and verified official YouTube/Meta policy inputs.
The synthetic foundation does not satisfy those deployment gates. Publishing
requires a distinct OAuth/account authorization and publisher design; an
approved review record must never be treated as a platform publish credential.
