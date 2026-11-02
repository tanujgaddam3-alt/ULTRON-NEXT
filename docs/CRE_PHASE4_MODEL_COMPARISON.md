# CRE Phase 4: Local Model and Architecture Comparison

## Decision summary

The only confirmed local model with vision capability is Ollama
`qwen3.5:9b` (Q4_K_M, 6.6 GB on disk). It is a reasonable model for a
carefully bounded image-captioning feasibility test. It is not confirmed as a
video-input model, no inference was run, and its visual quality, latency, peak
memory, and temporal accuracy are unknown.

**Recommended near-term combination:** FFmpeg for bounded, timestamped scene
cuts and frame extraction; the local Qwen VLM for sparse still-image
descriptions; deterministic application logic to bind descriptions to source
timestamps and rank evidence conservatively. Do not start with the VLM on every
frame. No local detector or image-embedding model was verified, so those should
remain candidates for later evaluation rather than hidden dependencies.

## Confirmed local models

| Exact identifier | Model type | Local availability and measured size | Input and output evidence | Resource requirements / hardware | License | Strengths, weaknesses, and CRE suitability |
|---|---|---|---|---|---|---|
| `qwen3.5:9b` | Vision-language model; Ollama architecture `qwen35` with CLIP projector | Installed in Ollama. `ollama list`: 6.6 GB. `ollama show`: 9.0B parameters, Q4_K_M; projector 456.01M parameters and 1152 embedding dimensions. | Local metadata advertises `vision`; suitable to evaluate on images. It does not establish direct video input, temporal grounding, object tracking, or multi-frame reasoning quality. Captions/descriptions are plausible outputs to validate experimentally, not guaranteed structured facts. | 6.6 GB model file is a disk figure, not a measured runtime-RAM requirement. Runtime also needs model workspace, image encoder activations, and KV cache. The listed 262,144-token context can be costly; prototype with a deliberately short context. CPU path is the viable baseline. 512 MiB dedicated VRAM is inadequate for assuming whole-model GPU residency; supported shared-memory acceleration was not established. | Ollama metadata reports Apache License 2.0. Review the exact model card/notices and intended commercial-use terms before shipping. | Best local semantic visual candidate. Strong simplicity/locality; weakly constrained by CPU/RAM, unverified local inference, and no evidence of calibrated confidence. Use on selected frames, not all frames. |
| `qwen2.5-coder:1.5b` | Text/code language model | Installed in Ollama; 986 MB, Q4_K_M, 1.5B parameters. | `ollama show` lists completion/tools/insert, not vision. | Smallest listed Ollama weight footprint; CPU-friendly relative to 9B, but not a visual model. | Ollama metadata reports Apache License 2.0. | Not a source-video understanding option. Do not conflate general text ability with image capability. |
| `qwen2.5-coder:7b` | Text/code language model | Installed in Ollama; 4.7 GB, Q4_K_M, 7.6B parameters. | `ollama show` lists completion/tools/insert, not vision. | Larger CPU memory/latency burden than 1.5B; not a visual model. | Ollama metadata reports Apache License 2.0. | Not suitable for visual evidence generation. |

Ollama itself was version **0.40.1**; `ollama ps` showed no model loaded at
inspection. The local model directory occupied 7.1 GB because model layers
are shared. No images or prompts were submitted to Ollama during this audit.

## Model and runtime availability gaps

| Capability | Local findings | Assessment |
|---|---|---|
| Vision-language/image understanding | `qwen3.5:9b` advertises vision | Available as a local candidate; actual inference and CRE suitability not yet verified. |
| Video understanding | No model with confirmed video-input contract found | Not available as a demonstrated capability. A sequence of stills may be evaluated later, but do not label it native video understanding without evidence. |
| Object detection/person detection | No local detector weights or supported package confirmed; `ultralytics` absent | No runnable local detector demonstrated. |
| Image classification | No local classifier model confirmed; `torchvision` absent | Not demonstrated. |
| Image embeddings/visual similarity | No image encoder weights or `sentence-transformers` package confirmed | Not demonstrated. Qwen's projector/embedding metadata is not evidence that a reusable image-vector inference path or search index exists. |
| Transformers runtime | `transformers` absent | No local Transformers inference path ready in the workspace venv. |
| PyTorch | `2.10.0+cpu` | CPU-only build. |
| ONNX Runtime | `onnxruntime 1.31.0` present; `onnxruntime-gpu` absent | CPU runtime installed; GPU execution not established. |
| OpenCV | `opencv-python` and `opencv-python-headless` absent | Use existing FFmpeg for a first frame pipeline rather than adding a second video decoder without need. |
| Hugging Face model cache | 1.4 GiB total; identified repos: `SWivid/F5-TTS` (1,348,435,761-byte checkpoint), `speechbrain/spkrec-ecapa-voxceleb` (83,316,686-byte embedding checkpoint), and `charactr/vocos-mel-24khz` (54,365,991-byte checkpoint) | These are speech/audio artifacts, not vision models. Cached license metadata was absent for F5-TTS and SpeechBrain; Vocos' cached README identifies MIT. They do not fill the visual detector, image encoder, or video-model gap. |
| Existing image/diagram tools | Pillow 13.0.0, used by deterministic script-scene diagrams | Draws layouts from source script labels; not image understanding. |

## Candidate architectural combinations

Scores below are reasoned tradeoffs, not measured model benchmarks.

| Combination | Availability now | Advantages | Weaknesses / constraints | Recommendation |
|---|---|---|---|---|
| **FFmpeg scene cuts + sparse `qwen3.5:9b` image descriptions + deterministic evidence aggregation** | FFmpeg and the Qwen model are local; no inference was run | Reuses the existing media probe; keeps every VLM answer tied to an exact sampled frame; avoids adding a new Python inference framework; supports explanations and human review. | CPU latency/RAM unknown; sparse stills cannot establish continuous motion or exact event boundaries; VLM may hallucinate; no object IDs or similarity search. | **Recommended first feasibility/prototype path**, subject to separate inference authorization and model/license verification. |
| **One VLM on every frame** | Model present; exhaustive pipeline absent | Simple conceptual flow and potentially rich per-frame descriptions. | Wasteful on CPU; high cost, repeated captions, memory and latency pressure; frame-level descriptions are not temporal tracking; likely exceeds useful source-processing budget. | **Do not implement as default.** |
| **FFmpeg sampling + lightweight detector + image embeddings + selective VLM** | Detector and image-embedding model/runtime/weights not confirmed locally | In principle, detector/embeddings can cheaply screen frames, cluster similar shots, identify recurring objects, and reserve VLM calls for ambiguous or salient events. Can outperform a single large model on throughput/retrieval tasks if the models are compatible and evaluation confirms it. | More dependencies, licenses, model/version handling, confidence fusion, and failure modes; detector labels do not explain events; embeddings do not provide semantic explanations; no available weights or target benchmark establish the expected benefit. | **Possible later optimization, not currently deployable.** Compare using a labeled set before choosing models. |
| **One dedicated video-understanding model** | None confirmed locally | Could model temporal input directly if a suitable model and runtime were available. | No local candidate or accelerator path established; high hardware/quality uncertainty; opaque outputs must still be grounded in frame/time evidence. | **Not recommended for the first milestone.** |
| **External cloud VLM/video API** | Not assessed or configured | Potentially faster and more capable on larger models. | Conflicts with local-first/privacy scope, introduces egress, credentials, cost, availability, and vendor dependence. | Mentioned only as an alternative; **not proposed as a dependency**. |

### Why a hybrid may be better than a single large model

For a fixed clip, cut detection, uniform sampling, duplicate-frame filtering,
and possibly an inexpensive detector can reduce the number of images sent to
a VLM. This can save time and make each expensive caption more actionable.
Embeddings are useful for deduplication/retrieval but do not themselves say
what happened. Detection yields class/box evidence but not a narrative. The VLM
is the best fit for a concise evidence-backed description. A staged system can
combine these complementary roles, but there is currently no locally installed
detector/visual-embedding model to demonstrate that advantage. Therefore the
first milestone should avoid adding models and first measure whether a small
number of Qwen image calls is reliable enough.

## Host fit and resource estimates

### Measured values

- AMD Ryzen 7 4800H, 8 cores / 16 threads, AVX2.
- RAM: 15 GiB total; 6.6 GiB available at inspection; 4 GiB swap, unused.
- AMD Renoir Radeon RX Vega 6 integrated GPU.
- Dedicated VRAM: 512 MiB total, about 452 MiB reported used.
- Shared GTT accounting: about 7.5 GiB total, about 216 MiB used. Shared RAM
  must not be described as dedicated VRAM.
- No `nvidia-smi`, `nvcc`, `rocminfo`, `vulkaninfo`, or `vainfo`; `/dev/kfd`
  and a DRM render node exist but do not prove an inference runtime.
- 39 GiB disk available. No Python vision-model cache was found in the
  inspected Hugging Face repositories.

### Estimates and assumptions

| Scenario | Assessment | Approximate performance/memory planning only |
|---|---|---|
| CPU-only Qwen VLM | Plausible for sparse images; constrained for long clips and interactive use. One worker and a low context are necessary. | Very rough estimate: **5–50 seconds per low-resolution image** (0.02–0.2 frames/s), or **about 1–8 minutes for ten images**, including variable startup. Assumes CPU placement, Q4_K_M model, brief output, and modest image resolution. No speed was measured. Runtime memory must exceed the 6.6-GB disk model size; image activations/KV cache add overhead. Current available memory (6.6 GiB) is near or below the model file size, so load may require additional memory availability and careful context control. |
| GPU-accelerated | Not currently established. Do not assume Radeon acceleration. | 512 MiB dedicated VRAM cannot hold this model. Shared memory would be necessary for a substantial GPU split, but accelerator provider/driver support and Ollama placement were not verified. No frame-rate estimate is defensible. |
| Hybrid sparse local | Best constrained path. | With a per-source cap of 12–24 samples (an example policy, not a benchmark), VLM call count remains bounded; FFmpeg handles decode/sampling and timestamp metadata. Actual end-to-end time is proportional to VLM calls and must be measured with synthetic clips. |

No expensive model load or inference was initiated to validate these estimates.

## Recommendation

1. Keep the current FFmpeg scene-change detector as a boundary signal only.
2. Before integrating model outputs, obtain approval for a disposable synthetic
   media inference probe using the local Qwen model; record peak RSS, load time,
   per-image latency, maximum stable image resolution, timeout behavior, and
   response validity. Do not use production footage for that benchmark.
3. If the probe fits a defined budget, add a small, isolated frame-sampling and
   VLM adapter behind an application protocol. Start with a low sample cap and
   no detector or embedding dependency.
4. Evaluate one or more detector/embedding candidates only if a labeled
   validation set shows that they reduce VLM calls or improve finding recall
   without harming precision. Verify actual local weights, supported runtime,
   model-card license, and measured memory before selection.

**Overall model verdict: READY WITH CONSTRAINTS.** Qwen `qwen3.5:9b` is a
genuinely installed vision-capable candidate; only its metadata, not its
inference behavior, has been verified.
