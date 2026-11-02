"""ULTRON voice - sample generation & CLI.

Usage
-----
    python -m voice.generate                          # all samples, all styles
    python -m voice.generate --phrase 1 --style warning

Produces WAV files under ``voice/samples/``.
"""

from __future__ import annotations

import argparse
import os
import time

from .pipeline import VoicePipeline

SAMPLES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples"
)

PHRASES = {
    1: "Systems are operational.",
    2: "I have completed the requested analysis.",
    3: "Your instruction has been received.",
    4: "Warning. Anomalous activity detected.",
    5: "All systems are functioning within acceptable parameters.",
}

STYLE_MAP = {
    1: "normal",
    2: "information",
    3: "confirmation",
    4: "warning",
    5: "normal",
}


def slug(text: str) -> str:
    keep = "".join(ch.lower() for ch in text if ch.isalnum() or ch in " ")
    return "_".join(keep.split())[:40]


def generate_all(engine: str = "xtts") -> list[str]:
    os.environ.setdefault(
        "ULTRON_FFMPEG",
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "third_party",
            "ffmpeg",
            "bin",
            "ffmpeg.exe",
        ),
    )
    os.makedirs(SAMPLES_DIR, exist_ok=True)
    pipeline = VoicePipeline(engine_name=engine)
    pipeline.prepare()
    paths = []
    for num, phrase in PHRASES.items():
        style = STYLE_MAP[num]
        name = f"{num:02d}_{style}_{slug(phrase)}.wav"
        out = os.path.join(SAMPLES_DIR, name)
        t0 = time.perf_counter()
        res = pipeline.speak(phrase, style, output_path=out)
        elapsed = time.perf_counter() - t0
        print(
            f"[sample] #{num} style={style} engine={res.engine} "
            f"clone={res.is_clone} {round(elapsed, 1)}s -> {name}"
        )
        paths.append(out)
    pipeline.close()
    return paths


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="xtts", choices=["xtts", "edge"])
    ap.add_argument("--phrase", type=int, default=None)
    args = ap.parse_args()
    if args.phrase is not None:
        os.environ.setdefault(
            "ULTRON_FFMPEG",
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "third_party",
                "ffmpeg",
                "bin",
                "ffmpeg.exe",
            ),
        )
        os.makedirs(SAMPLES_DIR, exist_ok=True)
        p = VoicePipeline(engine_name=args.engine)
        p.prepare()
        phrase = PHRASES[args.phrase]
        style = STYLE_MAP[args.phrase]
        out = os.path.join(SAMPLES_DIR, f"single_{args.phrase}_{style}.wav")
        res = p.speak(phrase, style, output_path=out)
        print(f"wrote {out} (engine={res.engine}, clone={res.is_clone})")
    else:
        generate_all(engine=args.engine)