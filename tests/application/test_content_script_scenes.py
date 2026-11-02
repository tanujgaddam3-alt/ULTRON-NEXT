import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from ultron.application.content.script_scenes import (
    MAX_SCENE_LIMIT,
    SCENE_PLANNER_VERSION,
    SCENE_SCHEMA_VERSION,
    SceneVisualInstructions,
    deserialize_scene_plan,
    plan_script_scenes,
    render_scene_diagram,
    rendered_scene_composition,
    serialize_scene_plan,
    validate_scene_plan,
    visual_theme,
    visual_treatment,
)
from ultron.application.content.clip_renderer import (
    FFmpegLocalClipRenderer,
    RenderCaption,
)


def test_scene_plan_preserves_exact_script_offsets_and_deterministic_labels():
    script = (
        "First, compare leaves with shorter daylight. "
        "Then chlorophyll breaks down."
    )

    first = plan_script_scenes(script)
    second = plan_script_scenes(script)

    assert first == second
    assert first
    for scene in first:
        assert script[scene.start_offset : scene.end_offset] == scene.excerpt
        assert scene.start_offset < scene.end_offset
        assert scene.labels
        assert all(label in scene.excerpt for label in scene.labels)


def test_scene_plan_splits_at_caption_bounds_without_rewriting_source_text():
    first_sentence = (
        "The first explanation includes several connected ideas about how "
        "chlorophyll affects the visible color of leaves."
    )
    second_sentence = (
        "The second explanation describes how shorter daylight changes the "
        "signals that plants receive."
    )
    script = f"{first_sentence} {second_sentence}"

    scenes = plan_script_scenes(script)

    assert len(scenes) == 2
    assert scenes[0].excerpt == first_sentence
    assert scenes[1].excerpt == second_sentence
    assert script[scenes[0].start_offset : scenes[0].end_offset] == scenes[0].excerpt
    assert script[scenes[1].start_offset : scenes[1].end_offset] == scenes[1].excerpt


def test_scene_plan_rejects_more_scenes_than_the_configured_bound():
    with pytest.raises(ValueError, match="more than 1 scenes"):
        plan_script_scenes(
            "One sentence. Two sentence. Three sentence.",
            max_scenes=1,
            max_characters_per_scene=36,
        )


def test_comparison_diagram_uses_both_source_phrases():
    script = (
        "Leaves with abundant chlorophyll look green, whereas "
        "shorter daylight reveals other pigments."
    )

    scene = plan_script_scenes(script)[0]

    assert scene.visual_type == "COMPARISON"
    assert len(scene.labels) == 2
    assert all(label in script for label in scene.labels)
    assert "Leaves with abundant chlorophyll look green" in scene.labels[0]
    assert "shorter daylight reveals other pigments" in scene.labels[1]


def test_diagram_is_written_as_a_local_png_using_only_scene_labels(tmp_path):
    scene = plan_script_scenes("Water moves through roots.")[0]
    destination = tmp_path / "scene.png"

    render_scene_diagram(scene, destination, scene_count=1)

    assert destination.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_script_caption_preserves_literal_angle_brackets(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    renderer = FFmpegLocalClipRenderer(managed_staging_directory=staging)

    caption_file = renderer._write_captions(
        (RenderCaption(0.0, 1.0, "Use x < y, not x > y.", sanitize_markup=False),),
        0.0,
        1.0,
    )
    assert caption_file is not None
    try:
        assert "Use x < y, not x > y." in caption_file.read_text(encoding="utf-8")
    finally:
        caption_file.unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("script", "role", "composition"),
    [
        ("Water carries minerals through the roots.", "EXPLANATION", "EXPLANATORY_CONCEPT_MAP"),
        ("First water enters roots, then travels upward.", "PROCESS", "PROCESS_DIAGRAM"),
        ("Leaves look green, whereas other pigments appear.", "COMPARISON", "COMPARISON_LAYOUT"),
        ("Because daylight shortens, green pigments fade.", "CAUSE_EFFECT", "CAUSE_EFFECT_DIAGRAM"),
        ("Leaves contain 12% water during summer.", "NUMERICAL_FACT", "NUMERICAL_CALLOUT"),
        ("By 2020, leaf patterns changed across seasons.", "TIMELINE", "TIMELINE_LAYOUT"),
        ("Why do leaves change color? Chlorophyll breaks down.", "QUESTION_ANSWER", "QUESTION_ANSWER_LAYOUT"),
        ("Why do leaves change color?", "EXPLANATION", "EXPLANATORY_CONCEPT_MAP"),
        ("In conclusion, shorter days reveal leaf pigments.", "SUMMARY", "CONCLUSION_LAYOUT"),
    ],
)
def test_scene_planner_classifies_bounded_narrative_roles(script, role, composition):
    scenes = plan_script_scenes(script)

    assert len(scenes) == 1
    assert scenes[0].narrative_role == role
    assert scenes[0].key_message == scenes[0].excerpt
    assert scenes[0].visual_strategy in {
        "COMPARISON_LAYOUT",
        "PROCESS_DIAGRAM",
        "NUMERICAL_CALLOUT",
        "CONCEPT_MAP_DIAGRAM",
    }
    assert scenes[0].renderer_support == "SUPPORTED"
    assert rendered_scene_composition(scenes[0]) == composition
    assert scenes[0].timing is None
    assert scenes[0].narration_ref == f"{scenes[0].scene_id}:narration"
    assert scenes[0].caption_ref == f"{scenes[0].scene_id}:caption"
    assert scenes[0].asset_state == "NOT_GENERATED"
    assert scenes[0].technical_validation == "NOT_RUN"


def test_scene_compositions_are_distinct_and_deterministic(tmp_path):
    scripts = (
        "Leaves look green, whereas other pigments appear.",
        "By 2020, leaf patterns changed across seasons.",
        "In conclusion, shorter days reveal leaf pigments.",
    )
    outputs = []
    for index, script in enumerate(scripts):
        scene = plan_script_scenes(script)[0]
        first = tmp_path / f"scene-{index}-first.png"
        second = tmp_path / f"scene-{index}-second.png"
        render_scene_diagram(scene, first, scene_count=1)
        render_scene_diagram(scene, second, scene_count=1)
        first_bytes = first.read_bytes()
        assert first_bytes == second.read_bytes()
        outputs.append(first_bytes)

    assert len(set(outputs)) == len(outputs)


def test_visual_treatments_follow_source_meaning_and_keep_baseline_scenes_distinct(
    tmp_path,
):
    script = (
        "Synthetic local verification only. "
        "This preview contains no factual claims and is not authorized for publication."
    )
    scenes = plan_script_scenes(script, max_characters_per_scene=96)

    assert [visual_treatment(scene) for scene in scenes] == [
        "LOCAL_TEST_HERO",
        "REVIEW_BOUNDARY",
    ]
    assert all(
        script[scene.start_offset : scene.end_offset] == scene.excerpt
        for scene in scenes
    )
    assert visual_theme("Leaves reveal chlorophyll as daylight gets shorter.") == (
        "BOTANICAL"
    )
    assert visual_theme("Software changes how a computer stores data.") == "DIGITAL"

    images = []
    for scene in scenes:
        destination = tmp_path / f"{scene.scene_id}.png"
        render_scene_diagram(scene, destination, scene_count=len(scenes))
        images.append(destination.read_bytes())
    assert len(set(images)) == len(scenes)


def test_comparison_and_process_labels_fit_without_card_clipping(tmp_path):
    scripts = (
        "Leaves look green, whereas shorter daylight reveals the other pigments hidden beneath.",
        "First, daylight fades; then chlorophyll breaks down and other pigments show.",
    )
    for index, script in enumerate(scripts):
        scene = plan_script_scenes(script)[0]
        destination = tmp_path / f"bounded-scene-{index}.png"

        render_scene_diagram(scene, destination, scene_count=1)

        with Image.open(destination) as image:
            assert image.size == (720, 1280)
            image.verify()


def test_scene_plan_contract_is_deterministic_hashed_and_round_trips():
    script = "  Why do leaves change color? Chlorophyll breaks down.  "
    first = plan_script_scenes(script)
    second = plan_script_scenes(script)
    serialized = serialize_scene_plan(first)

    assert first == second
    assert first[0].scene_id == "scene-01"
    assert first[0].order == 1
    assert first[0].planner_version == SCENE_PLANNER_VERSION
    assert first[0].to_dict()["excerptSha256"] == hashlib.sha256(
        first[0].excerpt.encode("utf-8")
    ).hexdigest()
    assert deserialize_scene_plan(script, serialized) == first
    assert serialize_scene_plan(deserialize_scene_plan(script, serialized)) == serialized
    assert SCENE_SCHEMA_VERSION == 2


def test_scene_plan_preserves_source_offsets_and_allows_only_whitespace_gaps():
    script = "  " + ("water moves through roots " * 5) + "  "
    scenes = plan_script_scenes(
        script,
        max_scenes=12,
        max_characters_per_scene=36,
    )

    validate_scene_plan(
        script,
        scenes,
        max_scenes=12,
        max_characters_per_scene=36,
    )
    assert script[scenes[0].start_offset : scenes[0].end_offset] == scenes[0].excerpt
    assert len({scene.scene_id for scene in scenes}) == len(scenes)
    assert [scene.order for scene in scenes] == list(range(1, len(scenes) + 1))
    assert script[: scenes[0].start_offset].isspace()
    assert all(
        script[left.end_offset : right.start_offset].isspace()
        for left, right in zip(scenes, scenes[1:])
    )
    assert script[scenes[-1].end_offset :].isspace()


def test_scene_plan_rejects_overlapping_offsets_and_tampered_bindings():
    script = "First water enters roots, then travels upward."
    (scene,) = plan_script_scenes(script)
    invalid_span = replace(scene, start_offset=scene.start_offset + 1)
    invalid_hash = scene.to_dict()
    invalid_hash["excerptSha256"] = "0" * 64

    with pytest.raises(ValueError, match="source span"):
        validate_scene_plan(script, (invalid_span,))
    with pytest.raises(ValueError, match="hash"):
        type(scene).from_dict(invalid_hash)


def test_scene_plan_rejects_unexplained_source_gaps_and_wrong_scene_order():
    script = "Water moves through roots. Then minerals travel upward."
    scenes = plan_script_scenes(script, max_characters_per_scene=36)
    assert len(scenes) == 2

    gap_scene = replace(
        scenes[1],
        start_offset=scenes[1].start_offset + 1,
        end_offset=scenes[1].end_offset + 1,
    )
    overlapping_scene = replace(
        scenes[1],
        start_offset=scenes[0].end_offset - 1,
    )
    with pytest.raises(ValueError):
        validate_scene_plan(script, (scenes[0], gap_scene))
    with pytest.raises(ValueError):
        validate_scene_plan(script, (scenes[0], overlapping_scene))
    with pytest.raises(ValueError):
        validate_scene_plan(script, (replace(scenes[0], order=2), scenes[1]))


def test_scene_plan_rejects_invalid_timing_references_and_unsupported_renderer():
    script = "Water moves through roots."
    (scene,) = plan_script_scenes(script)
    invalid_timing = replace(scene, timing={"startSeconds": float("nan")})
    unsupported = replace(
        scene,
        visual_strategy="CINEMATIC_ESTABLISHING_SHOT",
        renderer_support="UNSUPPORTED",
        visual_instructions=SceneVisualInstructions(
            layout="CINEMATIC_ESTABLISHING_SHOT",
            labels=scene.labels,
            relationship="EXTRACTED_SCRIPT_TERMS_ONLY",
            source_text_scope="EXACT_SCENE_EXCERPT",
        ),
    )
    invalid_reference = replace(scene, narration_ref="other-scene:narration")

    for invalid in (invalid_timing, invalid_reference):
        with pytest.raises(ValueError):
            validate_scene_plan(script, (invalid,))
    validate_scene_plan(script, (unsupported,))
    destination = Path("unused.png")
    with pytest.raises(ValueError, match="not supported"):
        render_scene_diagram(
            unsupported,
            destination,
            scene_count=1,
            font_path=Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
        )


def test_scene_plan_deserializer_rejects_malformed_json_and_unknown_version():
    scenes = plan_script_scenes("Water moves through roots.")
    with pytest.raises(ValueError, match="JSON"):
        deserialize_scene_plan("Water moves through roots.", "{")
    unsupported_version = serialize_scene_plan(scenes).replace(
        '"schemaVersion":2',
        '"schemaVersion":99',
    )
    with pytest.raises(ValueError, match="version"):
        deserialize_scene_plan("Water moves through roots.", unsupported_version)


def test_scene_planner_accepts_realistic_multiline_two_thousand_character_script():
    script = """# Section One: Coastal Water Safety
A small community protects its harbor by watching the tide, recording each change, and sharing clear warnings with nearby residents.
Morning surveys show how wind, boats, and seasonal weather can move debris into places where swimmers and children play.

# Section Two: Learning from Observation
Local volunteers keep a simple log of water temperature, visibility, and unusual shoreline activity throughout the year.
These notes help families prepare for dangerous conditions without pretending that every observation provides a complete forecast.
When measurements disagree with familiar experience, trained staff review the evidence before issuing public guidance.

# Section Three: A Practical Response
A useful safety plan assigns responsibilities, stores emergency equipment, and gives everyone a reliable way to request help.
People also practice the route to higher ground so that a sudden warning does not become a confusing search for missing supplies.
The plan remains private to the community, while the information needed for cooperation stays visible and easy to understand.

# Section Four: Responsible Communication
Clear language should distinguish a measured fact from an estimate and preserve the source of every important recommendation.
Editors shorten the message when possible, but they never remove uncertainty that could change a person's decision.
The final reminder is direct: respect local instructions, verify the latest official update, and avoid unsafe water during an active warning."""
    assert 400 < len(script) <= 20_000
    scenes = plan_script_scenes(
        script,
        max_scenes=140,
        max_characters_per_scene=144,
    )
    assert len(scenes) > 4
    assert len(scenes) <= 140
    validate_scene_plan(
        script,
        scenes,
        max_scenes=140,
        max_characters_per_scene=144,
    )


def test_scene_planner_default_uses_full_preview_scene_limit():
    script = (
        "# Planning section\n"
        + "A clear multilingual sentence about coastal safety. 🌊\n" * 30
    )
    scenes = plan_script_scenes(script)
    assert 4 < len(scenes) <= MAX_SCENE_LIMIT
    validate_scene_plan(
        script,
        scenes,
        max_scenes=MAX_SCENE_LIMIT,
        max_characters_per_scene=144,
    )


def test_scene_planner_rejects_unbounded_and_invalid_unicode_inputs():
    with pytest.raises(ValueError, match="input"):
        plan_script_scenes("Water moves through roots.", max_scenes=True)
    with pytest.raises(ValueError, match="input"):
        plan_script_scenes("Water moves through roots.", max_characters_per_scene=True)
    with pytest.raises(ValueError, match="20,?000|scene planning"):
        plan_script_scenes("water " * 7000)
    with pytest.raises(ValueError, match="UTF-8"):
        plan_script_scenes("water \ud800 moves through roots")
