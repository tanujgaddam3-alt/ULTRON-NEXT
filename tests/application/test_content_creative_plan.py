from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from ultron.domain.content import (
    ContentCreativeEditPlan,
    ContentCreativeEditPlanV2,
    ContentCreativeEditPlanV3,
    ContentEditPlanGraphic,
    ContentEditPlanSegment,
    ContentEditPlanSource,
    ContentEditPlanTransition,
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
        "602e5ef664be9cb5c508139fccf76015e6125039e08dc6c1c49a00330930746c"
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


def _graphic_directive(**overrides):
    values = {
        "graphic_type": "educational_overlay",
        "segment_order": 2,
        "start_seconds": 5.2,
        "end_seconds": 5.8,
        "x": 0.08,
        "y": 0.1,
        "width": 0.84,
        "height": 0.16,
        "visual_hierarchy": "primary",
        "typography_profile": "educational_storytelling",
        "entry_animation": "slide_up",
        "exit_animation": "fade",
        "entry_duration_seconds": 0.15,
        "exit_duration_seconds": 0.15,
        "easing": "ease_out",
        "font_size": 32,
        "content_text": "A supported phrase",
        "evidence_transcript_id": "transcript-1",
        "evidence_id": "transcript-segment-2",
        "evidence_asset_id": "source-a",
        "evidence_text": "A supported phrase from the transcript.",
        "editorial_rationale": "Emphasis segment with exact transcript evidence.",
    }
    values.update(overrides)
    return ContentEditPlanGraphic(**values)


def test_v3_graphics_directive_validates_persists_and_reloads():
    data = _v2_plan_data()
    data["schema_version"] = "cre-creative-plan-v3"
    data["segments"][1]["graphics"] = [_graphic_directive().model_dump(mode="json")]

    plan = ContentCreativeEditPlanV3.model_validate(data)
    reloaded = ContentCreativeEditPlanV3.model_validate_json(plan.model_dump_json())

    assert reloaded == plan
    assert reloaded.segments[1].graphics[0].content_text == "A supported phrase"
    assert reloaded.segments[1].graphics[0].evidence_id == "transcript-segment-2"


@pytest.mark.parametrize(
    "overrides",
    [
        {"x": 0.8, "width": 0.2},
        {"y": 0.7, "height": 0.1},
        {"start_seconds": 5.8, "end_seconds": 5.2},
        {"content_text": "Unsupported extra claim"},
        {"content_text": "x" * 33, "evidence_text": "x" * 33},
        {"entry_animation": "arbitrary_filter"},
        {"graphic_type": "invented_box"},
    ],
)
def test_graphic_directive_rejects_unsafe_geometry_timing_or_unproven_text(overrides):
    with pytest.raises(ValidationError):
        _graphic_directive(**overrides)


def test_graphic_timing_and_source_association_are_bound_to_plan_segment():
    data = _v2_plan_data()
    data["schema_version"] = "cre-creative-plan-v3"
    graphic = _graphic_directive().model_dump(mode="json")
    graphic["end_seconds"] = 7.1
    data["segments"][1]["graphics"] = [graphic]
    with pytest.raises(ValidationError):
        ContentCreativeEditPlanV3.model_validate(data)

    data["segments"][1]["graphics"][0]["end_seconds"] = 5.8
    data["segments"][1]["graphics"][0]["evidence_asset_id"] = "another-source"
    with pytest.raises(ValidationError):
        ContentCreativeEditPlanV3.model_validate(data)


def test_graphics_cannot_overlap_within_a_source_segment():
    data = _v2_plan_data()
    data["schema_version"] = "cre-creative-plan-v3"
    data["segments"][1]["graphics"] = [
        _graphic_directive().model_dump(mode="json"),
        _graphic_directive(
            start_seconds=5.7,
            end_seconds=6.3,
        ).model_dump(mode="json"),
    ]
    with pytest.raises(ValidationError):
        ContentCreativeEditPlanV3.model_validate(data)


def test_v2_plan_rejects_v3_only_graphic_directives():
    data = _v2_plan_data()
    data["segments"][1]["graphics"] = [_graphic_directive().model_dump(mode="json")]
    with pytest.raises(ValidationError):
        ContentCreativeEditPlanV2.model_validate(data)


@pytest.mark.parametrize(
    ("kind", "duration", "direction"),
    [
        ("hard_cut", 0.0, "none"),
        ("cross_dissolve", 0.25, "none"),
        ("fade_through_black", 0.3, "none"),
        ("fade_through_white", 0.3, "none"),
        ("directional_wipe", 0.2, "left"),
        ("zoom", 0.2, "none"),
    ],
)
def test_transition_directive_validates_supported_families(kind, duration, direction):
    directive = ContentEditPlanTransition(
        kind=kind,
        duration_seconds=duration,
        direction=direction,
        from_timeline_order=1,
        to_timeline_order=2,
        decision_reason="Selected at a verified scene boundary.",
        evidence=(
            ("observed_scene_boundary", "editing_style")
            if kind != "hard_cut"
            else ("observed_scene_boundary",)
        ),
    )

    assert directive.kind == kind
    assert directive.duration_seconds == duration
    assert directive.easing == "linear"


@pytest.mark.parametrize(
    "values",
    [
        {"kind": "hard_cut", "duration_seconds": 0.1},
        {"kind": "cross_dissolve", "duration_seconds": 0.0},
        {"kind": "directional_wipe", "duration_seconds": 0.2},
        {"kind": "cross_dissolve", "duration_seconds": 0.2, "direction": "left"},
        {"kind": "zoom", "duration_seconds": 0.2, "easing": "bezier"},
        {
            "kind": "cross_dissolve",
            "duration_seconds": 0.2,
            "from_timeline_order": 1,
            "to_timeline_order": 3,
        },
        {"kind": "motion_matched", "duration_seconds": 0.2},
    ],
)
def test_transition_directive_rejects_invalid_configuration(values):
    with pytest.raises(ValidationError):
        ContentEditPlanTransition.model_validate(values)


def test_v2_transition_directive_round_trips_with_boundary_association():
    data = _v2_plan_data()
    data["sources"][0]["scene_boundary_seconds"] = [2.0, 4.0, 6.0]
    second = data["segments"][1]
    second.update(
        {
            "source_in_seconds": 4.0,
            "source_out_seconds": 6.0,
            "selection_method": "distinct_detected_scene_interval",
            "selected_scene_cut_seconds": 4.0,
            "transition_before": "cross_dissolve",
            "transition_duration_seconds": 0.25,
            "transition": {
                "kind": "cross_dissolve",
                "duration_seconds": 0.25,
                "direction": "none",
                "from_timeline_order": 1,
                "to_timeline_order": 2,
                "decision_reason": "Style-selected at a detected scene boundary.",
                "evidence": ["observed_scene_boundary", "editing_style"],
            },
        }
    )
    second["audio"]["source_in_seconds"] = 4.0
    second["audio"]["source_out_seconds"] = 6.0

    plan = ContentCreativeEditPlanV2.model_validate(data)
    reloaded = ContentCreativeEditPlanV2.model_validate_json(
        plan.model_dump_json()
    )

    assert reloaded == plan
    assert reloaded.segments[1].transition is not None
    assert reloaded.segments[1].transition.to_timeline_order == 2
    assert reloaded.segments[1].transition.kind == "cross_dissolve"


def test_v2_plan_rejects_transition_larger_than_adjacent_segment_window():
    data = _v2_plan_data()
    second = data["segments"][1]
    second.update(
        {
            "transition_before": "cross_dissolve",
            "transition_duration_seconds": 1.1,
            "transition": {
                "kind": "cross_dissolve",
                "duration_seconds": 1.1,
                "direction": "none",
                "from_timeline_order": 1,
                "to_timeline_order": 2,
                "decision_reason": "Style-selected at a detected scene boundary.",
                "evidence": ["observed_scene_boundary", "editing_style"],
            },
        }
    )
    with pytest.raises(ValidationError, match="half of an adjacent segment"):
        ContentCreativeEditPlanV2.model_validate(data)


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


def test_continuous_bed_plan_requires_one_contiguous_bounded_audio_source():
    data = _v2_plan_data()
    data["audio_policy"] = "continuous_bed"
    for segment, (bed_start, bed_end) in zip(
        data["segments"],
        ((0.0, 2.0), (2.0, 4.0)),
    ):
        segment["audio"].update(
            {
                "bed_source_asset_id": "source-a",
                "bed_source_in_seconds": bed_start,
                "bed_source_out_seconds": bed_end,
            }
        )

    plan = ContentCreativeEditPlanV2.model_validate(data)
    assert plan.audio_policy == "continuous_bed"

    data["segments"][1]["audio"]["bed_source_in_seconds"] = 1.0
    data["segments"][1]["audio"]["bed_source_out_seconds"] = 3.0
    with pytest.raises(ValidationError, match="contiguous"):
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
    assert set(reloaded.model_dump(mode="json")["segments"][0]) == (
        set(_plan_data()["segments"][0]) | {"motion", "transition"}
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
