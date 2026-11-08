"""Reproducible, isolated baseline for the current CRE video editor.

Run with:
    pytest -q -s tests/application/test_cre_evaluation_baseline.py

The test prints a structured synthetic-media report. Encoded output hashes are
recorded for artifact identity but are intentionally not compared across runs.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest

from tests.application import test_content_lifecycle as lifecycle
from tests.application.test_content_processing import _harness
from ultron.application.content.clip_renderer import (
    FFmpegLocalClipRenderer,
)
from ultron.application.content.rendering import ContentClipRenderingService
from ultron.domain.content import (
    AcquisitionMethod,
    AssetCategory,
    ContentEditProject,
    RightsAction,
    SourceAsset,
    SourceProvenance,
)


_SCENE_SECONDS = 2
_OUTPUT_SAMPLE_RATE = 8_000
_TONE_HZ_BY_SOURCE = {"source-a": 440, "source-b": 880, "source-c": 660}


def _run(command: list[str], *, timeout: int = 30) -> bytes:
    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result.stdout


def _make_fixture(
    directory: Path,
    *,
    name: str,
    colors: tuple[str, ...],
    size: str,
    fps: int,
    tone_hz: int,
    scene_seconds: int = _SCENE_SECONDS,
) -> bytes:
    fixture = directory / f"{name}.mp4"
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]
    for color in colors:
        command.extend(
            [
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s={size}:r={fps}:d={scene_seconds}",
            ]
        )
    command.extend(
        [
            "-f",
            "lavfi",
            "-i",
            (
                f"sine=frequency={tone_hz}:sample_rate=16000:"
                f"duration={len(colors) * scene_seconds}"
            ),
            "-filter_complex",
            ";".join(
                f"[{index}:v]drawbox="
                "x='mod(t*50,iw-24)':y=24:w=24:h=ih-48:"
                f"color=white:t=fill[v{index}]"
                for index in range(len(colors))
            )
            + ";"
            + "".join(f"[v{index}]" for index in range(len(colors)))
            + f"concat=n={len(colors)}:v=1:a=0[vout]",
            "-map",
            "[vout]",
            "-map",
            f"{len(colors)}:a:0",
            "-c:v",
            "mpeg4",
            "-q:v",
            "8",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-shortest",
            str(fixture),
        ]
    )
    _run(command)
    return fixture.read_bytes()


def _register_second_source(
    harness,
    content: bytes,
    *,
    asset_id: str = "baseline-source-b",
) -> SourceAsset:
    asset_suffix = asset_id.removeprefix("baseline-source-")
    asset = SourceAsset(
        asset_id=asset_id,
        owner_id="alice",
        category=AssetCategory.FOOTAGE,
        content_sha256=hashlib.sha256(content).hexdigest(),
        provenance=SourceProvenance(
            acquisition_method=AcquisitionMethod.OWNER_UPLOAD,
            acquired_at=datetime.now(UTC),
            source_reference=f"upload:synthetic-{asset_id}",
        ),
        storage_key=f"asset:baseline-{asset_suffix}",
    )
    harness.invoke(
        "content_register_asset",
        asset.model_dump(mode="json"),
        harness.service.register_asset,
        asset=asset,
        source_stream=BytesIO(content),
    )
    return asset


def _create_project(harness, source_ids: tuple[str, ...], project_id: str):
    project = ContentEditProject(
        project_id=project_id,
        owner_id="alice",
        source_asset_ids=source_ids,
        script="Synthetic evaluation baseline.",
    )
    harness.invoke(
        "content_create_edit_project",
        project.model_dump(mode="json"),
        harness.service.create_edit_project,
        project=project,
        command_id=f"create-{project_id}",
    )
    return project


def _plan(harness, rendering, project_id: str, instruction: str, command_id: str):
    return harness.invoke(
        "content_autoplan_edit_project",
        {
            "project_id": project_id,
            "instruction_length": len(instruction),
        },
        rendering.autoplan_edit_project,
        project_id=project_id,
        instruction=instruction,
        command_id=command_id,
    )


def _normalized_plan(plan, labels: dict[str, str]) -> dict[str, object]:
    segments = plan["segments"]
    source_counts = {
        source: sum(
            labels[segment["source_asset_id"]] == source for segment in segments
        )
        for source in sorted(set(labels.values()))
    }
    return {
        "segmentCount": plan["segmentCount"],
        "durationSeconds": round(
            sum(
                segment["source_out_seconds"] - segment["source_in_seconds"]
                for segment in segments
            ),
            3,
        ),
        "sourceCounts": {
            source: count for source, count in source_counts.items() if count
        },
        "segments": [
            {
                "source": labels[segment["source_asset_id"]],
                "sourceInSeconds": round(segment["source_in_seconds"], 3),
                "sourceOutSeconds": round(segment["source_out_seconds"], 3),
                "durationSeconds": round(
                    segment["source_out_seconds"] - segment["source_in_seconds"],
                    3,
                ),
                "timelineOrder": segment["timeline_order"],
            }
            for segment in segments
        ],
    }


def _render(harness, rendering, storage, project_id: str, vertical: bool):
    result = harness.invoke(
        "content_render_private_edit_project",
        {"project_id": project_id, "vertical": vertical},
        rendering.render_private_edit_project,
        project_id=project_id,
        vertical=vertical,
        command_id=f"render-{project_id}",
    )
    output = storage.read_clip_output(result["artifactId"], result["sha256"])
    assert hashlib.sha256(output).hexdigest() == result["sha256"]
    staged = storage.create_clip_staging_path(result["renderId"])
    try:
        staged.write_bytes(output)
        rendering.renderer._decode(staged)
        media = asdict(rendering.renderer._probe(staged, kind="output"))
        quality = rendering.renderer.inspect_render_quality(staged)
        assert quality["status"] == "PASSED"
        return result, output, media, quality, "PASSED"
    finally:
        storage.delete_clip_staging(staged)


def _reference_plan(plan: dict[str, object]) -> dict[str, object]:
    return {
        "segmentCount": plan["segmentCount"],
        "durationSeconds": plan["durationSeconds"],
        "sourceCounts": plan["sourceCounts"],
        "segments": [
            [
                segment["source"],
                segment["sourceInSeconds"],
                segment["sourceOutSeconds"],
            ]
            for segment in plan["segments"]
        ],
    }


def _baseline_signature(single, two_source, revision):
    return {
        "singleSource": _reference_plan(single),
        "twoSourceBaseline": _reference_plan(two_source),
        "twoSourceRevision": _reference_plan(revision),
    }


def _assert_plan_in_bounds(plan, allowed_sources, source_metadata):
    assert plan["segmentCount"] == len(plan["segments"])
    for order, segment in enumerate(plan["segments"], start=1):
        source = segment["source"]
        duration = source_metadata[source]["media"]["duration_seconds"]
        assert source in allowed_sources
        assert segment["timelineOrder"] == order
        assert 0 <= segment["sourceInSeconds"] < segment["sourceOutSeconds"] <= duration


def _substantial_overlap_count(segments) -> int:
    return sum(
        min(left.source_out_seconds, right.source_out_seconds)
        - max(left.source_in_seconds, right.source_in_seconds)
        >= 0.5
        * min(
            left.source_out_seconds - left.source_in_seconds,
            right.source_out_seconds - right.source_in_seconds,
        )
        for index, left in enumerate(segments)
        for right in segments[index + 1 :]
        if left.source_asset_id == right.source_asset_id
    )


def _tone_power(samples: bytes, frequency_hz: int) -> float:
    values = (sample[0] for sample in struct.iter_unpack("<h", samples))
    coefficient = 2 * math.cos(2 * math.pi * frequency_hz / _OUTPUT_SAMPLE_RATE)
    previous = previous_previous = 0.0
    for value in values:
        current = value + coefficient * previous - previous_previous
        previous_previous, previous = previous, current
    return (
        previous_previous**2
        + previous**2
        - coefficient * previous * previous_previous
    )


def _measure_audio_tones(
    renderer: FFmpegLocalClipRenderer,
    output_path: Path,
    segments: list[dict[str, object]],
) -> list[dict[str, object]]:
    measurements = []
    for segment in segments:
        sample_start = float(segment["timelineStartSeconds"]) + 0.5
        samples = _run(
            [
                renderer.ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(output_path),
                "-ss",
                f"{sample_start:.6f}",
                "-t",
                "0.5",
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(_OUTPUT_SAMPLE_RATE),
                "-f",
                "s16le",
                "pipe:1",
            ]
        )
        source = str(segment["source"])
        expected = _TONE_HZ_BY_SOURCE[source]
        other = next(
            frequency for label, frequency in _TONE_HZ_BY_SOURCE.items()
            if label != source
        )
        expected_power = _tone_power(samples, expected)
        other_power = _tone_power(samples, other)
        assert expected_power > other_power * 4
        measurements.append(
            {
                "source": source,
                "timelineStartSeconds": segment["timelineStartSeconds"],
                "expectedToneHz": expected,
                "expectedTonePower": round(expected_power, 2),
                "otherToneHz": other,
                "otherTonePower": round(other_power, 2),
                "mappingPassed": True,
            }
        )
    return measurements


def test_synthetic_cre_editing_baseline(tmp_path, monkeypatch, record_property):
    """Run isolated planner/render cases and emit a machine-readable report."""
    source_a = _make_fixture(
        tmp_path,
        name="source-a",
        colors=("red", "green", "blue", "yellow"),
        size="160x120",
        fps=15,
        tone_hz=_TONE_HZ_BY_SOURCE["source-a"],
    )
    source_b = _make_fixture(
        tmp_path,
        name="source-b",
        colors=("cyan", "magenta", "orange", "purple"),
        size="120x160",
        fps=24,
        tone_hz=_TONE_HZ_BY_SOURCE["source-b"],
    )
    source_c = _make_fixture(
        tmp_path,
        name="source-c-scene-diversity",
        colors=(
            "red",
            "green",
            "blue",
            "yellow",
            "cyan",
            "magenta",
            "orange",
            "purple",
            "white",
            "lime",
        ),
        size="160x120",
        fps=15,
        tone_hz=_TONE_HZ_BY_SOURCE["source-c"],
        scene_seconds=4,
    )
    isolated_root = tmp_path / "isolated"
    isolated_root.mkdir()
    harness, asset_a, _, storage = _harness(
        isolated_root,
        monkeypatch,
        media_bytes=source_a,
        rights_actions=frozenset({RightsAction.ANALYZE}),
    )
    asset_b = _register_second_source(harness, source_b)
    asset_c = _register_second_source(
        harness,
        source_c,
        asset_id="baseline-source-c",
    )
    renderer = FFmpegLocalClipRenderer(
        managed_staging_directory=storage.clip_staging_directory,
    )
    rendering = ContentClipRenderingService(
        content_rights=harness.service,
        renderer=renderer,
    )
    labels = {
        asset_a.asset_id: "source-a",
        asset_b.asset_id: "source-b",
        asset_c.asset_id: "source-c",
    }
    source_data = {
        "source-a": source_a,
        "source-b": source_b,
        "source-c": source_c,
    }
    try:
        source_metadata = {}
        for label, content in source_data.items():
            path = storage.stage_clip_source(f"baseline-{label}", content)
            try:
                source_metadata[label] = {
                    "fixtureId": label,
                    "container": "mp4",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "sizeBytes": len(content),
                    "media": asdict(renderer.inspect_source_video(path)),
                    "sceneChangesSeconds": list(renderer.detect_scene_changes(path)),
                }
            finally:
                storage.delete_clip_staging(path)

        single = _create_project(
            harness,
            (asset_a.asset_id,),
            "baseline-single-source",
        )
        single_plan = _plan(
            harness,
            rendering,
            single.project_id,
            "Create a single-source baseline edit.",
            "plan-single-source",
        )
        persisted_single_plan = harness.store.get_latest_edit_plan(
            single.project_id,
            "alice",
        )
        assert persisted_single_plan is not None
        single_repeated_interval_count = sum(
            segment.selection_method
            in {
                "repeated_detected_scene_interval",
                "repeated_source_interval_fallback",
            }
            for segment in persisted_single_plan.segments
        )
        single_overlap_count = _substantial_overlap_count(
            persisted_single_plan.segments
        )
        assert single_repeated_interval_count == 0
        assert single_overlap_count == 0
        (
            single_result,
            single_output,
            single_media,
            single_quality,
            single_decode_status,
        ) = _render(
            harness, rendering, storage, single.project_id, False
        )

        project = _create_project(
            harness,
            (asset_a.asset_id, asset_b.asset_id),
            "baseline-two-source",
        )
        baseline_instruction = "Make a cinematic slow edit, 20 sec."
        baseline_plan = _plan(
            harness,
            rendering,
            project.project_id,
            baseline_instruction,
            "plan-two-source-baseline",
        )
        baseline_plan_record = harness.store.get_latest_edit_plan(
            project.project_id,
            "alice",
        )
        assert baseline_plan_record is not None
        repeated_plan = _plan(
            harness,
            rendering,
            project.project_id,
            baseline_instruction,
            "plan-two-source-repeat",
        )
        repeated_plan_record = harness.store.get_latest_edit_plan(
            project.project_id,
            "alice",
        )
        assert repeated_plan_record is not None
        baseline_projection = _normalized_plan(baseline_plan, labels)
        assert baseline_projection == _normalized_plan(repeated_plan, labels)
        assert tuple(segment.audio for segment in baseline_plan_record.segments) == tuple(
            segment.audio for segment in repeated_plan_record.segments
        )
        persisted_baseline_plan = harness.store.get_latest_edit_plan(
            project.project_id,
            "alice",
        )
        assert persisted_baseline_plan is not None
        baseline_plan_digest = hashlib.sha256(
            harness.store._canonical(persisted_baseline_plan).encode("utf-8")
        ).hexdigest()
        assert persisted_baseline_plan.revision == 2
        assert [segment.transition_before for segment in persisted_baseline_plan.segments] == [
            "start",
            *("hard_cut" for _ in persisted_baseline_plan.segments[1:]),
        ]
        assert persisted_baseline_plan.schema_version == "cre-creative-plan-v3"
        assert persisted_baseline_plan.audio_policy == "explicit_segment_audio"
        assert all(
            segment.audio.mode == "source_audio"
            and segment.audio.source_asset_id == segment.source_asset_id
            and segment.audio.gain_db == 0.0
            for segment in persisted_baseline_plan.segments
        )

        (
            render_result,
            rendered_output,
            output_metadata,
            quality,
            decode_status,
        ) = _render(
            harness, rendering, storage, project.project_id, False
        )
        assert render_result["planMode"] == "structured"
        assert render_result["planRevision"] == persisted_baseline_plan.revision
        assert render_result["planSha256"] == baseline_plan_digest
        assert render_result["rendererSettings"]["transitions"] == "hard_cut"
        assert (
            render_result["rendererSettings"]["audioPolicy"]
            == persisted_baseline_plan.audio_policy
        )
        assert render_result["planVersion"] == "cre-creative-plan-v3"
        assert render_result["audioValidation"]["status"] == "PASSED"
        assert render_result["audioValidation"]["outputPresent"] is True
        assert render_result["audioValidation"]["decodedPeakDbfs"] <= -1.0
        assert render_result["orientation"] == "landscape"
        staged_render = storage.create_clip_staging_path(
            render_result["renderId"]
        )
        try:
            staged_render.write_bytes(rendered_output)
            render_segments = []
            timeline_offset = 0.0
            for segment in baseline_projection["segments"]:
                render_segments.append(
                    {
                        "source": segment["source"],
                        "timelineStartSeconds": round(timeline_offset, 3),
                    }
                )
                timeline_offset += segment["durationSeconds"]
            audio_mapping = _measure_audio_tones(
                renderer,
                staged_render,
                render_segments,
            )
        finally:
            storage.delete_clip_staging(staged_render)

        revision_instruction = "Make the pacing energetic and fast, 20 sec."
        revision_plan = _plan(
            harness,
            rendering,
            project.project_id,
            revision_instruction,
            "plan-two-source-revision",
        )
        revision_projection = _normalized_plan(revision_plan, labels)
        _assert_plan_in_bounds(
            _normalized_plan(single_plan, labels),
            {"source-a"},
            source_metadata,
        )
        _assert_plan_in_bounds(
            baseline_projection,
            {"source-a", "source-b"},
            source_metadata,
        )
        _assert_plan_in_bounds(
            revision_projection,
            {"source-a", "source-b"},
            source_metadata,
        )
        assert revision_projection["segmentCount"] > baseline_projection["segmentCount"]
        assert (
            revision_projection["segments"][0]["durationSeconds"]
            < baseline_projection["segments"][0]["durationSeconds"]
        )
        assert len(single_plan["segments"]) >= 2
        assert not harness.store._connection.in_transaction
        assert asset_a.content_sha256 == source_metadata["source-a"]["sha256"]
        assert asset_b.content_sha256 == source_metadata["source-b"]["sha256"]
        assert abs(
            output_metadata["audio_duration_seconds"]
            - output_metadata["video_duration_seconds"]
        ) <= renderer.SYNC_TOLERANCE_SECONDS
        assert single_quality["status"] == "PASSED"
        assert single_media["has_video"] is True
        assert single_media["has_audio"] is True
        assert single_media["video_width"] == 1280
        assert single_media["video_height"] == 720
        assert single_media["video_fps"] == pytest.approx(30)

        scene_project = _create_project(
            harness,
            (asset_c.asset_id,),
            "baseline-scene-diversity",
        )
        scene_instruction = "Make a slow edit, 30 sec."
        first_scene_plan_result = _plan(
            harness,
            rendering,
            scene_project.project_id,
            scene_instruction,
            "plan-scene-diversity-first",
        )
        first_scene_plan = harness.store.get_latest_edit_plan(
            scene_project.project_id,
            "alice",
        )
        assert first_scene_plan is not None
        second_scene_plan_result = _plan(
            harness,
            rendering,
            scene_project.project_id,
            scene_instruction,
            "plan-scene-diversity-repeat",
        )
        scene_plan = harness.store.get_latest_edit_plan(
            scene_project.project_id,
            "alice",
        )
        assert scene_plan is not None
        assert scene_plan == first_scene_plan.model_copy(
            update={"revision": scene_plan.revision}
        )
        assert first_scene_plan_result["planRevision"] == 1
        assert second_scene_plan_result["planRevision"] == 2
        assert scene_plan.revision == 2
        assert len(scene_plan.segments) == 9
        scene_source = scene_plan.sources[0]
        assert scene_source.source_asset_id == asset_c.asset_id
        scene_spans = tuple(
            zip(
                (0.0, *scene_source.scene_boundary_seconds),
                (
                    *scene_source.scene_boundary_seconds,
                    scene_source.duration_seconds,
                ),
            )
        )
        expected_shot_starts = {
            start
            for start, end in scene_spans
            if end - start + 1e-6
            >= scene_plan.segments[0].source_out_seconds
            - scene_plan.segments[0].source_in_seconds
        }
        assert len(expected_shot_starts) >= len(scene_plan.segments)
        selected_shot_starts = [
            segment.selected_scene_cut_seconds or 0.0
            for segment in scene_plan.segments
        ]
        assert len(set(selected_shot_starts)) == len(scene_plan.segments)
        assert all(
            segment.selection_method == "distinct_detected_scene_interval"
            for segment in scene_plan.segments
        )
        assert all(
            segment.source_asset_id == asset_c.asset_id
            and any(
                start <= segment.source_in_seconds
                < segment.source_out_seconds <= end
                and (segment.selected_scene_cut_seconds or 0.0) == start
                for start, end in scene_spans
            )
            for segment in scene_plan.segments
        )
        scene_projection = harness.store.get_edit_segments(
            scene_project.project_id,
            "alice",
        )
        assert tuple(
            (
                segment.source_asset_id,
                segment.source_in_seconds,
                segment.source_out_seconds,
                segment.timeline_order,
            )
            for segment in scene_projection
        ) == tuple(
            (
                segment.source_asset_id,
                segment.source_in_seconds,
                segment.source_out_seconds,
                segment.timeline_order,
            )
            for segment in scene_plan.segments
        )
        scene_plan_digest = hashlib.sha256(
            harness.store._canonical(scene_plan).encode("utf-8")
        ).hexdigest()
        (
            scene_render_result,
            scene_output,
            scene_output_metadata,
            scene_quality,
            scene_decode_status,
        ) = _render(
            harness,
            rendering,
            storage,
            scene_project.project_id,
            False,
        )
        assert scene_render_result["planMode"] == "structured"
        assert scene_render_result["planRevision"] == scene_plan.revision
        assert scene_render_result["planSha256"] == scene_plan_digest
        assert scene_render_result["orientation"] == "landscape"
        assert hashlib.sha256(scene_output).hexdigest() == scene_render_result["sha256"]
        assert scene_decode_status == "PASSED"
        assert scene_quality["status"] == "PASSED"
        assert scene_output_metadata["has_video"] is True
        assert scene_output_metadata["has_audio"] is True
        assert scene_output_metadata["video_width"] == 1280
        assert scene_output_metadata["video_height"] == 720
        assert scene_output_metadata["video_fps"] == pytest.approx(30)
        assert scene_output_metadata["duration_seconds"] == pytest.approx(
            30.0,
            abs=0.75,
        )
        assert abs(
            scene_output_metadata["audio_duration_seconds"]
            - scene_output_metadata["video_duration_seconds"]
        ) <= renderer.SYNC_TOLERANCE_SECONDS
        scene_staged = storage.create_clip_staging_path(
            scene_render_result["renderId"]
        )
        try:
            scene_staged.write_bytes(scene_output)
            scene_audio_mapping = _measure_audio_tones(
                renderer,
                scene_staged,
                [
                    {
                        "source": "source-c",
                        "timelineStartSeconds": round(
                            sum(
                                prior.source_out_seconds
                                - prior.source_in_seconds
                                for prior in scene_plan.segments[:index]
                            ),
                            3,
                        ),
                    }
                    for index in range(len(scene_plan.segments))
                ],
            )
        finally:
            storage.delete_clip_staging(scene_staged)

        scene_plan_duration = sum(
            segment.source_out_seconds - segment.source_in_seconds
            for segment in scene_plan.segments
        )
        scene_overlap_count = _substantial_overlap_count(scene_plan.segments)
        assert scene_plan_duration == pytest.approx(30.0)
        assert scene_overlap_count == 0
        assert len(scene_audio_mapping) == len(scene_plan.segments)

        reference_path = (
            Path(__file__).parent
            / "fixtures"
            / "cre_autonomous_editing_baseline_v1.json"
        )
        reference = json.loads(reference_path.read_text(encoding="utf-8"))
        assert reference["schemaVersion"] == "cre-edit-baseline-v1"
        toolchain = {
            "ffmpeg": _run([renderer.ffmpeg, "-version"]).decode().splitlines()[0],
            "ffprobe": _run([renderer.ffprobe, "-version"]).decode().splitlines()[0],
        }
        assert toolchain == reference["toolchain"]
        observed_signature = _baseline_signature(
            _normalized_plan(single_plan, labels),
            baseline_projection,
            revision_projection,
        )
        assert observed_signature == reference["planDecisions"]
        assert {
            "width": output_metadata["video_width"],
            "height": output_metadata["video_height"],
            "fps": output_metadata["video_fps"],
            "video": output_metadata["has_video"],
            "audio": output_metadata["has_audio"],
            "quality": quality["status"],
        } == reference["twoSourceRender"]

        report = {
            "schemaVersion": "cre-edit-baseline-v1",
            "toolchain": toolchain,
            "isolation": {
                "database": str(isolated_root / "content.db"),
                "managedStorage": str(storage._root),
                "productionDatabaseUsed": False,
                "liveServicesUsed": False,
            },
            "inputs": source_metadata,
            "editingDecisions": {
                "singleSource": _normalized_plan(single_plan, labels),
                "twoSourceBaseline": baseline_projection,
                "twoSourceRepeatEquivalent": True,
                "singleSourceInsufficientFootageFallback": {
                    "selectionMethods": [
                        segment.selection_method
                        for segment in persisted_single_plan.segments
                    ],
                    "repeatedIntervalCount": single_repeated_interval_count,
                    "substantiallyOverlappingIntervalCount": single_overlap_count,
                },
                "revisionInstruction": revision_instruction,
                "twoSourceRevision": revision_projection,
                "transitionDecision": {
                    "firstSegment": "start",
                    "subsequentSegments": "hard_cut",
                    "persistedExplicitly": True,
                },
                "audioPolicy": (
                    persisted_baseline_plan.audio_policy
                ),
                "audioTimeline": [
                    {
                        "timelineOrder": segment.timeline_order,
                        "mode": segment.audio.mode,
                        "sourceAssetId": segment.audio.source_asset_id,
                        "sourceInSeconds": segment.audio.source_in_seconds,
                        "sourceOutSeconds": segment.audio.source_out_seconds,
                        "gainDb": segment.audio.gain_db,
                        "timingPolicy": segment.audio.timing_policy,
                    }
                    for segment in persisted_baseline_plan.segments
                ],
                "planProvenance": {
                    "revision": persisted_baseline_plan.revision,
                    "sha256": baseline_plan_digest,
                },
            },
            "renders": {
                "singleSource": {
                    **single_result,
                    "outputSha256": hashlib.sha256(single_output).hexdigest(),
                    "media": single_media,
                    "quality": single_quality,
                    "decodeStatus": single_decode_status,
                },
                "twoSourceBaseline": {
                    **render_result,
                    "outputSha256": hashlib.sha256(rendered_output).hexdigest(),
                    "media": output_metadata,
                    "quality": quality,
                    "decodeStatus": decode_status,
                    "audioToneMapping": audio_mapping,
                    "audioValidation": render_result["audioValidation"],
                    "decodedPeakDbfs": (
                        render_result["audioValidation"]["decodedPeakDbfs"]
                    ),
                },
                "sceneDiverseSelection": {
                    "requestedDurationSeconds": 30.0,
                    "actualPlanDurationSeconds": scene_plan_duration,
                    "actualRenderDurationSeconds": scene_output_metadata[
                        "duration_seconds"
                    ],
                    "distinctShotCoverage": len(set(selected_shot_starts)),
                    "repeatedIntervalCount": sum(
                        segment.selection_method
                        in {
                            "repeated_detected_scene_interval",
                            "repeated_source_interval_fallback",
                        }
                        for segment in scene_plan.segments
                    ),
                    "substantiallyOverlappingIntervalCount": scene_overlap_count,
                    "sceneBoundaryCount": len(
                        scene_source.scene_boundary_seconds
                    ),
                    "sourceAssetId": asset_c.asset_id,
                    "sourceSha256": asset_c.content_sha256,
                    "planRevision": scene_plan.revision,
                    "planSha256": scene_plan_digest,
                    "artifactSha256": hashlib.sha256(scene_output).hexdigest(),
                    "planMode": scene_render_result["planMode"],
                    "orientation": scene_render_result["orientation"],
                    "rendererSettings": scene_render_result["rendererSettings"],
                    "timelineProjectionConsistent": True,
                    "segments": [
                        {
                            "sourceAssetId": segment.source_asset_id,
                            "sourceInSeconds": segment.source_in_seconds,
                            "sourceOutSeconds": segment.source_out_seconds,
                            "timelineOrder": segment.timeline_order,
                            "selectionMethod": segment.selection_method,
                            "selectedSceneOpeningCutSeconds": (
                                segment.selected_scene_cut_seconds
                            ),
                            "detectedSceneInterval": list(
                                next(
                                    span
                                    for span in scene_spans
                                    if (
                                        segment.selected_scene_cut_seconds
                                        or 0.0
                                    )
                                    == span[0]
                                )
                            ),
                        }
                        for segment in scene_plan.segments
                    ],
                    "audioToneMapping": scene_audio_mapping,
                    "media": scene_output_metadata,
                    "quality": scene_quality,
                    "decodeStatus": scene_decode_status,
                },
            },
            "reproducibility": {
                "planEquivalentForSameInputsAndInstruction": True,
                "outputHashRequiredToMatchAcrossRuns": False,
                "comparisonExclusions": [
                    "render IDs",
                    "timestamps",
                    "container/encoder metadata that varies by toolchain",
                    "encoded output SHA-256",
                ],
            },
        }
        assert source_metadata["source-a"]["media"]["video_width"] == 160
        assert source_metadata["source-a"]["media"]["video_height"] == 120
        assert source_metadata["source-a"]["media"]["video_fps"] == pytest.approx(15)
        assert source_metadata["source-a"]["media"]["has_audio"] is True
        assert source_metadata["source-a"]["media"]["audio_sample_rate"] == 16000
        assert source_metadata["source-a"]["media"]["audio_channels"] == 1
        assert source_metadata["source-b"]["media"]["video_width"] == 120
        assert source_metadata["source-b"]["media"]["video_height"] == 160
        assert source_metadata["source-b"]["media"]["video_fps"] == pytest.approx(24)
        assert source_metadata["source-b"]["media"]["has_audio"] is True
        assert source_metadata["source-b"]["media"]["audio_sample_rate"] == 16000
        assert source_metadata["source-b"]["media"]["audio_channels"] == 1
        assert output_metadata["video_width"] == 1280
        assert output_metadata["video_height"] == 720
        assert output_metadata["video_fps"] == pytest.approx(30)
        assert output_metadata["has_video"] is True
        assert output_metadata["has_audio"] is True
        assert output_metadata["duration_seconds"] == pytest.approx(
            baseline_projection["durationSeconds"],
            abs=0.75,
        )
        assert Path(report["isolation"]["database"]).parent == isolated_root
        assert Path(report["isolation"]["managedStorage"]).parent == isolated_root
        record_property("cre_edit_baseline", json.dumps(report, sort_keys=True))
        print(json.dumps(report, sort_keys=True, indent=2))
    finally:
        harness.close()
