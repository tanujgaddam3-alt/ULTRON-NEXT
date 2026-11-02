# CRE Phase 4: Visual Intelligence Feasibility and Architecture Audit

## Executive summary

**Verdict: READY WITH CONSTRAINTS.**

CRE already has the right integration seam for an additive visual-intelligence
layer: registered, owner-scoped source assets are inspected before planning;
autoplan persists a validated structured plan revision; and private rendering,
artifact integrity, and render-operation recovery remain separate. The current
visual signal is limited to FFmpeg scene-cut timestamps. It does not identify
what objects, people, activities, or events appear in a source video.

One local Ollama model, `qwen3.5:9b`, is listed with a `vision` capability and
an image projector. It is an authentic local image-understanding candidate,
not proof that local video input, temporal reasoning, quality, latency, or
resource use has been validated. No inference was run. No local image
embedding model or object detector was confirmed. The CPU-only PyTorch
installation, 15-GiB host RAM, and integrated Radeon Vega 6 with 512-MiB
dedicated VRAM argue for sparse, explicitly bounded frame selection rather
than exhaustive frame-by-frame inference.

The recommended first implementation milestone is a local, opt-in,
single-frame/short-frame-sequence feasibility benchmark on synthetic media,
followed by a bounded frame-manifest and evidence contract. Only after that
should analysis be persisted and offered as optional input to the current
planner. Keep the existing renderer, render operations, ownership checks,
source hashes, plan validation, and recovery path unchanged.

## Scope, method, and limits

This was a read-only source, package-metadata, model-metadata, and host audit.
Only the three requested Phase 4 documents are created. No application code,
runtime configuration, environment variables, database, or schema was changed.
No database was opened, no private media was accessed, no model was downloaded,
and no model inference or expensive benchmark was run. `.env.financial` and
financial data were not accessed.

Evidence inspected:

- CRE source under `ultron/application/content/`, the content domain models,
  active gateway wiring, and relevant tests.
- Ollama `list`, `show`, `ps`, and version output. `show` reads model metadata;
  no generation request was made.
- Hugging Face cache repository names and file-link metadata.
- Installed package metadata in the workspace virtual environment.
- CPU, RAM, swap, GPU device, accelerator tooling, disk, and cache measurements.

Hardware and package values below are point-in-time observations from
2026-10-30, not guaranteed steady-state capacity. “Estimate” is explicitly
marked and is not benchmark evidence.

## A. Existing visual intelligence inventory

| Capability | Classification | Evidence and interpretation |
|---|---|---|
| Video stream and media metadata | IMPLEMENTED AND FUNCTIONAL | `FFmpegLocalClipRenderer.inspect()` validates the managed source and `ffprobe` metadata, including duration, streams, resolution, frame rate, and codecs. It does not infer depicted content. See [`clip_renderer.py`](../ultron/application/content/clip_renderer.py#L229) and [`clip_renderer.py`](../ultron/application/content/clip_renderer.py#L337). |
| Frame extraction for semantic inspection | NOT IMPLEMENTED | No CRE service extracts or persists representative source frames for a vision model. FFmpeg decodes frames internally while measuring scene-change scores and rendering. That is not semantic frame understanding. |
| Scene-cut detection | IMPLEMENTED AND FUNCTIONAL; NON-SEMANTIC | `detect_scene_changes()` uses FFmpeg's `select='gt(scene,0.30)'` filter to return cut timestamps, bounded by existing input/runtime/output limits. It answers “where did image content change enough?” and not “what happened?” See [`clip_renderer.py`](../ultron/application/content/clip_renderer.py#L435). |
| Object/person/environment recognition | NOT IMPLEMENTED | No object detector, image classifier, person recognizer, or visual environment classifier is wired into the CRE application. |
| Visual activity or event recognition | NOT IMPLEMENTED | No source-video action-recognition or event-localization model is present in the CRE path. |
| Image embeddings or visual similarity | NOT IMPLEMENTED | No local visual embedding runtime/model or image-nearest-neighbor index was found in the inspected project dependencies or model caches. Speech-speaker embeddings in the Hugging Face cache are audio, not image embeddings. |
| Temporal video understanding/tracking | NOT IMPLEMENTED | Cut timestamps are ordered but do not describe an event interval, track entities, or explain changes in activity. `ollama show` reports `vision` for the installed VLM but does not establish a video input contract. |
| Project-plan moment selection | HEURISTIC ONLY | Autoplan chooses intervals based on duration, instruction keywords, source count, and scene-cut intervals. It uses no visual semantics. Its persisted response explicitly says semantic vision and speech understanding were not inferred. See [`rendering.py`](../ultron/application/content/rendering.py#L531) and [`rendering.py`](../ultron/application/content/rendering.py#L693). |
| Transcript highlight selection | HEURISTIC ONLY | Highlight windows are ranked with deterministic text/timing signals (hook, coherence, information density, narrative, ending, duration fit) and an 8% scene-boundary-alignment component. That “visual” component only measures proximity to cuts; the score is not a calibrated engagement probability. See [`intelligence.py`](../ultron/application/content/intelligence.py#L306), [`intelligence.py`](../ultron/application/content/intelligence.py#L333), and [`intelligence.py`](../ultron/application/content/intelligence.py#L467). |
| Script scene classification and diagrams | IMPLEMENTED AND FUNCTIONAL, BUT SCRIPT-DERIVED | `plan_script_scenes()` applies deterministic text rules and `render_scene_diagram()` draws labels/layouts with Pillow. The visuals explain script text; they are not extracted from or evidence about source-video frames. See [`script_scenes.py`](../ultron/application/content/script_scenes.py#L550) and [`script_scenes.py`](../ultron/application/content/script_scenes.py#L1020). |
| Evidence and provenance for audio findings | IMPLEMENTED AND FUNCTIONAL | Transcript and highlight records bind to source asset IDs and hashes, timestamps, engine/scoring versions, transcript segment IDs, evidence labels, explanations, and warnings. These are audio/text-derived facts, not visual findings. See [`content_intelligence.py`](../ultron/domain/content_intelligence.py#L195). |
| Evidence and provenance for scene cuts | IMPLEMENTED BUT INCOMPLETE FOR SEMANTICS | Scene boundaries are validated and saved in plan source records, with selection method and optional selected cut. There is no model observation, frame reference, caption, visual confidence, or event explanation. See [`content.py`](../ultron/domain/content.py#L469) and [`content.py`](../ultron/domain/content.py#L509). |
| Durable plan/render lifecycle | IMPLEMENTED AND FUNCTIONAL | Immutable plan revisions and v10 private-render operations preserve the current lifecycle. The new layer should supply validated evidence to autoplan rather than replace plan persistence or rendering. See [`store.py`](../ultron/application/content/store.py#L1264), [`store.py`](../ultron/application/content/store.py#L460), and [`rendering.py`](../ultron/application/content/rendering.py#L1116). |

### Existing analysis-to-planner flow

The gateway wires a `ContentClipRenderingService` from the CRE runtime. During
`autoplan_edit_project`, the service reads each authorized registered asset
through verified managed storage, stages it, probes video metadata, detects
scene cuts, and builds/stores the next structured plan. Scene intervals may be
selected distinctly before a bounded fallback/repetition is recorded. This
function is a natural additive integration seam; it is not a reason to move
analysis into the renderer. See [`status.py`](../ultron/application/content/status.py#L184),
[`interface_gateway.py`](../ultron/application/runtime/interface_gateway.py#L241),
[`interface_gateway.py`](../ultron/application/runtime/interface_gateway.py#L7218),
[`rendering.py`](../ultron/application/content/rendering.py#L50), and
[`rendering.py`](../ultron/application/content/rendering.py#L531).

## B. Local models and inference runtimes

### Confirmed local vision-capable model

| Model | Type | Local evidence and size | Input/output capability evidence | Memory and hardware | License | CRE suitability |
|---|---|---|---|---|---|---|
| `qwen3.5:9b` | Vision-language model (Ollama architecture `qwen35`; CLIP projector) | Present in `ollama list`, listed as 6.6 GB. `ollama show` reports 9.0B parameters, Q4_K_M, a 456.01M-parameter projector, and `vision` capability. | Image input/vision capability is advertised in local Ollama metadata. No test established video-file input, ordered multi-frame temporal accuracy, output quality, or CRE-specific captioning. Text output follows the model interface; structured findings must be validated by application code. | CPU execution is plausible but not measured. The 6.6-GB disk model size is not a full runtime-RAM measurement; activations and KV cache add memory, particularly at the reported 262,144-token context. GPU execution is not established (see hardware assessment). | Ollama model metadata reports Apache License 2.0. Verify upstream model-card terms and notices before distributing or using in a commercial deployment. | Best currently available local semantic-image candidate, but only a feasibility candidate. Use sparse, timestamped frames and short bounded outputs. Do not treat it as a video model or as calibrated event confidence. |

The installed Ollama version is **0.40.1**, and the model metadata states a
minimum Ollama version of 0.30.0. `ollama ps` showed no model loaded at audit
time. Compatibility metadata and installed weights are evidence of local
availability, not proof of successful inference. No prompt or image was sent.

### Other discovered local model artifacts

| Model/artifact | Local evidence | Modality/type | License and CRE relevance |
|---|---|---|---|
| `qwen2.5-coder:1.5b` | Ollama lists 986 MB; Q4_K_M, 1.5B parameters | Text/code completion; `ollama show` does not list vision | Apache-2.0 in Ollama metadata. Not a visual model. |
| `qwen2.5-coder:7b` | Ollama lists 4.7 GB; Q4_K_M, 7.6B parameters | Text/code completion; no vision capability listed | Apache-2.0 in Ollama metadata. Not a visual model. |
| `SWivid/F5-TTS` | A Hugging Face cache snapshot contains `F5TTS_v1_Base/model_1250000.safetensors`, 1,348,435,761 bytes | Speech generation | Audio-only; not visual intelligence. The cached snapshot had no README/license metadata; license is unverified and must be checked upstream before use. |
| `speechbrain/spkrec-ecapa-voxceleb` | A Hugging Face cache snapshot contains classifier/embedding checkpoint links; `embedding_model.ckpt` is 83,316,686 bytes | Speaker/audio embeddings | Audio identity features; not image embeddings. No cached README/license metadata was present; license is unverified and must be checked upstream before use. |
| `charactr/vocos-mel-24khz` | A Hugging Face cache snapshot contains `pytorch_model.bin`, 54,365,991 bytes, and config links | Audio vocoder | Audio synthesis; not visual intelligence. Cached README identifies MIT license. |

The Hugging Face cache occupied 1.4 GiB total at measurement time; its three
identified repository names are speech/audio artifacts. The listed checkpoint
sizes are measured file sizes, not full runtime memory requirements. The
Ollama model directory occupied 7.1 GB physically, whereas per-tag Ollama
logical sizes sum to more because model blobs are shared.

### Relevant installed software

| Runtime/package | Observed version/status | Implication |
|---|---|---|
| Ollama | 0.40.1 | Local Qwen VLM model metadata and vision capability are available. No inference was run. |
| PyTorch | 2.10.0+cpu | CPU-only build; not an accelerator-enabled image stack. |
| ONNX Runtime | 1.31.0; `onnxruntime-gpu` absent | No installed GPU execution provider package was confirmed. |
| Transformers | Not installed | No Hugging Face Transformers-based VLM, detector, or image encoder path is ready in the workspace venv. |
| torchvision | Not installed | No PyTorch image model utilities/weights were confirmed. |
| OpenCV Python packages | `opencv-python` and `opencv-python-headless` not installed | No Python OpenCV frame-analysis pipeline is currently available. FFmpeg remains present. |
| Ultralytics | Not installed | No Ultralytics detector/model package was confirmed. |
| sentence-transformers | Not installed | No visual embedding application runtime was confirmed. |
| Faster-Whisper | 1.2.1, CTranslate2 4.8.3, PyAV 19.0.1 | Existing local audio transcription stack only; its engine explicitly selects CPU and local files. See [`intelligence.py`](../ultron/application/content/intelligence.py#L44) and [`intelligence.py`](../ultron/application/content/intelligence.py#L99). |
| FFmpeg/ffprobe | Installed; FFmpeg 8.0.1 observed | Suitable for bounded media probing, scene-cut detection, frame extraction, and existing rendering. It does not supply semantic understanding by itself. |
| Pillow | 13.0.0 | Used to draw script-derived diagrams, not to infer source-video content. |

No local object-detection weights, image-classification weights, or image
embedding weights were found in the inspected Ollama model list or Hugging Face
cache repository identities. Registry listings or package availability were
not treated as downloaded weights.

## C. Host hardware and performance constraints

### Measured host snapshot

| Resource | Observation |
|---|---|
| CPU | AMD Ryzen 7 4800H with Radeon Graphics; 8 cores / 16 threads; AVX2 present. |
| RAM | 15 GiB total; 2.3 GiB free and 6.6 GiB available at inspection. |
| Swap | 4 GiB total; 0 used at inspection. |
| GPU | AMD/ATI Renoir Radeon RX Vega 6 integrated GPU. |
| Dedicated GPU memory | 512 MiB total; approximately 452 MiB reported used at inspection. |
| Shared GPU/GTT accounting | Approximately 7.5 GiB total; about 216 MiB reported used at inspection. This is shared system memory, not dedicated VRAM. |
| Accelerator software | `nvidia-smi`, `nvcc`, `rocminfo`, `vulkaninfo`, and `vainfo` were unavailable. `/dev/kfd` and `/dev/dri/renderD128` existed, but their existence alone does not establish a working inference provider. |
| Disk | 39 GiB available on the filesystem backing `/` and `/home`; 78% used. |
| Loaded Ollama models | None at the instant `ollama ps` was inspected. |

### Scenario 1 — CPU-only

**Feasibility: constrained but suitable for a sparse prototype.** A Q4_K_M 9B
VLM has a 6.6-GB on-disk footprint. Runtime memory exceeds the weights because
of the image encoder/projector, activations, and KV cache. The machine has
15 GiB RAM but only 6.6 GiB available at the snapshot; a long context or
multiple parallel requests could cause memory pressure or swapping.

**Unmeasured planning estimate:** on this 8-core CPU, budget on the order of
5–50 seconds per low-resolution sampled image (roughly 0.02–0.2 analyzed
frames/second), depending on input resolution, context, prompt, output length,
Ollama backend, and system load. Ten frames could therefore take roughly
1–8 minutes including process/model startup. This wide range is an assumption
for capacity planning only; it is not a benchmark, guarantee, or observed
performance. Measure only after separate authorization, using synthetic
inputs, a short context, one worker, and bounded timeouts.

### Scenario 2 — GPU-accelerated

**Feasibility: not established; do not plan around GPU acceleration.** The
detected GPU is integrated and has 512 MiB dedicated VRAM. Although shared
memory accounting is larger, no installed ROCm/CUDA/Vulkan inference provider
was confirmed. The existing PyTorch build is CPU-only, ONNX Runtime GPU is
absent, and no model was loaded to inspect actual processor placement. The
presence of `/dev/kfd` does not establish ROCm compatibility. Avoid claiming
accelerated throughput or assigning the 7.5-GiB GTT as usable model VRAM.

### Scenario 3 — Hybrid sparse analysis

**Feasibility: recommended.** Use FFmpeg for deterministic metadata, scene
boundaries, and timestamped frame extraction; use cut-adjacent plus capped
uniform samples to select a small number of representative frames; invoke the
local VLM only for those frames; and aggregate observations into timestamped
event candidates without inventing motion/tracking evidence. Cache results by
asset digest, model/version, sampling policy, and prompt/schema version.

The renderer currently bounds source duration at 300 seconds and scene analysis
at 60 seconds. A reasonable initial policy is a small per-source cap (for
example 12–24 frames, not a production commitment), one inference at a time,
and stricter limits on image dimensions and output tokens. Hybrid savings scale
with sampled frames; no end-to-end rate was measured.

## D. Proposed additive architecture

1. **Authorized source access:** retain the current gateway principal,
   rights/provenance checks, registered asset ID, SHA-256, and verified managed
   storage read. The visual service must not accept arbitrary filesystem paths
   or browser-uploaded frame paths.
2. **Frame manifest:** add a bounded analysis component that uses FFmpeg to
   extract selected stills into a private, temporary staging area. Record each
   frame's source timestamp, extraction policy/version, pixel dimensions,
   frame digest, and parent asset digest. Validate timestamps as finite,
   nonnegative, ordered, and no later than the probed video duration.
3. **Frame understanding:** wrap the local Ollama vision interface behind an
   application protocol with explicit model ID/version, timeout, concurrency
   cap, image-size cap, output-token cap, and local-only mode. Require
   schema-validated output; model prose is untrusted data. Do not persist
   unvalidated outputs as evidence.
4. **Temporal/event assembly:** initially compare adjacent sparse observations
   and relevant scene-boundary neighborhoods. Create event intervals only
   when supported by observed frame timestamps; identify these as coarse
   intervals, not exact action boundaries or tracked entities. Do not call
   sparse frame comparison continuous temporal tracking.
5. **Evidence record:** represent an observation with source asset ID/hash,
   start/end timestamps, frame IDs/hashes, literal observation/caption,
   event category, model identifier and version, prompt/schema/sampling
   versions, analysis time, and explicit confidence semantics. Confidence is
   optional and must be described as a model score, not a calibrated
   probability. Preserve the source text/visual observation separately from
   planner interpretation.
6. **Persistence and recovery:** keep analysis state/data distinct from render
   operations. Use a versioned analysis record keyed by content digest and
   analysis configuration; owner-scope reads; make retries idempotent; and
   represent interruption/failure explicitly. Avoid duplicate expensive
   analysis and do not silently rerun it on page reload.
7. **Planner integration:** add an optional validated evidence input at the
   existing autoplan seam in `ContentClipRenderingService.autoplan_edit_project`.
   The planner may choose source intervals supported by evidence, but remains
   responsible for the existing v2 plan validation and revision persistence.
   Preserve the old heuristic-only path when visual analysis is absent or
   explicitly unavailable. If evidence becomes part of plan decisions, bind
   plan revision/digest to the selected evidence IDs and version; do not put
   raw model output directly into FFmpeg commands.
8. **Renderer and UI:** do not replace FFmpeg, durable render IDs, artifact
   hashes, recovery, or playback verification. Expose visual evidence and
   selection rationale as an explainable plan view only after ownership and
   source provenance checks.

Current integration points are the `ContentClipRenderingService` wiring in
[`status.py`](../ultron/application/content/status.py#L184) and
[`interface_gateway.py`](../ultron/application/runtime/interface_gateway.py#L7218),
and the analysis/planning portion of
[`autoplan_edit_project`](../ultron/application/content/rendering.py#L531).
Existing immutable plan revisions and schema-v10 durable render operations
remain authoritative in [`store.py`](../ultron/application/content/store.py#L1264)
and [`store.py`](../ultron/application/content/store.py#L460).

## E. Integration risks and mitigations

| Risk | Mitigation |
|---|---|
| CPU/RAM exhaustion or swap due to VLM weights/context | Single worker; short context; fixed resolution/frame/output caps; semaphore; memory-pressure refusal; explicit timeout; report actual host resource availability. |
| GPU assumption or driver incompatibility | Treat CPU as the supported baseline until an approved non-production probe proves a provider, model placement, and stable memory use. |
| Long videos and excessive extraction/inference | Enforce existing source-size/duration bounds plus new per-source frame, image-pixel, elapsed-time, and output-token budgets. |
| Duplicate analysis after reload or operation retry | Content-addressed analysis key and durable state/result; retry same analysis identity; do not bind analysis to a fresh render request. |
| False-positive objects/events or hallucinated descriptions | Ground every claim in frame references and timestamps; label model-derived content; require human review; preserve abstention/unknown; never infer off-screen actions. |
| Invalid temporal intervals | Validate all timestamps and duration bounds before candidate persistence or plan use; clamp only under explicit documented policy, otherwise reject. |
| Uncalibrated model confidence | Store raw score separately with model-specific semantics; never multiply into planner scores as probability until calibrated on a representative validation set. |
| Planner incompatibility or over-trust | Keep evidence optional; preserve structured plan validation; add deterministic fallback; test old v1/v2 plan compatibility and immutable plan revisions. |
| Analysis failure changes a completed render | Keep analysis lifecycle separate; no analysis, transfer, or playback failure may mutate a completed render state. |
| Multiple workers or concurrent use | Durable claim/lease and idempotent operation semantics for analysis; cap global model concurrency to one until measured. |
| Model/prompt changes invalidate evidence | Persist exact model tag/digest, Ollama version, prompt/schema/sampler versions, and input digest; stale analyses are explicit and regenerated only on request. |
| Licensing, privacy, retention | Review the exact model card/license before distribution; retain only bounded derived evidence; set deletion policy for temporary frames and owner artifacts. |

## F. Test and evidence plan

Existing source tests exercise scene-cut detection with synthetic hard cuts,
scene-aware interval selection, transcript heuristics, and deterministic
script-derived diagrams. They do not prove semantic visual understanding.
Relevant tests include
[`test_content_rendering.py`](../tests/application/test_content_rendering.py#L350),
[`test_content_rendering.py`](../tests/application/test_content_rendering.py#L3307),
[`test_content_intelligence.py`](../tests/application/test_content_intelligence.py#L641),
and [`test_content_script_scenes.py`](../tests/application/test_content_script_scenes.py#L75).
Tests were inspected but not executed for this audit.

Before any implementation is accepted, add tests for: sampling caps and
timestamp-to-frame binding; corrupt/empty frame handling; model timeout and
invalid JSON/schema output; owner-isolated analysis lookup; cache-key changes
when model/prompt/sampling version changes; duplicate concurrent analysis;
interruption/recovery without unrequested rerun; explicit unavailable fallback;
plan evidence linkage; and ensuring render completion remains unchanged by
analysis/transfer/playback failures. Use synthetic media only, then evaluate
human-annotated representative content before claiming event quality.

## Final assessment

1. **Already possessed:** bounded media metadata inspection, non-semantic
   FFmpeg scene-cut timestamps, deterministic scene-diverse interval selection,
   transcript-derived heuristic scoring, script-derived illustrative
   diagrams, source hashes, and durable structured plan/render lifecycles.
2. **Currently lacking:** source-frame semantic recognition, objects/persons/
   activities/events, visual embeddings, continuous temporal understanding,
   video-grounded relevance scoring, and visual evidence records.
3. **Genuinely available locally:** one Ollama model advertising image vision
   (`qwen3.5:9b`, 6.6 GB Q4_K_M); no locally confirmed detector or image
   embedding weights/runtime. Inference success and speed remain unverified.
4. **Most suitable current combination:** FFmpeg for constrained frame
   selection plus sparse local Qwen VLM descriptions plus deterministic,
   timestamp/evidence-bound aggregation. Do not add a detector/embedding model
   until weights, runtime, license, and measured benefit are established.
5. **Hardware:** supports a bounded CPU feasibility prototype with constraints;
   GPU inference is not established. Do not promise real-time or exhaustive
   analysis.
6. **First milestone:** with separate authorization, benchmark one VLM frame
   and a short synthetic sequence in an isolated local process; measure
   latency, peak RSS, timeout behavior, output validity, and image-size effects.
   No such inference was performed here.
7. **Blockers:** no verified visual inference run or performance budget; no
   detected local visual detector/embedding model; GPU provider unverified;
   no visual evidence schema/persistence; model quality and license terms need
   task-specific validation.

**Final verdict: READY WITH CONSTRAINTS.** The installed local VLM and existing
autoplan seam justify a gated feasibility prototype, not an assertion that
genuine video understanding is already available or production-ready.
