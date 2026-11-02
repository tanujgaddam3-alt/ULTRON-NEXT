# CRE Phase 4.1: Recommended Next Steps

## Decision

**Do not begin CRE integration and do not infer anything about Qwen's visual
quality from this run.** The installed model advertises vision, but the
guarded synthetic request was canceled at 25.471 seconds when system
`MemAvailable` fell below 2 GiB. No response was received.

## Recommended next milestone

If feasibility work remains important, repeat only the bounded local-model
probe on a machine or time window with materially greater memory headroom. The
model's 6.55-GB on-disk tag size plus the required 2-GiB free-memory floor
indicates that starting with at least roughly 9 GiB available is a prudent
preflight target, **not a measured sufficiency guarantee**. Keep the same
per-request watchdog, synthetic-only inputs, one request at a time, short
context, and immediate abort threshold. No existing service should be stopped
to create headroom.

If no suitable local host is available, a separately authorized model inventory
can identify already-installed smaller vision-capable candidates. Do not pull
or install models as part of this recommendation. The two other Ollama tags
observed in this run were text/code models and did not advertise vision.

## Evidence-based disposition of proposed paths

1. **Continue with Qwen vision:** defer further requests on this host. The
   metadata supports a later feasibility attempt but the resource abort provides
   no output-quality evidence.
2. **Add a lightweight detector:** not justified. No detector inference or
   comparative benchmark was run.
3. **Add image embeddings:** not justified. No embedding model or retrieval
   experiment was run.
4. **Build temporal analysis:** do not proceed. Neither six-frame sequence was
   submitted; there is no evidence for temporal capability.
5. **Evaluate another model:** only if a separate read-only inventory finds an
   already-installed, vision-capable model that fits a defined resource gate.
   No download or model switch is authorized by this experiment.

## Gates before any CRE design work

- Obtain a completed response to at least the four synthetic single-frame
  tasks and both ordered six-frame tasks, or explicitly narrow the intended
  capability to the subset actually measured.
- Preserve request/response data and validate JSON without repairing failures.
- Score object identification, spatial relations, activity, unsupported claims,
  and evidence grounding against the known synthetic scenes.
- Measure actual response latency and complete inference-process peak RSS;
  the aborted attempt yielded neither.
- Establish repeatable resource headroom and failure/timeout behavior.
- Review the exact model card and applicable license notices.
- Only after the feasibility gates pass, propose a separate CRE integration
  design. Keep inference optional, evidence-bound, local, and independent of
  render lifecycle state.
