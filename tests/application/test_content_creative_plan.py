from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from ultron.domain.content import (
    ContentCreativeEditPlan,
    ContentCreativeEditPlanV2,
    ContentEditPlanSegment,
    ContentEditPlanSource,
)


SOURCE_HASH = hashlib.sha256(b"synthetic-source").hexdigest()


def _plan_data() -> dict[str, object]:
    return {
        "schema_version": "cre-creative-plan-v1",
        "project_id": "creative-plan-test",
        "revision": 1,
        "creative_direction": "Use the detected color changes.",
        "sources": [
            {
                "source_asset_id": "source-a",
                "content_sha256": SOURCE_HASH,
                "duration_seconds": 8.0,
                "scene_boundary_seconds": [2.0, 4.0, 6.0],
            }
        ],
        "segments": [
            {
                "source_asset_id": "source-a",
                "source_in_seconds": 1.0,
                "source_out_seconds": 3.0,
                "timeline_order": 1,
                "selection_method": "nearest_detected_scene_cut",
                "selected_scene_cut_seconds": 2.0,
                "transition_before": "start",
            },
            {
                "source_asset_id": "source-a",
                "source_in_seconds": 5.0,
                "source_out_seconds": 7.0,
                "timeline_order": 2,
                "selection_method": "evenly_spaced_sample",
                "selected_scene_cut_seconds": None,
                "transition_before": "hard_cut",
            },
        ],
        "audio_policy": "preserve_segment_source_audio",
        "output_constraints": {
            "frame_rate_fps": 30,
            "max_width": 1280,
            "max_height": 1280,
            "max_duration_seconds": 60.0,
            "audio_sample_rate_hz": 48000,
        },
    }


def test_creative_plan_validates_and_round_trips_deterministically():
    plan = ContentCreativeEditPlan.model_validate(_plan_data())
    serialized = plan.model_dump_json()
    reloaded = ContentCreativeEditPlan.model_validate_json(serialized)
    canonical = json.dumps(
        plan.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    reloaded_canonical = json.dumps(
        reloaded.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    assert reloaded == plan
    assert reloaded_canonical == canonical
    assert hashlib.sha256(reloaded_canonical.encode()).hexdigest() == hashlib.sha256(
        canonical.encode()
    ).hexdigest()
    assert hashlib.sha256(canonical.encode()).hexdigest() == (
        "c5dfc6343899cc787c88ad7f830a309addb0b5fb95811da3b8c37ec7c822701d"
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), "cre-creative-plan-v2"),
        (("audio_policy",), "mute_source_audio"),
        (("segments", 0, "transition_before"), "crossfade"),
        (("segments", 0, "selection_method"), "semantic_highlight"),
        (("unknown_field",), True),
        (("segments", 1, "timeline_order"), 3),
        (("segments", 0, "source_asset_id"), "unregistered-source"),
        (("segments", 0, "source_out_seconds"), 9.0),
        (("segments", 0, "source_in_seconds"), float("nan")),
        (("segments", 0, "selected_scene_cut_seconds"), 4.0),
        (("output_constraints", "frame_rate_fps"), 60),
    ],
)
def test_creative_plan_rejects_unsupported_or_inconsistent_values(path, value):
    data = _plan_data()
    cursor = data
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    with pytest.raises(ValidationError):
        ContentCreativeEditPlan.model_validate(data)


def test_plan_source_rejects_unordered_or_out_of_range_scene_boundaries():
    with pytest.raises(ValidationError):
        ContentEditPlanSource(
            source_asset_id="source-a",
            content_sha256=SOURCE_HASH,
            duration_seconds=8,
            scene_boundary_seconds=(4, 2),
        )
    with pytest.raises(ValidationError):
        ContentEditPlanSource(
            source_asset_id="source-a",
            content_sha256=SOURCE_HASH,
            duration_seconds=8,
            scene_boundary_seconds=(8,),
        )


def _v2_plan_data() -> dict[str, object]:
    data = _plan_data()
    data["schema_version"] = "cre-creative-plan-v2"
    data.pop("audio_policy")
    data["audio_policy"] = "explicit_segment_audio"
    data["peak_protection_policy"] = "sample_peak_limit_minus_1_5_dbfs"
    for segment in data["segments"]:
        segment["audio"] = {
            "mode": "source_audio",
            "source_asset_id": "source-a",
            "source_in_seconds": segment["source_in_seconds"],
            "source_out_seconds": segment["source_out_seconds"],
            "gain_db": 0.0,
            "timing_policy": "trim_pad_to_video_segment",
        }
    return data


def test_v2_audio_plan_validates_and_round_trips_with_stable_digest():
    plan = ContentCreativeEditPlanV2.model_validate(_v2_plan_data())
    canonical = json.dumps(
        plan.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    reloaded = ContentCreativeEditPlanV2.model_validate_json(
        plan.model_dump_json()
    )
    reloaded_canonical = json.dumps(
        reloaded.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    assert reloaded == plan
    assert reloaded_canonical == canonical
    assert hashlib.sha256(reloaded_canonical.encode()).digest() == hashlib.sha256(
        canonical.encode()
    ).digest()
    assert plan.schema_version == "cre-creative-plan-v2"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("gain_db", float("nan")),
        ("gain_db", float("inf")),
        ("gain_db", -24.01),
        ("gain_db", 6.01),
        ("source_asset_id", "unregistered-source"),
        ("source_out_seconds", 3.1),
    ],
)
def test_v2_audio_plan_rejects_invalid_gain_source_and_timing(field, value):
    data = _v2_plan_data()
    data["segments"][0]["audio"][field] = value
    with pytest.raises(ValidationError):
        ContentCreativeEditPlanV2.model_validate(data)


def test_v2_intentional_silence_requires_null_source_bounds_and_zero_gain():
    data = _v2_plan_data()
    data["segments"][0]["audio"] = {
        "mode": "silence",
        "source_asset_id": None,
        "source_in_seconds": None,
        "source_out_seconds": None,
        "gain_db": 0.0,
        "timing_policy": "trim_pad_to_video_segment",
    }
    plan = ContentCreativeEditPlanV2.model_validate(data)
    assert plan.segments[0].audio.mode == "silence"

    data["segments"][0]["audio"]["gain_db"] = 1.0
    with pytest.raises(ValidationError):
        ContentCreativeEditPlanV2.model_validate(data)


def test_plan_segment_rejects_scene_cut_without_observed_timestamp():
    with pytest.raises(ValidationError):
        ContentEditPlanSegment(
            source_asset_id="source-a",
            source_in_seconds=1,
            source_out_seconds=3,
            timeline_order=1,
            selection_method="nearest_detected_scene_cut",
            transition_before="start",
        )


def _scene_diversity_plan(
    methods: tuple[str, ...],
) -> ContentCreativeEditPlan:
    scene_starts = (0.0, 2.0, 4.0, 6.0, 0.0)
    segments = []
    for order, (method, scene_start) in enumerate(
        zip(methods, scene_starts),
        start=1,
    ):
        segments.append(
            {
                "source_asset_id": "source-a",
                "source_in_seconds": scene_start,
                "source_out_seconds": scene_start + 1.5,
                "timeline_order": order,
                "selection_method": method,
                "selected_scene_cut_seconds": (
                    scene_start if scene_start > 0 else None
                ),
                "transition_before": "start" if order == 1 else "hard_cut",
            }
        )
    data = _plan_data()
    data["segments"] = segments
    data["sources"][0]["scene_boundary_seconds"] = [2.0, 4.0, 6.0]
    return ContentCreativeEditPlan.model_validate(data)


def test_scene_diversity_plan_records_distinct_scenes_and_explicit_repetition():
    plan = _scene_diversity_plan(
        (
            "distinct_detected_scene_interval",
            "distinct_detected_scene_interval",
            "distinct_detected_scene_interval",
            "distinct_detected_scene_interval",
            "repeated_detected_scene_interval",
        )
    )

    encoded = plan.model_dump_json()
    reloaded = ContentCreativeEditPlan.model_validate_json(encoded)
    assert reloaded == plan
    assert reloaded.segments[-1].selection_method == (
        "repeated_detected_scene_interval"
    )
    assert set(reloaded.model_dump(mode="json")) == set(_plan_data())
    assert set(reloaded.model_dump(mode="json")["segments"][0]) == set(
        _plan_data()["segments"][0]
    )


def test_scene_diversity_plan_rejects_repetition_before_distinct_candidates():
    with pytest.raises(ValidationError, match="all usable distinct"):
        _scene_diversity_plan(
            (
                "distinct_detected_scene_interval",
                "repeated_detected_scene_interval",
                "distinct_detected_scene_interval",
                "distinct_detected_scene_interval",
                "distinct_detected_scene_interval",
            )
        )


def test_scene_diversity_plan_rejects_unmarked_or_false_fallback():
    data = _plan_data()
    data["sources"][0]["scene_boundary_seconds"] = [2.0, 4.0, 6.0]
    data["segments"][0].update(
        {
            "source_in_seconds": 0.0,
            "source_out_seconds": 1.5,
            "selection_method": "insufficient_scene_duration_fallback",
            "selected_scene_cut_seconds": None,
        }
    )
    with pytest.raises(ValidationError, match="usable detected scene interval"):
        ContentCreativeEditPlan.model_validate(data)
