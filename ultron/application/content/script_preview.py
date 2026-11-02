from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import math
import os
import unicodedata
import re
import struct
import subprocess
import time
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from PIL import Image

from .clip_renderer import FFmpegLocalClipRenderer, RenderCaption
from .script_scenes import (
    SCENE_PLANNER_VERSION,
    SCENE_SCHEMA_VERSION,
    SceneVisualRenderError,
    ScriptScene,
    plan_script_scenes,
    render_scene_diagram,
    rendered_scene_composition,
    serialize_scene_plan,
    validate_scene_plan,
    visual_theme,
    visual_treatment,
)
from .store import ContentStore


class RenderedScriptPreview(NamedTuple):
    artifact: bytes
    manifest: dict[str, object]
    source_wav: bytes


class OriginalScriptPreviewService:
    """Owner-scoped storage and local narrated previews for original scripts."""

    MAX_SCRIPT_CHARACTERS = 20_000
    MAX_REFERENCES = 12
    MAX_REFERENCE_LABEL_CHARACTERS = 120
    MAX_PREVIEWS_PER_OWNER = 100
    MAX_REVIEWS_PER_PREVIEW = 10
    MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
    MAX_SCENES = 140
    MAX_SCENE_CHARACTERS = 96
    MAX_SCENE_VISUAL_BYTES = 512 * 1024
    MAX_SCENE_VISUAL_BYTES_PER_PREVIEW = 2 * 1024 * 1024
    SCENE_VISUAL_EVIDENCE_VERSION = 2
    LEGACY_SCENE_VISUAL_EVIDENCE_VERSION = 1
    LEGACY_SCENE_RENDERER_VERSION = "pillow-scene-composer-v2"
    SCENE_RENDERER_VERSION = "pillow-scene-composer-v3-motion-xfade"
    MAX_ESTIMATED_DURATION_SECONDS = 54.0
    ESTIMATED_CHARACTERS_PER_SECOND = 8.0
    MAX_SCENE_NARRATION_SECONDS = 24.0
    NARRATION_SAMPLE_RATE = 24_000
    NARRATION_GAP_FRAMES = 3_600
    MIN_NARRATION_RMS_DBFS = -45.0
    MAX_NARRATION_PEAK_DBFS = -1.0
    QUIET_WINDOW_DBFS = -50.0
    AUDIO_WINDOW_SAMPLES = 2_400
    AUDIO_EVIDENCE_ALGORITHM = "PCM_S16LE_MONO_24000_CLIP_32760_V1"
    MAX_AUDIO_EVIDENCE_BYTES = (
        int(MAX_ESTIMATED_DURATION_SECONDS * NARRATION_SAMPLE_RATE * 2) + 128
    )
    MAX_AUDIO_EVIDENCE_BYTES_PER_OWNER = 64 * 1024 * 1024
    MAX_AUDIO_EVIDENCE_SAMPLES = int(
        MAX_ESTIMATED_DURATION_SECONDS * NARRATION_SAMPLE_RATE
    )
    FLITE_VOICE = "slt"
    COMMAND_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
    _CREDENTIAL_QUERY_KEYS = frozenset(
        {
            "access_token",
            "api_key",
            "apikey",
            "auth",
            "authorization",
            "client_secret",
            "credential",
            "password",
            "secret",
            "sig",
            "signature",
            "token",
            "x-amz-signature",
            "x-goog-signature",
            "x-amz-credential",
            "x-goog-credential",
        }
    )

    def __init__(self, *, store: ContentStore, staging_directory: Path) -> None:
        if type(store) is not ContentStore:
            raise TypeError("Script previews require the configured ContentStore.")
        if (
            not isinstance(staging_directory, Path)
            or staging_directory.is_symlink()
            or not staging_directory.is_dir()
        ):
            raise ValueError("Script preview staging must be a real directory.")
        self.store = store
        self.staging_directory = staging_directory.resolve(strict=True)
        self.renderer: FFmpegLocalClipRenderer | None = None

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def _validate_references(cls, references: object) -> list[dict[str, str]]:
        if not isinstance(references, list) or len(references) > cls.MAX_REFERENCES:
            raise ValueError("References must be a list of at most 12 entries.")
        validated: list[dict[str, str]] = []
        for item in references:
            if not isinstance(item, dict) or set(item) != {"label", "url"}:
                raise ValueError("Each reference must contain only a label and URL.")
            label = item["label"]
            url = item["url"]
            if (
                not isinstance(label, str)
                or not label.strip()
                or len(label.strip()) > cls.MAX_REFERENCE_LABEL_CHARACTERS
                or any(ord(character) < 32 or ord(character) == 127 for character in label)
                or not isinstance(url, str)
                or len(url) > 2048
            ):
                raise ValueError("A reference label or URL is invalid.")
            normalized_url = url.strip()
            if (
                any(ord(character) < 32 or character.isspace() for character in normalized_url)
                or "\\" in normalized_url
            ):
                raise ValueError("Reference URL contains invalid whitespace.")
            parsed = urlsplit(normalized_url)
            hostname = parsed.hostname
            has_query_credentials = any(
                key.lower() in cls._CREDENTIAL_QUERY_KEYS
                for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
            )
            try:
                port = parsed.port
            except ValueError as exc:
                raise ValueError("Reference URL contains an invalid port.") from exc
            if (
                parsed.scheme.lower() != "https"
                or not parsed.netloc
                or not hostname
                or parsed.username is not None
                or parsed.password is not None
                or has_query_credentials
                or (port is not None and not 1 <= port <= 65535)
                or hostname.startswith(".")
                or hostname.endswith(".")
                or ".." in hostname
            ):
                raise ValueError(
                    "References must be credential-free HTTPS URLs."
                )
            validated.append({"label": label.strip(), "url": normalized_url})
        return validated

    @classmethod
    def _measure_pcm(
        cls,
        pcm: bytes,
    ) -> tuple[int, int, int, int, int, int]:
        if not pcm or len(pcm) % 2:
            raise ValueError("Local narration returned malformed PCM samples.")
        peak = square_sum = clipped = count = 0
        for (sample,) in struct.iter_unpack("<h", pcm):
            magnitude = abs(sample)
            peak = max(peak, magnitude)
            square_sum += sample * sample
            clipped += magnitude >= 32760
            count += 1
        window_bytes = cls.AUDIO_WINDOW_SAMPLES * 2
        quiet_windows = total_windows = 0
        quiet_threshold = 32768 * 10 ** (cls.QUIET_WINDOW_DBFS / 20)
        for start in range(0, len(pcm), window_bytes):
            window = pcm[start : start + window_bytes]
            window_square_sum = sum(
                sample * sample
                for (sample,) in struct.iter_unpack("<h", window)
            )
            window_count = len(window) // 2
            if math.sqrt(window_square_sum / window_count) < quiet_threshold:
                quiet_windows += 1
            total_windows += 1
        return peak, square_sum, clipped, count, quiet_windows, total_windows

    @staticmethod
    def _scene_plan_entries(
        scenes: tuple[ScriptScene, ...],
    ) -> list[dict[str, object]]:
        return [
            {
                "sceneId": scene.scene_id,
                "order": scene.order,
                "scriptOffsets": {
                    "start": scene.start_offset,
                    "endExclusive": scene.end_offset,
                },
                "excerptSha256": hashlib.sha256(
                    scene.excerpt.encode("utf-8")
                ).hexdigest(),
                "visualType": scene.visual_type,
                "diagramLabels": list(scene.labels),
            }
            for scene in scenes
        ]

    @classmethod
    def _scene_plan_sha256(cls, scenes: tuple[ScriptScene, ...]) -> str:
        return hashlib.sha256(
            cls._canonical(cls._scene_plan_entries(scenes)).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _scene_contract_sha256(scenes: tuple[ScriptScene, ...]) -> str:
        return hashlib.sha256(serialize_scene_plan(scenes).encode("utf-8")).hexdigest()

    @staticmethod
    def _preview_failure_details(exc: Exception) -> dict[str, str]:
        details = {"errorType": type(exc).__name__}
        if isinstance(exc, SceneVisualRenderError):
            details["failedSceneId"] = exc.scene_id
            details["failureReason"] = exc.failure_code
        return details

    @classmethod
    def _scene_visual_asset(
        cls,
        *,
        scene: ScriptScene,
        diagram_bytes: bytes,
        script_sha256: str,
        scene_contract_sha256: str,
        generated_at: str,
    ) -> dict[str, object]:
        if (
            not isinstance(diagram_bytes, bytes)
            or not diagram_bytes
            or len(diagram_bytes) > cls.MAX_SCENE_VISUAL_BYTES
        ):
            raise ValueError("Scene visual asset exceeds its bounded storage limit.")
        try:
            with Image.open(io.BytesIO(diagram_bytes)) as image:
                if image.format != "PNG" or image.size != (720, 1280):
                    raise ValueError("Scene visual asset dimensions or format are invalid.")
                image.verify()
        except (OSError, Image.DecompressionBombError) as exc:
            raise ValueError("Scene visual asset could not be decoded.") from exc
        visual_sha256 = hashlib.sha256(diagram_bytes).hexdigest()
        composition = rendered_scene_composition(scene)
        theme = visual_theme(scene.excerpt)
        treatment = visual_treatment(scene)
        identity = hashlib.sha256(
            cls._canonical(
                {
                    "composition": composition,
                    "rendererVersion": cls.SCENE_RENDERER_VERSION,
                    "sceneContractSha256": scene_contract_sha256,
                    "sceneId": scene.scene_id,
                    "visualTheme": theme,
                    "visualTreatment": treatment,
                    "visualSha256": visual_sha256,
                }
            ).encode("utf-8")
        ).hexdigest()
        return {
            "assetId": identity,
            "format": "PNG",
            "width": 720,
            "height": 1280,
            "byteSize": len(diagram_bytes),
            "sha256": visual_sha256,
            "rendererVersion": cls.SCENE_RENDERER_VERSION,
            "generatedAt": generated_at,
            "validationStatus": "TECHNICAL_CHECKS_PASSED",
            "sourceScriptSha256": script_sha256,
            "sceneContractSha256": scene_contract_sha256,
            "excerptSha256": hashlib.sha256(
                scene.excerpt.encode("utf-8")
            ).hexdigest(),
            "visualStrategy": scene.visual_strategy,
            "composition": composition,
            "visualTheme": theme,
            "visualTreatment": treatment,
            "safeArea": {
                "leftPx": 42,
                "topPx": 222,
                "rightPx": 678,
                "bottomPx": 908,
                "captionBoundaryPx": 1060,
            },
            "dataBase64": base64.b64encode(diagram_bytes).decode("ascii"),
        }

    @classmethod
    def _scene_visual_manifest_sha256(
        cls,
        manifest_scenes: list[dict[str, object]],
    ) -> str:
        entries = []
        for scene in manifest_scenes:
            asset = scene.get("visualAsset")
            if not isinstance(asset, dict):
                raise ValueError("Scene visual manifest is incomplete.")
            entry = {
                    "sceneId": scene.get("sceneId"),
                    "order": scene.get("order"),
                    "scriptOffsets": scene.get("scriptOffsets"),
                    "excerptSha256": scene.get("excerptSha256"),
                    "renderedComposition": scene.get("renderedComposition"),
                    "assetId": asset.get("assetId"),
                    "sha256": asset.get("sha256"),
                    "byteSize": asset.get("byteSize"),
                    "width": asset.get("width"),
                    "height": asset.get("height"),
                    "rendererVersion": asset.get("rendererVersion"),
                    "generatedAt": asset.get("generatedAt"),
                    "validationStatus": asset.get("validationStatus"),
                    "sourceScriptSha256": asset.get("sourceScriptSha256"),
                    "sceneContractSha256": asset.get("sceneContractSha256"),
                    "excerptSha256": asset.get("excerptSha256"),
                    "visualStrategy": asset.get("visualStrategy"),
                    "composition": asset.get("composition"),
                    "safeArea": asset.get("safeArea"),
                }
            if "visualTheme" in asset or "visualTreatment" in asset:
                entry["visualTheme"] = asset.get("visualTheme")
                entry["visualTreatment"] = asset.get("visualTreatment")
            entries.append(entry)
        return hashlib.sha256(cls._canonical(entries).encode("utf-8")).hexdigest()

    @classmethod
    def _validate_scene_visual_asset(
        cls,
        *,
        scene: ScriptScene,
        asset: object,
        script_sha256: str,
        scene_contract_sha256: str,
    ) -> None:
        legacy_required = {
            "assetId",
            "format",
            "width",
            "height",
            "byteSize",
            "sha256",
            "rendererVersion",
            "generatedAt",
            "validationStatus",
            "sourceScriptSha256",
            "sceneContractSha256",
            "excerptSha256",
            "visualStrategy",
            "composition",
            "safeArea",
            "dataBase64",
        }
        current_required = legacy_required | {"visualTheme", "visualTreatment"}
        if (
            not isinstance(asset, dict)
            or set(asset) not in (legacy_required, current_required)
        ):
            raise ValueError("Scene visual evidence is missing or malformed.")
        try:
            encoded = asset["dataBase64"]
            if not isinstance(encoded, str):
                raise ValueError("Scene visual evidence bytes are malformed.")
            diagram_bytes = base64.b64decode(encoded, validate=True)
            generated_at = datetime.fromisoformat(asset["generatedAt"])
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError("Scene visual evidence encoding is invalid.") from exc
        expected_composition = rendered_scene_composition(scene)
        renderer_version = asset.get("rendererVersion")
        is_current_renderer = renderer_version == cls.SCENE_RENDERER_VERSION
        expected_id_fields = {
            "composition": expected_composition,
            "rendererVersion": renderer_version,
            "sceneContractSha256": scene_contract_sha256,
            "sceneId": scene.scene_id,
            "visualSha256": hashlib.sha256(diagram_bytes).hexdigest(),
        }
        if is_current_renderer:
            expected_id_fields["visualTheme"] = visual_theme(scene.excerpt)
            expected_id_fields["visualTreatment"] = visual_treatment(scene)
        expected_id = hashlib.sha256(
            cls._canonical(expected_id_fields).encode("utf-8")
        ).hexdigest()
        if (
            not diagram_bytes
            or len(diagram_bytes) > cls.MAX_SCENE_VISUAL_BYTES
            or type(asset["width"]) is not int
            or type(asset["height"]) is not int
            or type(asset["byteSize"]) is not int
            or asset["format"] != "PNG"
            or asset["width"] != 720
            or asset["height"] != 1280
            or asset["byteSize"] != len(diagram_bytes)
            or asset["sha256"] != hashlib.sha256(diagram_bytes).hexdigest()
            or asset["assetId"] != expected_id
            or renderer_version not in {
                cls.LEGACY_SCENE_RENDERER_VERSION,
                cls.SCENE_RENDERER_VERSION,
            }
            or (is_current_renderer and set(asset) != current_required)
            or (
                not is_current_renderer
                and (
                    renderer_version != cls.LEGACY_SCENE_RENDERER_VERSION
                    or set(asset) != legacy_required
                )
            )
            or generated_at.tzinfo is None
            or asset["validationStatus"] != "TECHNICAL_CHECKS_PASSED"
            or asset["sourceScriptSha256"] != script_sha256
            or asset["sceneContractSha256"] != scene_contract_sha256
            or asset["excerptSha256"]
            != hashlib.sha256(scene.excerpt.encode("utf-8")).hexdigest()
            or asset["visualStrategy"] != scene.visual_strategy
            or asset["composition"] != expected_composition
            or (
                is_current_renderer
                and (
                    asset["visualTheme"] != visual_theme(scene.excerpt)
                    or asset["visualTreatment"] != visual_treatment(scene)
                )
            )
            or asset["safeArea"]
            != {
                "leftPx": 42,
                "topPx": 222,
                "rightPx": 678,
                "bottomPx": 908,
                "captionBoundaryPx": 1060,
            }
        ):
            raise ValueError("Scene visual evidence binding or metadata failed validation.")
        try:
            with Image.open(io.BytesIO(diagram_bytes)) as image:
                if image.format != "PNG" or image.size != (720, 1280):
                    raise ValueError("Scene visual image dimensions or format are invalid.")
                image.verify()
        except (OSError, Image.DecompressionBombError) as exc:
            raise ValueError("Scene visual image failed decoding validation.") from exc

    @classmethod
    def _measure_audio_evidence(cls, source_wav: bytes) -> dict[str, int | float | str]:
        if (
            not isinstance(source_wav, bytes)
            or len(source_wav) < 44
            or len(source_wav) > cls.MAX_AUDIO_EVIDENCE_BYTES
        ):
            raise ValueError("Audio evidence source is missing or exceeds its size limit.")
        try:
            with wave.open(io.BytesIO(source_wav), "rb") as audio:
                parameters = audio.getparams()
                if (
                    parameters.nchannels != 1
                    or parameters.sampwidth != 2
                    or parameters.framerate != cls.NARRATION_SAMPLE_RATE
                    or parameters.comptype != "NONE"
                    or parameters.nframes <= 0
                    or parameters.nframes > cls.MAX_AUDIO_EVIDENCE_SAMPLES
                ):
                    raise ValueError("Audio evidence WAV format or duration is invalid.")
                pcm = audio.readframes(parameters.nframes)
                if len(pcm) != parameters.nframes * 2:
                    raise ValueError("Audio evidence WAV is truncated.")
        except (wave.Error, EOFError) as exc:
            raise ValueError("Audio evidence WAV is malformed.") from exc
        peak, square_sum, clipped, count, quiet_windows, total_windows = (
            cls._measure_pcm(pcm)
        )
        if count != parameters.nframes or count <= 0 or total_windows <= 0:
            raise ValueError("Audio evidence sample counts are inconsistent.")
        rms = math.sqrt(square_sum / count)
        return {
            "sourceWavSha256": hashlib.sha256(source_wav).hexdigest(),
            "sourceWavBytes": len(source_wav),
            "sampleCount": count,
            "clippedSamples": clipped,
            "clippedSampleRatio": clipped / count,
            "peakDbfs": 20 * math.log10(peak / 32768) if peak else float("-inf"),
            "rmsDbfs": 20 * math.log10(rms / 32768) if rms else float("-inf"),
            "quietWindowRatio": quiet_windows / total_windows,
        }

    @classmethod
    def _audio_evidence_id(
        cls,
        *,
        preview_id: str,
        script_sha256: str,
        artifact_sha256: str,
        source_wav_sha256: str,
    ) -> str:
        binding = {
            "algorithmVersion": cls.AUDIO_EVIDENCE_ALGORITHM,
            "previewId": preview_id,
            "scriptSha256": script_sha256,
            "artifactSha256": artifact_sha256,
            "sourceWavSha256": source_wav_sha256,
        }
        return hashlib.sha256(cls._canonical(binding).encode("utf-8")).hexdigest()

    @classmethod
    def _build_audio_evidence_manifest(
        cls,
        *,
        preview_id: str,
        script_sha256: str,
        artifact: bytes,
        source_wav: bytes,
        created_at: str,
    ) -> dict[str, object]:
        metrics = cls._measure_audio_evidence(source_wav)
        artifact_sha256 = hashlib.sha256(artifact).hexdigest()
        return {
            "status": "VERIFIED",
            "evidenceId": cls._audio_evidence_id(
                preview_id=preview_id,
                script_sha256=script_sha256,
                artifact_sha256=artifact_sha256,
                source_wav_sha256=str(metrics["sourceWavSha256"]),
            ),
            "algorithmVersion": cls.AUDIO_EVIDENCE_ALGORITHM,
            "previewId": preview_id,
            "scriptSha256": script_sha256,
            "artifactSha256": artifact_sha256,
            **metrics,
            "clippingThreshold": 0.001,
            "createdAt": created_at,
        }

    @classmethod
    def _validate_render_manifest(
        cls,
        *,
        preview_id: str,
        script: str,
        script_sha256: str,
        artifact: bytes,
        source_wav: bytes | None,
        manifest: object,
    ) -> None:
        if not isinstance(manifest, dict):
            raise ValueError("Narrated script preview failed its output contract.")
        scenes = plan_script_scenes(
            script,
            max_scenes=cls.MAX_SCENES,
            max_characters_per_scene=cls.MAX_SCENE_CHARACTERS,
        )
        validate_scene_plan(
            script,
            scenes,
            max_scenes=cls.MAX_SCENES,
            max_characters_per_scene=cls.MAX_SCENE_CHARACTERS,
        )
        manifest_scenes = manifest.get("scenes")
        narration = manifest.get("narration")
        captions = manifest.get("captions")
        video = manifest.get("video")
        audio_evidence = manifest.get("audioEvidence")
        audio_evidence_declared = "audioEvidence" in manifest
        scene_contract_declared = "sceneContractVersion" in manifest
        visual_evidence_declared = "sceneVisualEvidenceVersion" in manifest
        scene_contract_version = manifest.get("sceneContractVersion")
        scene_contract_sha256 = cls._scene_contract_sha256(scenes)
        quality = manifest.get("qualityAssurance")
        rendered_review = manifest.get("review")
        if (
            manifest.get("workflow") != "LOCAL_NARRATED_ORIGINAL_SCRIPT_PREVIEW"
            or manifest.get("scriptSha256") != script_sha256
            or manifest.get("scenePlanSha256") != cls._scene_plan_sha256(scenes)
            or (
                scene_contract_declared
                and (
                    scene_contract_version != SCENE_SCHEMA_VERSION
                    or manifest.get("scenePlannerVersion") != SCENE_PLANNER_VERSION
                    or manifest.get("sceneContractSha256")
                    != cls._scene_contract_sha256(scenes)
                )
            )
            or (
                visual_evidence_declared
                and (
                    manifest.get("sceneVisualEvidenceVersion")
                    not in {
                        cls.LEGACY_SCENE_VISUAL_EVIDENCE_VERSION,
                        cls.SCENE_VISUAL_EVIDENCE_VERSION,
                    }
                    or (
                        manifest.get("sceneVisualEvidenceVersion")
                        == cls.LEGACY_SCENE_VISUAL_EVIDENCE_VERSION
                        and manifest.get("sceneRendererVersion")
                        != cls.LEGACY_SCENE_RENDERER_VERSION
                    )
                    or (
                        manifest.get("sceneVisualEvidenceVersion")
                        == cls.SCENE_VISUAL_EVIDENCE_VERSION
                        and manifest.get("sceneRendererVersion")
                        != cls.SCENE_RENDERER_VERSION
                    )
                    or not re.fullmatch(
                        r"[a-f0-9]{64}",
                        str(manifest.get("sceneVisualManifestSha256", "")),
                    )
                )
            )
            or not isinstance(manifest_scenes, list)
            or len(manifest_scenes) != len(scenes)
            or not isinstance(narration, dict)
            or not isinstance(captions, dict)
            or not isinstance(video, dict)
            or not isinstance(quality, dict)
            or not isinstance(rendered_review, dict)
        ):
            raise ValueError("Narrated script preview failed its output contract.")
        if not isinstance(audio_evidence, dict):
            if source_wav is not None or audio_evidence_declared:
                raise ValueError("Narrated script preview audio evidence is missing.")
            audio_evidence = {}
        expected_scene_start = 0.0
        for index, (planned, rendered) in enumerate(zip(scenes, manifest_scenes)):
            if not isinstance(rendered, dict):
                raise ValueError("Narrated script preview scene manifest is invalid.")
            if visual_evidence_declared:
                visual_asset = rendered.get("visualAsset")
                if not isinstance(visual_asset, dict):
                    raise ValueError("Scene visual asset is malformed.")
                cls._validate_scene_visual_asset(
                    scene=planned,
                    asset=visual_asset,
                    script_sha256=script_sha256,
                    scene_contract_sha256=scene_contract_sha256,
                )
                if (
                    visual_asset["sha256"] != rendered.get("diagramSha256")
                    or visual_asset["byteSize"] != rendered.get("diagramBytes")
                ):
                    raise ValueError(
                        "Scene visual asset and manifest hash do not match."
                    )
            offsets = rendered.get("scriptOffsets")
            caption = rendered.get("caption")
            try:
                speech_start = float(rendered["speechStartSeconds"])
                speech_end = float(rendered["speechEndSeconds"])
                speech_duration = float(rendered["measuredSpeechDurationSeconds"])
                visual_duration = float(rendered["visualDurationSeconds"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    "Narrated script preview scene timing is incomplete."
                ) from exc
            if (
                rendered.get("sceneId") != planned.scene_id
                or type(rendered.get("order")) is not int
                or rendered["order"] != planned.order
                or not isinstance(offsets, dict)
                or type(offsets.get("start")) is not int
                or type(offsets.get("endExclusive")) is not int
                or offsets
                != {
                    "start": planned.start_offset,
                    "endExclusive": planned.end_offset,
                }
                or script[planned.start_offset : planned.end_offset]
                != planned.excerpt
                or rendered.get("excerpt") != planned.excerpt
                or rendered.get("excerptSha256")
                != hashlib.sha256(planned.excerpt.encode("utf-8")).hexdigest()
                or rendered.get("visualType") != planned.visual_type
                or rendered.get("diagramLabels") != list(planned.labels)
                or (
                    scene_contract_version == SCENE_SCHEMA_VERSION
                    and (
                        rendered.get("sceneContract") != planned.to_dict()
                        or rendered.get("narrationRef") != planned.narration_ref
                        or rendered.get("captionRef") != planned.caption_ref
                        or rendered.get("assetState") != "GENERATED"
                        or rendered.get("technicalValidation")
                        != "TECHNICAL_CHECKS_PASSED"
                        or planned.renderer_support != "SUPPORTED"
                    )
                )
                or (
                    visual_evidence_declared
                    and (
                        rendered.get("renderedComposition")
                        != rendered_scene_composition(planned)
                        or (
                            manifest.get("sceneVisualEvidenceVersion")
                            == cls.SCENE_VISUAL_EVIDENCE_VERSION
                            and rendered.get("visualTreatment")
                            != visual_treatment(planned)
                        )
                    )
                )
                or not re.fullmatch(
                    r"[a-f0-9]{64}",
                    str(rendered.get("diagramSha256", "")),
                )
                or type(rendered.get("diagramBytes")) is not int
                or rendered["diagramBytes"] <= 0
                or not math.isfinite(speech_start)
                or speech_start < 0
                or not all(
                    math.isfinite(value) and value > 0
                    for value in (
                        speech_end,
                        speech_duration,
                        visual_duration,
                    )
                )
                or abs(speech_start - expected_scene_start) > 0.01
                or abs((speech_end - speech_start) - speech_duration) > 0.01
                or abs(
                    visual_duration
                    - speech_duration
                    - (
                        cls.NARRATION_GAP_FRAMES / cls.NARRATION_SAMPLE_RATE
                        if index < len(scenes) - 1
                        else 0.0
                    )
                ) > 0.01
                or not isinstance(caption, dict)
                or caption.get("text") != planned.excerpt
                or caption.get("scriptOffsets") != offsets
                or type(caption.get("startSeconds")) not in (int, float)
                or type(caption.get("endSeconds")) not in (int, float)
                or caption.get("startSeconds") != round(speech_start, 2)
                or caption.get("endSeconds") != round(speech_end, 2)
                or type(caption.get("lineCount")) is not int
                or not 1 <= caption["lineCount"] <= 4
                or type(caption.get("maximumLineWidthPx")) is not int
                or not 0 <= caption["maximumLineWidthPx"] <= 592
            ):
                raise ValueError(
                    "Narrated script preview scene or caption does not match the script."
                )
            expected_scene_start += visual_duration
        if (
            visual_evidence_declared
            and manifest.get("sceneVisualManifestSha256")
            != cls._scene_visual_manifest_sha256(manifest_scenes)
        ):
            raise ValueError("Scene visual manifest hash failed validation.")
        expected_artifact_sha256 = hashlib.sha256(artifact).hexdigest()
        evidence_metrics = (
            cls._measure_audio_evidence(source_wav)
            if source_wav is not None
            else None
        )
        if evidence_metrics is not None:
            try:
                evidence_created_at = datetime.fromisoformat(
                    str(audio_evidence["createdAt"])
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    "Narrated script preview evidence timestamp is invalid."
                ) from exc
            expected_evidence_id = cls._audio_evidence_id(
                preview_id=preview_id,
                script_sha256=script_sha256,
                artifact_sha256=expected_artifact_sha256,
                source_wav_sha256=str(evidence_metrics["sourceWavSha256"]),
            )
        try:
            peak_dbfs = float(narration["peakDbfs"])
            rms_dbfs = float(narration["rmsDbfs"])
            quiet_window_ratio = float(narration["quietWindowRatio"])
            if audio_evidence_declared:
                clipped_samples = int(narration["clippedSamples"])
                sample_count = int(narration["sampleCount"])
                clipped_sample_ratio = float(narration["clippedSampleRatio"])
            else:
                clipped_samples = sample_count = 0
                clipped_sample_ratio = 0.0
            duration = float(narration["durationSeconds"])
            output_duration = float(video["durationSeconds"])
            audio_duration = float(video["audioDurationSeconds"])
            video_duration = float(video["videoDurationSeconds"])
            artifact_bytes = int(video["artifactBytes"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Narrated script preview metrics are incomplete.") from exc
        if (
            narration.get("state") != "GENERATED_LOCALLY"
            or narration.get("voiceQuality") != "NOT_HUMAN_REVIEWED"
            or narration.get("intelligibility") != "NOT_ASSESSED"
            or narration.get("naturalness") != "NOT_ASSESSED"
            or not math.isfinite(peak_dbfs)
            or not math.isfinite(rms_dbfs)
            or peak_dbfs > cls.MAX_NARRATION_PEAK_DBFS
            or rms_dbfs < cls.MIN_NARRATION_RMS_DBFS
            or not 0 <= quiet_window_ratio <= 1
            or (
                audio_evidence_declared
                and (
                    type(narration.get("clippedSamples")) is not int
                    or clipped_samples < 0
                    or type(narration.get("sampleCount")) is not int
                    or sample_count <= 0
                    or sample_count > cls.MAX_AUDIO_EVIDENCE_SAMPLES
                    or clipped_samples > sample_count
                    or type(narration.get("clippedSampleRatio")) not in (int, float)
                    or not math.isfinite(clipped_sample_ratio)
                    or clipped_samples / sample_count > 0.001
                    or abs(clipped_sample_ratio - clipped_samples / sample_count)
                    > 0.0000001
                    or narration.get("sampleRateHz") != cls.NARRATION_SAMPLE_RATE
                    or narration.get("channels") != 1
                    or not isinstance(narration.get("sourceWavSha256"), str)
                    or not re.fullmatch(
                        r"[a-f0-9]{64}", narration["sourceWavSha256"]
                    )
                )
            )
            or (
                evidence_metrics is not None
                and (
                    narration["sourceWavSha256"]
                    != evidence_metrics["sourceWavSha256"]
                    or narration["sampleCount"] != evidence_metrics["sampleCount"]
                    or narration["clippedSamples"]
                    != evidence_metrics["clippedSamples"]
                    or abs(
                        narration["clippedSampleRatio"]
                        - evidence_metrics["clippedSampleRatio"]
                    ) > 0.0000001
                    or abs(peak_dbfs - evidence_metrics["peakDbfs"]) > 0.02
                    or abs(rms_dbfs - evidence_metrics["rmsDbfs"]) > 0.02
                    or abs(
                        quiet_window_ratio - evidence_metrics["quietWindowRatio"]
                    ) > 0.0001
                )
            )
            or type(narration.get("encodedAudioRmsDbfs")) not in (int, float)
            or not math.isfinite(narration["encodedAudioRmsDbfs"])
            or narration["encodedAudioRmsDbfs"] < cls.MIN_NARRATION_RMS_DBFS
            or not isinstance(narration.get("sceneBoundaryGaps"), list)
            or len(narration["sceneBoundaryGaps"]) != len(scenes) - 1
            or any(
                not isinstance(gap, dict)
                or gap.get("state") != "INTENTIONAL_SILENCE_VERIFIED"
                or gap.get("windowMilliseconds") != 50
                for gap in narration["sceneBoundaryGaps"]
            )
            or not 0 < duration <= cls.MAX_ESTIMATED_DURATION_SECONDS
            or captions.get("state") != "BURNED_IN"
            or captions.get("safeArea")
            != {
                "leftPx": 64,
                "rightPx": 64,
                "bottomPx": 220,
                "maximumWidthPx": 592,
                "fontFamily": "DejaVu Sans",
                "fontSizePx": 36,
                "maximumLines": 4,
            }
            or video.get("container") != "mp4"
            or video.get("artifactSha256") != expected_artifact_sha256
            or type(video.get("artifactBytes")) is not int
            or artifact_bytes != len(artifact)
            or artifact_bytes <= 0
            or video.get("width") != 720
            or video.get("height") != 1280
            or video.get("videoCodec") != "h264"
            or video.get("audioCodec") != "aac"
            or video.get("audioSampleRateHz") != cls.NARRATION_SAMPLE_RATE
            or video.get("audioChannels") != 1
            or (
                manifest.get("sceneRendererVersion") == cls.SCENE_RENDERER_VERSION
                and (
                    video.get("motion")
                    != {
                        "mode": FFmpegLocalClipRenderer.SCENE_MOTION,
                        "maxZoom": 1.05,
                    }
                    or video.get("transitions")
                    != {
                        "type": FFmpegLocalClipRenderer.SCENE_TRANSITION,
                        "durationSeconds": (
                            FFmpegLocalClipRenderer.SCENE_TRANSITION_SECONDS
                        ),
                        "count": len(scenes) - 1,
                        "alignment": "CENTERED_ON_NARRATION_BOUNDARIES",
                    }
                )
            )
            or not all(
                math.isfinite(value) and value > 0
                for value in (output_duration, audio_duration, video_duration)
            )
            or max(
                abs(output_duration - duration),
                abs(audio_duration - duration),
                abs(video_duration - duration),
            ) > 0.25
            or quality.get("state") != "TECHNICAL_CHECKS_PASSED"
            or rendered_review.get("humanReviewState") != "PENDING"
            or rendered_review.get("factualReviewState") != "REQUIRED"
            or rendered_review.get("rightsState") != "NOT_EVALUATED"
            or rendered_review.get("approvalState") != "NOT_GRANTED"
            or rendered_review.get("publicationState") != "NOT_PUBLISHED"
            or (
                evidence_metrics is not None
                and (
                    audio_evidence.get("status") != "VERIFIED"
                    or audio_evidence.get("evidenceId") != expected_evidence_id
                    or audio_evidence.get("algorithmVersion")
                    != cls.AUDIO_EVIDENCE_ALGORITHM
                    or audio_evidence.get("previewId") != preview_id
                    or audio_evidence.get("scriptSha256") != script_sha256
                    or audio_evidence.get("artifactSha256") != expected_artifact_sha256
                    or audio_evidence.get("sourceWavSha256")
                    != evidence_metrics["sourceWavSha256"]
                    or audio_evidence.get("sourceWavBytes")
                    != evidence_metrics["sourceWavBytes"]
                    or type(audio_evidence.get("sampleCount")) is not int
                    or audio_evidence.get("sampleCount")
                    != evidence_metrics["sampleCount"]
                    or type(audio_evidence.get("clippedSamples")) is not int
                    or audio_evidence.get("clippedSamples")
                    != evidence_metrics["clippedSamples"]
                    or type(audio_evidence.get("clippedSampleRatio"))
                    not in (int, float)
                    or audio_evidence.get("clippedSampleRatio")
                    != evidence_metrics["clippedSampleRatio"]
                    or audio_evidence.get("clippingThreshold") != 0.001
                    or evidence_created_at.tzinfo is None
                )
            )
        ):
            raise ValueError("Narrated script preview failed its output contract.")

    def _verify_audio_evidence(
        self,
        *,
        preview_id: str,
        owner_id: str,
        preview: object,
        render_event: object,
        evidence: object,
        checked_at: str,
    ) -> dict[str, object]:
        manifest: dict[str, object] = {}
        event_details: dict[str, object] = {}
        if render_event is not None:
            try:
                event_details = json.loads(render_event["detail_json"])
                if not isinstance(event_details, dict):
                    raise TypeError("Preview render event is not an object.")
                manifest = event_details.get("manifest", {})
                if not isinstance(manifest, dict):
                    manifest = {}
            except (json.JSONDecodeError, KeyError, TypeError):
                event_details = {}
                manifest = {}
        declared_present = "audioEvidence" in manifest
        declared = manifest.get("audioEvidence")
        evidence_required = preview["audio_evidence_required"] == 1
        record = evidence
        legacy = (
            render_event is not None
            and not declared_present
            and not evidence_required
            and record is None
        )
        malformed_render_event = render_event is None or not isinstance(
            event_details.get("manifest"), dict
        )
        missing_required_manifest = evidence_required and not declared_present
        malformed_declaration = declared_present and declared is None
        fields: dict[str, object] = {
            "status": (
                "INTEGRITY_FAILURE"
                if malformed_render_event
                or malformed_declaration
                or missing_required_manifest
                else "UNVERIFIABLE"
                if record is None and legacy
                else "MISSING"
            ),
            "evidenceId": None,
            "algorithmVersion": None,
            "createdAt": None,
            "lastCheckedAt": checked_at,
            "lastVerifiedAt": None,
            "sourceWavSha256": None,
            "sourceBytes": None,
            "sourceBinding": "UNVERIFIED",
            "scriptBinding": "UNVERIFIED",
            "artifactBinding": "UNVERIFIED",
            "sampleCount": None,
            "clippedSamples": None,
            "clippedSampleRatio": None,
            "clippingThreshold": 0.001,
            "legacy": legacy and not malformed_render_event,
            "failureReason": (
                "RENDER_MANIFEST_MALFORMED"
                if malformed_render_event
                or malformed_declaration
                or missing_required_manifest
                else "LEGACY_EVIDENCE_UNAVAILABLE"
                if record is None and legacy
                else "DURABLE_EVIDENCE_MISSING"
            ),
            "nextAction": (
                "Do not rely on technical QA; regenerate the preview and evidence."
                if malformed_render_event
                or malformed_declaration
                or missing_required_manifest
                else "Generate a new preview to create durable audio evidence."
                if record is None
                else "Restore or regenerate the missing audio evidence."
            ),
        }
        if record is None:
            return fields

        fields.update(
            {
                "evidenceId": record["evidence_id"],
                "algorithmVersion": record["algorithm_version"],
                "createdAt": record["created_at"],
                "lastVerifiedAt": record["last_verified_at"],
                "sourceWavSha256": record["source_wav_sha256"],
                "sourceBytes": len(record["source_wav"])
                if isinstance(record["source_wav"], (bytes, bytearray))
                else None,
            }
        )
        reason: str | None = None
        source_binding = "VERIFIED"
        script_binding = "VERIFIED"
        artifact_binding = "VERIFIED"
        try:
            evidence_created_at = datetime.fromisoformat(record["created_at"])
            if evidence_created_at.tzinfo is None:
                raise ValueError("Audio evidence timestamp is not timezone-aware.")
            script_text = preview["script_text"]
            script_sha256 = hashlib.sha256(script_text.encode("utf-8")).hexdigest()
            if (
                preview["state"] != "PREVIEW_READY"
                or script_sha256 != preview["script_sha256"]
                or record["script_sha256"] != preview["script_sha256"]
                or not isinstance(declared, dict)
                or declared.get("scriptSha256") != preview["script_sha256"]
            ):
                script_binding = "FAILED"
                reason = "SCRIPT_BINDING_MISMATCH"

            artifact = bytes(preview["artifact_mp4"])
            artifact_sha256 = hashlib.sha256(artifact).hexdigest()
            if (
                not artifact
                or artifact_sha256 != preview["artifact_sha256"]
                or record["artifact_sha256"] != preview["artifact_sha256"]
                or not isinstance(declared, dict)
                or declared.get("artifactSha256") != preview["artifact_sha256"]
                or event_details.get("artifactSha256") != preview["artifact_sha256"]
                or event_details.get("artifactBytes") != len(artifact)
                or manifest.get("scriptSha256") != preview["script_sha256"]
                or not isinstance(manifest.get("video"), dict)
                or manifest["video"].get("artifactSha256")
                != preview["artifact_sha256"]
            ):
                artifact_binding = "FAILED"
                reason = reason or "ARTIFACT_BINDING_MISMATCH"

            source_wav = record["source_wav"]
            metrics = self._measure_audio_evidence(source_wav)
            if (
                metrics["sourceWavSha256"] != record["source_wav_sha256"]
                or not isinstance(declared, dict)
                or declared.get("sourceWavSha256")
                != record["source_wav_sha256"]
                or not isinstance(manifest.get("narration"), dict)
                or manifest["narration"].get("sourceWavSha256")
                != record["source_wav_sha256"]
            ):
                source_binding = "FAILED"
                reason = reason or "SOURCE_HASH_MISMATCH"

            expected_id = self._audio_evidence_id(
                preview_id=preview_id,
                script_sha256=preview["script_sha256"],
                artifact_sha256=preview["artifact_sha256"],
                source_wav_sha256=record["source_wav_sha256"],
            )
            if (
                record["script_id"] != preview_id
                or record["owner_id"] != owner_id
                or record["evidence_id"] != expected_id
                or record["algorithm_version"] != self.AUDIO_EVIDENCE_ALGORITHM
                or not isinstance(declared, dict)
                or declared.get("evidenceId") != record["evidence_id"]
                or declared.get("algorithmVersion")
                != self.AUDIO_EVIDENCE_ALGORITHM
                or declared.get("previewId") != preview_id
                or declared.get("sourceWavBytes") != len(source_wav)
                or declared.get("createdAt") != record["created_at"]
            ):
                source_binding = "FAILED"
                reason = reason or "EVIDENCE_BINDING_MISMATCH"

            narration = manifest.get("narration")
            if (
                not isinstance(narration, dict)
                or not isinstance(declared, dict)
                or type(record["sample_count"]) is not int
                or record["sample_count"] != metrics["sampleCount"]
                or type(record["clipped_samples"]) is not int
                or record["clipped_samples"] != metrics["clippedSamples"]
                or type(record["clipped_sample_ratio"]) not in (int, float)
                or not math.isfinite(record["clipped_sample_ratio"])
                or abs(
                    record["clipped_sample_ratio"]
                    - metrics["clippedSampleRatio"]
                ) > 0.0000001
                or declared.get("sampleCount") != metrics["sampleCount"]
                or declared.get("clippedSamples") != metrics["clippedSamples"]
                or declared.get("clippedSampleRatio")
                != metrics["clippedSampleRatio"]
                or narration.get("sampleCount") != metrics["sampleCount"]
                or narration.get("clippedSamples") != metrics["clippedSamples"]
                or narration.get("clippedSampleRatio")
                != round(float(metrics["clippedSampleRatio"]), 8)
                or record["clipped_sample_ratio"] > 0.001
            ):
                reason = reason or "METRIC_MISMATCH"
        except (KeyError, TypeError, ValueError, OverflowError):
            reason = reason or "EVIDENCE_MALFORMED"
            source_binding = "FAILED"

        state = "VERIFIED" if reason is None else "INTEGRITY_FAILURE"
        if state == "VERIFIED":
            fields.update(
                {
                    "sampleCount": metrics["sampleCount"],
                    "clippedSamples": metrics["clippedSamples"],
                    "clippedSampleRatio": metrics["clippedSampleRatio"],
                    "lastVerifiedAt": checked_at,
                    "failureReason": None,
                    "nextAction": "No evidence action required.",
                }
            )
        else:
            fields.update(
                {
                    "failureReason": reason,
                    "nextAction": "Do not rely on technical QA; regenerate the preview and evidence.",
                }
            )
        fields.update(
            {
                "status": state,
                "sourceBinding": source_binding,
                "scriptBinding": script_binding,
                "artifactBinding": artifact_binding,
            }
        )
        return fields

    def _record_audio_evidence_verification(
        self,
        *,
        preview_id: str,
        owner_id: str,
        status: dict[str, object],
    ) -> None:
        if status["evidenceId"] is None:
            return
        with self.store._transaction() as connection:
            connection.execute(
                """
                UPDATE content_original_script_preview_audio_evidence
                SET last_checked_at = ?, last_verified_at = CASE
                        WHEN ? = 'VERIFIED' THEN ?
                        ELSE last_verified_at
                    END,
                    last_verification_state = ?, last_failure_reason = ?
                WHERE script_id = ? AND owner_id = ? AND evidence_id = ?
                """,
                (
                    status["lastCheckedAt"],
                    status["status"],
                    status["lastCheckedAt"],
                    status["status"],
                    status["failureReason"],
                    preview_id,
                    owner_id,
                    status["evidenceId"],
                ),
            )

    def _scene_visual_evidence_summary(
        self,
        *,
        script_sha256: str,
        script: str,
        render_event: object,
    ) -> dict[str, object]:
        def optional_sha256(value: object) -> str | None:
            return value if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) else None

        checked_at = self._now()
        event_details: object = None
        manifest: object = None
        if isinstance(render_event, dict):
            try:
                event_details = json.loads(render_event["detail_json"])
                manifest = (
                    event_details.get("manifest")
                    if isinstance(event_details, dict)
                    else None
                )
            except (json.JSONDecodeError, KeyError, TypeError):
                manifest = None

        planned = plan_script_scenes(
            script,
            max_scenes=self.MAX_SCENES,
            max_characters_per_scene=self.MAX_SCENE_CHARACTERS,
        )
        missing_scenes = [
            {
                "sceneId": scene.scene_id,
                "order": scene.order,
                "narrativeRole": scene.narrative_role,
                "visualStrategy": scene.visual_strategy,
                "sourceOffsets": {
                    "start": scene.start_offset,
                    "endExclusive": scene.end_offset,
                },
                "excerpt": scene.excerpt,
                "excerptSha256": hashlib.sha256(
                    scene.excerpt.encode("utf-8")
                ).hexdigest(),
                "renderedComposition": None,
                "state": "MISSING",
                "failureReason": "VISUAL_EVIDENCE_NOT_RETAINED",
                "asset": None,
            }
            for scene in planned
        ]
        if not isinstance(manifest, dict):
            return {
                "state": "INTEGRITY_FAILURE",
                "statusScope": "CURRENT_READBACK",
                "lastCheckedAt": checked_at,
                "sceneContractSha256": None,
                "sceneVisualManifestSha256": None,
                "rendererVersion": None,
                "failureReason": "RENDER_MANIFEST_MALFORMED",
                "nextAction": "Regenerate the preview and inspect each scene visual.",
                "scenes": [
                    {
                        **scene,
                        "state": "INTEGRITY_FAILURE",
                        "failureReason": "RENDER_MANIFEST_MALFORMED",
                    }
                    for scene in missing_scenes
                ],
            }
        if manifest.get("sceneVisualEvidenceVersion") is None:
            return {
                "state": "MISSING",
                "statusScope": "CURRENT_READBACK",
                "lastCheckedAt": checked_at,
                "sceneContractSha256": optional_sha256(
                    manifest.get("sceneContractSha256")
                ),
                "sceneVisualManifestSha256": optional_sha256(
                    manifest.get("sceneVisualManifestSha256")
                ),
                "rendererVersion": None,
                "failureReason": "VISUAL_EVIDENCE_NOT_RETAINED",
                "nextAction": "Generate a new preview with durable scene visual evidence.",
                "scenes": missing_scenes,
            }

        scene_contract_sha256 = self._scene_contract_sha256(planned)
        try:
            manifest_scenes = manifest.get("scenes")
            if (
                manifest.get("sceneVisualEvidenceVersion")
                not in {
                    self.LEGACY_SCENE_VISUAL_EVIDENCE_VERSION,
                    self.SCENE_VISUAL_EVIDENCE_VERSION,
                }
                or (
                    manifest.get("sceneVisualEvidenceVersion")
                    == self.LEGACY_SCENE_VISUAL_EVIDENCE_VERSION
                    and manifest.get("sceneRendererVersion")
                    != self.LEGACY_SCENE_RENDERER_VERSION
                )
                or (
                    manifest.get("sceneVisualEvidenceVersion")
                    == self.SCENE_VISUAL_EVIDENCE_VERSION
                    and manifest.get("sceneRendererVersion")
                    != self.SCENE_RENDERER_VERSION
                )
                or manifest.get("sceneContractVersion") != SCENE_SCHEMA_VERSION
                or manifest.get("scenePlannerVersion") != SCENE_PLANNER_VERSION
                or manifest.get("sceneContractSha256") != scene_contract_sha256
                or manifest.get("scriptSha256") != script_sha256
                or not isinstance(manifest_scenes, list)
                or len(manifest_scenes) != len(planned)
            ):
                raise ValueError("Scene visual manifest binding is invalid.")
            verified_scenes: list[dict[str, object]] = []
            for planned_scene, rendered in zip(planned, manifest_scenes):
                if (
                    not isinstance(rendered, dict)
                    or rendered.get("sceneId") != planned_scene.scene_id
                    or rendered.get("order") != planned_scene.order
                    or rendered.get("excerpt") != planned_scene.excerpt
                    or rendered.get("sceneContract") != planned_scene.to_dict()
                    or rendered.get("renderedComposition")
                    != rendered_scene_composition(planned_scene)
                    or (
                        manifest.get("sceneVisualEvidenceVersion")
                        == self.SCENE_VISUAL_EVIDENCE_VERSION
                        and rendered.get("visualTreatment")
                        != visual_treatment(planned_scene)
                    )
                ):
                    raise ValueError("Scene visual source binding is invalid.")
                asset = rendered.get("visualAsset")
                if not isinstance(asset, dict):
                    raise ValueError("Scene visual evidence is missing.")
                self._validate_scene_visual_asset(
                    scene=planned_scene,
                    asset=asset,
                    script_sha256=script_sha256,
                    scene_contract_sha256=scene_contract_sha256,
                )
                if (
                    asset["sha256"] != rendered.get("diagramSha256")
                    or asset["byteSize"] != rendered.get("diagramBytes")
                ):
                    raise ValueError("Scene visual evidence hash binding is invalid.")
                verified_scenes.append(
                    {
                        **missing_scenes[planned_scene.order - 1],
                        "renderedComposition": rendered["renderedComposition"],
                        "state": "VERIFIED",
                        "failureReason": None,
                        "asset": {
                            "assetId": asset["assetId"],
                            "format": asset["format"],
                            "width": asset["width"],
                            "height": asset["height"],
                            "byteSize": asset["byteSize"],
                            "sha256": asset["sha256"],
                            "rendererVersion": asset["rendererVersion"],
                            "visualTheme": asset.get("visualTheme"),
                            "visualTreatment": asset.get("visualTreatment"),
                            "generatedAt": asset["generatedAt"],
                            "validationStatus": asset["validationStatus"],
                        },
                    }
                )
            if (
                manifest.get("sceneVisualManifestSha256")
                != self._scene_visual_manifest_sha256(manifest_scenes)
            ):
                raise ValueError("Scene visual manifest hash is invalid.")
        except (ValueError, TypeError, KeyError, IndexError):
            return {
                "state": "INTEGRITY_FAILURE",
                "statusScope": "CURRENT_READBACK",
                "lastCheckedAt": checked_at,
                "sceneContractSha256": scene_contract_sha256,
                "sceneVisualManifestSha256": optional_sha256(
                    manifest.get("sceneVisualManifestSha256")
                ),
                "rendererVersion": (
                    manifest.get("sceneRendererVersion")
                    if isinstance(manifest.get("sceneRendererVersion"), str)
                    else None
                ),
                "failureReason": "SCENE_VISUAL_INTEGRITY_FAILURE",
                "nextAction": "Do not rely on scene QA; regenerate the preview.",
                "scenes": [
                    {
                        **scene,
                        "state": "INTEGRITY_FAILURE",
                        "failureReason": "SCENE_VISUAL_INTEGRITY_FAILURE",
                    }
                    for scene in missing_scenes
                ],
            }
        return {
            "state": "VERIFIED",
            "statusScope": "CURRENT_READBACK",
            "lastCheckedAt": checked_at,
            "sceneContractSha256": scene_contract_sha256,
            "sceneVisualManifestSha256": manifest.get(
                "sceneVisualManifestSha256"
            ),
            "rendererVersion": manifest.get("sceneRendererVersion"),
            "failureReason": None,
            "nextAction": "Review the video and scene visuals editorially.",
            "scenes": verified_scenes,
        }

    def list_audio_evidence(
        self,
        *,
        principal: str,
        limit: int = 50,
    ) -> dict[str, object]:
        if not isinstance(principal, str) or not principal.strip() or len(principal) > 200:
            raise PermissionError("An authenticated owner is required.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Preview evidence limit must be between 1 and 100.")
        with self.store._lock:
            previews = self.store._connection.execute(
                """
                SELECT p.script_id, p.owner_id, p.script_text, p.script_sha256,
                       p.state, p.artifact_mp4, p.artifact_sha256, p.created_at,
                       p.audio_evidence_required,
                       e.detail_json
                FROM content_original_script_previews p
                LEFT JOIN content_original_script_preview_events e
                  ON e.sequence = (
                      SELECT MAX(latest.sequence)
                      FROM content_original_script_preview_events latest
                      WHERE latest.script_id = p.script_id
                        AND latest.owner_id = p.owner_id
                        AND latest.event_type = 'PREVIEW_RENDERED'
                  )
                WHERE p.owner_id = ?
                  AND p.state = 'PREVIEW_READY'
                ORDER BY p.created_at DESC, p.script_id DESC
                LIMIT ?
                """,
                (principal, limit),
            ).fetchall()
            evidence_rows = {
                row["script_id"]: row
                for row in self.store._connection.execute(
                    """
                    SELECT * FROM content_original_script_preview_audio_evidence
                    WHERE owner_id = ?
                    """,
                    (principal,),
                ).fetchall()
            }
        items: list[dict[str, object]] = []
        for preview in previews:
            render_event = (
                {"detail_json": preview["detail_json"]}
                if preview["detail_json"] is not None
                else None
            )
            verification = self._verify_audio_evidence(
                preview_id=preview["script_id"],
                owner_id=principal,
                preview=preview,
                render_event=render_event,
                evidence=evidence_rows.get(preview["script_id"]),
                checked_at=self._now(),
            )
            scene_visual_evidence = self._scene_visual_evidence_summary(
                script_sha256=preview["script_sha256"],
                script=preview["script_text"],
                render_event=render_event,
            )
            self._record_audio_evidence_verification(
                preview_id=preview["script_id"],
                owner_id=principal,
                status=verification,
            )
            items.append(
                {
                    "scriptId": preview["script_id"],
                    "scriptSha256": preview["script_sha256"],
                    "artifactSha256": preview["artifact_sha256"],
                    "previewState": preview["state"],
                    "createdAt": preview["created_at"],
                    "audioEvidence": verification,
                    "sceneVisualEvidence": scene_visual_evidence,
                }
            )
        return {
            "state": "AVAILABLE",
            "generatedAt": self._now(),
            "items": items,
        }

    def _render(self, script_id: str, script: str) -> RenderedScriptPreview:
        try:
            return self._render_narrated(script_id, script)
        finally:
            for pattern in (
                f".clip-script-{script_id}-scene-*.png",
                f".clip-script-{script_id}-audio-*.wav",
                f".clip-script-{script_id}-narration-text-*.txt",
            ):
                for path in self.staging_directory.glob(pattern):
                    if not path.is_symlink() and path.is_file():
                        path.unlink(missing_ok=True)

    def _render_narrated(
        self,
        script_id: str,
        script: str,
    ) -> RenderedScriptPreview:
        if self.renderer is None:
            self.renderer = FFmpegLocalClipRenderer(
                managed_staging_directory=self.staging_directory
            )
        scenes = plan_script_scenes(
            script,
            max_scenes=self.MAX_SCENES,
            max_characters_per_scene=self.MAX_SCENE_CHARACTERS,
        )
        script_sha256 = hashlib.sha256(script.encode("utf-8")).hexdigest()
        scene_contract_sha256 = self._scene_contract_sha256(scenes)
        estimated_duration = (
            len(script) / self.ESTIMATED_CHARACTERS_PER_SECOND
            + len(scenes) * 0.25
        )
        scene_paths: list[Path] = []
        narration_paths: list[Path] = []
        text_paths: list[Path] = []
        scene_durations: list[float] = []
        captions: list[RenderCaption] = []
        manifest_scenes: list[dict[str, object]] = []
        scene_visual_bytes = 0
        combined_audio = bytearray()
        scene_start = 0.0
        deadline = time.monotonic() + 120.0

        for scene in scenes:
            if time.monotonic() >= deadline:
                raise TimeoutError("Local narration exceeded its total time limit.")
            token = uuid4().hex
            scene_path = self.staging_directory / (
                f".clip-script-{script_id}-scene-{scene.order:02d}-{token}.png"
            )
            descriptor = os.open(
                scene_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.close(descriptor)
            scene_paths.append(scene_path)
            try:
                render_scene_diagram(
                    scene,
                    scene_path,
                    scene_count=len(scenes),
                )
                diagram_bytes = scene_path.read_bytes()
            except (OSError, RuntimeError, ValueError) as exc:
                raise SceneVisualRenderError(
                    scene.scene_id,
                    "SCENE_COMPOSITION_FAILED",
                ) from exc
            scene_visual_bytes += len(diagram_bytes)
            if scene_visual_bytes > self.MAX_SCENE_VISUAL_BYTES_PER_PREVIEW:
                raise ValueError(
                    "Scene visuals exceed the bounded per-preview storage limit."
                )
            try:
                visual_asset = self._scene_visual_asset(
                    scene=scene,
                    diagram_bytes=diagram_bytes,
                    script_sha256=script_sha256,
                    scene_contract_sha256=scene_contract_sha256,
                    generated_at=self._now(),
                )
            except ValueError as exc:
                raise SceneVisualRenderError(
                    scene.scene_id,
                    "SCENE_ASSET_VALIDATION_FAILED",
                ) from exc

            text_path = self.staging_directory / (
                f".clip-script-{script_id}-narration-text-{token}.txt"
            )
            descriptor = os.open(
                text_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            text_paths.append(text_path)
            narration_excerpt = unicodedata.normalize("NFKD", scene.excerpt)
            narration_excerpt = narration_excerpt.encode("ascii", "ignore").decode("ascii")
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write(narration_excerpt or "Narration unavailable for this character set.")
                stream.flush()
                os.fsync(stream.fileno())

            narration_path = self.staging_directory / (
                f".clip-script-{script_id}-audio-{token}.wav"
            )
            descriptor = os.open(
                narration_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.close(descriptor)
            narration_paths.append(narration_path)
            filter_path = self.renderer._escape_filter_path(str(text_path))
            command = [
                self.renderer.ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-xerror",
                "-y",
                "-threads",
                "1",
                "-max_alloc",
                "33554432",
                "-f",
                "lavfi",
                "-i",
                f"flite=textfile='{filter_path}':voice={self.FLITE_VOICE}",
                "-ar",
                str(self.NARRATION_SAMPLE_RATE),
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                "-t",
                f"{self.MAX_SCENE_NARRATION_SECONDS:.0f}",
                "-fs",
                str(4 * 1024 * 1024),
                "-f",
                "wav",
                str(narration_path),
            ]
            try:
                remaining = max(0.1, min(20.0, deadline - time.monotonic()))
                result = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=remaining,
                )
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError(
                    f"Local narration exceeded its time limit for {scene.scene_id}."
                ) from exc
            except OSError as exc:
                raise RuntimeError("The local narration engine could not be started.") from exc
            if result.returncode != 0:
                raise RuntimeError(
                    f"Local narration failed for {scene.scene_id}; no preview was created."
                )
            audio_info = self.renderer.inspect_script_audio(
                narration_path,
                script_id=script_id,
            )
            try:
                with wave.open(str(narration_path), "rb") as audio:
                    parameters = audio.getparams()
                    if (
                        parameters.nchannels != 1
                        or parameters.sampwidth != 2
                        or parameters.framerate != self.NARRATION_SAMPLE_RATE
                        or parameters.nframes <= 0
                        or parameters.nframes
                        >= int(
                            self.NARRATION_SAMPLE_RATE
                            * (self.MAX_SCENE_NARRATION_SECONDS - 0.05)
                        )
                    ):
                        raise ValueError(
                            "Local narration exceeded its per-scene duration limit "
                            "or may have been truncated."
                        )
                    pcm = audio.readframes(parameters.nframes)
            except (wave.Error, EOFError) as exc:
                raise ValueError("Local narration returned a malformed WAV file.") from exc
            if len(pcm) != parameters.nframes * 2 or not pcm:
                raise ValueError("Local narration returned truncated audio.")
            peak, square_sum, clipped, count = self._measure_pcm(pcm)[:4]
            rms = math.sqrt(square_sum / count)
            peak_dbfs = 20 * math.log10(peak / 32768) if peak else float("-inf")
            rms_dbfs = 20 * math.log10(rms / 32768) if rms else float("-inf")
            if rms_dbfs < self.MIN_NARRATION_RMS_DBFS:
                raise ValueError(
                    f"Local narration was effectively silent for {scene.scene_id}; "
                    "no preview was created."
                )
            if peak_dbfs > self.MAX_NARRATION_PEAK_DBFS:
                raise ValueError(
                    f"Local narration peak exceeded the headroom limit for {scene.scene_id}."
                )
            if clipped / count > 0.001:
                raise ValueError(
                    f"Local narration clipped excessively for {scene.scene_id}."
                )
            speech_duration = parameters.nframes / self.NARRATION_SAMPLE_RATE
            combined_audio.extend(pcm)
            padding_frames = (
                self.NARRATION_GAP_FRAMES
                if scene.order < len(scenes)
                else 0
            )
            if padding_frames:
                padding = b"\x00\x00" * padding_frames
                combined_audio.extend(padding)
            visual_duration = (
                speech_duration + padding_frames / self.NARRATION_SAMPLE_RATE
            )
            scene_durations.append(visual_duration)
            captions.append(
                RenderCaption(
                    scene_start,
                    scene_start + speech_duration,
                    scene.excerpt,
                    sanitize_markup=False,
                )
            )
            caption_lines, caption_width = self.renderer._caption_layout(
                scene.excerpt
            )
            manifest_scenes.append(
                {
                    "sceneId": scene.scene_id,
                    "order": scene.order,
                    "sceneContract": scene.to_dict(),
                    "narrationRef": scene.narration_ref,
                    "captionRef": scene.caption_ref,
                    "assetState": "GENERATED",
                    "technicalValidation": "TECHNICAL_CHECKS_PASSED",
                    "scriptOffsets": {
                        "start": scene.start_offset,
                        "endExclusive": scene.end_offset,
                    },
                    "excerpt": scene.excerpt,
                    "excerptSha256": hashlib.sha256(
                        scene.excerpt.encode("utf-8")
                    ).hexdigest(),
                    "visualType": scene.visual_type,
                    "renderedComposition": rendered_scene_composition(scene),
                    "visualTreatment": visual_treatment(scene),
                    "diagramLabels": list(scene.labels),
                    "diagramSha256": hashlib.sha256(diagram_bytes).hexdigest(),
                    "diagramBytes": len(diagram_bytes),
                    "visualAsset": visual_asset,
                    "speechStartSeconds": round(scene_start, 3),
                    "speechEndSeconds": round(scene_start + speech_duration, 3),
                    "measuredSpeechDurationSeconds": round(speech_duration, 3),
                    "visualDurationSeconds": round(visual_duration, 3),
                    "caption": {
                        "startSeconds": round(round(scene_start, 3), 2),
                        "endSeconds": round(
                            round(scene_start + speech_duration, 3),
                            2,
                        ),
                        "text": scene.excerpt,
                        "scriptOffsets": {
                            "start": scene.start_offset,
                            "endExclusive": scene.end_offset,
                        },
                        "lineCount": len(caption_lines),
                        "maximumLineWidthPx": caption_width,
                    },
                }
            )
            scene_start += visual_duration

        if not combined_audio:
            raise ValueError("Local narration generated no audio samples.")
        (
            sample_peak,
            sample_square_sum,
            clipped_sample_count,
            sample_count,
            quiet_window_count,
            audio_window_count,
        ) = self._measure_pcm(bytes(combined_audio))
        duration = len(combined_audio) / (2 * self.NARRATION_SAMPLE_RATE)
        audio_path = self.staging_directory / (
            f".clip-script-{script_id}-audio-{uuid4().hex}.wav"
        )
        descriptor = os.open(
            audio_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        narration_paths.append(audio_path)
        with os.fdopen(descriptor, "wb") as stream:
            with wave.open(stream, "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(self.NARRATION_SAMPLE_RATE)
                audio.writeframes(combined_audio)
            stream.flush()
            os.fsync(stream.fileno())
        audio_info = self.renderer.inspect_script_audio(
            audio_path,
            script_id=script_id,
        )
        if abs(audio_info.duration_seconds - duration) > 0.02:
            raise ValueError("Combined narration audio failed its measured duration check.")
        source_wav = audio_path.read_bytes()
        audio_artifact_sha256 = hashlib.sha256(source_wav).hexdigest()

        staged_output = self.staging_directory / (
            f".clip-script-{script_id}-output-{uuid4().hex}.mp4"
        )
        descriptor = os.open(
            staged_output,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        os.close(descriptor)
        try:
            output_info = self.renderer.render_narrated_script_preview(
                tuple(scene_paths),
                tuple(scene_durations),
                audio_path,
                script_id=script_id,
                duration_seconds=duration,
                captions=tuple(captions),
                staged_output=staged_output,
            )
            encoded_audio = self.renderer.inspect_script_preview_audio(
                staged_output,
                tuple(scene_durations),
            )
            with staged_output.open("rb") as artifact:
                content = artifact.read(self.MAX_ARTIFACT_BYTES + 1)
            if not content or len(content) > self.MAX_ARTIFACT_BYTES:
                raise ValueError("Script preview artifact exceeds its size limit.")
            rms = math.sqrt(sample_square_sum / sample_count)
            manifest = {
                "workflow": "LOCAL_NARRATED_ORIGINAL_SCRIPT_PREVIEW",
                "scriptSha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
                "scenePlanSha256": self._scene_plan_sha256(scenes),
                "sceneContractVersion": SCENE_SCHEMA_VERSION,
                "scenePlannerVersion": SCENE_PLANNER_VERSION,
                "sceneContractSha256": scene_contract_sha256,
                "sceneVisualEvidenceVersion": self.SCENE_VISUAL_EVIDENCE_VERSION,
                "sceneRendererVersion": self.SCENE_RENDERER_VERSION,
                "sceneVisualManifestSha256": self._scene_visual_manifest_sha256(
                    manifest_scenes
                ),
                "scenes": manifest_scenes,
                "narration": {
                    "state": "GENERATED_LOCALLY",
                    "engine": "FFmpeg libflite",
                    "voice": self.FLITE_VOICE,
                    "language": "English (US)",
                    "voiceQuality": "NOT_HUMAN_REVIEWED",
                    "sampleRateHz": self.NARRATION_SAMPLE_RATE,
                    "channels": 1,
                    "sourceWavSha256": audio_artifact_sha256,
                    "durationSeconds": round(duration, 3),
                    "peakDbfs": round(20 * math.log10(sample_peak / 32768), 2),
                    "rmsDbfs": round(20 * math.log10(rms / 32768), 2),
                    "clippedSamples": clipped_sample_count,
                    "sampleCount": sample_count,
                    "clippedSampleRatio": round(
                        clipped_sample_count / sample_count,
                        8,
                    ),
                    "quietWindowRatio": round(
                        quiet_window_count / audio_window_count,
                        4,
                    ),
                    "intelligibility": "NOT_ASSESSED",
                    "naturalness": "NOT_ASSESSED",
                    "encodedAudioRmsDbfs": encoded_audio["rmsDbfs"],
                    "sceneBoundaryGaps": encoded_audio["sceneBoundaryGaps"],
                },
                "captions": {
                    "state": "BURNED_IN",
                    "timingMethod": (
                        "MEASURED_PER_SCENE_WAV_DURATION; phrase-level boundaries, "
                        "not word-level alignment"
                    ),
                    "safeArea": {
                        "leftPx": self.renderer.CAPTION_SAFE_LEFT,
                        "rightPx": self.renderer.CAPTION_SAFE_RIGHT,
                        "bottomPx": self.renderer.CAPTION_SAFE_BOTTOM,
                        "maximumWidthPx": self.renderer.CAPTION_SAFE_MAX_WIDTH,
                        "fontFamily": "DejaVu Sans",
                        "fontSizePx": self.renderer.CAPTION_FONT_SIZE,
                        "maximumLines": self.renderer.MAX_CAPTION_LINES,
                    },
                },
                "video": {
                    "container": "mp4",
                    "artifactSha256": hashlib.sha256(content).hexdigest(),
                    "artifactBytes": len(content),
                    "videoCodec": output_info.video_codec,
                    "audioCodec": output_info.audio_codec,
                    "audioSampleRateHz": output_info.audio_sample_rate,
                    "audioChannels": output_info.audio_channels,
                    "width": output_info.video_width,
                    "height": output_info.video_height,
                    "frameRate": output_info.video_fps,
                    "motion": {
                        "mode": self.renderer.SCENE_MOTION,
                        "maxZoom": 1.05,
                    },
                    "transitions": {
                        "type": self.renderer.SCENE_TRANSITION,
                        "durationSeconds": self.renderer.SCENE_TRANSITION_SECONDS,
                        "count": len(scenes) - 1,
                        "alignment": "CENTERED_ON_NARRATION_BOUNDARIES",
                    },
                    "durationSeconds": round(output_info.duration_seconds, 3),
                    "audioDurationSeconds": round(
                        output_info.audio_duration_seconds or 0.0,
                        3,
                    ),
                    "videoDurationSeconds": round(
                        output_info.video_duration_seconds or 0.0,
                        3,
                    ),
                },
                "qualityAssurance": {
                    "state": "TECHNICAL_CHECKS_PASSED",
                    "checks": [
                        "MP4 streams probed and codecs verified",
                        "720x1280 portrait video and bounded duration verified",
                        "Narration RMS, peak headroom, and clipping ratio passed bounded thresholds",
                        "Scene PNGs decoded with expected 720x1280 dimensions",
                        "Caption widths and line counts fit the declared safe area",
                        "Caption text and source offsets match each planned scene",
                        "Encoded MP4 audio level and intentional scene-boundary silence windows measured after decode",
                        "Alternating camera movement and narration-centered scene transitions rendered",
                        "Audio/video synchronization and full decode verified",
                    ],
                    "limitations": [
                        "Signal measurements do not establish speech intelligibility or naturalness.",
                        "Caption timing is phrase-level, not word-aligned; visual contrast and device-level readability still require human review.",
                        "The 150ms measured boundary gap is intentional silence, not a continuity or pacing judgment.",
                        "Scene diagrams are deterministic script-derived visuals, not factual verification.",
                        "References remain unverified; factual and rights review are required.",
                        "No approval, publication, audience analytics, or revenue is established.",
                    ],
                },
                "review": {
                    "humanReviewState": "PENDING",
                    "factualReviewState": "REQUIRED",
                    "rightsState": "NOT_EVALUATED",
                    "approvalState": "NOT_GRANTED",
                    "publicationState": "NOT_PUBLISHED",
                },
            }
            return RenderedScriptPreview(content, manifest, source_wav)
        finally:
            staged_output.unlink(missing_ok=True)
            for path in scene_paths + narration_paths + text_paths:
                path.unlink(missing_ok=True)

    def create_preview(
        self,
        *,
        principal: str,
        script: str,
        references: object,
        command_id: str,
    ) -> dict[str, object]:
        if (
            not isinstance(principal, str)
            or not principal.strip()
            or len(principal) > 200
        ):
            raise PermissionError("An authenticated owner is required.")
        if (
            not isinstance(script, str)
            or not script.strip()
            or len(script) > self.MAX_SCRIPT_CHARACTERS
            or any(
                ord(character) < 32 and character not in "\t\n\r"
                for character in script
            )
        ):
            raise ValueError(
                "Script must contain 1 to 20,000 Unicode characters with no control characters."
            )
        if (
            not isinstance(command_id, str)
            or not self.COMMAND_ID_PATTERN.fullmatch(command_id)
        ):
            raise ValueError("A bounded command ID is required.")
        validated_references = self._validate_references(references)
        try:
            script_bytes = script.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("Script must be valid UTF-8 text.") from exc
        fingerprint = hashlib.sha256(
            self._canonical(
                {"script": script, "references": validated_references}
            ).encode("utf-8")
        ).hexdigest()
        script_sha256 = hashlib.sha256(script_bytes).hexdigest()
        script_id = str(uuid4())
        now = self._now()
        existing_id: str | None = None

        with self.store._transaction() as connection:
            row = connection.execute(
                """
                SELECT script_id, owner_id, request_sha256, state
                FROM content_original_script_previews
                WHERE command_id = ?
                """,
                (command_id,),
            ).fetchone()
            if row is not None:
                if (
                    row["owner_id"] != principal
                    or row["request_sha256"] != fingerprint
                ):
                    raise ValueError(
                        "Command ID was already used for a different script request."
                    )
                existing_id = row["script_id"]
                existing_state = row["state"]
            else:
                count = connection.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM content_original_script_previews
                    WHERE owner_id = ?
                    """,
                    (principal,),
                ).fetchone()["total"]
                if count >= self.MAX_PREVIEWS_PER_OWNER:
                    raise ValueError(
                        "The owner has reached the 100-preview storage limit."
                    )
                connection.execute(
                    """
                    INSERT INTO content_original_script_previews (
                        script_id, owner_id, command_id, request_sha256,
                        script_text, script_sha256, references_json,
                        audio_evidence_required, state,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'RENDERING', ?, ?)
                    """,
                    (
                        script_id,
                        principal,
                        command_id,
                        fingerprint,
                        script,
                        script_sha256,
                        self._canonical(validated_references),
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO content_original_script_preview_events (
                        script_id, owner_id, event_type, detail_json, created_at
                    ) VALUES (?, ?, 'PREVIEW_REQUESTED', ?, ?)
                    """,
                    (
                        script_id,
                        principal,
                        self._canonical(
                            {
                                "scriptSha256": script_sha256,
                                "referenceCount": len(validated_references),
                                "referencesState": "UNVERIFIED",
                            }
                        ),
                        now,
                    ),
                )
                existing_state = "RENDERING"

        if existing_id is not None:
            if existing_state == "PREVIEW_READY":
                return self._response(existing_id, principal)
            if existing_state == "FAILED":
                raise RuntimeError(
                    "This script preview request previously failed and was not retried."
                )
            raise RuntimeError(
                "This script preview request is already in progress."
            )

        rendered: RenderedScriptPreview
        artifact_sha256: str
        evidence_manifest: dict[str, object]
        persisted_source_wav: bytes
        try:
            rendered = self._render(script_id, script)
            artifact = rendered.artifact
            render_manifest = dict(rendered.manifest)
            persisted_source_wav = rendered.source_wav
            if (
                not isinstance(artifact, bytes)
                or not artifact
                or len(artifact) > self.MAX_ARTIFACT_BYTES
            ):
                raise ValueError("Narrated script preview artifact exceeds its size limit.")
            if not isinstance(persisted_source_wav, bytes):
                raise ValueError("Narrated script preview audio evidence is unavailable.")
            artifact_sha256 = hashlib.sha256(artifact).hexdigest()
            evidence_manifest = self._build_audio_evidence_manifest(
                preview_id=script_id,
                script_sha256=script_sha256,
                artifact=artifact,
                source_wav=persisted_source_wav,
                created_at=self._now(),
            )
            render_manifest["audioEvidence"] = evidence_manifest
            self._validate_render_manifest(
                preview_id=script_id,
                script=script,
                script_sha256=script_sha256,
                artifact=artifact,
                source_wav=persisted_source_wav,
                manifest=render_manifest,
            )
        except Exception as exc:
            with self.store._transaction() as connection:
                connection.execute(
                    """
                    UPDATE content_original_script_previews
                    SET state = 'FAILED', updated_at = ?
                    WHERE script_id = ? AND owner_id = ? AND state = 'RENDERING'
                    """,
                    (self._now(), script_id, principal),
                )
                connection.execute(
                    """
                    INSERT INTO content_original_script_preview_events (
                        script_id, owner_id, event_type, detail_json, created_at
                    ) VALUES (?, ?, 'PREVIEW_FAILED', ?, ?)
                    """,
                    (
                        script_id,
                        principal,
                        self._canonical(self._preview_failure_details(exc)),
                        self._now(),
                    ),
                )
            raise

        evidence_storage_limit_exceeded = False
        try:
                with self.store._transaction() as connection:
                    stored_bytes = connection.execute(
                        """
                        SELECT COALESCE(SUM(length(source_wav)), 0) AS total
                        FROM content_original_script_preview_audio_evidence
                        WHERE owner_id = ?
                        """,
                        (principal,),
                    ).fetchone()["total"]
                    if (
                        stored_bytes + len(persisted_source_wav)
                        > self.MAX_AUDIO_EVIDENCE_BYTES_PER_OWNER
                    ):
                        failed_at = self._now()
                        connection.execute(
                            """
                            UPDATE content_original_script_previews
                            SET state = 'FAILED', updated_at = ?
                            WHERE script_id = ? AND owner_id = ? AND state = 'RENDERING'
                            """,
                            (failed_at, script_id, principal),
                        )
                        connection.execute(
                            """
                            INSERT INTO content_original_script_preview_events (
                                script_id, owner_id, event_type, detail_json, created_at
                            ) VALUES (?, ?, 'PREVIEW_FAILED', ?, ?)
                            """,
                            (
                                script_id,
                                principal,
                                self._canonical({"errorType": "AudioEvidenceStorageLimit"}),
                                failed_at,
                            ),
                        )
                        evidence_storage_limit_exceeded = True
                    else:
                        connection.execute(
                            """
                            INSERT INTO content_original_script_preview_audio_evidence (
                                script_id, owner_id, evidence_id, algorithm_version,
                                source_wav, source_wav_sha256, script_sha256,
                                artifact_sha256, sample_count, clipped_samples,
                                clipped_sample_ratio, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                script_id,
                                principal,
                                evidence_manifest["evidenceId"],
                                evidence_manifest["algorithmVersion"],
                                persisted_source_wav,
                                evidence_manifest["sourceWavSha256"],
                                script_sha256,
                                artifact_sha256,
                                evidence_manifest["sampleCount"],
                                evidence_manifest["clippedSamples"],
                                evidence_manifest["clippedSampleRatio"],
                                evidence_manifest["createdAt"],
                            ),
                        )
                        updated = connection.execute(
                            """
                            UPDATE content_original_script_previews
                            SET state = 'PREVIEW_READY', artifact_mp4 = ?,
                                artifact_sha256 = ?, updated_at = ?
                            WHERE script_id = ? AND owner_id = ? AND state = 'RENDERING'
                            """,
                            (artifact, artifact_sha256, self._now(), script_id, principal),
                        )
                        if updated.rowcount != 1:
                            raise RuntimeError(
                                "Script preview state changed during rendering."
                            )
                        connection.execute(
                            """
                            INSERT INTO content_original_script_preview_events (
                                script_id, owner_id, event_type, detail_json, created_at
                            ) VALUES (?, ?, 'PREVIEW_RENDERED', ?, ?)
                            """,
                            (
                                script_id,
                                principal,
                                self._canonical(
                                    {
                                        "artifactSha256": artifact_sha256,
                                        "artifactBytes": len(artifact),
                                        "audioIncluded": True,
                                        "manifest": render_manifest,
                                    }
                                ),
                                self._now(),
                            ),
                        )
        except Exception as exc:
                failed_at = self._now()
                with self.store._transaction() as connection:
                    updated = connection.execute(
                        """
                        UPDATE content_original_script_previews
                        SET state = 'FAILED', updated_at = ?
                        WHERE script_id = ? AND owner_id = ? AND state = 'RENDERING'
                        """,
                        (failed_at, script_id, principal),
                    )
                    if updated.rowcount == 1:
                        connection.execute(
                            """
                            INSERT INTO content_original_script_preview_events (
                                script_id, owner_id, event_type, detail_json, created_at
                            ) VALUES (?, ?, 'PREVIEW_FAILED', ?, ?)
                            """,
                            (
                                script_id,
                                principal,
                                self._canonical(self._preview_failure_details(exc)),
                                failed_at,
                            ),
                        )
                raise
        if evidence_storage_limit_exceeded:
                raise ValueError(
                    "The owner audio-evidence storage limit has been reached."
                )
        return self._response(script_id, principal)

    def record_editorial_review(
        self,
        *,
        principal: str,
        script_id: str,
        artifact_sha256: str,
        command_id: str,
        editorial_verdict: str,
        narration_assessment: str,
        pacing_assessment: str,
        caption_assessment: str,
        scene_assessment: str,
        factual_review_attested: bool,
        notes: str,
    ) -> dict[str, object]:
        if (
            not isinstance(principal, str)
            or not principal.strip()
            or len(principal) > 200
            or not isinstance(script_id, str)
            or not re.fullmatch(
                r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}",
                script_id,
            )
            or not isinstance(artifact_sha256, str)
            or not re.fullmatch(r"[a-f0-9]{64}", artifact_sha256)
            or not isinstance(command_id, str)
            or not self.COMMAND_ID_PATTERN.fullmatch(command_id)
        ):
            raise ValueError("A valid owner-scoped review target is required.")
        allowed_assessments = {
            "editorial_verdict": {
                "READY_FOR_FACTUAL_REVIEW",
                "REVISIONS_REQUIRED",
                "REJECTED",
            },
            "narration_assessment": {"INTELLIGIBLE", "UNINTELLIGIBLE"},
            "pacing_assessment": {"TOO_FAST", "APPROPRIATE", "TOO_SLOW"},
            "caption_assessment": {"SYNCHRONIZED", "NEEDS_CORRECTION"},
            "scene_assessment": {"COHERENT", "NEEDS_CORRECTION"},
        }
        values = {
            "editorial_verdict": editorial_verdict,
            "narration_assessment": narration_assessment,
            "pacing_assessment": pacing_assessment,
            "caption_assessment": caption_assessment,
            "scene_assessment": scene_assessment,
        }
        if any(
            not isinstance(value, str) or value not in allowed_assessments[field]
            for field, value in values.items()
        ):
            raise ValueError("Editorial review assessment contains an invalid value.")
        if type(factual_review_attested) is not bool:
            raise ValueError("Factual review attestation must be a boolean.")
        if (
            not isinstance(notes, str)
            or len(notes) > 1200
            or any(
                ord(character) < 32 and character not in "\t\n\r"
                for character in notes
            )
            or (factual_review_attested and not notes.strip())
        ):
            raise ValueError(
                "Review notes must be printable, bounded, and present for factual attestation."
            )
        review_payload = {
            "scriptId": script_id,
            "artifactSha256": artifact_sha256,
            **values,
            "factualReviewAttested": factual_review_attested,
            "notes": notes,
        }
        fingerprint = hashlib.sha256(
            self._canonical(review_payload).encode("utf-8")
        ).hexdigest()
        with self.store._transaction() as connection:
            preview = connection.execute(
                """
                SELECT owner_id, state, artifact_sha256
                FROM content_original_script_previews
                WHERE script_id = ?
                """,
                (script_id,),
            ).fetchone()
            if preview is None or preview["owner_id"] != principal:
                raise PermissionError(
                    "Script preview is outside the authenticated account."
                )
            if (
                preview["state"] != "PREVIEW_READY"
                or preview["artifact_sha256"] != artifact_sha256
            ):
                raise ValueError(
                    "Editorial review must match the current rendered artifact."
                )
            existing = connection.execute(
                """
                SELECT owner_id, request_sha256
                FROM content_original_script_preview_reviews
                WHERE command_id = ?
                """,
                (command_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["owner_id"] != principal
                    or existing["request_sha256"] != fingerprint
                ):
                    raise ValueError(
                        "Review command ID was already used for a different assessment."
                    )
            else:
                review_count = connection.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM content_original_script_preview_reviews
                    WHERE script_id = ? AND owner_id = ?
                    """,
                    (script_id, principal),
                ).fetchone()["total"]
                if review_count >= self.MAX_REVIEWS_PER_PREVIEW:
                    raise ValueError(
                        "The script preview has reached its review record limit."
                    )
                connection.execute(
                    """
                    INSERT INTO content_original_script_preview_reviews (
                        review_id, script_id, owner_id, reviewer_id,
                        artifact_sha256, command_id, request_sha256,
                        editorial_verdict, narration_assessment,
                        pacing_assessment, caption_assessment, scene_assessment,
                        factual_review_attested, notes, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        script_id,
                        principal,
                        principal,
                        artifact_sha256,
                        command_id,
                        fingerprint,
                        editorial_verdict,
                        narration_assessment,
                        pacing_assessment,
                        caption_assessment,
                        scene_assessment,
                        int(factual_review_attested),
                        notes,
                        self._now(),
                    ),
                )
        return self._response(script_id, principal, include_artifact=False)

    def _response(
        self,
        script_id: str,
        principal: str,
        *,
        include_artifact: bool = True,
    ) -> dict[str, object]:
        with self.store._lock:
            row = self.store._connection.execute(
                """
                SELECT owner_id, script_text, script_sha256, references_json, state,
                       artifact_mp4, artifact_sha256, created_at, updated_at,
                       audio_evidence_required
                FROM content_original_script_previews
                WHERE script_id = ? AND owner_id = ?
                """,
                (script_id, principal),
            ).fetchone()
            rendered_event = self.store._connection.execute(
                """
                SELECT detail_json
                FROM content_original_script_preview_events
                WHERE script_id = ? AND owner_id = ?
                  AND event_type = 'PREVIEW_RENDERED'
                ORDER BY sequence DESC LIMIT 1
                """,
                (script_id, principal),
            ).fetchone()
            review_row = self.store._connection.execute(
                """
                SELECT review_id, reviewer_id, artifact_sha256, editorial_verdict,
                       narration_assessment, pacing_assessment,
                       caption_assessment, scene_assessment,
                       factual_review_attested, notes, created_at
                FROM content_original_script_preview_reviews
                WHERE script_id = ? AND owner_id = ?
                ORDER BY sequence DESC LIMIT 1
                """,
                (script_id, principal),
            ).fetchone()
            audio_evidence_row = self.store._connection.execute(
                """
                SELECT *
                FROM content_original_script_preview_audio_evidence
                WHERE script_id = ? AND owner_id = ?
                """,
                (script_id, principal),
            ).fetchone()
        if row is None:
            raise PermissionError("Script preview is outside the authenticated account.")
        if row["state"] != "PREVIEW_READY" or row["artifact_mp4"] is None:
            raise RuntimeError("A completed script preview artifact is unavailable.")
        if rendered_event is None:
            raise RuntimeError("The persisted preview quality manifest is unavailable.")
        try:
            render_details = json.loads(rendered_event["detail_json"])
            render_manifest = render_details["manifest"]
            event_artifact_sha256 = render_details["artifactSha256"]
            event_artifact_bytes = render_details["artifactBytes"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RuntimeError("The persisted preview quality manifest is malformed.") from exc
        artifact = bytes(row["artifact_mp4"])
        if (
            len(artifact) > self.MAX_ARTIFACT_BYTES
            or hashlib.sha256(artifact).hexdigest() != row["artifact_sha256"]
            or event_artifact_sha256 != row["artifact_sha256"]
            or event_artifact_bytes != len(artifact)
        ):
            raise RuntimeError("Stored script preview failed its integrity check.")
        if hashlib.sha256(row["script_text"].encode("utf-8")).hexdigest() != row[
            "script_sha256"
        ]:
            raise RuntimeError("Stored script text failed its integrity check.")
        audio_evidence = self._verify_audio_evidence(
            preview_id=script_id,
            owner_id=principal,
            preview=row,
            render_event={
                "detail_json": rendered_event["detail_json"],
            },
            evidence=audio_evidence_row,
            checked_at=self._now(),
        )
        scene_visual_evidence = self._scene_visual_evidence_summary(
            script_sha256=row["script_sha256"],
            script=row["script_text"],
            render_event={"detail_json": rendered_event["detail_json"]},
        )
        source_wav = (
            bytes(audio_evidence_row["source_wav"])
            if audio_evidence["status"] == "VERIFIED"
            and audio_evidence_row is not None
            else None
        )
        validation_manifest = render_manifest
        if (
            scene_visual_evidence["state"] != "VERIFIED"
            and audio_evidence["status"] == "VERIFIED"
        ):
            validation_manifest = copy.deepcopy(render_manifest)
            validation_manifest.pop("sceneVisualEvidenceVersion", None)
        try:
            self._validate_render_manifest(
                preview_id=script_id,
                script=row["script_text"],
                script_sha256=row["script_sha256"],
                artifact=artifact,
                source_wav=source_wav,
                manifest=validation_manifest,
            )
        except ValueError as exc:
            if audio_evidence["status"] == "VERIFIED":
                raise RuntimeError(
                    "Stored script preview manifest failed its integrity check."
                ) from exc
            audio_evidence["status"] = "INTEGRITY_FAILURE"
            audio_evidence["failureReason"] = "MANIFEST_INTEGRITY_FAILURE"
            audio_evidence["nextAction"] = (
                "Do not rely on technical QA; regenerate the preview and evidence."
            )
        self._record_audio_evidence_verification(
            preview_id=script_id,
            owner_id=principal,
            status=audio_evidence,
        )
        response_manifest = copy.deepcopy(render_manifest)
        current_manifest_evidence = response_manifest.get("audioEvidence")
        render_time_status = (
            current_manifest_evidence.get("status")
            if isinstance(current_manifest_evidence, dict)
            else None
        )
        response_manifest["audioEvidence"] = {
            **(
                current_manifest_evidence
                if isinstance(current_manifest_evidence, dict)
                else {}
            ),
            **audio_evidence,
            "renderTimeStatus": render_time_status,
            "statusScope": "CURRENT_READBACK",
        }
        response_manifest["sceneVisualEvidence"] = scene_visual_evidence
        if scene_visual_evidence["state"] != "VERIFIED":
            for scene in response_manifest.get("scenes", []):
                if isinstance(scene, dict):
                    scene["assetState"] = scene_visual_evidence["state"]
                    scene["technicalValidation"] = (
                        "FAILED"
                        if scene_visual_evidence["state"] == "INTEGRITY_FAILURE"
                        else "NOT_RUN"
                    )
                    scene["visualAsset"] = None
            quality_assurance = response_manifest["qualityAssurance"]
            quality_assurance["state"] = (
                "SCENE_VISUAL_EVIDENCE_"
                f"{scene_visual_evidence['state']}"
            )
            limitations = quality_assurance.get("limitations")
            if not isinstance(limitations, list):
                limitations = []
                quality_assurance["limitations"] = limitations
            limitations.append(
                "Durable scene visual evidence is not currently verified."
            )
        if audio_evidence["status"] != "VERIFIED":
            quality_assurance = response_manifest["qualityAssurance"]
            quality_assurance["state"] = (
                f"AUDIO_EVIDENCE_{audio_evidence['status']}"
            )
            limitations = quality_assurance.get("limitations")
            if not isinstance(limitations, list):
                limitations = []
                quality_assurance["limitations"] = limitations
            limitations.append(
                "Durable audio evidence is not currently verified."
            )
        editorial_review: dict[str, object] = {
            "state": "PENDING",
            "reviewerId": None,
            "artifactSha256": row["artifact_sha256"],
            "editorialVerdict": None,
            "narrationAssessment": "NOT_ASSESSED",
            "pacingAssessment": "NOT_ASSESSED",
            "captionAssessment": "NOT_ASSESSED",
            "sceneAssessment": "NOT_ASSESSED",
            "factualReviewState": "REQUIRED",
            "selfReview": False,
            "notes": "",
            "createdAt": None,
        }
        if review_row is not None:
            editorial_review.update(
                {
                    "state": "RECORDED",
                    "reviewId": review_row["review_id"],
                    "reviewerId": review_row["reviewer_id"],
                    "editorialVerdict": review_row["editorial_verdict"],
                    "narrationAssessment": review_row["narration_assessment"],
                    "pacingAssessment": review_row["pacing_assessment"],
                    "captionAssessment": review_row["caption_assessment"],
                    "sceneAssessment": review_row["scene_assessment"],
                    "factualReviewState": (
                        "OPERATOR_ATTESTED_COMPLETE"
                        if review_row["factual_review_attested"]
                        else "REQUIRED"
                    ),
                    "selfReview": review_row["reviewer_id"] == principal,
                    "notes": review_row["notes"],
                    "createdAt": review_row["created_at"],
                }
            )
        preview_response: dict[str, object] = {
            "state": "RENDERED",
            "mimeType": "video/mp4",
            "sha256": row["artifact_sha256"],
            "sizeBytes": len(artifact),
            "audioIncluded": True,
        }
        if include_artifact:
            preview_response["base64"] = base64.b64encode(artifact).decode("ascii")
        return {
            "scriptId": script_id,
            "script": row["script_text"],
            "scriptSha256": row["script_sha256"],
            "references": {
                "state": "UNVERIFIED",
                "items": json.loads(row["references_json"]),
            },
            "preview": preview_response,
            "productionManifest": response_manifest,
            "sceneVisualEvidence": scene_visual_evidence,
            "audioEvidence": audio_evidence,
            "review": {
                "humanReviewState": editorial_review["state"],
                "factualReviewState": editorial_review["factualReviewState"],
                "rightsState": "NOT_EVALUATED",
                "approvalState": "NOT_GRANTED",
                "publicationState": "NOT_PUBLISHED",
                "editorial": editorial_review,
            },
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
