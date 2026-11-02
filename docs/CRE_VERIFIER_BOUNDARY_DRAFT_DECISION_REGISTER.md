# CRE Verifier Boundary — Draft Decision Register and Engineering-Readiness Plan

**Status: DRAFT / NO-GO — ORGANIZATIONAL DECISIONS PENDING**

This document is planning and design support only. It does not modify the migration packet,
the operational database, service configuration, or migration state. It does not assign
organizational owners, approve trust roots, authenticate approvals, collect production
evidence, authorize execution, or establish migration readiness.

The current packet state remains `INCOMPLETE`, `independentlyVerifiedCount: 0`, and
`executionAuthorization: "NOT_GRANTED"` as required by the production migration runbook.
Structural packet representation is not authenticated approval.

## 1. Draft V-01 through V-12 decision register

All decisions below are `PENDING` unless their repository authority is explicit. The repository
identifies no accountable person or organizational control, so every accountable role remains
`UNASSIGNED`. Repository structural packet-schema support is recorded only where it exists and
does not change a decision status.

| ID | Required decision | Accountable role | Status | Evidence needed | Blocker |
|---|---|---|---|---|---|
| V-01 | Designate who may establish and change verifier trust roots, through an organization-controlled bootstrap channel independent of evidence and the packet manifest. | Security/trust authority — `UNASSIGNED` | `PENDING` | Authoritative, versioned trust policy; trust-store provisioning channel; named approvers; change-control record; bootstrap and verification procedure. | Independent verification and trust-configuration implementation cannot proceed. |
| V-02 | Define verifier-key custody, access control, rotation, overlap, compromise handling, revocation, and maximum revocation-information age. | Security/key custodian — `UNASSIGNED` | `PENDING` | Approved key-management and revocation policy; roles; lifecycle audit requirements; recovery procedure; stale-revocation behavior. | Trust configuration, key acceptance, and verification cannot proceed. |
| V-03 | Define human-approver authentication, role-to-action authority, withdrawal, and expiry. | Security/data approval authority — `UNASSIGNED` | `PENDING` | Approved identity and approval-policy specification; authoritative approval record; permitted action scopes; expiry/revocation handling; audit references. | Human approvals cannot be treated as verified or satisfy action gates. |
| V-04 | Define evidence-gatherer, verifier operator, approver, executor roles and prohibited combinations. | Migration approver and security authority — `UNASSIGNED` | `PENDING` | Approved segregation-of-duties matrix; independent-review workflow; enforcement and audit evidence. | Independent review and approval authentication cannot be established. |
| V-05 | Define verifier coverage and authoritative sources for local schema/recovery checks, backup checks, host process/descriptor checks, restart sources, and writer-inventory completeness. | Repository engineering, deployment/host operator, and security — `UNASSIGNED` | `PENDING` | Signed coverage matrix identifying claim, verifier boundary, source of truth, limits, external blind spots, and required human attestation. | Claims cannot be marked `VERIFIED`; writer completeness cannot be established. |
| V-06 | Define maintenance-window identity, approved communication, challenge/nonce issuance, one-use replay ledger, and recovery behavior. | Deployment/host operator and migration approver — `UNASSIGNED` | `PENDING` | Approved maintenance-window procedure; unique window reference; time bounds; nonce ownership; consumption and replay rejection procedure; audit behavior. | Evidence and approvals cannot be bound safely to a window or rejected after replay. |
| V-07 | Define trusted time, timezone, freshness, future-time handling, and expiry relationships among capture, verifier, approval, and packet times. | Security authority and deployment/host operator — `UNASSIGNED` | `PENDING` | Approved clock/time and freshness policy; authoritative clock sources; skew bounds; maximum evidence/revocation age; future/incomparable handling. | Freshness and cross-system validity cannot be determined. |
| V-08 | Define retention, access, privacy, audit, and disposal for evidence, signatures, approvals, verifier results, trust policy, and revocation records. | Security/data owner — `UNASSIGNED` | `PENDING` | Approved retention/access policy; record classes; authorized readers; protected storage; privacy review; disposal requirements. | Verifier output and retained signed records cannot be accepted safely. |
| V-09 | Define fail-closed behavior for verifier, identity/approval system, trust store, revocation, and time-source outages or uncertainty. | Security authority and service owner — `UNASSIGNED` | `PENDING` | Approved failure-mode policy; unavailable/indeterminate dependency behavior; bounded authenticated/offline cache policy if any; freshness and recovery procedure. | Outages cannot be prevented from yielding positive or fail-open results. |
| V-10 | Define independent-verification result semantics and per-category successful-check requirements, including exact-byte digest, source identity, scope, database, window, validity, limitations, and audit fields. | Repository engineering and security — `UNASSIGNED` | `PENDING` | Reviewed evidence-to-check specification; category contracts; independently reviewed binding specification; negative-test acceptance criteria. | `independentlyVerifiedCount` cannot advance or a packet cannot become `READY_FOR_OPERATOR_REVIEW`. |
| V-11 | Define missing/non-required evidence policy and whether any category may be `NOT_APPLICABLE`. | Migration approver and security/data owner — `UNASSIGNED` | `PENDING` | Explicit category policy; authorized decision role; reason and scope requirements; audit representation. Until decided, all current required categories remain required. | N/A status, waiver, or category removal cannot be considered. |
| V-12 | Define the review-only `READY_FOR_OPERATOR_REVIEW` state and preserve separation from execution authorization. | Migration approver and security authority — `UNASSIGNED` | `PENDING` | Approved state-transition and user-facing-language policy; permanent review-only semantics; separate action approvals; `executionAuthorization: "NOT_GRANTED"`. | Packet state behavior cannot change without risk of implying migration authorization. |

## 2. Current repository-authoritative structural fact

The existing packet schema structurally represents the following as separate categories:

- backup-output creation;
- backup destination/privacy/retention;
- v7→v9 migration;
- v9→v10 migration;
- rollback restore;
- gateway control;
- external-writer control; and
- cutover.

This is complete only as structural representation. It does **not** authenticate approval identities,
authority, signatures, validity, or independence. These organizational decisions remain
`UNASSIGNED`, and the structural packet-schema item remains distinct from authenticated approval.

## 3. Verifier-boundary implementation-readiness checklist

The following preserves the runbook's meaning. A completed item is evidence of readiness only;
it is not production readiness, migration approval, or execution authorization.

- [ ] Authoritative owners/roles are assigned for all currently `UNASSIGNED`
  decisions; no person or team is inferred from repository authorship.
- [ ] Trust-root bootstrap, protected custody, rotation, revocation, and
  stale-revocation behavior are approved.
- [ ] Approver identity, scope/role policy, revocation, and separation of
  duties are approved for each action.
- [x] Packet schema structurally represents backup-output creation separately
  from destination/privacy/retention, both migration stages separately,
  restore separately from cutover, and gateway control separately from
  external-writer controls. This does not authenticate approval identities,
  authority, or signatures; future verifier work must do so without treating
  structural validity as approval.
- [ ] Coverage matrix states what local verifier, external trusted system,
  and human operator each can and cannot establish.
- [ ] Window identity, one-use challenge/replay protection, trusted time,
  freshness, and expiry semantics are approved.
- [ ] Privacy, audit retention, access, and disposal requirements are
  approved.
- [ ] Typed status semantics and fail-closed outage behavior are approved;
  no JSON field can self-promote evidence or approvals.
- [ ] Exact-byte digest binding and category/scope/database/window binding
  are specified and independently reviewed.
- [ ] Negative tests cover forged/unknown/revoked keys, signature mismatch,
  altered bytes, replayed approval/challenge, wrong database/window/scope,
  expired verification, verifier/revocation outage, and self-asserted
  `VERIFIED`/`APPROVED` claims.
- [ ] User-facing packet language preserves review-only status and
  `executionAuthorization: "NOT_GRANTED"`; action-specific approvals remain
  distinct.

## 4. Proposed engineering/security review agenda

### 4.1 Verification contract by category

Review and approve, separately for each category, the following contract elements:

1. The exact claim being asserted, including explicit limitations.
2. The evidence source and expected representation.
3. Required exact-byte digest and canonicalization assumptions.
4. Required source/process identity and verifier identity.
5. Required integrity, schema, state, and relationship checks.
6. Successful, failed, incomplete, stale, blocked, and unavailable outcomes.
7. Required human attestation versus locally observable evidence.
8. Evidence validity interval and required freshness source.
9. Audit fields produced by the verifier and evidence-to-claim traceability.

Category contracts should cover backup output, backup destination/privacy/retention, v7→v9,
v9→v10, rollback restore, gateway control, external-writer control, cutover, and production
schema/identity evidence without conflating any approval or independent verification result.

### 4.2 Threat model

Review the following adversary and control scenarios:

- Evidence alteration between capture, storage, submission, and verification.
- Packet or evidence JSON self-asserting `VERIFIED` or `APPROVED`.
- Forged, unknown, expired, revoked, or reused verifier/approval keys.
- Replay of evidence, approvals, challenges, nonce tokens, or whole packets.
- Wrong database, backup, maintenance window, action, scope, schema, or release.
- Trust-root substitution, self-signing, or trust bootstrap through the packet.
- Verifier, identity-provider, clock, or revocation-service outage.
- Stale authenticated cache, clock rollback/future timestamps, and fresh-time skew.
- Partial writer inventory, missed host/process sources, or external/remote writers.
- Bypass through producer or packet fields that can self-promote packet state.
- Partial implementation, interrupted migration evidence, inconsistent sidecars,
  and ambiguous rollback identity.

### 4.3 State semantics

Agree and document:

- Distinct states for supplied, structurally valid, independently verified,
approved, blocked, incomplete, stale, and non-applicable evidence only if
organizational policy permits the state.
- The invariant that independently verified evidence does not authenticate approval.
- The invariant that approval does not imply execution authorization.
- The invariant that `READY_FOR_OPERATOR_REVIEW` is review-only.
- The invariant that the packet must preserve `INCOMPLETE`,
`independentlyVerifiedCount: 0`, and `executionAuthorization: "NOT_GRANTED"`
until authenticated organizational policy permits each transition.
- Fail-closed behavior for missing dependencies, unresolved checks, and contradictory evidence.

### 4.4 Scope, database, and maintenance-window bindings

Independently review bindings for:

- Exact evidence bytes and digest.
- Evidence category and claim identity.
- Exact database identity, path resolution if approved, and schema identity.
- Release/archive identity when relevant.
- Maintenance-window identifier and authorized time bounds.
- Challenge/nonce one-use ledger identity.
- Action scope, including each migration stage and restore/control/cutover separation.
- Approval identity, authority, action, expiry/revocation, and scope.

### 4.5 Negative-test plan

Require the following tests before implementation review closes:

1. Forged, unknown, and revoked signing identities.
2. Signature mismatch and altered canonical/evidence bytes.
3. Trust-root substitution and self-asserted trust bootstrap.
4. Replay of approval, evidence, challenge, nonce, or packet.
5. Wrong database, database identity, maintenance window, action, or scope.
6. Expired, stale, future, incomparable, or unverifiable timestamps.
7. Missing or unavailable verifier, identity, trust-store, clock, or revocation data.
8. Contradictory evidence and partial/failed migration or restore states.
9. Missing required categories and attempts to use broad approval for staged actions.
10. Self-asserted `VERIFIED` or `APPROVED` claims without authenticated verification.
11. `NOT_APPLICABLE` misuse while required categories remain mandatory.
12. Any user-facing path that could imply review is execution authorization.

## 5. Dependency-ordered implementation plan

Implementation begins only after V-01 through V-12 are recorded with accountable roles and
approved evidence by the authorized organizational process. The plan is engineering-only and
does not authorize any production operation.

1. **Freeze organizational decisions and transition rules.**
   Obtain dispositions and authoritative evidence for V-01 through V-12. Cross-check role
   assignments, scope policy, and absence of unresolved trust/time/replay decisions. Stop if
   any decision is absent or ambiguous.
2. **Approve and implement a trust-policy fixture.**
   Add test-only policy/identity fixtures derived from the organization's recorded trust
   design. Do not embed organizational trust roots without authorization.
3. **Define canonicalized evidence contracts.**
   Specify schemas, required fields, digest representation, limitations, category names, and
   exact database/window/action binding. Review V-05 and V-10 coverage first.
4. **Implement state machines without execution hooks.**
   Introduce typed supplied/verified/approval states and fail-closed transitions. Ensure no
   state can promote evidence, approval, packet readiness, or execution authorization.
5. **Implement exact-byte verification primitives.**
   Add canonicalization and digest/signature primitives only after approved cryptographic and
   envelope decisions exist. No production trust root or key is introduced during repository
   engineering.
6. **Implement approval and scope verification.**
   Enforce distinct action scopes, identity, authority, expiry/revocation, window, database,
   separation-of-duties, and action-specific approval. Do not combine migration stages.
7. **Implement category verifiers.**
   Build local/category adapters against the approved coverage matrix. Each adapter must
   report limitations and fail closed rather than infer unsupported observations.
8. **Implement replay, nonce, and window enforcement.**
   Add one-use challenge handling and replay/audit storage semantics approved under V-06,
   with outage behavior defined under V-09.
9. **Integrate packet assembly.**
   Preserve structural representation while preventing unverified JSON from self-promoting
   verification or approval. Keep `INCOMPLETE`/`READY_FOR_OPERATOR_REVIEW` semantics distinct
   from execution authorization.
10. **Run the negative-test matrix.**
    Execute forge, alteration, replay, wrong-binding, expiry, outage, and self-assertion tests.
    Block release on any case that is not fail closed.
11. **Complete engineering and security review.**
    Review threat model, contracts, state transitions, bindings, negative tests, audit/privacy,
    outage behavior, and user-facing language. No production evidence is collected in this gate.
12. **Gate documentation-only acceptance.**
    Record engineering review completion and explicitly preserve the packet's review-only flags.
    Migration execution remains a separate approved procedure requiring new in-window evidence.

## 6. NO-GO status and reconsideration conditions

The current gate is **NO-GO**.

The gate may be reconsidered only after all of the following are completed as organization-owned
actions:

1. V-01 through V-12 have authoritative dispositions and accountable roles are no longer
   `UNASSIGNED`.
2. Trust-root bootstrap, key custody/revocation, approval identity/authority, separation of
   duties, coverage, window/replay, trusted time, privacy/audit, outage, status, and binding
   policies are approved in authoritative artifacts.
3. Engineering and security approve the category verification contracts, threat model, state
   semantics, database/window/action bindings, and negative-test plan.
4. Every implementation-readiness checklist item is checked with supporting evidence,
   while the structural-schema item is treated only as structural and not as approval.
5. Repository implementation passes the approved negative-test plan and preserves fail-closed
   packet behavior.

Even after this verifier-boundary engineering gate passes, the following repository packet
flags and migration constraints remain mandatory:

- `INCOMPLETE` remains the safe packet state until independent verification is established.
- `independentlyVerifiedCount: 0` remains the verified count until authenticated and approved
  independent verification evidence exists.
- `executionAuthorization: "NOT_GRANTED"` remains unchanged unless a separately authorized,
  process-compliant action explicitly changes it.
- No migration, backup, restore, service control, or cutover is authorized by this plan.

## 7. Unresolved blockers

The repository currently has no authenticated organization-owned evidence sufficient to resolve:

- trust-root authority and bootstrap; 
- verifier-key custody and revocation policy;
- approver identity and authority;
- separation of duties;
- verifier coverage and source-of-truth boundaries;
- maintenance-window/replay authority;
- trusted time and freshness policy;
- evidence retention/privacy/disposal policy;
- outage behavior for trust dependencies;
- category verification-result semantics;
- `NOT_APPLICABLE` policy; and
- review-only-to-execution separation governance.

These items require organizational decisions and approval outside repository-derived
assumptions. They are unresolved until authoritative evidence is attached to the controlled
decision process.
