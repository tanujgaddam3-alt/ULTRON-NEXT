from __future__ import annotations

import hashlib
import base64
import io
import json
import math
import sqlite3
import struct
import subprocess
import wave

import pytest
from PIL import Image

from ultron.application.content import script_preview as script_preview_module
from ultron.application.content.script_scenes import plan_script_scenes
from ultron.application.content.script_scenes import (
    rendered_scene_composition,
    visual_theme,
    visual_treatment,
)
from ultron.application.content.script_scenes import SceneVisualRenderError
from ultron.application.content.script_preview import (
    OriginalScriptPreviewService,
    RenderedScriptPreview,
)
from ultron.application.content.store import ContentStore


SCRIPT = "Why do leaves change color? Chlorophyll breaks down as days get shorter."
MULTI_SCENE_SCRIPT = (
    "Plants absorb sunlight through chlorophyll in their leaves, which supports "
    "photosynthesis and stores energy for later growth. "
    "As daylight hours become shorter in autumn, chlorophyll breaks down and "
    "other pigments become visible across the leaves."
)
REFERENCES = [
    {"label": "Reference 1", "url": "https://example.org/leaf-color"}
]
def _mock_render(script=SCRIPT, content=b"mp4"):
    scene_plan = plan_script_scenes(
        script,
        max_scenes=OriginalScriptPreviewService.MAX_SCENES,
        max_characters_per_scene=OriginalScriptPreviewService.MAX_SCENE_CHARACTERS,
    )
    scenes = []
    script_sha256 = hashlib.sha256(script.encode("utf-8")).hexdigest()
    scene_contract_sha256 = OriginalScriptPreviewService._scene_contract_sha256(
        scene_plan
    )
    scene_start = 0.0
    for index, scene in enumerate(scene_plan):
        image = Image.new(
            "RGB",
            (720, 1280),
            (15 + index * 20, 25 + index * 10, 38 + index * 5),
        )
        png = io.BytesIO()
        image.save(png, format="PNG", optimize=True)
        image.close()
        diagram_bytes = png.getvalue()
        visual_asset = OriginalScriptPreviewService._scene_visual_asset(
            scene=scene,
            diagram_bytes=diagram_bytes,
            script_sha256=script_sha256,
            scene_contract_sha256=scene_contract_sha256,
            generated_at="2026-10-26T00:00:00+00:00",
        )
        speech_end = scene_start + 2.0
        visual_duration = 2.15 if index < len(scene_plan) - 1 else 2.0
        scenes.append(
            {
                "sceneId": scene.scene_id,
                "order": scene.order,
                "scriptOffsets": {
                    "start": scene.start_offset,
                    "endExclusive": scene.end_offset,
                },
                "excerpt": scene.excerpt,
                "excerptSha256": hashlib.sha256(scene.excerpt.encode("utf-8")).hexdigest(),
                "visualType": scene.visual_type,
                "sceneContract": scene.to_dict(),
                "narrationRef": scene.narration_ref,
                "captionRef": scene.caption_ref,
                "assetState": "GENERATED",
                "technicalValidation": "TECHNICAL_CHECKS_PASSED",
                "renderedComposition": rendered_scene_composition(scene),
                "visualTreatment": visual_treatment(scene),
                "diagramLabels": list(scene.labels),
                "diagramSha256": visual_asset["sha256"],
                "diagramBytes": visual_asset["byteSize"],
                "visualAsset": visual_asset,
                "speechStartSeconds": round(scene_start, 3),
                "speechEndSeconds": round(speech_end, 3),
                "measuredSpeechDurationSeconds": 2.0,
                "visualDurationSeconds": visual_duration,
                "caption": {
                    "startSeconds": round(round(scene_start, 3), 2),
                    "endSeconds": round(round(speech_end, 3), 2),
                    "text": scene.excerpt,
                    "scriptOffsets": {
                        "start": scene.start_offset,
                        "endExclusive": scene.end_offset,
                    },
                    "lineCount": 1,
                    "maximumLineWidthPx": 300,
                },
            }
        )
        scene_start += visual_duration
    pcm = bytearray()
    speech_samples = struct.pack("<h", 3276) * 48_000
    first_sample = struct.pack("<h", 16_422)
    for index in range(len(scene_plan)):
        scene_pcm = bytearray(speech_samples)
        scene_pcm[:2] = first_sample
        pcm.extend(scene_pcm)
        if index < len(scene_plan) - 1:
            pcm.extend(b"\x00\x00" * 3_600)
    source = io.BytesIO()
    with wave.open(source, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24_000)
        audio.writeframes(pcm)
    source_wav = source.getvalue()
    peak, square_sum, clipped, sample_count, quiet_windows, total_windows = (
        OriginalScriptPreviewService._measure_pcm(bytes(pcm))
    )
    rms = math.sqrt(square_sum / sample_count)
    manifest = {
        "workflow": "LOCAL_NARRATED_ORIGINAL_SCRIPT_PREVIEW",
        "scriptSha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
        "scenePlanSha256": OriginalScriptPreviewService._scene_plan_sha256(scene_plan),
        "sceneContractVersion": 2,
        "scenePlannerVersion": "deterministic-heuristic-v2",
        "sceneContractSha256": scene_contract_sha256,
        "sceneVisualEvidenceVersion": 2,
        "sceneRendererVersion": "pillow-scene-composer-v3-motion-xfade",
        "sceneVisualManifestSha256": (
            OriginalScriptPreviewService._scene_visual_manifest_sha256(scenes)
        ),
        "scenes": scenes,
        "narration": {
            "state": "GENERATED_LOCALLY",
            "voiceQuality": "NOT_HUMAN_REVIEWED",
            "intelligibility": "NOT_ASSESSED",
            "naturalness": "NOT_ASSESSED",
            "peakDbfs": round(20 * math.log10(peak / 32768), 2),
            "rmsDbfs": round(20 * math.log10(rms / 32768), 2),
            "quietWindowRatio": round(quiet_windows / total_windows, 4),
            "clippedSamples": clipped,
            "sampleCount": sample_count,
            "clippedSampleRatio": round(clipped / sample_count, 8),
            "sourceWavSha256": hashlib.sha256(source_wav).hexdigest(),
            "encodedAudioRmsDbfs": -20.0,
            "sceneBoundaryGaps": [
                {
                    "centerSeconds": 2.075 + (2.15 * index),
                    "windowMilliseconds": 50,
                    "rmsDbfs": -80.0,
                    "state": "INTENTIONAL_SILENCE_VERIFIED",
                }
                for index in range(len(scene_plan) - 1)
            ],
            "sampleRateHz": 24000,
            "channels": 1,
            "durationSeconds": scene_start,
        },
        "captions": {
            "state": "BURNED_IN",
            "safeArea": {
                "leftPx": 64,
                "rightPx": 64,
                "bottomPx": 220,
                "maximumWidthPx": 592,
                "fontFamily": "DejaVu Sans",
                "fontSizePx": 36,
                "maximumLines": 4,
            },
        },
        "video": {
            "container": "mp4",
            "artifactSha256": hashlib.sha256(content).hexdigest(),
            "artifactBytes": len(content),
            "width": 720,
            "height": 1280,
            "videoCodec": "h264",
            "audioCodec": "aac",
            "motion": {
                "mode": "restrained_alternating_zoom",
                "maxZoom": 1.05,
            },
            "transitions": {
                "type": "crossfade",
                "durationSeconds": 0.25,
                "count": len(scene_plan) - 1,
                "alignment": "CENTERED_ON_NARRATION_BOUNDARIES",
            },
            "audioSampleRateHz": 24000,
            "audioChannels": 1,
            "durationSeconds": scene_start,
            "audioDurationSeconds": scene_start,
            "videoDurationSeconds": scene_start,
        },
        "qualityAssurance": {"state": "TECHNICAL_CHECKS_PASSED"},
        "review": {
            "humanReviewState": "PENDING",
            "factualReviewState": "REQUIRED",
            "rightsState": "NOT_EVALUATED",
            "approvalState": "NOT_GRANTED",
            "publicationState": "NOT_PUBLISHED",
        },
    }
    return RenderedScriptPreview(content, manifest, source_wav)


def _service(tmp_path):
    store = ContentStore(tmp_path / "content.db")
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    return store, OriginalScriptPreviewService(
        store=store,
        staging_directory=staging,
    )


def test_script_preview_preserves_identity_and_reports_review_state(tmp_path, monkeypatch):
    store, service = _service(tmp_path)
    script = "  Original wording stays exact.\nSecond line.  "
    calls = []
    monkeypatch.setattr(
        service,
        "_render",
        lambda script_id, text: calls.append(text) or _mock_render(text),
    )
    try:
        result = service.create_preview(
            principal="operator",
            script=script,
            references=REFERENCES,
            command_id="script-preview-one",
        )

        assert result["script"] == script
        assert result["scriptSha256"] == hashlib.sha256(script.encode()).hexdigest()
        assert result["references"] == {
            "state": "UNVERIFIED",
            "items": REFERENCES,
        }
        assert result["preview"]["sha256"] == hashlib.sha256(b"mp4").hexdigest()
        assert result["preview"]["audioIncluded"] is True
        assert result["productionManifest"]["workflow"] == (
            _mock_render(script)[1]["workflow"]
        )
        assert result["audioEvidence"]["status"] == "VERIFIED"
        assert result["sceneVisualEvidence"]["state"] == "VERIFIED"
        assert result["productionManifest"]["sceneVisualEvidence"]["state"] == "VERIFIED"
        scene_visual = result["productionManifest"]["scenes"][0]["visualAsset"]
        assert scene_visual["format"] == "PNG"
        assert scene_visual["width"] == 720
        assert scene_visual["height"] == 1280
        assert scene_visual["validationStatus"] == "TECHNICAL_CHECKS_PASSED"
        assert scene_visual["visualTheme"] == visual_theme(script)
        assert scene_visual["visualTreatment"] == visual_treatment(
            plan_script_scenes(script)[0]
        )
        assert result["review"]["humanReviewState"] == "PENDING"
        assert result["review"]["factualReviewState"] == "REQUIRED"
        assert result["review"]["rightsState"] == "NOT_EVALUATED"
        assert result["review"]["approvalState"] == "NOT_GRANTED"
        assert result["review"]["publicationState"] == "NOT_PUBLISHED"
        assert result["review"]["editorial"]["state"] == "PENDING"
        assert calls == [script]
        events = store._connection.execute(
            """
            SELECT event_type FROM content_original_script_preview_events
            WHERE owner_id = ? ORDER BY sequence
            """,
            ("operator",),
        ).fetchall()
        assert [event["event_type"] for event in events] == [
            "PREVIEW_REQUESTED",
            "PREVIEW_RENDERED",
        ]
        assert store._connection.execute(
            "SELECT script_text FROM content_original_script_previews"
        ).fetchone()["script_text"] == script
    finally:
        store.close()


def test_scene_visual_assets_are_durable_and_owner_scoped_in_status(tmp_path, monkeypatch):
    store, service = _service(tmp_path)
    monkeypatch.setattr(service, "_render", lambda script_id, text: _mock_render(text))
    try:
        preview = service.create_preview(
            principal="operator",
            script=SCRIPT,
            references=[],
            command_id="scene-visual-readback",
        )
        listed = service.list_audio_evidence(principal="operator")["items"][0]
        scene_summary = listed["sceneVisualEvidence"]
        assert scene_summary["state"] == "VERIFIED"
        assert scene_summary["scenes"][0]["state"] == "VERIFIED"
        assert scene_summary["scenes"][0]["asset"]["sha256"]
        assert "dataBase64" not in scene_summary["scenes"][0]["asset"]

        readback = service._response(
            preview["scriptId"],
            "operator",
            include_artifact=False,
        )
        asset = readback["productionManifest"]["scenes"][0]["visualAsset"]
        image_bytes = base64.b64decode(asset["dataBase64"], validate=True)
        assert hashlib.sha256(image_bytes).hexdigest() == asset["sha256"]
        assert asset["assetId"] == scene_summary["scenes"][0]["asset"]["assetId"]
        assert not list((tmp_path / "staging").glob("*.png"))
    finally:
        store.close()


def test_tampered_scene_visual_is_integrity_failure_and_downgrades_qa(
    tmp_path, monkeypatch
):
    store, service = _service(tmp_path)
    monkeypatch.setattr(service, "_render", lambda script_id, text: _mock_render(text))
    try:
        preview = service.create_preview(
            principal="operator",
            script=SCRIPT,
            references=[],
            command_id="scene-visual-tamper",
        )
        store._connection.execute(
            "DROP TRIGGER content_original_script_preview_events_no_update"
        )
        event = store._connection.execute(
            """
            SELECT sequence, detail_json
            FROM content_original_script_preview_events
            WHERE script_id = ? AND event_type = 'PREVIEW_RENDERED'
            """,
            (preview["scriptId"],),
        ).fetchone()
        details = json.loads(event["detail_json"])
        encoded = details["manifest"]["scenes"][0]["visualAsset"]["dataBase64"]
        details["manifest"]["scenes"][0]["visualAsset"]["dataBase64"] = (
            ("A" if encoded[0] != "A" else "B") + encoded[1:]
        )
        store._connection.execute(
            """
            UPDATE content_original_script_preview_events
            SET detail_json = ? WHERE sequence = ?
            """,
            (json.dumps(details), event["sequence"]),
        )

        readback = service._response(
            preview["scriptId"],
            "operator",
            include_artifact=False,
        )
        assert readback["sceneVisualEvidence"]["state"] == "INTEGRITY_FAILURE"
        assert readback["productionManifest"]["scenes"][0]["visualAsset"] is None
        assert readback["productionManifest"]["qualityAssurance"]["state"] == (
            "SCENE_VISUAL_EVIDENCE_INTEGRITY_FAILURE"
        )
        listed = service.list_audio_evidence(principal="operator")["items"][0]
        assert listed["sceneVisualEvidence"]["state"] == "INTEGRITY_FAILURE"
    finally:
        store.close()


def test_legacy_preview_without_visual_evidence_is_not_upgraded_to_verified(
    tmp_path, monkeypatch
):
    store, service = _service(tmp_path)
    monkeypatch.setattr(service, "_render", lambda script_id, text: _mock_render(text))
    try:
        preview = service.create_preview(
            principal="operator",
            script=SCRIPT,
            references=[],
            command_id="legacy-scene-visual",
        )
        store._connection.execute(
            "DROP TRIGGER content_original_script_preview_events_no_update"
        )
        event = store._connection.execute(
            """
            SELECT sequence, detail_json
            FROM content_original_script_preview_events
            WHERE script_id = ? AND event_type = 'PREVIEW_RENDERED'
            """,
            (preview["scriptId"],),
        ).fetchone()
        details = json.loads(event["detail_json"])
        details["manifest"].pop("sceneVisualEvidenceVersion")
        store._connection.execute(
            """
            UPDATE content_original_script_preview_events
            SET detail_json = ? WHERE sequence = ?
            """,
            (json.dumps(details), event["sequence"]),
        )

        readback = service._response(
            preview["scriptId"],
            "operator",
            include_artifact=False,
        )
        assert readback["sceneVisualEvidence"]["state"] == "MISSING"
        assert readback["productionManifest"]["scenes"][0]["visualAsset"] is None
        assert readback["productionManifest"]["qualityAssurance"]["state"] == (
            "SCENE_VISUAL_EVIDENCE_MISSING"
        )
    finally:
        store.close()


def test_stored_renderer_v2_visual_evidence_remains_verifiable(tmp_path, monkeypatch):
    store, service = _service(tmp_path)
    monkeypatch.setattr(service, "_render", lambda script_id, text: _mock_render(text))
    try:
        preview = service.create_preview(
            principal="operator",
            script=SCRIPT,
            references=[],
            command_id="legacy-scene-renderer-v2",
        )
        store._connection.execute(
            "DROP TRIGGER content_original_script_preview_events_no_update"
        )
        event = store._connection.execute(
            """
            SELECT sequence, detail_json
            FROM content_original_script_preview_events
            WHERE script_id = ? AND event_type = 'PREVIEW_RENDERED'
            """,
            (preview["scriptId"],),
        ).fetchone()
        details = json.loads(event["detail_json"])
        manifest = details["manifest"]
        manifest["sceneVisualEvidenceVersion"] = (
            OriginalScriptPreviewService.LEGACY_SCENE_VISUAL_EVIDENCE_VERSION
        )
        manifest["sceneRendererVersion"] = (
            OriginalScriptPreviewService.LEGACY_SCENE_RENDERER_VERSION
        )
        for scene in manifest["scenes"]:
            scene.pop("visualTreatment", None)
            asset = scene["visualAsset"]
            asset.pop("visualTheme")
            asset.pop("visualTreatment")
            asset["rendererVersion"] = (
                OriginalScriptPreviewService.LEGACY_SCENE_RENDERER_VERSION
            )
            identity = {
                "composition": asset["composition"],
                "rendererVersion": asset["rendererVersion"],
                "sceneContractSha256": asset["sceneContractSha256"],
                "sceneId": scene["sceneId"],
                "visualSha256": asset["sha256"],
            }
            asset["assetId"] = hashlib.sha256(
                OriginalScriptPreviewService._canonical(identity).encode("utf-8")
            ).hexdigest()
        manifest["sceneVisualManifestSha256"] = (
            OriginalScriptPreviewService._scene_visual_manifest_sha256(
                manifest["scenes"]
            )
        )
        store._connection.execute(
            """
            UPDATE content_original_script_preview_events
            SET detail_json = ? WHERE sequence = ?
            """,
            (json.dumps(details), event["sequence"]),
        )

        readback = service._response(
            preview["scriptId"],
            "operator",
            include_artifact=False,
        )

        assert readback["sceneVisualEvidence"]["state"] == "VERIFIED"
        assert readback["productionManifest"]["sceneRendererVersion"] == (
            OriginalScriptPreviewService.LEGACY_SCENE_RENDERER_VERSION
        )
        assert readback["productionManifest"]["sceneVisualEvidenceVersion"] == 1
        assert all(
            "visualTheme" not in scene["visualAsset"]
            and "visualTreatment" not in scene["visualAsset"]
            for scene in readback["productionManifest"]["scenes"]
        )
    finally:
        store.close()


def test_script_preview_command_is_idempotent_and_rejects_changed_payload(
    tmp_path, monkeypatch
):
    store, service = _service(tmp_path)
    calls = []
    monkeypatch.setattr(
        service,
        "_render",
        lambda script_id, text: calls.append(text) or _mock_render(text),
    )
    try:
        first = service.create_preview(
            principal="operator",
            script=SCRIPT,
            references=REFERENCES,
            command_id="same-command",
        )
        second = service.create_preview(
            principal="operator",
            script=SCRIPT,
            references=REFERENCES,
            command_id="same-command",
        )
        assert second["scriptId"] == first["scriptId"]
        assert calls == [SCRIPT]
        evidence = store._connection.execute(
            """
            SELECT evidence_id, source_wav_sha256, source_wav
            FROM content_original_script_preview_audio_evidence
            WHERE script_id = ?
            """,
            (first["scriptId"],),
        ).fetchone()
        assert evidence["evidence_id"] == second["audioEvidence"]["evidenceId"]
        assert hashlib.sha256(evidence["source_wav"]).hexdigest() == (
            evidence["source_wav_sha256"]
        )
        with pytest.raises(ValueError, match="different script request"):
            service.create_preview(
                principal="operator",
                script=SCRIPT + " Changed.",
                references=REFERENCES,
                command_id="same-command",
            )
    finally:
        store.close()


def test_script_preview_failure_is_audited_and_not_retried(tmp_path, monkeypatch):
    store, service = _service(tmp_path)
    calls = []

    def fail(script_id, script):
        calls.append(script_id)
        raise RuntimeError("renderer detail must not be stored")

    monkeypatch.setattr(service, "_render", fail)
    try:
        with pytest.raises(RuntimeError, match="renderer detail"):
            service.create_preview(
                principal="operator",
                script=SCRIPT,
                references=REFERENCES,
                command_id="failed-command",
            )
        row = store._connection.execute(
            "SELECT state FROM content_original_script_previews"
        ).fetchone()
        assert row["state"] == "FAILED"
        event = store._connection.execute(
            """
            SELECT event_type, detail_json
            FROM content_original_script_preview_events
            WHERE event_type = 'PREVIEW_FAILED'
            """
        ).fetchone()
        assert event["event_type"] == "PREVIEW_FAILED"
        assert "renderer detail" not in event["detail_json"]
        with pytest.raises(RuntimeError, match="previously failed"):
            service.create_preview(
                principal="operator",
                script=SCRIPT,
                references=REFERENCES,
                command_id="failed-command",
            )
        assert len(calls) == 1
    finally:
        store.close()


def test_scene_render_failure_audit_identifies_failed_scene_safely(
    tmp_path, monkeypatch
):
    store, service = _service(tmp_path)

    def fail_render(script_id, script):
        raise SceneVisualRenderError("scene-02", "SCENE_COMPOSITION_FAILED")

    monkeypatch.setattr(service, "_render", fail_render)
    try:
        with pytest.raises(SceneVisualRenderError, match="scene-02"):
            service.create_preview(
                principal="operator",
                script=SCRIPT,
                references=[],
                command_id="scene-render-failure",
            )
        event = store._connection.execute(
            """
            SELECT detail_json FROM content_original_script_preview_events
            WHERE event_type = 'PREVIEW_FAILED'
            """
        ).fetchone()
        failure = json.loads(event["detail_json"])
        assert failure["failedSceneId"] == "scene-02"
        assert failure["failureReason"] == "SCENE_COMPOSITION_FAILED"
        assert "path" not in failure
        assert "script" not in failure
    finally:
        store.close()


def test_oversized_preview_is_recorded_as_failed_without_persisting_artifact(
    tmp_path, monkeypatch
):
    store, service = _service(tmp_path)
    monkeypatch.setattr(
        service,
        "_render",
        lambda script_id, script: _mock_render(
            script,
            b"x" * (service.MAX_ARTIFACT_BYTES + 1)
        ),
    )
    try:
        with pytest.raises(ValueError, match="size limit"):
            service.create_preview(
                principal="operator",
                script=SCRIPT,
                references=[],
                command_id="oversized-preview",
            )
        row = store._connection.execute(
            """
            SELECT state, artifact_mp4, artifact_sha256
            FROM content_original_script_previews
            """
        ).fetchone()
        assert row["state"] == "FAILED"
        assert row["artifact_mp4"] is None
        assert row["artifact_sha256"] is None
    finally:
        store.close()


def test_preview_rejects_manifest_that_does_not_bind_script_and_artifact(
    tmp_path,
    monkeypatch,
):
    store, service = _service(tmp_path)
    artifact, manifest, _ = _mock_render(SCRIPT)
    manifest["video"]["artifactSha256"] = "f" * 64
    monkeypatch.setattr(
        service,
        "_render",
        lambda script_id, script: RenderedScriptPreview(
            artifact, manifest, _mock_render(script)[2]
        ),
    )
    try:
        with pytest.raises(ValueError, match="output contract"):
            service.create_preview(
                principal="operator",
                script=SCRIPT,
                references=[],
                command_id="manifest-hash-mismatch",
            )
        row = store._connection.execute(
            "SELECT state, artifact_mp4 FROM content_original_script_previews"
        ).fetchone()
        assert row["state"] == "FAILED"
        assert row["artifact_mp4"] is None
    finally:
        store.close()


def test_preview_rejects_manifest_with_inconsistent_clipped_sample_ratio(
    tmp_path,
    monkeypatch,
):
    store, service = _service(tmp_path)
    artifact, manifest, _ = _mock_render(SCRIPT)
    manifest["narration"]["sampleCount"] = 100
    manifest["narration"]["clippedSamples"] = 1
    manifest["narration"]["clippedSampleRatio"] = 0.01
    monkeypatch.setattr(
        service,
        "_render",
        lambda script_id, script: RenderedScriptPreview(
            artifact, manifest, _mock_render(script)[2]
        ),
    )
    try:
        with pytest.raises(ValueError, match="output contract"):
            service.create_preview(
                principal="operator",
                script=SCRIPT,
                references=[],
                command_id="manifest-clipping-ratio-mismatch",
            )
        row = store._connection.execute(
            "SELECT state, artifact_mp4 FROM content_original_script_previews"
        ).fetchone()
        assert row["state"] == "FAILED"
        assert row["artifact_mp4"] is None
    finally:
        store.close()


def test_durable_audio_evidence_is_independently_recomputed_after_store_reopen(
    tmp_path, monkeypatch
):
    store, service = _service(tmp_path)
    database = tmp_path / "content.db"
    monkeypatch.setattr(
        service,
        "_render",
        lambda script_id, text: _mock_render(text),
    )
    try:
        result = service.create_preview(
            principal="operator",
            script=SCRIPT,
            references=[],
            command_id="durable-evidence-roundtrip",
        )
        record = store._connection.execute(
            """
            SELECT source_wav, source_wav_sha256, sample_count,
                   clipped_samples, clipped_sample_ratio
            FROM content_original_script_preview_audio_evidence
            WHERE script_id = ?
            """,
            (result["scriptId"],),
        ).fetchone()
        source_wav = bytes(record["source_wav"])
        assert hashlib.sha256(source_wav).hexdigest() == record["source_wav_sha256"]
        with wave.open(io.BytesIO(source_wav), "rb") as audio:
            assert audio.getframerate() == 24_000
            assert audio.getnchannels() == 1
            assert audio.getsampwidth() == 2
            samples = [
                sample
                for (sample,) in struct.iter_unpack(
                    "<h", audio.readframes(audio.getnframes())
                )
            ]
        expected_clipped = sum(abs(sample) >= 32760 for sample in samples)
        assert record["sample_count"] == len(samples)
        assert record["clipped_samples"] == expected_clipped
        assert record["clipped_sample_ratio"] == expected_clipped / len(samples)
    finally:
        store.close()

    reopened = ContentStore(database)
    reopened_service = OriginalScriptPreviewService(
        store=reopened,
        staging_directory=tmp_path / "staging",
    )
    try:
        readback = reopened_service._response(
            result["scriptId"],
            "operator",
            include_artifact=False,
        )
        evidence = readback["audioEvidence"]
        assert evidence["status"] == "VERIFIED"
        assert evidence["sampleCount"] == len(samples)
        assert evidence["clippedSamples"] == expected_clipped
        assert evidence["clippedSampleRatio"] == expected_clipped / len(samples)
        assert evidence["createdAt"]
        assert evidence["lastCheckedAt"]
        assert evidence["lastVerifiedAt"] == evidence["lastCheckedAt"]
        assert readback["productionManifest"]["qualityAssurance"]["state"] == (
            "TECHNICAL_CHECKS_PASSED"
        )
        assert readback["productionManifest"]["audioEvidence"]["status"] == (
            "VERIFIED"
        )
        assert readback["productionManifest"]["audioEvidence"]["renderTimeStatus"] == (
            "VERIFIED"
        )
        assert readback["productionManifest"]["audioEvidence"]["statusScope"] == (
            "CURRENT_READBACK"
        )
    finally:
        reopened.close()


def test_altered_audio_evidence_is_reported_and_cannot_keep_qa_passing(
    tmp_path, monkeypatch
):
    store, service = _service(tmp_path)
    monkeypatch.setattr(service, "_render", lambda script_id, text: _mock_render(text))
    try:
        preview = service.create_preview(
            principal="operator",
            script=SCRIPT,
            references=[],
            command_id="altered-audio-evidence",
        )
        store._connection.execute(
            "DROP TRIGGER content_original_script_preview_audio_evidence_immutable"
        )
        source_wav = bytes(
            store._connection.execute(
                """
                SELECT source_wav FROM content_original_script_preview_audio_evidence
                WHERE script_id = ?
                """,
                (preview["scriptId"],),
            ).fetchone()["source_wav"]
        )
        store._connection.execute(
            """
            UPDATE content_original_script_preview_audio_evidence
            SET source_wav = ?
            WHERE script_id = ?
            """,
            (
                source_wav[:44] + b"\x01\x00" + source_wav[46:],
                preview["scriptId"],
            ),
        )
        response = service._response(
            preview["scriptId"],
            "operator",
            include_artifact=False,
        )
        assert response["audioEvidence"]["status"] == "INTEGRITY_FAILURE"
        assert response["audioEvidence"]["failureReason"] == "SOURCE_HASH_MISMATCH"
        assert response["productionManifest"]["qualityAssurance"]["state"] == (
            "AUDIO_EVIDENCE_INTEGRITY_FAILURE"
        )
        assert response["productionManifest"]["audioEvidence"]["status"] == (
            "INTEGRITY_FAILURE"
        )
        assert response["productionManifest"]["audioEvidence"]["renderTimeStatus"] == (
            "VERIFIED"
        )
        assert response["audioEvidence"]["sampleCount"] is None
        assert response["audioEvidence"]["lastVerifiedAt"]
    finally:
        store.close()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("sample_count", 1),
        ("clipped_samples", 1),
        ("clipped_sample_ratio", 0.0005),
        ("script_sha256", "f" * 64),
        ("artifact_sha256", "f" * 64),
    ],
)
def test_audio_evidence_metric_and_binding_mismatches_fail_verification(
    tmp_path, monkeypatch, column, value
):
    store, service = _service(tmp_path)
    monkeypatch.setattr(service, "_render", lambda script_id, text: _mock_render(text))
    try:
        preview = service.create_preview(
            principal="operator",
            script=SCRIPT,
            references=[],
            command_id=f"evidence-mismatch-{column}",
        )
        store._connection.execute(
            "DROP TRIGGER content_original_script_preview_audio_evidence_immutable"
        )
        store._connection.execute(
            f"""
            UPDATE content_original_script_preview_audio_evidence
            SET {column} = ?
            WHERE script_id = ?
            """,
            (value, preview["scriptId"]),
        )
        result = service.list_audio_evidence(principal="operator")
        evidence = result["items"][0]["audioEvidence"]
        assert evidence["status"] == "INTEGRITY_FAILURE"
        assert evidence["sampleCount"] is None
        assert evidence["clippedSamples"] is None
        assert evidence["clippedSampleRatio"] is None
        if column == "script_sha256":
            assert evidence["scriptBinding"] == "FAILED"
        if column == "artifact_sha256":
            assert evidence["artifactBinding"] == "FAILED"
    finally:
        store.close()


def test_replacing_source_wav_and_removing_new_evidence_are_not_verified(
    tmp_path, monkeypatch
):
    store, service = _service(tmp_path)
    monkeypatch.setattr(service, "_render", lambda script_id, text: _mock_render(text))
    try:
        preview = service.create_preview(
            principal="operator",
            script=SCRIPT,
            references=[],
            command_id="replace-source-wav",
        )
        source = bytearray(
            store._connection.execute(
                "SELECT source_wav FROM content_original_script_preview_audio_evidence"
            ).fetchone()["source_wav"]
        )
        source[44:46] = struct.pack("<h", 123)
        store._connection.execute(
            "DROP TRIGGER content_original_script_preview_audio_evidence_immutable"
        )
        store._connection.execute(
            """
            UPDATE content_original_script_preview_audio_evidence
            SET source_wav = ?
            WHERE script_id = ?
            """,
            (bytes(source), preview["scriptId"]),
        )
        evidence = service.list_audio_evidence(principal="operator")["items"][0][
            "audioEvidence"
        ]
        assert evidence["status"] == "INTEGRITY_FAILURE"
        assert evidence["sourceBinding"] == "FAILED"

        store._connection.execute(
            "DROP TRIGGER content_original_script_preview_audio_evidence_no_delete"
        )
        store._connection.execute(
            "DELETE FROM content_original_script_preview_audio_evidence WHERE script_id = ?",
            (preview["scriptId"],),
        )
        missing = service.list_audio_evidence(principal="operator")["items"][0][
            "audioEvidence"
        ]
        assert missing["status"] == "MISSING"
        assert missing["legacy"] is False
        assert missing["sampleCount"] is None
        response = service._response(
            preview["scriptId"],
            "operator",
            include_artifact=False,
        )
        assert response["productionManifest"]["qualityAssurance"]["state"] == (
            "AUDIO_EVIDENCE_MISSING"
        )
        assert response["productionManifest"]["audioEvidence"]["status"] == "MISSING"
    finally:
        store.close()


def test_legacy_preview_without_audio_evidence_is_unverifiable_not_verified(
    tmp_path, monkeypatch
):
    store, service = _service(tmp_path)
    monkeypatch.setattr(service, "_render", lambda script_id, text: _mock_render(text))
    try:
        preview = service.create_preview(
            principal="operator",
            script=SCRIPT,
            references=[],
            command_id="legacy-preview-evidence",
        )
        store._connection.execute(
            "DROP TRIGGER content_original_script_previews_audio_evidence_requirement_immutable"
        )
        store._connection.execute(
            """
            UPDATE content_original_script_previews
            SET audio_evidence_required = 0
            WHERE script_id = ?
            """,
            (preview["scriptId"],),
        )
        store._connection.execute(
            "DROP TRIGGER content_original_script_preview_audio_evidence_no_delete"
        )
        store._connection.execute(
            "DELETE FROM content_original_script_preview_audio_evidence WHERE script_id = ?",
            (preview["scriptId"],),
        )
        store._connection.execute(
            "DROP TRIGGER content_original_script_preview_events_no_update"
        )
        event = store._connection.execute(
            """
            SELECT sequence, detail_json
            FROM content_original_script_preview_events
            WHERE script_id = ? AND event_type = 'PREVIEW_RENDERED'
            """,
            (preview["scriptId"],),
        ).fetchone()
        details = json.loads(event["detail_json"])
        details["manifest"].pop("audioEvidence")
        narration = details["manifest"]["narration"]
        narration.pop("sampleCount", None)
        narration.pop("clippedSampleRatio", None)
        narration.pop("sourceWavSha256", None)
        store._connection.execute(
            """
            UPDATE content_original_script_preview_events
            SET detail_json = ? WHERE sequence = ?
            """,
            (json.dumps(details), event["sequence"]),
        )
        readback = service._response(
            preview["scriptId"],
            "operator",
            include_artifact=False,
        )
        assert readback["audioEvidence"]["status"] == "UNVERIFIABLE"
        assert readback["audioEvidence"]["legacy"] is True
        assert readback["audioEvidence"]["sampleCount"] is None
        assert readback["productionManifest"]["audioEvidence"]["status"] == (
            "UNVERIFIABLE"
        )
        assert readback["productionManifest"]["audioEvidence"]["renderTimeStatus"] is None
        assert readback["productionManifest"]["qualityAssurance"]["state"] == (
            "AUDIO_EVIDENCE_UNVERIFIABLE"
        )
    finally:
        store.close()


def test_explicit_null_audio_evidence_is_malformed_not_legacy(tmp_path, monkeypatch):
    store, service = _service(tmp_path)
    monkeypatch.setattr(service, "_render", lambda script_id, text: _mock_render(text))
    try:
        preview = service.create_preview(
            principal="operator",
            script=SCRIPT,
            references=[],
            command_id="null-audio-evidence",
        )
        store._connection.execute(
            "DROP TRIGGER content_original_script_preview_audio_evidence_no_delete"
        )
        store._connection.execute(
            "DELETE FROM content_original_script_preview_audio_evidence WHERE script_id = ?",
            (preview["scriptId"],),
        )
        store._connection.execute(
            "DROP TRIGGER content_original_script_preview_events_no_update"
        )
        event = store._connection.execute(
            """
            SELECT sequence, detail_json
            FROM content_original_script_preview_events
            WHERE script_id = ? AND event_type = 'PREVIEW_RENDERED'
            """,
            (preview["scriptId"],),
        ).fetchone()
        details = json.loads(event["detail_json"])
        details["manifest"]["audioEvidence"] = None
        store._connection.execute(
            "UPDATE content_original_script_preview_events SET detail_json = ? WHERE sequence = ?",
            (json.dumps(details), event["sequence"]),
        )

        response = service._response(
            preview["scriptId"],
            "operator",
            include_artifact=False,
        )
        assert response["audioEvidence"]["status"] == "INTEGRITY_FAILURE"
        assert response["audioEvidence"]["legacy"] is False
        assert response["productionManifest"]["audioEvidence"]["status"] == (
            "INTEGRITY_FAILURE"
        )
        assert response["productionManifest"]["qualityAssurance"]["state"] == (
            "AUDIO_EVIDENCE_INTEGRITY_FAILURE"
        )
    finally:
        store.close()


def test_missing_evidence_declaration_on_new_preview_is_not_legacy(
    tmp_path, monkeypatch
):
    store, service = _service(tmp_path)
    monkeypatch.setattr(service, "_render", lambda script_id, text: _mock_render(text))
    try:
        preview = service.create_preview(
            principal="operator",
            script=SCRIPT,
            references=[],
            command_id="missing-new-audio-evidence",
        )
        store._connection.execute(
            "DROP TRIGGER content_original_script_preview_audio_evidence_no_delete"
        )
        store._connection.execute(
            """
            DELETE FROM content_original_script_preview_audio_evidence
            WHERE script_id = ?
            """,
            (preview["scriptId"],),
        )
        store._connection.execute(
            "DROP TRIGGER content_original_script_preview_events_no_update"
        )
        event = store._connection.execute(
            """
            SELECT sequence, detail_json
            FROM content_original_script_preview_events
            WHERE script_id = ? AND event_type = 'PREVIEW_RENDERED'
            """,
            (preview["scriptId"],),
        ).fetchone()
        details = json.loads(event["detail_json"])
        details["manifest"].pop("audioEvidence")
        store._connection.execute(
            """
            UPDATE content_original_script_preview_events
            SET detail_json = ? WHERE sequence = ?
            """,
            (json.dumps(details), event["sequence"]),
        )

        response = service._response(
            preview["scriptId"],
            "operator",
            include_artifact=False,
        )
        assert response["audioEvidence"]["status"] == "INTEGRITY_FAILURE"
        assert response["audioEvidence"]["legacy"] is False
        assert response["productionManifest"]["audioEvidence"]["status"] == (
            "INTEGRITY_FAILURE"
        )
        assert response["productionManifest"]["qualityAssurance"]["state"] == (
            "AUDIO_EVIDENCE_INTEGRITY_FAILURE"
        )
    finally:
        store.close()


def test_unsupported_evidence_algorithm_cannot_verify(tmp_path, monkeypatch):
    store, service = _service(tmp_path)
    monkeypatch.setattr(service, "_render", lambda script_id, text: _mock_render(text))
    try:
        preview = service.create_preview(
            principal="operator",
            script=SCRIPT,
            references=[],
            command_id="unsupported-audio-algorithm",
        )
        store._connection.execute(
            "DROP TRIGGER content_original_script_preview_audio_evidence_immutable"
        )
        store._connection.execute(
            """
            UPDATE content_original_script_preview_audio_evidence
            SET algorithm_version = 'UNSUPPORTED'
            WHERE script_id = ?
            """,
            (preview["scriptId"],),
        )
        response = service._response(
            preview["scriptId"],
            "operator",
            include_artifact=False,
        )
        assert response["audioEvidence"]["status"] == "INTEGRITY_FAILURE"
        assert response["audioEvidence"]["failureReason"] == "EVIDENCE_BINDING_MISMATCH"
        assert response["productionManifest"]["audioEvidence"]["status"] == (
            "INTEGRITY_FAILURE"
        )
        assert response["productionManifest"]["qualityAssurance"]["state"] == (
            "AUDIO_EVIDENCE_INTEGRITY_FAILURE"
        )
    finally:
        store.close()


@pytest.mark.parametrize(
    ("column", "value", "database_rejects"),
    [
        ("sample_count", 1.5, False),
        ("sample_count", 1_296_001, True),
        ("clipped_samples", 0.5, False),
        ("clipped_samples", 1_296_001, True),
        ("clipped_sample_ratio", 0.0011, True),
    ],
)
def test_invalid_numeric_evidence_is_rejected_or_cannot_verify(
    tmp_path, monkeypatch, column, value, database_rejects
):
    store, service = _service(tmp_path)
    monkeypatch.setattr(service, "_render", lambda script_id, text: _mock_render(text))
    try:
        preview = service.create_preview(
            principal="operator",
            script=SCRIPT,
            references=[],
            command_id=f"invalid-audio-number-{column}-{value}",
        )
        store._connection.execute(
            "DROP TRIGGER content_original_script_preview_audio_evidence_immutable"
        )
        sql = f"""
            UPDATE content_original_script_preview_audio_evidence
            SET {column} = ?
            WHERE script_id = ?
        """
        if database_rejects:
            with pytest.raises(sqlite3.IntegrityError):
                store._connection.execute(sql, (value, preview["scriptId"]))
            assert service._response(
                preview["scriptId"],
                "operator",
                include_artifact=False,
            )["audioEvidence"]["status"] == "VERIFIED"
        else:
            store._connection.execute(sql, (value, preview["scriptId"]))
            response = service._response(
                preview["scriptId"],
                "operator",
                include_artifact=False,
            )
            assert response["audioEvidence"]["status"] == "INTEGRITY_FAILURE"
            assert response["productionManifest"]["qualityAssurance"]["state"] == (
                "AUDIO_EVIDENCE_INTEGRITY_FAILURE"
            )
    finally:
        store.close()


def test_evidence_persistence_failure_marks_preview_failed_and_never_success(
    tmp_path, monkeypatch
):
    store, service = _service(tmp_path)
    monkeypatch.setattr(service, "_render", lambda script_id, text: _mock_render(text))
    store._connection.execute(
        """
        CREATE TRIGGER reject_audio_evidence_insert
        BEFORE INSERT ON content_original_script_preview_audio_evidence
        BEGIN
            SELECT RAISE(ABORT, 'synthetic evidence persistence failure');
        END
        """
    )
    try:
        with pytest.raises(sqlite3.IntegrityError, match="synthetic evidence"):
            service.create_preview(
                principal="operator",
                script=SCRIPT,
                references=[],
                command_id="evidence-persistence-failure",
            )
        row = store._connection.execute(
            """
            SELECT state, artifact_mp4, artifact_sha256
            FROM content_original_script_previews
            """
        ).fetchone()
        assert row["state"] == "FAILED"
        assert row["artifact_mp4"] is None
        assert row["artifact_sha256"] is None
        assert store._connection.execute(
            "SELECT COUNT(*) FROM content_original_script_preview_audio_evidence"
        ).fetchone()[0] == 0
        assert store._connection.execute(
            """
            SELECT event_type FROM content_original_script_preview_events
            ORDER BY sequence DESC LIMIT 1
            """
        ).fetchone()["event_type"] == "PREVIEW_FAILED"
    finally:
        store.close()


def test_audio_evidence_schema_missing_from_v10_is_rejected_on_restart(tmp_path):
    database = tmp_path / "additive-evidence.db"
    store = ContentStore(database)
    store.close()

    connection = sqlite3.connect(database)
    connection.execute(
        "DROP TABLE content_original_script_preview_audio_evidence"
    )
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="incomplete or inconsistent"):
        ContentStore(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
        ).fetchone()[0] == 10
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'content_original_script_preview_audio_evidence'"
        ).fetchone() is None


def test_incomplete_v10_preview_schema_is_rejected_without_backfill(
    tmp_path, monkeypatch
):
    database = tmp_path / "content.db"
    store, service = _service(tmp_path)
    monkeypatch.setattr(service, "_render", lambda script_id, text: _mock_render(text))
    preview = service.create_preview(
        principal="operator",
        script=SCRIPT,
        references=[],
        command_id="evidence-requirement-backfill",
    )
    legacy = service.create_preview(
        principal="operator",
        script=SCRIPT,
        references=[],
        command_id="legacy-preview-before-requirement-column",
    )
    store._connection.execute(
        "DROP TRIGGER content_original_script_previews_audio_evidence_requirement_immutable"
    )
    store._connection.execute(
        """
        UPDATE content_original_script_previews
        SET audio_evidence_required = 0 WHERE script_id = ?
        """,
        (legacy["scriptId"],),
    )
    store._connection.execute(
        "DROP TRIGGER content_original_script_preview_audio_evidence_no_delete"
    )
    store._connection.execute(
        """
        DELETE FROM content_original_script_preview_audio_evidence
        WHERE script_id = ?
        """,
        (legacy["scriptId"],),
    )
    store._connection.execute(
        "DROP TRIGGER content_original_script_preview_events_no_update"
    )
    legacy_event = store._connection.execute(
        """
        SELECT sequence, detail_json
        FROM content_original_script_preview_events
        WHERE script_id = ? AND event_type = 'PREVIEW_RENDERED'
        """,
        (legacy["scriptId"],),
    ).fetchone()
    legacy_details = json.loads(legacy_event["detail_json"])
    legacy_details["manifest"].pop("audioEvidence")
    for field in ("sampleCount", "clippedSampleRatio", "sourceWavSha256"):
        legacy_details["manifest"]["narration"].pop(field, None)
    store._connection.execute(
        """
        UPDATE content_original_script_preview_events
        SET detail_json = ? WHERE sequence = ?
        """,
        (json.dumps(legacy_details), legacy_event["sequence"]),
    )
    store._connection.execute(
        "ALTER TABLE content_original_script_previews DROP COLUMN audio_evidence_required"
    )
    store.close()

    with pytest.raises(RuntimeError, match="incomplete or inconsistent"):
        ContentStore(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT 1 FROM pragma_table_info('content_original_script_previews') "
            "WHERE name = 'audio_evidence_required'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT schema_version FROM content_schema_meta WHERE singleton = 1"
        ).fetchone()[0] == 10


def test_effectively_silent_local_narration_fails_and_is_audited(
    tmp_path,
    monkeypatch,
):
    store, service = _service(tmp_path)
    monkeypatch.setattr(
        service,
        "_measure_pcm",
        lambda pcm: (1, 100, 0, len(pcm) // 2, 1, 1),
    )
    try:
        with pytest.raises(ValueError, match="effectively silent"):
            service.create_preview(
                principal="operator",
                script=SCRIPT,
                references=[],
                command_id="effectively-silent-narration",
            )
        row = store._connection.execute(
            "SELECT state, artifact_mp4 FROM content_original_script_previews"
        ).fetchone()
        assert row["state"] == "FAILED"
        assert row["artifact_mp4"] is None
        assert not tuple(service.staging_directory.iterdir())
    finally:
        store.close()


def test_excessive_narration_peak_fails_closed(tmp_path, monkeypatch):
    store, service = _service(tmp_path)
    monkeypatch.setattr(
        service,
        "_measure_pcm",
        lambda pcm: (32767, 32767 * 32767 * 4800, 0, 4800, 0, 2),
    )
    try:
        with pytest.raises(ValueError, match="peak exceeded the headroom limit"):
            service.create_preview(
                principal="operator",
                script=SCRIPT,
                references=[],
                command_id="excessive-narration-peak",
            )
        row = store._connection.execute(
            "SELECT state, artifact_mp4 FROM content_original_script_previews"
        ).fetchone()
        assert row["state"] == "FAILED"
        assert row["artifact_mp4"] is None
        assert not tuple(service.staging_directory.iterdir())
    finally:
        store.close()


def test_editorial_review_is_hash_bound_idempotent_and_not_an_approval(
    tmp_path,
    monkeypatch,
):
    store, service = _service(tmp_path)
    monkeypatch.setattr(
        service,
        "_render",
        lambda script_id, text: _mock_render(text),
    )
    try:
        preview = service.create_preview(
            principal="operator",
            script=SCRIPT,
            references=REFERENCES,
            command_id="reviewable-preview",
        )
        review = {
            "principal": "operator",
            "script_id": preview["scriptId"],
            "artifact_sha256": preview["preview"]["sha256"],
            "command_id": "editorial-review-one",
            "editorial_verdict": "READY_FOR_FACTUAL_REVIEW",
            "narration_assessment": "INTELLIGIBLE",
            "pacing_assessment": "APPROPRIATE",
            "caption_assessment": "SYNCHRONIZED",
            "scene_assessment": "COHERENT",
            "factual_review_attested": True,
            "notes": "Test-only operator attestation.",
        }
        result = service.record_editorial_review(**review)
        record = result["review"]
        assert record["humanReviewState"] == "RECORDED"
        assert record["editorial"]["selfReview"] is True
        assert record["editorial"]["artifactSha256"] == preview["preview"]["sha256"]
        assert record["factualReviewState"] == "OPERATOR_ATTESTED_COMPLETE"
        assert record["rightsState"] == "NOT_EVALUATED"
        assert record["approvalState"] == "NOT_GRANTED"
        assert record["publicationState"] == "NOT_PUBLISHED"
        assert service.record_editorial_review(**review)["review"] == record
        assert store._connection.execute(
            "SELECT COUNT(*) FROM content_original_script_preview_reviews"
        ).fetchone()[0] == 1

        with pytest.raises(ValueError, match="different assessment"):
            service.record_editorial_review(**{**review, "notes": "Changed note."})
        with pytest.raises(ValueError, match="current rendered artifact"):
            service.record_editorial_review(
                **{**review, "command_id": "stale-artifact-review", "artifact_sha256": "f" * 64}
            )
        with pytest.raises(PermissionError, match="outside"):
            service.record_editorial_review(
                **{**review, "principal": "another-operator", "command_id": "cross-owner"}
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._connection.execute(
                """
                UPDATE content_original_script_preview_reviews
                SET notes = 'overwritten'
                """
            )
    finally:
        store.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org/source",
        "https://user:secret@example.org/source",
        "https://example.org/source?access_token=secret",
    ],
)
def test_script_preview_rejects_unsafe_references(tmp_path, url):
    store, service = _service(tmp_path)
    try:
        with pytest.raises(ValueError, match="credential-free HTTPS"):
            service.create_preview(
                principal="operator",
                script=SCRIPT,
                references=[{"label": "reference", "url": url}],
                command_id="invalid-reference",
            )
        assert store._connection.execute(
            "SELECT COUNT(*) FROM content_original_script_previews"
        ).fetchone()[0] == 0
    finally:
        store.close()


def test_preview_manifest_is_owner_scoped_and_event_log_is_append_only(
    tmp_path, monkeypatch
):
    store, service = _service(tmp_path)
    monkeypatch.setattr(
        service,
        "_render",
        lambda script_id, text: _mock_render(text),
    )
    try:
        result = service.create_preview(
            principal="operator",
            script=SCRIPT,
            references=REFERENCES,
            command_id="owner-test",
        )
        with pytest.raises(PermissionError, match="outside"):
            service._response(result["scriptId"], "another-operator")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._connection.execute(
                """
                UPDATE content_original_script_preview_events
                SET detail_json = '{}'
                """
            )
    finally:
        store.close()


def test_preview_response_rejects_mutated_persisted_artifact(tmp_path, monkeypatch):
    store, service = _service(tmp_path)
    monkeypatch.setattr(
        service,
        "_render",
        lambda script_id, text: _mock_render(text),
    )
    try:
        result = service.create_preview(
            principal="operator",
            script=SCRIPT,
            references=[],
            command_id="mutated-artifact-test",
        )
        store._connection.execute(
            """
            UPDATE content_original_script_previews
            SET artifact_mp4 = ?
            WHERE script_id = ?
            """,
            (b"mutated", result["scriptId"]),
        )
        with pytest.raises(RuntimeError, match="integrity check"):
            service._response(result["scriptId"], "operator")
    finally:
        store.close()


def test_script_preview_renders_decodable_narrated_video_with_scene_timing(tmp_path):
    store, service = _service(tmp_path)
    try:
        result = service.create_preview(
            principal="operator",
            script=MULTI_SCENE_SCRIPT,
            references=[],
            command_id="actual-render",
        )
        artifact = base64.b64decode(result["preview"]["base64"])
        assert artifact[4:8] == b"ftyp"
        assert result["preview"]["audioIncluded"] is True
        manifest = result["productionManifest"]
        assert manifest["narration"]["engine"] == "FFmpeg libflite"
        assert manifest["narration"]["voiceQuality"] == "NOT_HUMAN_REVIEWED"
        assert manifest["narration"]["sampleCount"] > 0
        assert manifest["narration"]["clippedSampleRatio"] == (
            manifest["narration"]["clippedSamples"]
            / manifest["narration"]["sampleCount"]
        )
        assert manifest["captions"]["state"] == "BURNED_IN"
        assert manifest["qualityAssurance"]["state"] == "TECHNICAL_CHECKS_PASSED"
        assert manifest["sceneContractVersion"] == 2
        assert manifest["scenePlannerVersion"] == "deterministic-heuristic-v2"
        assert manifest["sceneVisualEvidenceVersion"] == 2
        assert manifest["sceneRendererVersion"] == (
            "pillow-scene-composer-v3-motion-xfade"
        )
        assert len(manifest["sceneContractSha256"]) == 64
        assert manifest["video"]["width"] == 720
        assert manifest["video"]["height"] == 1280
        assert manifest["video"]["motion"] == {
            "mode": "restrained_alternating_zoom",
            "maxZoom": 1.05,
        }
        assert manifest["video"]["transitions"] == {
            "type": "crossfade",
            "durationSeconds": 0.25,
            "count": len(manifest["scenes"]) - 1,
            "alignment": "CENTERED_ON_NARRATION_BOUNDARIES",
        }
        assert manifest["video"]["videoCodec"] == "h264"
        assert manifest["video"]["audioCodec"] == "aac"
        assert manifest["video"]["frameRate"] == 30.0
        assert manifest["video"]["audioDurationSeconds"] > 0
        assert manifest["video"]["videoDurationSeconds"] > 0
        assert abs(
            manifest["video"]["audioDurationSeconds"]
            - manifest["video"]["videoDurationSeconds"]
        ) <= service.renderer.SYNC_TOLERANCE_SECONDS
        assert manifest["video"]["artifactSha256"] == result["preview"]["sha256"]
        assert manifest["video"]["artifactBytes"] == result["preview"]["sizeBytes"]
        assert manifest["video"]["audioSampleRateHz"] == 24_000
        assert 2 <= len(manifest["scenes"]) <= service.MAX_SCENES
        assert manifest["scenes"][0]["excerpt"] in MULTI_SCENE_SCRIPT
        assert manifest["scenes"][1]["excerpt"] in MULTI_SCENE_SCRIPT
        assert all(scene["diagramSha256"] for scene in manifest["scenes"])
        assert all(
            scene["sceneContract"]["assetState"] == "NOT_GENERATED"
            and scene["sceneContract"]["technicalValidation"] == "NOT_RUN"
            and scene["sceneContract"]["timing"] is None
            and scene["narrationRef"] == scene["sceneContract"]["narrationRef"]
            and scene["captionRef"] == scene["sceneContract"]["captionRef"]
            and scene["assetState"] == "GENERATED"
            and scene["technicalValidation"] == "TECHNICAL_CHECKS_PASSED"
            for scene in manifest["scenes"]
        )
        assert all(
            MULTI_SCENE_SCRIPT[scene["scriptOffsets"]["start"] : scene["scriptOffsets"]["endExclusive"]]
            == scene["excerpt"]
            for scene in manifest["scenes"]
        )
        samples = []
        motion_frames = []
        for scene in manifest["scenes"]:
            sample_time = scene["speechStartSeconds"] + 0.4
            frame = subprocess.run(
                [
                    service.renderer.ffmpeg,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    "pipe:0",
                    "-ss",
                    str(sample_time),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=16:16",
                    "-pix_fmt",
                    "rgb24",
                    "-f",
                    "rawvideo",
                    "pipe:1",
                ],
                input=artifact,
                capture_output=True,
                check=False,
                timeout=30,
            )
            assert frame.returncode == 0, frame.stderr.decode("utf-8", errors="replace")
            assert len(frame.stdout) == 16 * 16 * 3
            samples.append(hashlib.sha256(frame.stdout).hexdigest())
            scene_motion_frames = []
            for relative_time in (0.35, 0.9):
                motion_time = scene["speechStartSeconds"] + relative_time
                motion_frame = subprocess.run(
                    [
                        service.renderer.ffmpeg,
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-i",
                        "pipe:0",
                        "-ss",
                        str(motion_time),
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=96:170",
                        "-pix_fmt",
                        "rgb24",
                        "-f",
                        "rawvideo",
                        "pipe:1",
                    ],
                    input=artifact,
                    capture_output=True,
                    check=False,
                    timeout=30,
                )
                assert motion_frame.returncode == 0
                assert len(motion_frame.stdout) == 96 * 170 * 3
                scene_motion_frames.append(motion_frame.stdout)
            motion_frames.append(scene_motion_frames)
        assert samples[0] != samples[1]
        assert all(
            sum(abs(left - right) for left, right in zip(first, second)) > 25_000
            for first, second in motion_frames
        )
        first_caption = manifest["scenes"][0]["speechStartSeconds"] + 0.4
        caption_frame = subprocess.run(
            [
                service.renderer.ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-ss",
                str(first_caption),
                "-frames:v",
                "1",
                "-vf",
                "crop=592:160:64:960,scale=148:40",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "pipe:1",
            ],
            input=artifact,
            capture_output=True,
            check=False,
            timeout=30,
        )
        assert caption_frame.returncode == 0
        assert len(caption_frame.stdout) == 148 * 40 * 3
        assert sum(
            min(caption_frame.stdout[index : index + 3]) > 210
            for index in range(0, len(caption_frame.stdout), 3)
        ) > 10

        transition_boundary = manifest["scenes"][1]["speechStartSeconds"]
        transition_frames = []
        for timestamp in (
            transition_boundary - 0.2,
            transition_boundary,
            transition_boundary + 0.2,
        ):
            transition_frame = subprocess.run(
                [
                    service.renderer.ffmpeg,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    "pipe:0",
                    "-ss",
                    str(timestamp),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=96:170",
                    "-pix_fmt",
                    "rgb24",
                    "-f",
                    "rawvideo",
                    "pipe:1",
                ],
                input=artifact,
                capture_output=True,
                check=False,
                timeout=30,
            )
            assert transition_frame.returncode == 0
            assert len(transition_frame.stdout) == 96 * 170 * 3
            transition_frames.append(transition_frame.stdout)
        before, midpoint, after = transition_frames
        before_after_distance = sum(
            abs(left - right) for left, right in zip(before, after)
        )
        assert sum(
            abs(left - right) for left, right in zip(before, midpoint)
        ) < before_after_distance
        assert sum(
            abs(left - right) for left, right in zip(midpoint, after)
        ) < before_after_distance
        assert not tuple(service.staging_directory.glob(".clip-*.mp4"))
        assert not tuple(service.staging_directory.glob(".captions-*.ass"))
        assert not tuple(service.staging_directory.glob(".clip-script-*.png"))
        assert not tuple(service.staging_directory.glob(".clip-script-*.wav"))
        assert not tuple(
            service.staging_directory.glob(
                ".clip-script-*-narration-text-*.txt"
            )
        )
        database = tmp_path / "content.db"
        store.close()
        store = None
        reopened = ContentStore(database)
        reopened_service = OriginalScriptPreviewService(
            store=reopened,
            staging_directory=tmp_path / "staging",
        )
        try:
            durable = reopened_service._response(
                result["scriptId"],
                "operator",
                include_artifact=False,
            )
            assert durable["audioEvidence"]["status"] == "VERIFIED"
            assert durable["productionManifest"]["audioEvidence"]["status"] == (
                "VERIFIED"
            )
            assert durable["productionManifest"]["qualityAssurance"]["state"] == (
                "TECHNICAL_CHECKS_PASSED"
            )
            assert not tuple(service.staging_directory.glob(".clip-script-*.wav"))
        finally:
            reopened.close()
    finally:
        if store is not None:
            store.close()


def test_unicode_multiline_and_limit_boundaries_are_accepted(tmp_path, monkeypatch):
    store, service = _service(tmp_path)

    def render_unicode(script_id, script):
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(struct.pack("<h", 0))
        manifest = {
            "scriptSha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
            "qualityAssurance": {},
            "review": {},
            "scenes": [],
        }
        return RenderedScriptPreview(b"preview", manifest, wav_buffer.getvalue())

    monkeypatch.setattr(service, "_render_narrated", render_unicode)
    monkeypatch.setattr(
        script_preview_module.OriginalScriptPreviewService,
        "_validate_render_manifest",
        lambda *args, **kwargs: None,
    )
    try:
        realistic_multiline_script = (
            "# Coastal community preview\n"
            "The harbor team records tides, weather, and safety guidance for nearby residents.\n\n"
            "Clear captions explain each decision, preserve paragraph breaks, and include important local context.\n\n"
        )
        realistic_multiline_script = (realistic_multiline_script * 20)[:1982].rstrip()
        assert 1900 <= len(realistic_multiline_script) <= 2000
        samples = [
            "A short script.",
            "Café plants use sunlight.\n日光と成長。 🌱",
            "Paragraph one.\n\nParagraph two with Unicode: naïve — café.",
            realistic_multiline_script,
        ]
        for index, script in enumerate(samples):
            result = service.create_preview(
                principal="operator",
                script=script,
                references=[],
                command_id=f"unicode-script-{index}",
            )
            assert result["scriptId"]
        assert len("A" * service.MAX_SCRIPT_CHARACTERS) == 20_000
        with pytest.raises(ValueError, match="20,000"):
            service.create_preview(
                principal="operator",
                script="A" * (service.MAX_SCRIPT_CHARACTERS + 1),
                references=[],
                command_id="over-limit-script",
            )
    finally:
        store.close()


def test_local_narration_failure_is_audited_and_cleans_staged_assets(
    tmp_path,
    monkeypatch,
):
    store, service = _service(tmp_path)

    def fail_narration(command, **kwargs):
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr(script_preview_module.subprocess, "run", fail_narration)
    try:
        with pytest.raises(RuntimeError, match="Local narration failed"):
            service.create_preview(
                principal="operator",
                script=SCRIPT,
                references=[],
                command_id="failed-local-narration",
            )
        row = store._connection.execute(
            "SELECT state, artifact_mp4 FROM content_original_script_previews"
        ).fetchone()
        assert row["state"] == "FAILED"
        assert row["artifact_mp4"] is None
        assert not tuple(service.staging_directory.iterdir())
    finally:
        store.close()
