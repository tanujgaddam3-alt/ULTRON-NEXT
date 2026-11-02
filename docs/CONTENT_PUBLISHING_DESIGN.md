# Content Publishing Design: Missions 10–11

## Status and scope

This document records an official-documentation review, an additive design,
and the Mission 14 offline YouTube protocol boundary. The local Mission 12
foundation is implemented, but it does not provide or authorize external
publishing, create OAuth credentials, configure an account, or claim production
readiness. No content was uploaded. Official pages were accessed on
**2026-10-24**; policies and API requirements can change, so the implementation
must recheck these sources before enabling uploads.

The repository already has rights, content-version, human-review, audit, and
local rendered-output foundations. Mission 12 adds a database-backed local
publishing request/approval workflow and a deterministic mock adapter. It has
**no live external publishing adapter, OAuth token store, resumable-upload
worker, or remote-publication recovery process**. The separate existing
publication row still starts at `NOT_PUBLISHED`; a mock acknowledgment is not
a publication.

## Mission 14 offline adapter boundary

An additive `YouTubeAdapter` boundary now constructs the `videos.insert`
resumable-session request and metadata, validates the returned session URI,
uploads bounded byte ranges, probes the same session after uncertain outcomes,
and queries a known video ID to distinguish `UPLOADED`, `PROCESSING`,
`PUBLISHED`, and `AVAILABLE_PRIVATE`. Authorization is injected and checked
before dispatch, each status probe and each chunk. The access token is sent
only as the required transport `Authorization` header. Protocol, normalized
transport and credential errors use fixed codes without response bodies or
session URLs; no adapter logger is installed. Offline tests use only fake
transport, credential and session-progress providers.

Transport calls are given a 60-second timeout. Only HTTP 500, 502, 503, and 504
are retried on the existing session; retries are capped at three per byte
offset, twelve total, and a 15-minute deadline, with each
`Retry-After`/fallback backoff capped at five minutes. The adapter rejects
upload payloads over 16 MiB and response bodies over 1 MiB after the transport
returns. With no production transport, streaming-time allocation limits remain
unverified and any future transport/output provider must enforce bounds before
materializing data. A `SESSION_EXPIRED` outcome does not create another
session. An uncertain session requires an explicit status-only reconciliation
before a later resume.

The session-progress contract now explicitly provides protected-secret
retrieve, store, replace, invalidate, and delete operations. The repository's
default unavailable implementation fails closed; it does not fall back to
plain SQLite or other plaintext storage. The test-only in-memory provider
verifies the interface semantics but is neither durable nor secure storage.
If a session URI cannot be persisted after session creation, the local
operation becomes `UNCERTAIN` and cannot be redispatched. If ordinary progress
persistence fails after the URI is stored, recovery can inspect only that
same URI and reconcile its remote offset before resuming. Completion and
session-expiration paths invalidate and delete the stored URI. Missing or
malformed session material does not cause replacement-session creation.
Adapter requests and tests use redacted transport recordings; an injected
production transport would still need to enforce the same redaction and
bounded-response contract. Cancellation is not implemented or implied by a
local state change.

This is an **unregistered offline protocol boundary, not an operational
publisher**. `PublishingService` still accepts only `MockPublishingAdapter`,
and its durable job state machine does not represent resumable progress or the
YouTube lifecycle states. There is no production transport, OAuth provider,
secure and restart-durable session-URI store, or durable YouTube progress
repository. The injected authorization callback has not been wired to the
publishing service's final rights/approval/kill-switch transaction. The test
store is in-memory and is not evidence of encryption or secure persistence.
Therefore the adapter must not be registered with `PublishingService`, and
the repository has no production transport capable of dispatching a real
request or establishing remote correctness. Because the transport is an
injected boundary, supplying an unreviewed external transport would bypass
that limitation and is unsupported. A production integration remains blocked
until protected durable storage, authorized service integration, durable
YouTube progress/state migration, transport/OAuth controls, and restart
recovery are designed and tested together.

## Mission 16 secure-storage and integration feasibility

The Mission 15 baseline was re-run before editing: the adapter and publishing
tests passed **45 tests**. The combined content regressions later passed
**172 tests** before the Mission 16 test additions. Git status/repository-root
inspection is unavailable in this checkout because the supplied directory has
no `.git` metadata; changes were limited to the files listed by the task.

The host exposes a D-Bus session and a `gnome-keyring-daemon` process was
observed, but `secret-tool` is absent, and the selected Python environment has
no `keyring` or `secretstorage` module. The daemon's unlocked state, access
controls for this application, and availability during unattended restart
were not verified. No credential service was contacted.
The existing package `cryptography` is not, by itself, a key-management
backend. Consequently, no production session-URI store is configured or
claimed, and the fail-closed unavailable implementation remains the only
repository-provided backend. The test-only provider demonstrates the contract
only; it does not prove secure or process-durable persistence.

The authoritative publishing-job state remains `ContentStore`'s schema-v7
SQLite record and its transaction/audit machinery. Its job states are the
local mock flow (`AWAITING_APPROVAL`, `QUEUED`, `DISPATCHING`,
`MOCK_ACCEPTED`, `FAILED`, `UNCERTAIN`, `BLOCKED`, `CANCELLED`); they do not
model YouTube byte progress or remote lifecycle. Startup converts interrupted
`DISPATCHING` records to `UNCERTAIN`, and the schema has no YouTube session or
progress columns. YouTube test progress is separate, keyed by the stable job
ID, and must not be represented as durable in this repository until secret
storage and the state migration are implemented together. No schema migration
was made in Mission 16.

`PublishingService` remains the sole authorized dispatch path and explicitly
rejects every adapter whose exact type is not `MockPublishingAdapter`. Its
transaction commits `DISPATCHING` before calling the mock; current rights,
approval, kill switch, uniqueness, and audit checks apply to that path. The
YouTube adapter accepts an injected authorization callback but is not wired to
that transaction, so direct use is not authorized service integration. A
regression test confirms the service rejects the offline YouTube adapter.
The adapter additionally records `BLOCKED` in its injected progress provider
if the final pre-session authorization check fails; this adapter-local state
does not change the durable publishing-job state machine.

Offline recovery tests establish only adapter behavior with a fake provider:
the same stored session is probed before continuing; missing or inaccessible
session material becomes uncertain; a failed session-URI store does not
redispatch; and failure to persist a received completion is recovered by
probing that same session rather than creating a new one. A new adapter
instance can resume from test-provider state, which is not evidence of a
production process restart. The existing schema-v7 startup recovery tests
cover mock `DISPATCHING -> UNCERTAIN`, not remote upload recovery.

Mission 16 validation completed with these commands (all exit code 0):

- `.venv/bin/python -m pytest -q tests/application/test_content_youtube_adapter.py`
  — **35 passed**.
- `.venv/bin/python -m pytest -q tests/application/test_content_publishing.py`
  — **15 passed**.
- `.venv/bin/python -m pytest -q tests/application/test_content_youtube_adapter.py tests/application/test_content_publishing.py tests/application/test_content_rights.py tests/application/test_content_lifecycle.py tests/application/test_content_processing.py tests/application/test_content_intelligence.py tests/application/test_content_rendering.py`
  — **177 passed**.
- Pylance diagnostics for the changed Python files returned no diagnostics;
  syntax checks found no errors.

## Mission 16 outcome

Implemented and locally tested: the secure-session interface's store/retrieve/
replace/invalidate/delete contract; fail-closed behavior when storage is
unavailable or inaccessible; sanitized errors; absence of session URIs from
ordinary publishing job/event/audit records; uncertain handling of persistence
failure; same-session reconciliation after interruption or local completion
persistence failure; and a blocked state for authorization denied immediately
before session creation. These are offline contract tests only.

Still blocked and deliberately disabled: an OS-backed protected secret store;
durable YouTube progress and recovery; integration through `PublishingService`
with rights/approval/kill-switch enforcement in the same dispatch contract;
production transport and OAuth; and live account/API-project verification.
The next safe milestone is to select and verify an operator-approved credential
service and its unattended access/ownership model, then design a coordinated
store migration and service dispatch contract before enabling any transport.

## Mission 17 publishing-state contract

Mission 17 reverified the Mission 16 environment and test position. The focused
YouTube adapter and publishing-service baseline passed **50 tests** before
Mission 17 edits. Git metadata remains unavailable in this checkout.
`secret-tool`, the Python `keyring` module, and `secretstorage` are absent.
A D-Bus address and a `gnome-keyring-daemon` process are present, but no
credential-store operation was attempted; unlocked state, application access,
and unattended restart behavior are therefore **not verified**. No production
secret backend can be claimed. The test provider is not production storage.

The current authoritative durable state is `PublishingJob.state` in
`ContentStore` schema version 7. The database trigger permits only these
transitions:

| State | Evidence and meaning | Allowed next state(s) | Retry/recovery |
|---|---|---|---|
| `AWAITING_APPROVAL` | Local request exists; release approval not yet accepted. | `QUEUED`, `BLOCKED`, `CANCELLED` | Approval may queue it; no remote action. |
| `QUEUED` | Local approval and request are queued. Rights/approval and kill switch are checked transactionally at dispatch. | `DISPATCHING`, `BLOCKED`, `CANCELLED` | Safe to attempt authorized dispatch while the row remains queued and checks pass. |
| `DISPATCHING` | Durable local dispatch intent committed before invoking the configured adapter. In this release the adapter is mock-only. | `MOCK_ACCEPTED`, `FAILED`, `UNCERTAIN` | Never blindly retried. Startup recovery changes it to `UNCERTAIN`, records a job event and system audit event. |
| `MOCK_ACCEPTED` | The local mock adapter acknowledged; this is not remote acceptance or publication. | None | Terminal in the current trigger. |
| `FAILED` | The mock adapter reported a definitive local failure. | None | Terminal in the current trigger; not evidence about YouTube. |
| `UNCERTAIN` | Dispatch outcome could not be established, including interrupted dispatch. | None | No automatic retry; current service requires operator resolution. |
| `BLOCKED` | Request/approval/rights validation prevented dispatch. | None | Terminal in the current trigger; create a newly authorized request as policy permits. |
| `CANCELLED` | A local request was cancelled before dispatch. | None | Terminal; no remote action. |

Job and audit events are appended by the service/store command; startup recovery
uses `system:startup_recovery` and reason
`dispatch_interrupted_outcome_unknown`. A kill-switch or expired-approval
denial can leave a job queued with an audit event so it can be retried after
the control is cleared or a fresh approval is obtained. Current rights changes
can invalidate approval and block a queued job. The database trigger, not the
YouTube adapter, enforces the durable job transitions.

`YouTubeUploadProgress` is only an adapter-level protocol record used through
an injected provider in offline tests; it is not durable authoritative job
state. Its states describe evidence as follows:

- `SESSION_CREATING` means session creation is being attempted; it is local
  intent, not proof that YouTube created a session.
- `UPLOADING` stores a byte offset acknowledged by a protocol response; it
  does not mean the complete upload is accepted.
- `RECONCILING` means a status probe is in progress. A timeout or invalid
  response remains `UNCERTAIN`.
- `UNCERTAIN` prohibits dispatch until an explicit same-session reconciliation
  provides evidence. Missing/inaccessible session material cannot be resumed.
- `UPLOADED` requires a validated remote video ID from an upload response or
  session probe. It establishes upload acceptance only.
- `PROCESSING`, `PUBLISHED`, and `AVAILABLE_PRIVATE` require a matching video
  ID and validated remote status response. `PUBLISHED` requires the requested
  public visibility and processed status; it is not monetization approval.
- `FAILED` is a definitive protocol/API rejection; `BLOCKED` records a denied
  final authorization check; `SESSION_EXPIRED` records a not-found session
  that requires an explicit policy-approved recovery. None creates a new
  session automatically.

The secure session URI and this progress record are keyed by publishing job
ID only in the test fake. No ordinary persistent record contains the URI, and
no reference or durable offset is currently stored alongside the schema-v7
job. Thus restart recovery of a real upload is **not implemented**. The fake
tests can reconstruct a new adapter over the same in-memory provider to verify
same-session logic, but cannot verify process-durable secret or progress
storage. Release identity is not independently persisted in the adapter
progress record; binding is rechecked from the immutable job fields in
`dispatch`, while safe production correlation still requires service
integration.

Mission 17 added contract tests for every unavailable-store operation, a
service-level rejection test for the offline YouTube adapter, authorization
revocation at the final session-creation boundary, and local persistence
failure after a remote completion response. No schema migration was made.
The authorized service integration, process-durable secure session storage,
one unified durable YouTube lifecycle, OAuth/transport, and live remote
reconciliation remain blocked. The next milestone is operator selection and
verification of an application-accessible secret service with an unattended
restart model; only then can a transaction-safe service/state migration be
implemented and tested without enabling live dispatch prematurely.

Mission 17 validation (all commands exited 0):

- Baseline before editing: `.venv/bin/python -m pytest -q tests/application/test_content_youtube_adapter.py tests/application/test_content_publishing.py`
  — **50 passed**.
- `.venv/bin/python -m pytest -q tests/application/test_content_youtube_adapter.py`
  — **36 passed**.
- `.venv/bin/python -m pytest -q tests/application/test_content_publishing.py`
  — **15 passed**.
- `.venv/bin/python -m pytest -q tests/application/test_content_youtube_adapter.py tests/application/test_content_publishing.py tests/application/test_content_rights.py tests/application/test_content_lifecycle.py tests/application/test_content_processing.py tests/application/test_content_intelligence.py tests/application/test_content_rendering.py`
  — **178 passed**.
- Pylance diagnostics returned no diagnostics for the adapter and both
  publishing test files; syntax checks found no errors in the changed Python
  test files.

## Mission 12 local foundation status

- `PublishingJob` binds a package version, render and verified output hash,
  metadata hash, platform/account, visibility, intended rights uses,
  disclosure value, rights decision IDs/snapshot, and idempotency identity.
  Release approval is a separate immutable record, requires a reviewer
  different from the requester, and expires within 24 hours. A changed binding
  creates a different identity; SQLite uniqueness prevents a second active,
  mock-acknowledged, or uncertain request for the same full binding, even with
  a different idempotency key.
- Application-service authorization uses the existing signed authorization
  context, rights service and transaction/audit path. Rights are evaluated
  when creating and approving a request and again in the same transaction
  immediately before mock dispatch. Rights changes invalidate approval and
  block dispatch. This does not create an independent derivative-rights
  record and does not eliminate the narrow possibility of rights changing
  after dispatch has committed.
- The mock-only state flow is `AWAITING_APPROVAL -> QUEUED -> DISPATCHING ->
  MOCK_ACCEPTED | FAILED | UNCERTAIN`; rights revocation may transition
  `QUEUED -> BLOCKED`. An expired approval or engaged kill switch leaves the
  request queued with an audit event; it can be reapproved or dispatched after
  the switch is cleared. Startup changes an interrupted `DISPATCHING` request
  to terminal `UNCERTAIN`. No uncertain job is automatically retried.
- The database has append-only approval/job events, immutable request and
  approval bindings, a unique owner/idempotency-key constraint, a partial
  uniqueness constraint for active, mock-acknowledged, and uncertain release
  identities, and allowed state-transition triggers. Deterministic concurrent
  submission tests verify a single durable result for a shared idempotency
  identity and a single winner for a shared release identity; uniqueness
  failures roll back their command and authorization-claim writes. These
  controls provide local duplicate prevention, not exactly-once remote
  execution. There is no remote adapter whose behavior is established by the
  mock.
- A kill switch set after a job has committed `DISPATCHING` does not rewrite
  that in-flight state or promise preemption. Startup preserves queued and
  already-uncertain jobs and changes interrupted `DISPATCHING` jobs to
  `UNCERTAIN`, with both a job event and a system audit event. Tests verify
  interruption at the adapter boundary and that recovery never redispatches.
- Rights-denied dispatches, deterministic adapter failures, and interrupted
  dispatch recovery are checked against persisted audit/job events. Mock
  adapter exception text is not returned or persisted; only a fixed failure
  reason is recorded. Unauthenticated or pre-transaction validation failures
  are rejected before a publishing audit event is written.
- OAuth secrets, upload session URLs, platform identity, resumable transfer,
  remote state reconciliation, deletion/takedown, and platform API behavior
  remain unimplemented. A kill switch prevents a new mock dispatch; state for
  work already in `DISPATCHING` is not rewritten. Rights revoked after any
  future real dispatch begins cannot be promised to preempt it or remove an
  already published item.

## Official requirements verified

### YouTube Data API and OAuth

- The YouTube Data API v3 `videos.insert` method inserts a video resource and
  accepts a video upload. It requires OAuth authorization; a service account
  is not a general substitute for a channel owner. The YouTube server-side
  OAuth guide lists `https://www.googleapis.com/auth/youtube.upload` as the
  upload scope. Start with this scope for `videos.insert`; only add a read
  scope such as `youtube.readonly` if a separately reviewed reconciliation
  operation requires it. Avoid the broader `youtube` or `youtube.force-ssl`
  scopes unless an explicit method requirement justifies them.
- OAuth consent is user authorization for an account. Request scopes only
  when the publishing feature is used and explain the action. Use a validated,
  exact redirect URI, unique `state`, offline access only when background
  work requires it, and handle refresh-token invalidation/expiry and user
  revocation by stopping dispatch and requiring reauthorization.
- Google OAuth guidance requires client credentials and user tokens to be
  stored securely, not committed or sent in plaintext; revoke and delete
  tokens when no longer needed. A public OAuth application using scopes that
  access user data may need Google OAuth verification. OAuth consent
  verification and YouTube API Services compliance audit are separate
  processes; neither is evidenced as completed for this repository.
- `videos.insert` supports metadata on the video resource, including title,
  description, tags, category, and status such as `privacyStatus`. Publishing
  requests must set visibility explicitly: the current API reference says
  the allowed values are `public`, `unlisted`, and `private`. The design does
  not rely on an omitted-field default; it requires an explicit value in every
  request. The video resource
  also has `status.selfDeclaredMadeForKids` for the channel owner to designate
  child-directed content; the audience decision must be explicitly approved
  and must not be inferred from a target context or rights grant. YouTube API
  policy requires that write-operation values remain under the user's final
  control and that YouTube write actions are distinct, clearly identified,
  and clearly user-initiated. The title field supports up to 100 characters;
  a client must not impose a shorter limit.
- **Unverified API projects:** the current official `videos.insert` reference
  explicitly says uploads from unverified API projects are **not** restricted
  to private viewing mode. Do not apply the older private-only restriction.
  Separately, Google documents a compliance audit for requests to exceed
  default quota and periodic audits. No ULTRON API project or audit status was
  checked; no additional quota is assumed.
- Default quota documentation currently lists a separate `videos.insert`
  allowance of 100 calls per day (one quota unit per call), plus a combined
  daily quota for other methods; invalid requests still cost at least one
  point. Quota allocation is project-specific and may change. Additional
  quota requires the documented audit/extension process. The application
  must persist quota errors and back off rather than retrying in a loop.

### Shorts, rights, and monetization

- YouTube's current Shorts help page says standard-channel videos uploaded on
  or after October 15, 2024 with square or vertical aspect ratio and duration
  up to three minutes are categorized as Shorts. Official Artist Channels have
  a separate effective date of December 8, 2025. This is a classification
  rule, not a guarantee of recommendation, eligibility, reach, or monetization.
- These Shorts criteria do not specify an API upload codec, container,
  resolution, bitrate, or file-size profile. Those technical constraints must
  be verified against the current official upload/API documentation and the
  chosen client before implementation; they are unresolved here.
- A Short longer than one minute with an active copyright claim is blocked
  globally, not playable or recommended, and not eligible for monetization
  until the claim is resolved. For standard channels, Shorts music-library
  tracks may have track-specific 30/60-second limits; some may be available
  for up to 90 seconds in a three-minute Short. A source license or a rights
  decision in ULTRON does not prevent an automated claim or establish that a
  particular track is eligible in the Shorts Audio Library.
- Copyright permission, YouTube account/API authorization, platform-policy
  compliance, recommendation eligibility, and monetization eligibility are
  distinct decisions. YouTube's copyright guidance states that no listed
  option guarantees avoiding a claim or strike. Fair use is a legal question
  that varies by jurisdiction; a license or public-domain label in ULTRON is
  not a platform approval.
- Monetization requires channel-level eligibility and review in the YouTube
  Partner Program, acceptance of applicable terms/modules, and compliance
  with channel and video policies. YouTube's current help pages include
  these full ad-revenue thresholds: 1,000 subscribers and either 4,000
  qualified public watch hours from long-form videos in the previous 12 months
  or 10 million qualified public Shorts views in the previous 90 days. The
  page also lists regional/account requirements, including no active
  Community Guidelines strikes, 2-Step Verification, advanced features, and
  an AdSense for YouTube account. Reaching thresholds does not automatically
  enroll a channel; YouTube reviews the channel and requires applicable
  agreements/modules. Reused-content monetization review is distinct from
  copyright: clips and compilations can be ineligible even when the uploader
  has permission. The July 2025 policy clarification uses **inauthentic
  content** for repetitive or mass-produced content; original, materially
  varied viewer value matters. ULTRON must not predict or claim that a video
  or channel will be recommended or monetized.
- YouTube requires creators to disclose realistic AI-generated or
  meaningfully altered material in specified cases, including realistic
  depictions of real people/events/places and AI-generated music that is the
  video's main focus. The official `videos` resource exposes
  `status.containsSyntheticMedia`, a boolean that can be set in `videos.insert`
  or `videos.update` to disclose realistic altered or synthetic content.
  Human review must decide the value for each exact version; do not infer it
  merely from the use of an AI tool. The Help Center describes disclosure
  during upload and says disclosure alone does not limit audience or
  monetization.
  Minor edits and production assistance such as captions are listed as
  examples that generally do not require disclosure. The boolean does not
  encode every nuance of the Help Center policy; the reviewer decision and
  disclosure basis must be captured in the immutable approved metadata.

### Resumable uploads and reconciliation

- Google's resumable protocol begins with a metadata POST and returns a
  session URI in `Location`; the client then sends media bytes to that URI.
  Store the URI as a secret because it is a bearer-like upload capability.
- After a network interruption, the documented status probe is an empty PUT
  with `Content-Range: bytes */<total-bytes>`. A `308 Resume Incomplete`
  response and optional `Range` identify the accepted byte prefix; honor
  `Retry-After`. Google documents resumable retry after `500`, `502`, `503`,
  and `504` with exponential backoff. A completed session returns the original
  completion response. Sessions expire; `404` means a new session is required
  to upload again, but ULTRON must not start a new session while it cannot
  establish whether the prior operation created a video.
- The protocol can resume bytes but does not provide an application-level
  exactly-once key for creating videos. A lost completion response or expired
  session without a known video ID must remain `UNCERTAIN` for reconciliation
  or operator resolution; do not blindly create another `videos.insert`.
- Once a video ID is known, status reconciliation can query that exact ID.
  The video may remain in platform processing after upload acceptance. An API
  upload response is not proof that processing completed, that the requested
  visibility is in effect, or that monetization is enabled.

## Source register

All sources below are official Google/YouTube documentation. “Supports”
describes only the requirements stated in the cited source, not a legal
conclusion or ULTRON implementation capability.

| Official source title | URL | Accessed | Requirement supported |
|---|---|---|---|
| `videos.insert` — YouTube Data API v3 | <https://developers.google.com/youtube/v3/docs/videos/insert> | 2026-10-24 | Video insertion/upload method, OAuth authorization, resource parts/fields, explicit visibility values, and current unverified-project behavior. The page reports last updated 2026-10-15 UTC. |
| `video` resource — YouTube Data API v3 | <https://developers.google.com/youtube/v3/docs/videos> | 2026-10-24 | `status.privacyStatus`, `status.selfDeclaredMadeForKids`, and `status.containsSyntheticMedia` fields and their API write semantics. |
| Implementing OAuth 2.0 Authorization | <https://developers.google.com/youtube/v3/guides/authentication> | 2026-10-24 | YouTube API user authorization, consent and scope concepts. |
| YouTube Data API — OAuth 2.0 for Server-side Web Applications | <https://developers.google.com/youtube/v3/guides/auth/server-side-web-apps> | 2026-10-24 | Server-side authorization flow, `youtube.upload` scope, refresh/offline access, incremental authorization, redirects, token handling. |
| OAuth 2.0 Best Practices | <https://developers.google.com/identity/protocols/oauth2/resources/best-practices> | 2026-10-24 | Secure client credentials/tokens, revocation/deletion, refresh-token invalidation and incremental authorization. |
| OAuth 2.0 Scopes for Google APIs | <https://developers.google.com/identity/protocols/oauth2/scopes#youtube> | 2026-10-24 | Scope catalog, sensitivity/verification context, use of least scope. |
| Upload a Video | <https://developers.google.com/youtube/v3/guides/uploading_a_video> | 2026-10-24 | API upload guide, resource fields, client-library resumable upload example. |
| Resumable Upload Protocol | <https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol> | 2026-10-24 | Session initiation, session URI, status probe, `308`/`Range`, retryable server errors, session expiry. |
| Quota Costs | <https://developers.google.com/youtube/v3/determine_quota_cost> | 2026-10-24 | Current default quota allocations, `videos.insert` allowance/cost, invalid-call cost and reset semantics. |
| Quota and Compliance Audits | <https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits> | 2026-10-24 | Compliance audit and quota-extension requirements and process. |
| YouTube API Services — Developer Policies | <https://developers.google.com/youtube/terms/developer-policies> | 2026-10-24 | API-client rules, user experience, privacy, API-data restrictions. |
| Complying with YouTube's Developer Policies | <https://developers.google.com/youtube/terms/developer-policies-guide> | 2026-10-24 | User final control over write values and clearly user-initiated YouTube actions. |
| Requirements for Minimum Functionality | <https://developers.google.com/youtube/terms/required-minimum-functionality> | 2026-10-24 | API-client user experience requirements and ongoing policy updates. |
| API Services Terms of Service | <https://developers.google.com/youtube/terms/api-services-terms-of-service> | 2026-10-24 | Binding API Services agreement and change/termination provisions. |
| YouTube Terms of Service Revision History | <https://developers.google.com/youtube/terms/revision-history> | 2026-10-24 | Official policy-change monitoring; last-listed policy revisions include 2026 items. |
| YouTube Shorts: 3-minute Shorts | <https://support.google.com/youtube/answer/15424877?hl=en> | 2026-10-24 | Shorts length/aspect-ratio classification, effective dates, copyright claim block for Shorts over one minute, music limits. |
| YouTube Shorts Monetization Policies | <https://support.google.com/youtube/answer/12504220?hl=en> | 2026-10-24 | Shorts revenue eligibility, accepted module, ineligible engaged views and claimed-content limits. |
| YouTube Channel Monetization Policies | <https://support.google.com/youtube/answer/1311392?hl=en> | 2026-10-24 | Reused and inauthentic content rules, channel-level evaluation and monetization restrictions. |
| YouTube Partner Program overview & eligibility | <https://support.google.com/youtube/answer/72851?hl=en> | 2026-10-24 | YPP account/channel eligibility thresholds, regional and account requirements, review. |
| Disclosing use of altered or synthetic content | <https://support.google.com/youtube/answer/14328491?hl=en> | 2026-10-24 | When realistic AI-generated or meaningfully altered content must be disclosed, examples, and disclosure effect. |
| Copyright on YouTube | <https://support.google.com/youtube/answer/2797466?hl=en> | 2026-10-24 | Copyright owner permissions, derivative-work rights, fair-use caveats and no guarantee against claims. |

## Existing ULTRON architecture inspected

- `ContentRightsService` is the authenticated SQLite application boundary. It
  uses signed `AuthorizationContext`, transactionally records commands and
  audit events, and applies `ContentRightsPolicy`. `RightsAction.PUBLISH`
  exists, but a durable platform policy state can still be `UNKNOWN` and
  publishing authorization defaults to `NOT_AUTHORIZED`.
- `ContentPackageVersion` is immutable by version and includes platform,
  account, artifact SHA-256, metadata SHA-256, metadata, and per-source
  `RightsUse` entries. It requires each source use to include `PUBLISH` and
  match the package platform/account. Creation and review evaluate source
  copyright and platform rights. `ReviewApproval` binds package/version,
  artifact and metadata hashes, platform/account, `PUBLISH`, expiry, nonce,
  and a rights snapshot.
- The current `approve_content_version` implementation checks a reviewer
  allowlist and exact package/version/hash, but also requires the package
  owner to equal the approving principal. It therefore does not establish
  independent approval and does not bind an explicit privacy setting or
  complete publish-intent tuple. The local Mission 12 release approval is a
  separate record/workflow binding the release tuple; the existing review
  approval remains insufficient for external publication.
- `ContentClipRender` records source asset/hash, transcript, candidate,
  source interval, output key/hash/size, output duration, and stream flags.
  The managed storage can read the output only after hash verification.
  A publishing request should use the rendered output as the immutable
  artifact, verify `output_sha256` and byte size again before dispatch, and
  retain the render/candidate/transcript/source IDs as provenance.
- For `YOUTUBE_SHORTS` and `INSTAGRAM_REELS` render targets, the local renderer
  produces a 720x1280 H.264/AAC preview with burned-in captions from the
  persisted highlight transcript. When the authorized source is audio-only,
  it uses a designed local graphic background with a contrast panel and
  progress indicator; it does not generate illustrative footage. Captions
  are word-wrapped without changing their wording, use a high-contrast backing,
  and fail explicitly when a segment exceeds the supported readable layout.
  When source audio exists, the renderer decodes the selected source interval
  and final AAC output as bounded PCM and rejects an output that becomes
  silent while the source interval has a non-zero sample peak. This is a
  signal-preservation check, not a narration-intelligibility, loudness,
  clipping, or transcription-accuracy assessment; those measurements are not
  persisted in the current render record.
  This remains a local derivative-render capability, not a platform publisher
  or evidence of factual accuracy, human release approval, platform
  eligibility, audience performance, or revenue. Other render targets retain
  the existing stream-preserving landscape behavior.
- `ContentClipRenderingService.get_clip_review_manifest` returns an audited,
  owner-authorized review projection for a render. It presents source/hash,
  rights-use, selected transcript/highlight, technical output validation, and
  the explicit human-review/release boundary. The current workflow has no
  persisted script or scene plan, so the manifest identifies that limitation
  rather than implying scene-aware authoring or factual verification.
- Existing `PublicationState` has `NOT_PUBLISHED`, `QUEUED`, `PUBLISHING`,
  `PUBLISHED`, `UNCERTAIN`, `FAILED`, and `TAKEDOWN_REQUIRED`. A package
  version creates a `NOT_PUBLISHED` row only. It remains distinct from the
  implemented local mock-job state machine and is not advanced by mock
  acknowledgment.
- Content database append-only audit and review events and command
  idempotency patterns are suitable foundations. Existing local content
  storage is explicitly not production object storage and is not a token
  vault.

## Proposed additive architecture

### Components and boundaries

1. **Platform-independent `PublishingService`**: authenticated application
   boundary. It creates and reads jobs, checks the kill switch, enforces
   immutable idempotency bindings, revalidates rights/approval immediately
   before an external call, records attempts/events/audit, and accepts
   reconciliation results. It must call the existing rights policy/service
   within the existing transaction/audit model; it must not copy policy
   evaluation rules or accept a caller-provided “rights cleared” boolean.
2. **`PublishingJob` and immutable publish request**: bind package ID/version,
   render ID, output key/hash/size, metadata hash, platform, exact account,
   explicit visibility, target context, intended actions (including
   commercial/monetization intent), synthetic-media disclosure decision,
   authorization/approval ID, rights decision IDs, canonical rights-snapshot
   hash, applicable grant/agreement versions, and idempotency key. The
   approval fingerprint binds the exact package/version, rendered-output
   SHA-256, metadata hash, platform, destination account, visibility, intended
   use/actions, disclosure decision, and rights decision/snapshot. Changing
   any bound value invalidates approval and requires a new approval. A
   versioned canonical request fingerprint and database unique constraints
   enforce a stable idempotency key and prevent a second active or successful
   job for the same immutable target tuple. A concurrent request with the
   same key returns the existing job only when its complete fingerprint
   matches; otherwise it fails deterministically.
3. **Human release approval**: explicitly affirm exact rendered bytes,
   metadata, platform, account/channel, visibility, intended use/actions,
   disclosure, and the rights decision/snapshot; record the approver, request
   principal, approval expiry, and fingerprint. Approval is enforced by the
   application/service layer, never only by the UI. The release approver must
   be a different authorized human from the request principal; the current
   package-review approval is not sufficient. The final external action must
   be a distinct, clearly labeled user confirmation. For visibility that can
   make a video externally viewable, that approval and confirmation are
   required before dispatch. Background execution may only carry out that
   exact confirmed request, not infer consent or schedule autonomous
   publication. Review approval and API account authorization remain separate.
4. **YouTube adapter interface**: transport-only methods for
   begin/resume/status-probe/reconcile/upload-status. The adapter receives
   only an already-authorized immutable request and cannot evaluate or
   override rights, approval, account, visibility, or kill-switch decisions.
   Every dispatch passes through `PublishingService`. A deterministic
   `MockPublishingAdapter` is used in unit tests only. Mission 14 added an
   offline YouTube protocol boundary with injected transport and providers;
   it is not registered with `PublishingService` and has no live transport.
   Do not enable external dispatch until durable YouTube states, a secure
   restart-persistent session store, application-service authorization
   integration, credential handling, API compliance, OAuth scopes, disclosure
   support, and policy gates are implemented and verified together.
5. **Credential provider**: platform/account-bound token lookup and refresh;
   credentials never enter job JSON, audit detail, events, exception text,
   application logs, or dashboard responses. Store refresh tokens in a
   dedicated encrypted secret store with least-privilege access, not in
   `ContentStore` or the local temporary media store. Redact authorization
   headers and resumable session URIs.
6. **Durable audit/state storage**: additive job, attempt, external-resource,
   approval, and transition-event tables with FK constraints, unique
   idempotency, append-only events, and transactional audit writes. Persist
   token-independent error classes, HTTP status/reason codes, quota signals,
   attempt time, upload session state (secret reference only), external video
   ID, observed processing/visibility status, rights decision IDs and
   approval snapshot.
7. **Dispatch transaction and recovery**: in a short transaction immediately
   before each external request, re-read the immutable request, approval,
   rights decisions, and kill switch; append the attempt/audit event and
   commit the dispatch intent before calling the adapter. Never hold a
   database transaction open across network I/O. After commit, a crash before
   or during the call is conservatively `UNCERTAIN` unless authoritative
   remote status proves the result. On restart, workers must reconcile the
   same session/video ID and must not create a new session or insert while
   outcome is uncertain. This is at-least-once local dispatch with
   reconciliation, not exactly-once remote execution; a rights change in the
   unavoidable interval between final preflight and remote acceptance is a
   residual race and must be audited.
8. **Bounded retries and timeouts**: proposed ULTRON safety caps, not
   YouTube requirements: a 60-second timeout per HTTP request; at most five
   automatic retries of the same resumable session within 15 minutes, with
   exponential backoff capped at five minutes and a longer valid
   `Retry-After` deferred only within that total deadline. Do not automatically
   retry an ambiguous session-initiation or completion request. Quota/rate
   limit responses stop upload retries until an explicitly persisted retry
   time or operator action. Processing-status polling is capped at 12
   requests in 24 hours; when exhausted, retain `PROCESSING` and require
   later/manual reconciliation rather than reporting failure. Exhausted
   transport retries become `UNCERTAIN` if remote completion cannot be
   established, otherwise `FAILED` only on an authoritative permanent
   rejection.
9. **Kill switch**: durable operator-controlled global and optionally
   per-account gate checked transactionally before creating a session and
   before each upload/resume chunk. When engaged, prevent new remote requests;
   preserve state and append an audit event. Do not cancel an in-flight HTTP
   request or fabricate a cancellation: record its confirmed response, or
   mark the outcome `UNCERTAIN` and reconcile. Re-enable requires explicit
   operator action and fresh preflight validation.

### Rights and authorization sequence

1. Resolve the authenticated owner, exact package version, successful render,
   candidate/transcript/source links, and managed output bytes. Rehash output
   and compare to both `ContentClipRender.output_sha256` and the package
   artifact hash; reject any mismatch or missing/corrupt output.
2. Validate the immutable version metadata against the final approved title,
   description, audience setting, explicit privacy, and disclosure decision.
   Confirm API account identity matches the approved target account.
3. Require unexpired release approval whose canonical fingerprint exactly
   binds the package/version, rendered-output SHA-256, metadata hash, platform,
   destination account, visibility, intended use/actions, disclosure
   decision, and rights decision IDs/snapshot. A change to any value requires
   new approval. Approval is not OAuth consent, legal permission, or YouTube
   policy approval.
4. Immediately before dispatch, in the application/service layer and a short
   transaction, reload all source assets and grants and evaluate each exact
   source `RightsUse` for the derivative's requested distribution. Confirm
   that the source grant explicitly covers derivative creation where required,
   `RightsAction.PUBLISH`, and the approved commercial/monetization actions,
   platform/account, territory, duration, and source hash. Verify agreement/
   grant validity and that no revocation/expiry or changed rights
   decision/snapshot invalidated approval. The current rights model evaluates
   source-use rights; it does not provide a separate independent derivative
   grant record, so do not claim one exists. Fail closed if copyright/platform
   rights are not cleared, platform compliance is `UNKNOWN`/non-compliant,
   or publishing authorization is not explicitly authorized.
5. Separately confirm the connected YouTube account through API identity
   evidence, valid OAuth token, granted minimal scope, and current project
   API/audit state. Record the account binding without persisting secrets.
6. Revalidate immediately before every network request, including resumed
   chunks; this is mandatory, not best-effort. Revocation or kill-switch
   activation stops subsequent requests. A known-incomplete session becomes
   `BLOCKED` pending review; an ambiguous in-flight result becomes
   `UNCERTAIN`; a confirmed completed upload is recorded as `UPLOADED` and
   then flagged `TAKEDOWN_REQUIRED` for human review. This does not cancel
   bytes already accepted by YouTube or preempt an in-flight request. The
   final-check-to-remote-acceptance interval cannot be made atomic.
7. After upload, rights revocation invalidates future dispatch and records
   `TAKEDOWN_REQUIRED`/manual review for an existing external video. Do not
   promise or perform automatic deletion; API scopes, permission, policy, and
   deletion behavior require separate official verification and human
   authorization.

### State-transition table

| Current state | Event/guard | Next state | Required behavior |
|---|---|---|---|
| `AWAITING_APPROVAL` | Exact human release approval plus explicit publish confirmation | `QUEUED` | Bind approval and idempotency key to exact target tuple. |
| `QUEUED` | Kill switch engaged, rights denied/unknown, stale approval, bad token/account, or hash mismatch | `BLOCKED` | No adapter call; persist reason and audit. `BLOCKED` is a proposed additive state. |
| `QUEUED` | Kill switch disengaged and all preflight checks pass | `UPLOADING` | Create or resume the one persisted resumable session; record attempt. |
| `UPLOADING` | `videos.insert` accepted with confirmed response/video ID | `UPLOADED` | Persist external ID and response facts before any subsequent action. |
| `UPLOADING` | Retryable `5xx`/`308` with authoritative session status | `UPLOADING` | Resume the same session with documented offset/backoff; do not create another video. |
| `UPLOADING` | Lost response, worker crash, ambiguous network outcome, or cannot query session | `UNCERTAIN` | No new session or duplicate insert. Probe/reconcile, or require operator resolution. |
| `UPLOADING` | Rights revoked or kill switch engaged between requests; remote status proves incomplete | `BLOCKED` | Stop subsequent chunks, preserve the same session reference, audit the reason; require fresh approval/preflight before any resumption. If status is ambiguous, use `UNCERTAIN`. |
| `UPLOADING` | Rights revoked after remote completion is confirmed | `UPLOADED` then `TAKEDOWN_REQUIRED` | Preserve the external ID and audit the revocation; stop further action and require human/legal review. |
| `UPLOADING` | Definitive permanent rejection before remote acceptance | `FAILED` | Persist safe error category and quota/authorization result; no secret data. |
| `UPLOADED` | YouTube reports processing in progress | `PROCESSING` | Poll only the known video ID within bounded quota/backoff. |
| `UPLOADED` / `PROCESSING` | Platform reports processing failure or metadata/visibility mismatch | `FAILED` | Preserve video ID and external state; require explicit remediation. |
| `PROCESSING` | Processing complete and verified visibility matches approval | `PUBLISHED` | For public/unlisted, describe exactly the observed privacy; no reach or revenue claim. |
| `PROCESSING` | Private visibility accepted and processing complete | `AVAILABLE_PRIVATE` | Proposed additive state; do not label as publicly published. |
| Any pre-upload state | Human withdraws approval or rights expire before a remote call | `CANCELLED` / `BLOCKED` | No upload. A new attempt requires a new explicit approval. |
| Existing external video | Rights revoked or policy/remediation signal | `TAKEDOWN_REQUIRED` | Freeze further actions; human/legal review. No automatic deletion. |
| `UNCERTAIN` | Session status says incomplete | `UPLOADING` | Resume same session only after fresh auth/rights/kill-switch checks. |
| `UNCERTAIN` | Completion response or known video ID reconciled | `UPLOADED` / `PROCESSING` | Record source of reconciliation and remote status. |
| `UNCERTAIN` | Session expired and no authoritative result/video ID | `UNCERTAIN` | Operator resolution only; never blindly reinsert. |

`BLOCKED`, `AWAITING_APPROVAL`, `CANCELLED`, and `AVAILABLE_PRIVATE` are
proposed additional job states; they should live in a publishing-specific
state model rather than overloading the existing `PublicationState` without
an additive migration and compatibility review.

## Threat and failure analysis

| Risk | Proposed control | Testable acceptance criterion |
|---|---|---|
| Unauthorized publication | Signed context, exact human approval by a principal distinct from the request principal, one-shot preflight, publish rights and platform authorization; no automatic scheduling | No external adapter call when approval is absent/expired, principal/target differs, rights fail, or user confirmation is absent; the service rejects direct adapter dispatch without its authorization decision. |
| Wrong account, visibility, or release tuple | Bind package/version, render SHA-256, metadata, platform/account, explicit visibility, intended use/actions, disclosure, and rights decision snapshot in the approval/job; compare OAuth account evidence before dispatch | Mutating any bound value after approval invalidates it and results in zero upload calls. |
| Duplicate publication | Stable unique job key and exact content tuple; persist one resumable session; never insert a new video for `UNCERTAIN` work | Concurrent/restarted requests produce one job/session; ambiguous completion never triggers a second insert. |
| Credential leakage | Secret-store references only; redact headers, tokens, error bodies and session URIs | Injected secret values do not appear in database job/audit rows, logs, exception messages, or read APIs. |
| Incorrect output association | Rehash managed output; bind render/version/output/metadata hashes and provenance IDs in FK-backed rows | Swapping output, candidate, version, or metadata causes preflight rejection before adapter invocation. |
| Rights expiry/revocation | Reevaluate every source `PUBLISH` use immediately before upload and resumed chunks; approval snapshot expiry bounded by rights expiry | Revoking/expiring any contributing grant before dispatch blocks; revocation during transfer stops subsequent chunks and records uncertainty if completion is unknown. |
| Partial/uncertain upload | Persist resumable session secret reference and confirmed range; probe status; bounded retry/timeout policy; no blind retry | Simulate dropped `201`, `308`, retryable `5xx`, `404` expired session, request timeout and restart; enforce retry/deadline caps and no second insert for an unresolved outcome. |
| API quota exhaustion | Durable quota error classification, bounded exponential backoff/`Retry-After`, circuit-break uploads, no tight loops | Simulated quota response does not retry immediately or lose the failure; counters and retry-at time are auditable. |
| Platform policy change | Version official policy references, startup/release gate for stale policy verification, monitor official revision history | A stale/unknown policy version blocks new uploads and records which source review is due. |
| False monetization/recommendation claim | Do not calculate eligibility from upload response; surface `UNKNOWN` absent verified creator-specific status | API/dashboard must not emit “monetized”, revenue or recommendation guarantees based only on rights, upload, or Shorts classification. |
| AI disclosure omitted | Human classification on exact approved version; map the decision to `status.containsSyntheticMedia` and verify the outgoing request | A required disclosure is serialized as `true`; a missing or mismatched field blocks the real adapter before any upload call. |
| OAuth revoked during upload | Stop future chunks, mark unresolved result uncertain, require reauthorization and reconcile same session | Revocation never silently refreshes with stale credentials or starts a new session; external state remains auditable. |
| Kill switch during upload | Check the durable gate before every request and preserve in-flight uncertainty | No request starts after the gate is observed; a response already in flight is recorded or reconciled without falsely marking cancellation. |
| Rights revoked after publication | Mark manual review/takedown required and preserve remote ID; no automatic deletion | Revocation creates a remediation record and blocks new work; no deletion occurs without separate verified scope and approval. |

## Unresolved questions and implementation gates

1. What audit/compliance status and quota allocation apply to the eventual
   YouTube API project? Unknown; no project was inspected. The current
   `videos.insert` reference says unverified projects are not restricted to
   private viewing, while audit requirements for additional quota and periodic
   audits remain applicable.
2. What Google OAuth consent-screen verification and user-facing disclosures
   apply to the production OAuth client and its classified scopes? Unknown;
   no credentials or Cloud Console settings exist in this task.
3. Does the selected API client library serialize
   `status.containsSyntheticMedia` correctly, and does the reviewer workflow
   map each disclosure-required case to the API's boolean? The official API
   field is verified; the future adapter serialization and classification
   still require tests and review.
4. Can the selected API methods reliably confirm the authenticated channel,
   final privacy, processing state, and a lost `videos.insert` outcome within
   quota? Verify exact `channels.list`, `videos.list`, and status fields before
   wiring an operational adapter.
5. Which configured role grants the independent release-approver capability
   remains a deployment decision. The invariant is fixed: the release
   approver must differ from the request principal, and the current
   package-review approval (whose implementation requires the package owner
   to approve) cannot be reused as external release approval.
6. What duration/territory rules should govern rights for a video whose
   metadata or audience setting changes? These must be encoded in the
   per-source `RightsUse`, not inferred from the platform adapter.
7. Deletion/takedown API scopes, requirements, timing, and behavior are not
   verified. No automatic takedown is designed.
8. Platform monetization and recommendation decisions are opaque and
   changeable; no per-video earning guarantee can be offered.
9. API quota allocations, audit requirements, OAuth scope sensitivity, and
   platform rules are time-dependent. Recheck official documentation and
   revision history before release.
10. Shorts classification requirements are documented, but the applicable
    API codec/container, resolution, bitrate, and maximum-file-size profile
    remains unverified. Confirm it against official upload/API documentation
    before fixing Mission 12's output-profile validation.

## Mission 12–14 implementation status and remaining work

1. **Mission 12 local foundation implemented:** domain state/request/approval
   records, additive SQLite schema, immutable release binding, authenticated
   service authorization, existing rights-policy reuse, audit events,
   managed-output verification, kill switch and mock-only adapter are present.
   Tests cover rights denial/revocation, approval independence, output hashes,
   duplicate identities, state transitions, failure/uncertainty, kill switch
   behavior and restart recovery. This is not a real adapter and not a claim
   of remote correctness.
2. **Mission 13 reliability and recovery accepted with limitations:** local
   interruption, idempotency, audit and uncertain-outcome recovery protections
   apply to mock dispatch only. They do not prove exactly-once remote effects.
3. Verify the official API request fields/scopes, OAuth verification
   obligations, API project audit/quota status, synthetic disclosure
   field behavior in the selected client library, quota policies, and account
   identity proof. Resolve the open items above in a dated implementation note
   before any credential is configured.
4. **Mission 14 offline boundary implemented with limitations:** request
   construction, fake-transport protocol handling, progress state and status
   parsing have focused tests. No real transport, secure session store,
   durable YouTube progress state, service-layer registration, or OAuth
   integration exists. The mock adapter remains the only registered adapter.
5. Any later operational integration requires those storage, state machine,
   authorization, credential, policy and compliance gaps to be closed and
   independently reviewed first. Obtain an explicit human go/no-go before any
   real external request. No live publishing, credentials, account changes, or
   uploads occurred during this mission.

## Validation boundary

This is documentation-only. The official documents above were read directly;
no media, credentials, OAuth flows, account settings, API endpoints, or
publishing actions were exercised. Repository status could not be inventoried
with Git because this workspace directory has no Git metadata. No source code
or test behavior is changed by this design.

## Mission 22B durable publishing attempts

The current implementation adds a durable attempt repository without adding
an operational publisher. Existing schema-v7 remains the ordinary startup
schema. `ContentStore` accepts an explicit `migrate_publishing_attempt_schema`
operation that upgrades an exact v7 database through schema-v8 to schema-v9;
constructor startup does not run either migration. The v8 schema adds immutable attempt
identity, opaque upload-secret references and append-only events. It stores no
session URI, OAuth credential, authorization header or provider response body.
Attempt identity and release/account bindings are derived from the persisted
owned job, approved release and successful render; the attempt UUID is created
inside the trusted service transaction.

Attempt creation is routed through `PublishingService.create_attempt`, which
rechecks owner, exact independent approval and expiry, rendered-output
integrity, fresh rights, and the existing kill switch before committing the
attempt and audit event. The exact-type `MockPublishingAdapter` guard and
existing mock job state machine are unchanged. No attempt transition sends a
network request; no OAuth, secure Secret Service backend, live-publishing
control, or remote YouTube account verification is implemented.

The attempt state machine distinguishes initiation uncertainty, same-session
reconciliation, remote upload, processing, public publication, private
availability and confirmed failure. A timeout is not failure. `FAILED` with a
provider rejection is retryable only when the rejection follows the persisted
initiation intent; processing failure does not establish that the remote upload
was absent. Processing failure is recorded as `RECONCILIATION_REQUIRED` with
`REMOTE_OUTCOME_UNKNOWN`, which remains unresolved and blocks another attempt.
Other failed attempts also remain unresolved unless safe retry evidence or an
audited operator disposition allows replacement. Publication states require
their corresponding evidence code. Only a separately authorized operator can
mark a blocked attempt `safe_to_replace`. Confirmed offsets are monotonic and
bounded by the immutable rendered byte size.

After a process restart, a persisted initiation intent becomes
`INITIATION_UNCERTAIN`; a ready or uploading attempt with an active opaque
reference becomes `RECONCILIATION_REQUIRED`, preserving its attempt ID and
last server-confirmed offset. If the database reference is missing, startup
sets `OPERATOR_ACTION_REQUIRED`. Startup never creates a session or retries
an external action. A database reference alone cannot prove that the secure
backend is unlocked or still contains the corresponding secret; a real
secure-store integration must fail closed and route that condition to
operator action.

Migration, state constraints, concurrency, restart recovery, append-only
events, idempotency and failure rollback are covered with disposable SQLite
databases. These tests establish local persistence behavior only. No
production database was opened or migrated, no production credential or
keyring was accessed, and no external API or live publishing operation was
performed. This work does not establish production readiness or remote
exactly-once behavior.

## Mission 23A safe retry correction

Schema-v9 tightens replacement eligibility. The unresolved-attempt index now
keeps failed attempts unresolved except those with a provider rejection
recorded from `INITIATION_INTENT`; the v9 migration verifies the rejection
event before treating it as safely retryable. A database trigger and the
repository transition check prevent `PROCESSING_FAILED_CONFIRMED` from
entering `FAILED`, and only allow confirmed failure codes from their expected
source states. Ambiguous or post-upload processing outcomes must remain in
reconciliation and cannot be cleared by a generic failure transition.

Existing v8 rows marked failed for processing failure cause the explicit v9
migration to fail closed for operator review rather than becoming retryable;
the same applies to any failed row without verifiable pre-upload rejection
evidence. The migration also installs the revised transitions from a
post-upload processing state into reconciliation.
The ordinary startup schema stays v7, and attempt APIs require explicitly
migrated schema-v9. Safe-replacement resolution now validates the signed
operator authorization context before recording its audit event. These
changes do not add transport or live publishing.

## Mission 23B authenticated mutation boundary

Attempt creation remains available only through `PublishingService.create_attempt`.
The application-facing attempt transition is now the fixed-purpose
`PublishingService.record_attempt_initiation_intent`: it accepts an attempt ID
and expected revision, but no caller-selected destination state, evidence code,
actor, or provider result. It consumes a signed owner-bound authorization grant
and checks the attempt/job/release binding, the current exact independent
approval and expiry, fresh rights, and the publishing kill switch in the
authorized database transaction before recording initiation intent.

Operator safe-replacement recovery remains available only through
`PublishingService.resolve_blocked_attempt_for_safe_replacement`. It requires an
operator principal, a signed grant bound to the exact attempt, expected
revision, resolution action, and bounded reason. The reason is included in the
authorization fingerprint and persisted authorized-audit details; the attempt
resolution event itself records the reason code without copying secret or
provider data.

Direct `ContentStore` attempt mutation primitives are underscore-prefixed
repository internals and are not used by application routes. This is an
in-process visibility convention, not a Python sandbox or a defense against
arbitrary code execution. Database constraints, transactional validation, and
authorization checks remain in place.

Confirmed-offset updates, remote outcome transitions, and upload-secret
reference lifecycle changes intentionally have no application-service
operation. The repository has neither a provider-evidence verifier nor a real
secure-store backend, so caller-supplied offsets, state labels, or secret-store
claims cannot establish provider confirmation or secret availability. These
operations remain unavailable until those trust interfaces are designed and
implemented. No session URI, token, secret, or provider response is exposed in
logs, audit details, or dashboard payloads. No live transport or dispatch path
was added; the exact-type mock adapter guard and existing mock publishing
state machine remain unchanged.

## Mission 23D–23E authorization and persistence hardening

`ContentRightsService._authorize` now returns an opaque
`ValidatedAuthorization` capability only after the configured
`WebSocketAuthenticator` validates the active session, action, operation and
resource binding. The store records the authenticator instances configured by
the content services; `PublishingService` seals that authority set after its
owner/reviewer/operator services are assembled. Later service instances are
accepted only when they share a configured authentication credential. The
capability factory rejects non-`WebSocketAuthenticator` authorities, and
`ContentStore.run_authorized` requires a capability from a configured
authority, revalidates its active session immediately before the transaction,
and checks that the exact action and payload match. Passing an
`AuthorizationContext` directly, including one with a forged signature, is
rejected before the operation callback or audit write can run. Service call
sites continue to use the same signed-context flow; this capability is an
in-process boundary and does not defend against arbitrary code execution,
credential theft, or tampering with private Python internals.

Explicit schema-v9 verification now compares the SQL definitions of required
attempt tables, indexes and triggers to the definitions generated by the
current schema code, in addition to checking object presence, SQLite
integrity, and foreign keys. Same-named altered definitions therefore fail
closed when the explicit migration/verification operation runs. Ordinary
startup still does not run this migration.

Disposable-database tests now exercise attempt creation and stale transition
contention across independent SQLite connections, concurrent idempotent
replays, idempotency-key conflicts, and attempt uniqueness across independent
processes. Process-level coverage exercises the repository transaction and
database constraints, not a live provider integration.

## Mission 27 original-script local preview

An authenticated operator can submit an original script and up to 12
credential-free HTTPS reference URLs through the existing WebSocket gateway.
The exact script text, its SHA-256, supplied reference labels/URLs, preview
artifact hash, bounded MP4 bytes, and append-only lifecycle events are stored
in additive CRE tables scoped to the authenticated principal. Reusing a
correlation ID with the same request returns the same artifact; reuse with
different input is rejected, and a failed render is not retried automatically.
The authenticated UI can display the local artifact and its review manifest.

This is a **visual-only script preview**, not a substitute for the
source-media/highlight pipeline. It renders the submitted wording as captions
over a local graphic background and includes no audio, imported media,
transcription, or generated factual claims. Supplied references remain
`UNVERIFIED`; factual review is `REQUIRED`, human review is `PENDING`, rights
are `NOT_EVALUATED`, approval is `NOT_GRANTED`, and publication is
`NOT_PUBLISHED`. The script path does not create source assets or rights
decisions and does not call any publishing adapter. Rendering is local and
requires FFmpeg/FFprobe; missing or failed tools return an explicit failure
and append a failed lifecycle event. Artifacts are capped at 4 MiB and are
persisted in the existing local CRE SQLite database, so operators should
retain normal filesystem protection and backup controls for that database.
This narrow path does not make media-based rendering, publishing, analytics,
platform eligibility, or profitability available.

## Mission 28 local narrated script preview

The authenticated original-script path now assembles a short narrated local
preview using the already installed FFmpeg `libflite` filter and the `slt`
English voice. It creates a deterministic scene plan from bounded excerpts of
the exact stored script, renders locally generated diagram cards and burned-in
captions, synthesizes each scene locally, and combines the measured WAV
segments into a portrait MP4. No cloud voice service, voice-cloning model,
source-media import, or ULTRON voice subsystem is used. Pillow is declared in
`requirements.txt` for local diagram generation; it was already present in the
workspace environment and was not installed during this work. The host must
provide FFmpeg/FFprobe built with `libflite` and the required local font.

Narrated previews accept at most 400 ASCII characters and four scenes, with a
maximum of 96 source characters per scene. These limits keep the workflow
bounded and reflect the installed Flite voice's English/ASCII constraints;
unsupported text fails the recorded request rather than being transliterated
or silently altered. Scene boundaries preserve source offsets and excerpts.
Numeric callouts, comparison/sequence layouts, and concept-map labels are
derived only from submitted script text; they are not evidence that its claims
or numbers are accurate.

The persisted `PREVIEW_RENDERED` lifecycle event contains a manifest with the
script and scene-plan hashes, per-scene excerpt offsets and hashes, diagram
hashes, measured scene/audio durations, local synthesis settings, audio signal
measurements, probed MP4 codecs/dimensions/timing, and technical QA results.
The renderer checks for a non-silent bounded-clipping narration signal,
portrait 720x1280 H.264/AAC output, audio/video timing, and a complete FFmpeg
decode. Captions are timed to measured per-scene WAV segment boundaries, not
word-level alignment.

This is **technical preview readiness only**. Signal measurements do not
establish voice naturalness or intelligibility; visual readability, factual
accuracy, rights, approval, publishing, platform eligibility, audience
response, and revenue are not inferred. At render time references are
`UNVERIFIED`, factual review is `REQUIRED`, human review is `PENDING`, rights
are `NOT_EVALUATED`, approval is `NOT_GRANTED`, and publication is
`NOT_PUBLISHED`. An authenticated operator can now append an artifact-bound
self-review as described under Mission 29 below. The local operating-system
Flite package notice grants permissive use/distribution rights subject to its
retained notices and conditions; operators must review applicable
package/voice terms before external commercial release. No publishing,
analytics, audience, or revenue data is fabricated, and live publishing
remains mock-only and disabled.

Mission 28 validation in this checkout:

- `.venv/bin/python -m pytest -q tests/application tests/interface/test_interface_gateway_cre_status.py`
  — **255 passed**.
- `cd interface && npm test` — **124 passed, 0 failed, 0 skipped**.
- Pylance diagnostics — no issues in the changed Python or JavaScript files.

The end-to-end test generated and fully decoded a two-scene narrated MP4,
probed its 720x1280 H.264/AAC streams and timing, checked non-silent audio, and
sampled different frames across the scene boundary. These are technical checks,
not a human assessment of speech quality or an approval to publish.

## Mission 29 narration, caption, and editorial checks

The production manifest is validated before persistence and again before the
artifact is returned. It binds the UTF-8 script hash, deterministic scene-plan
hash, each scene excerpt and source offsets, caption text and offsets, and the
MP4 hash and byte count. A response fails closed if any of those values do not
match the persisted script or artifact. Scene diagrams must decode as 720x1280
PNG files before assembly. Scenes are capped at 96 source characters so
measured caption lines fit the declared layout.

Caption wrapping uses the same installed DejaVu Sans family as the ASS style
and Pillow font metrics at 36 px. Each caption is limited to 36 characters per
line and four lines, a 592 px maximum width, 64 px side margins, and a 220 px
bottom margin. Timing remains measured per scene, phrase-level rather than
word-aligned. Machine checks do not establish contrast, comprehension, or
readability on every target device.

For each locally synthesized scene, objective checks require PCM RMS of at
least -45 dBFS, peak at or below -1 dBFS, and a clipped-sample ratio no greater
than 0.001. The manifest reports the aggregate clipped-sample count, sample
count, and ratio, and readback validation checks that they agree and remain
within the threshold. It also reports the fraction of 100 ms windows below
-50 dBFS without treating natural pauses as a quality score. The encoded MP4
is decoded again for RMS and scene-boundary checks: a 50 ms window centered
within each intended 150 ms silent transition must remain at or below -40 dBFS.
These are bounded signal/layout checks only; speech intelligibility,
naturalness, voice suitability, factual accuracy, and rights are not inferred.

Authenticated operators can append an editorial review tied to the exact MP4
hash. The current route records the artifact owner's self-review, its
recommendation, subjective narration/pacing/caption/scene assessments, notes,
and an optional explicit factual-review attestation. Review rows are
append-only and idempotent. An attestation is reported as
`OPERATOR_ATTESTED_COMPLETE`, not as independent verification. The route
cannot change rights evaluation, grant approval, or publish; those states
remain `NOT_EVALUATED`, `NOT_GRANTED`, and `NOT_PUBLISHED`. It does not replace
the existing authorized approval workflow.

Mission 29 validation in this checkout:

- `/home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python -m pytest -q tests/application tests/interface/test_interface_gateway_cre_status.py`
  — **262 passed** in 125.23 seconds.
- `cd interface && npm test` — **125 passed, 0 failed, 0 skipped**.
- Pylance diagnostics — no issues in the changed Python files.
- A four-scene, 720x1280 local MP4 was fully decoded; encoded audio RMS and
  all three intentional silence windows passed. A rendered frame was visually
  inspected after correcting the caption/diagram-footer overlap.

These checks do not establish speech intelligibility, factual accuracy, rights
clearance, approval, publishing readiness, platform eligibility, audience
response, or revenue. Live publishing remains mock-only and disabled.

## Mission 29.1 durable narration evidence

Each newly rendered preview retains the combined source narration WAV in an
owner-scoped row in the existing CRE SQLite database. The WAV is not returned
by the API or dashboard. It is limited to mono, 16-bit, 24 kHz PCM, at most
54 seconds and 2,592,128 bytes; an owner's retained evidence is limited to
64 MiB. This retains the renderer's scene-boundary silence as part of the
source being measured. The MP4 remains separately bound by its SHA-256 hash.

On every authenticated evidence-list or preview read, CRE hashes the retained
WAV, independently parses and measures its PCM, and compares the WAV hash,
preview/script/artifact bindings, evidence identity, sample count, clipped
sample count, and clipping ratio with both the immutable evidence row and the
persisted render manifest. The versioned algorithm is
`PCM_S16LE_MONO_24000_CLIP_32760_V1`; a sample is classified as clipped at an
absolute PCM16 value of 32760 or greater. The resulting evidence status and
check time are refreshed on read; stored measurement values are not treated as
proof by themselves. Immutable-field and delete triggers protect evidence
rows, and persistence failure marks the preview failed rather than returning a
successful preview without evidence.

The authenticated CRE status response and dashboard expose owner-scoped
evidence summaries without exposing WAV bytes. Statuses distinguish
`VERIFIED`, `INTEGRITY_FAILURE`, `MISSING`, and `UNVERIFIABLE`. Evidence
integrity failure or missing evidence prevents technical QA from remaining
passed. Pre-evidence legacy previews are explicitly `UNVERIFIABLE`; missing
historical measurements are not reconstructed or presented as verified. A
stale dashboard snapshot is labeled stale rather than represented as a fresh
verification.

The response copy of the production manifest labels `audioEvidence.status` as
the current readback result, sets `statusScope` to `CURRENT_READBACK`, and
preserves the persisted render-time label separately as `renderTimeStatus`.
The append-only render event is not changed. An additive immutable
`audio_evidence_required` preview marker distinguishes demonstrated
pre-evidence legacy rows from newer rows whose evidence declaration or row is
missing. Startup backfills that marker from existing render manifests and
evidence rows; a demonstrated legacy manifest without an evidence declaration
remains `UNVERIFIABLE`, while a removed declaration or row for a newer preview
is not silently treated as legacy.

This evidence establishes only a reproducible source-audio signal check and
its recorded bindings. It does not prove speech naturalness, intelligibility,
caption synchronization, factual accuracy, editorial quality, rights, or
approval. It does not publish content or provide audience analytics,
monetization eligibility, or revenue data. Live publishing remains disabled
and mock-only.

Mission 29.1 validation in this checkout:

- `/home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python -m pytest -q tests/application tests/interface/test_interface_gateway_cre_status.py`
  — **274 passed**.
- `cd interface && npm test` — **125 passed, 0 failed, 0 skipped**.

Mission 29.1A closure-hardening validation:

- `/home/ultron/Projects/ULTRON_INTERFACE_FINAL/.venv/bin/python -m pytest -q tests/application tests/interface/test_interface_gateway_cre_status.py`
  — **283 passed, 0 failed, 0 skipped**.
- `cd interface && npm test` — **130 passed, 0 failed, 0 skipped**.
- Pylance reported no diagnostics or syntax errors in `script_preview.py`.
- No human listened to the generated narration as part of this validation.

## Mission 30B deterministic scene contract

The local script planner emits schema version 2 (`deterministic-heuristic-v2`)
while retaining the prior scene ID, order, source offsets, excerpt, visual type,
and labels. Added fields record a heuristic narrative role, the exact excerpt as
the key-message source, a supported visual strategy and source-only visual
instructions, stable narration/caption references, and explicit absence of
planned timing or generated assets. Scene excerpt hashes use UTF-8 bytes. A
canonical serialized plan and `sceneContractSha256` bind this expanded contract;
the existing `scenePlanSha256` remains unchanged for persisted-preview
compatibility.

Sentence and length segmentation is unchanged. It intentionally trims
whitespace at scene boundaries and permits only whitespace between or outside
the exact source spans; no non-whitespace source text may be omitted or
overlapped. Narrative roles are deterministic keyword/syntax heuristics, not
deep semantic analysis or factual verification. The planner chooses only the
existing static comparison, process, numerical-callout, or concept-map
renderer strategies. Other known strategies are explicitly marked unsupported
and rejected by the local diagram renderer. Rendered manifests distinguish
planned asset absence from generated PNG assets that passed existing technical
format/dimension/decode checks; measured narration and caption timing remains
render-time data, not invented planner timing.

No database schema change was needed: the versioned contract is included in the
existing immutable render manifest. Legacy manifests without the version marker
continue through the existing validation path. The authenticated preview
provider validates the additive contract when present; no status endpoint or
dashboard layout was added. Human review, rights, approval, factual review, and
mock-only publishing states remain independent of scene classification and
technical rendering.

## Mission 30C scene visual production and evidence

The existing Pillow scene renderer now uses the planned narrative role and
source-derived labels to choose distinct comparison, timeline, question/answer,
conclusion, cause/effect, numerical, process, or explanatory compositions.
Comparison phrases and process steps are wrapped to fit their cards; layouts
that cannot fit within the bounded card area fail rather than returning a
clipped composition. Timeline years are ordered numerically, but the event
association remains explicitly review-required. These deterministic layouts
are not semantic verification or AI-generated footage.

Each generated scene visual is a fixed 720x1280 PNG. PNG bytes, their SHA-256,
scene and source bindings, dimensions, renderer identity, generation time, safe
area, and technical validation status are retained in the existing immutable
`PREVIEW_RENDERED` manifest. No database migration was needed. A visual is
limited to 512 KiB and all visuals in one preview to 2 MiB. Readback verifies
the stored manifest, source/scene bindings, hashes, format, dimensions, decode,
and declared safe area; it does not establish artistic quality or factual
correctness.

Current visual evidence is reported as `VERIFIED`, `MISSING`, or
`INTEGRITY_FAILURE`. A historical preview without retained visual evidence is
`MISSING`, never currently verified; malformed or mismatched evidence also
prevents technical QA from remaining passed. The authenticated preview response
includes persisted image data for the existing preview gallery, while
owner-scoped CRE status exposes only scene/evidence metadata. The dashboard
labels the current state and surfaces image-load failures. Human review,
factual review, rights clearance, approval, and mock-only publishing remain
separate gates and are not inferred from successful rendering.
