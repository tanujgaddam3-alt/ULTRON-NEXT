"""ULTRON voice pipeline QA tests.

Covers the Phase-6 acceptance checks:
  1. an utterance can be generated
  2. output does not clip
  3. loudness lands inside the configured band
  4. sample rate matches the pipeline target
  5. output is not unexpectedly silent
  6. errors are handled gracefully (empty/invalid input, bad style)
  7. longer responses produce longer, non-degenerate audio
  8. repeatability: identical calls produce similar duration/loudness
  9. style preferences are respected (energy -> target loudness shift)
 10. continuous speaking (multiple calls) does not fail

Run with the fast fallback engine by default:

    python -m pytest voice/tests -q

To also exercise the XTTS clone engine (slow; loads ~2 GB model):

    set ULTRON_VOICE_XTTS_TESTS=1
    python -m pytest voice/tests/test_pipeline.py -q -k xtts

Standalone runner (no pytest):

    python voice/tests/test_pipeline.py
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import soundfile as sf  # noqa: E402

from voice import dsp  # noqa: E402
from voice.pipeline import VoicePipeline  # noqa: E402

os.environ.setdefault(
    "ULTRON_FFMPEG",
    os.path.join(BASE, "voice", "third_party", "ffmpeg", "bin", "ffmpeg.exe"),
)

XTTS_ENABLED = os.environ.get("ULTRON_VOICE_XTTS_TESTS") == "1"
ENGINES = ["edge"] + (["xtts"] if XTTS_ENABLED else [])
SHORT = "Systems are operational."
LONG = (
    "All subsystems are nominal. The observation network reports stable "
    "baselines across every monitored domain. No anomalous activity has been "
    "detected within the review window, and all operational parameters remain "
    "within their configured bounds."
)


def _speak(engine, text, style="normal", output_path=None):
    p = VoicePipeline(engine_name=engine)
    t0 = time.perf_counter()
    try:
        p.prepare()
        return p.speak(text, style, output_path), p
    finally:
        p.close()


# --------------------------------------------------------------------------- #
# 1. generation
# --------------------------------------------------------------------------- #
def test_generates_an_utterance_edge():
    r, p = _speak("edge", SHORT)
    assert os.path.isfile(r.path)
    assert os.path.getsize(r.path) > 4000
    assert r.engine == "edge"
    assert r.is_clone is False
    assert r.elapsed_s > 0


def test_engines_meta_matches_expected_labels():
    assert VoicePipeline(engine_name="edge").engine.name == "edge"
    assert VoicePipeline(engine_name="xtts").engine.name == "xtts"


def test_generates_an_utterance_xtts():
    if not XTTS_ENABLED:
        return
    r, p = _speak("xtts", SHORT)
    assert r.engine == "xtts"
    assert r.is_clone is True
    assert os.path.isfile(r.path)


# --------------------------------------------------------------------------- #
# 2 / 3 / 4 / 5. acoustic invariants per engine
# --------------------------------------------------------------------------- #
def _acoustic_invariants(engine):
    r, _ = _speak(engine, SHORT)
    y, sr = sf.read(r.path, dtype="float64")
    assert sr == 24000, f"expected target sample rate 24000, got {sr}"
    peak = float(np.max(np.abs(y)))
    assert peak < 0.999, f"output clips (peak {peak})"
    lufs = dsp._approximate_lufs(y, sr, gate_db=-45.0)
    target = -22.5
    assert abs(lufs - target) <= 1.5, f"loudness {lufs:.1f} outside target band"
    active = np.sqrt(np.mean(y**2))
    assert 0.001 < active < 0.5, f"unexpected silence/level (rms {active})"
    return r, y, sr


def test_acoustic_invariants_edge():
    _acoustic_invariants("edge")


def test_acoustic_invariants_xtts():
    if not XTTS_ENABLED:
        return
    _acoustic_invariants("xtts")


# --------------------------------------------------------------------------- #
# 6. graceful errors
# --------------------------------------------------------------------------- #
def test_rejects_empty_utterance():
    p = VoicePipeline(engine_name="edge")
    for bad in ("", "   ", "###", "[EXEC]"):
        try:
            p.speak(bad, "normal")
            raise AssertionError(f"expected rejection for {bad!r}")
        except ValueError:
            pass
    p.close()


def test_unknown_style_raises():
    p = VoicePipeline(engine_name="edge")
    try:
        p.speak(SHORT, "no-such-style")
        raise AssertionError("expected unknown style rejection")
    except (KeyError, ValueError):
        pass
    finally:
        p.close()


def test_multiple_outputs_from_one_pipeline():
    # Each successful call must write its own file and not poison the engine.
    p = VoicePipeline(engine_name="edge")
    p.prepare()
    outs = []
    try:
        for i in range(3):
            r = p.speak(SHORT, "normal")
            outs.append(r.path)
            assert os.path.isfile(r.path)
    finally:
        p.close()
    assert len(set(outs)) >= 2, "scratch paths should be unique per call"
    y, sr = sf.read(outs[0], dtype="float64")
    assert np.max(np.abs(y)) < 0.999


# --------------------------------------------------------------------------- #
# 7. long responses
# --------------------------------------------------------------------------- #
def test_long_response_is_not_degenerate():
    short_r, _ = _speak("edge", SHORT)
    long_r, _ = _speak("edge", LONG)
    a = sf.info(short_r.path).duration
    b = sf.info(long_r.path).duration
    assert b > a, "long text produced shorter audio than short text"
    y, sr = sf.read(long_r.path, dtype="float64")
    assert np.max(np.abs(y)) < 0.999
    assert np.std(y) > 0.01


# --------------------------------------------------------------------------- #
# 8. repeatability
# --------------------------------------------------------------------------- #
def test_repeatability_edge():
    runs = []
    for _ in range(2):
        r, _ = _speak("edge", SHORT)
        y, sr = sf.read(r.path, dtype="float64")
        runs.append((sf.info(r.path).duration, dsp._approximate_lufs(y, sr)))
    assert abs(runs[0][0] - runs[1][0]) < 0.5, f"durations diverge {runs}"
    assert abs(runs[0][1] - runs[1][1]) < 1.0, f"loudness diverges {runs}"


# --------------------------------------------------------------------------- #
# 9. styles respected
# --------------------------------------------------------------------------- #
def test_style_changes_target_loudness():
    import tempfile

    p = VoicePipeline(engine_name="edge")
    p.prepare()
    try:
        calm_out = os.path.join(tempfile.gettempdir(), "test_calm_normal.wav")
        strong_out = os.path.join(tempfile.gettempdir(), "test_calm_critical.wav")
        calm = p.speak(SHORT, "normal", calm_out)
        strong = p.speak(SHORT, "critical", strong_out)
        a = dsp._approximate_lufs(sf.read(calm.path, dtype="float64")[0], 24000)
        b = dsp._approximate_lufs(sf.read(strong.path, dtype="float64")[0], 24000)
        assert b > a, "critical style should be louder than normal"
    finally:
        p.close()


# --------------------------------------------------------------------------- #
# 10. continuous speaking
# --------------------------------------------------------------------------- #
def test_continuous_speaking_does_not_fail():
    p = VoicePipeline(engine_name="edge")
    p.prepare()
    try:
        for _ in range(4):
            r = p.speak(SHORT, "normal")
            assert os.path.isfile(r.path)
    finally:
        p.close()


# --------------------------------------------------------------------------- #
# standalone runner
# --------------------------------------------------------------------------- #
def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        name = f"{t.__name__} ({ENGINES})"
        try:
            t()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {name}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())