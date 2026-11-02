# CRE Phase 4.1: Qwen Vision Feasibility Experiment

## Verdict

**BLOCKED: MODEL OR HARDWARE UNAVAILABLE** for safe execution on the inspected
host. The `qwen3.5:9b` model is installed and advertises vision, but its first
synthetic-image request was aborted when available memory crossed the required
2 GiB safety floor. Ollama returned no HTTP response. No visual understanding,
temporal reasoning, structured output, or CRE suitability claim can be made.

This verdict describes operational availability under the experiment's safety
limits; it does not mean the model files or vision capability metadata were
missing.

## Scope and safeguards

- Used only `qwen3.5:9b` already installed in local Ollama; no model pull,
  package installation, configuration change, external inference, or service
  restart occurred.
- Ollama API version and tag metadata were queried at `127.0.0.1:11434`; the
  listener was bound to `127.0.0.1`. No non-loopback inference endpoint was
  contacted.
- Ollama had no loaded model before the attempt. After cancellation, `ollama
  ps` again showed no loaded model, and the original Ollama daemon remained
  alive.
- All 16 inputs were synthetic vector scenes generated and rendered to PNG by
  FFmpeg under `/tmp/ultron_cre_phase4_1`. No real photographs, video, private
  media, or internet images were used.
- One request was attempted. The client monitored system memory once per
  second, canceled on `MemAvailable < 2 GiB`, and had a 120-second request
  timeout. No further inference was attempted after the guard fired.
- No CRE application code, database, storage, frontend, or runtime
  configuration was accessed or changed.

## Environment and preflight

| Measurement | Observed |
|---|---|
| Ollama | 0.40.1; local API `/api/version` returned HTTP 200 |
| Model | `qwen3.5:9b`, tag digest `56671c2ab9385f9cfcb404638e32cd62d88e3501d44822208363c010179a3c90`, 9.0B parameters, Q4_K_M, 6,550,825,550-byte model, architecture `qwen35`, runner `llamacpp` |
| Vision metadata | Ollama `show` and `/api/tags` advertise `vision`; projector architecture `clip` |
| Context metadata | 262,144 tokens; the experiment requested a bounded context of 2,048 tokens |
| Model state | No model loaded before or after the attempt |
| CPU | AMD Ryzen 7 4800H, 8 cores / 16 threads, AVX2 |
| RAM at preflight | 15 GiB total; `MemAvailable` was 5,160,235,008 bytes immediately before the request |
| Swap | 4 GiB total; 4,294,316,032 bytes free immediately before request |
| GPU | AMD/ATI Renoir Radeon RX Vega 6, confirmed by `lspci`; no GPU utilization or memory telemetry tool was available |
| Disk | 39 GiB available on `/` and `/tmp` at preflight |
| FFmpeg | 8.0.1; build includes `librsvg` |
| Other tools | Node.js v24.22.0 used to generate the dataset and run the guarded request; `curl` used for read-only local API checks |
| License | Ollama model metadata reports Apache License 2.0. Exact upstream model-card terms and notices were not independently reviewed |

The memory number in the table is the exact request-start value. Host values
are snapshots, not fixed machine capacity. GPU utilization, GPU memory, and
the model runner's complete peak RSS were not measurable.

## Synthetic dataset

FFmpeg rendered locally generated SVG scenes as 1024×768 PNG images. The set
contains:

1. `image1_objects`: person-like stick figure, table, red ball, blue box, chair.
2. `image2_activity`: person-like figure reaching toward a red ball at a table.
3. `image3_environment`: a stylized indoor room with window, seating, table,
   lamp, and plant.
4. `image4_relationships`: red ball, blue box, green object, table, and chair in
   distinct positions.
5. `sequenceA_1`–`sequenceA_6`: red ball at progressively rightward positions.
6. `sequenceB_1`–`sequenceB_6`: stick-figure states intended to depict standing,
   approaching, reaching, and holding/moving away with a ball.

The entire generated dataset and per-image SHA-256 values are listed in
[`CRE_PHASE4_1_RAW_RESULTS.json`](./CRE_PHASE4_1_RAW_RESULTS.json). The dataset
was created successfully; the temporary images were later removed.

## Inference attempt and resource outcome

| Field | Result |
|---|---|
| Case | `image1_objects` |
| Request time | 2026-10-30 16:21:00.022 UTC |
| Input | Synthetic 1024×768 PNG, 20,280 bytes |
| Request settings | `stream=false`, JSON format requested, `num_ctx=2048`, `num_predict=256`, temperature 0, `think=false`, `keep_alive=5m` |
| Request-to-abort duration | 25,471 ms; this is **not** inference latency |
| HTTP status | None; no response headers/body arrived |
| Raw model response | None |
| JSON/schema validation | Not possible; no model response |
| Memory at request start | 5,160,235,008 bytes available |
| Lowest observed memory before cancellation | 2,103,181,312 bytes available (below 2 GiB) |
| Memory recorded at cancellation handling | 2,203,377,664 bytes available |
| Swap free | 4,294,316,032 bytes at start; 4,120,870,912 bytes at cancellation handling |
| Cancellation | Client aborted the in-flight HTTP request immediately after the under-2-GiB sample |
| After cancellation | `MemAvailable` recovered to 5,108,992 KiB; `ollama ps` remained empty |

The model request did not complete and produced no assessable caption. RSS
sampling covered processes whose `/proc/comm` was exactly `ollama`; it observed
up to 40,542,208 bytes, but does **not** establish total model-runner RSS.
No process was terminated by the experiment. No sustained sub-2.5-GiB period
was required to trigger the stop: the hard 2-GiB floor was reached first.

## Evaluation

No accuracy score is assigned because no response was received. These are
**not measured** rather than zero-point results:

| Criterion | Score / status |
|---|---|
| Correct object identification | Not measured |
| Spatial relationships | Not measured |
| Activity interpretation | Not measured |
| Hallucination resistance | Not measured |
| Evidence-grounded description | Not measured |
| Individual frame understanding | Not measured |
| Temporal sequence understanding | Not tested |
| Structured visual observations | Not tested |
| Timestamp-grounded evidence | Not tested |
| Resource efficiency | Failed the configured safety gate before response |

Only the first individual-image request was attempted (1 of the maximum 4).
The remaining three individual-image requests and both sequence requests were
not sent. Thus there are no observations about object recognition, activity,
spatial relations, JSON validity, temporal understanding, confidence
calibration, or model hallucination behavior.

## Suitability and limitations

The model is **not demonstrated suitable for CRE integration** by this
experiment. The local metadata establishes that the installed tag advertises
vision, but does not establish that it can safely complete even a single
bounded request on this host under the specified memory floor. No result
supports claims about video understanding, event selection, timestamp
grounding, identity tracking, or editing usefulness.

The attempt's 25.471 seconds is the time to the safety abort, not a model
response latency. The experiment is therefore inconclusive about output
quality and performance when an inference does complete. It also cannot
establish that a higher-memory host would succeed.

## Cleanup and verification

The generated media and per-attempt telemetry were kept only in the designated
temporary directory during analysis. After the requested documents and raw
results were written, `/tmp/ultron_cre_phase4_1` was removed and its absence
verified. The Ollama model files were not changed. The existing application
database was not opened or migrated. No CRE test suite was run because no
application code changed.

## Outcome

**BLOCKED: MODEL OR HARDWARE UNAVAILABLE** under the experiment's resource
guard. Continue only with a separately authorized feasibility run on a host
with substantially more available-memory headroom; do not begin CRE
integration or infer model quality from this canceled request.
