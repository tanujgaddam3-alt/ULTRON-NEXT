import numpy as np
import soundfile as sf

from ultron.voice.dialogue_expression import (
    DeliveryProfile,
    classify_segment,
    express_dialogue,
    split_dialogue,
)
from ultron.voice.pipeline import VoicePipeline


def test_profiles_use_meaning_and_not_punctuation_alone():
    assert classify_segment("The systems are online?") is DeliveryProfile.NEUTRAL
    assert (
        classify_segment("The reason is a sensor malfunction.", "Why did this happen?")
        is DeliveryProfile.CURIOUS
    )
    assert (
        classify_segment("I understand. We can work through this.", "I'm worried.")
        is DeliveryProfile.CALM
    )
    assert (
        classify_segment("This is required. You must proceed.")
        is DeliveryProfile.ASSERTIVE
    )
    assert (
        classify_segment("There is an emergency. Act immediately.")
        is DeliveryProfile.INTENSE
    )
    assert (
        classify_segment("I am sorry for your loss.")
        is DeliveryProfile.SOMBER
    )


def test_sentence_split_preserves_source_text():
    text = "Systems are stable. Check the north array!"
    assert split_dialogue(text) == ["Systems are stable.", "Check the north array!"]
    assert " ".join(split_dialogue(text)) == text


def test_contextual_profiles_have_bounded_pauses_and_smooth_controls():
    segments = express_dialogue(
        "The system is stable. An emergency is active. The system is stable.",
        "Please help; there is an emergency.",
    )

    assert [segment.profile for segment in segments] == [
        DeliveryProfile.NEUTRAL,
        DeliveryProfile.INTENSE,
        DeliveryProfile.NEUTRAL,
    ]
    assert all(0.5 <= segment.rate <= 1.5 for segment in segments)
    assert all(0.5 <= segment.energy <= 1.5 for segment in segments)
    assert all(0.0 <= segment.pause_after_s <= 0.3 for segment in segments)
    assert abs(segments[1].rate - segments[0].rate) <= 0.04 + 1e-9
    assert abs(segments[1].energy - segments[0].energy) <= 0.04 + 1e-9
    assert abs(segments[2].rate - segments[1].rate) <= 0.04 + 1e-9
    assert abs(segments[2].energy - segments[1].energy) <= 0.04 + 1e-9


def test_empty_or_ambiguous_dialogue_falls_back_to_no_segments_or_neutral():
    assert express_dialogue("   ") == []
    segments = express_dialogue("The checks are complete.")
    assert len(segments) == 1
    assert segments[0].profile is DeliveryProfile.NEUTRAL


def test_dialogue_pipeline_synthesizes_segments_in_order_with_bounded_pauses(
    tmp_path,
):
    calls = []

    class TestEngine:
        name = "xtts"
        is_clone = True
        ready = True

        def synthesize(self, text, style_params, output_path):
            calls.append((text, style_params.copy()))
            samples = np.sin(np.linspace(0, 20 * np.pi, 2_400, endpoint=False))
            samples = samples.astype(np.float64) * 0.1
            sf.write(output_path, samples, 24_000, subtype="PCM_16")
            return type(
                "EngineResult",
                (),
                {"output_path": output_path, "sample_rate": 24_000},
            )()

    pipeline = object.__new__(VoicePipeline)
    pipeline.config = {
        "sample_rate": 24_000,
        "delivery": {"styles": {"normal": {"rate": 1.0, "energy": 1.0}}},
        "processing": {"enabled": False, "loudness": {"target_lufs": -22.5}},
    }
    pipeline.engine = TestEngine()
    output_path = str(tmp_path / "dialogue.wav")

    result = pipeline.speak_dialogue(
        "The system is stable. Routine checks are complete. An emergency is active.",
        context="Please help; there is an emergency.",
        output_path=output_path,
    )

    output, sample_rate = sf.read(output_path, dtype="float64")
    assert [text for text, _ in calls] == [
        "The system is stable. Routine checks are complete.",
        "An emergency is active.",
    ]
    assert calls[0][1]["rate"] == 1.0
    assert calls[0][1]["energy"] == 1.0
    assert calls[1][1]["rate"] < calls[0][1]["rate"]
    assert calls[1][1]["energy"] > calls[0][1]["energy"]
    assert result.engine == "xtts"
    assert result.is_clone is True
    assert sample_rate == 24_000
    assert 0.35 <= len(output) / sample_rate <= 0.45
    assert np.count_nonzero(output[:2_400]) > 0
    assert np.all(output[2_400:6_720] == 0)
    assert np.count_nonzero(output[6_720:]) > 0
