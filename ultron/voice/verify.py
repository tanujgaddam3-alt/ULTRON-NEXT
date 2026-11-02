"""Verify generated ULTRON speech against the measured reference profile.

Reports pitch (F0), spectral weight, loudness, clipping and silence for each
sample and compares them to ``config/voice_profile.json`` targets.

Usage: python -m voice.verify
"""

from __future__ import annotations

import glob
import json
import math
import os

import numpy as np
import scipy.ndimage
import scipy.signal
import soundfile as sf

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def measure(path: str) -> dict:
    y, sr = sf.read(path, dtype="float64")
    if y.ndim > 1:
        y = np.mean(y, axis=1)

    # ---- F0 (autocorrelation) ----
    try:
        b, a = scipy.signal.butter(4, [55, 380], btype="bandpass", fs=sr)
        y_bp = scipy.signal.filtfilt(b, a, y)
    except Exception:
        y_bp = y
    frame_len = int(sr * 0.05)
    hop = int(sr * 0.025)
    n = (len(y_bp) - frame_len) // hop
    f0s = []
    for i in range(max(1, n)):
        fr = y_bp[i * hop : i * hop + frame_len]
        if np.sqrt(np.mean(fr**2)) < 0.0025:
            continue
        fr = fr - np.mean(fr)
        nf = 1
        while nf < 2 * frame_len:
            nf *= 2
        acf = np.fft.irfft(np.abs(np.fft.rfft(fr, nf)) ** 2)
        acf = acf / (acf[0] + 1e-9)
        ml = int(sr / 380)
        mx = min(int(sr / 55), len(acf) - 1)
        if mx <= ml:
            continue
        pk = np.max(acf[ml : mx + 1])
        if pk < 0.3:
            continue
        idx = np.argmax(acf[ml : mx + 1]) + ml
        f0 = sr / idx if idx > 0 else 0
        if 55 <= f0 <= 380:
            f0s.append(f0)
    f0s = scipy.ndimage.median_filter(f0s, 5)
    f0 = float(np.median(f0s)) if len(f0s) > 0 else 0.0

    # ---- spectral low band + centroid ----
    fft = np.abs(np.fft.rfft(y))
    fr = np.fft.rfftfreq(len(y), 1 / sr)
    total = np.sum(fft**2) + 1e-9
    low_pct = float(np.sum(fft[fr < 300] ** 2) / total * 100)
    centroid = float(np.sum(fr * fft**2) / total)

    # ---- loudness (speech-active, same gate as the DSP chain) + peak ----
    import sys

    sys.path.insert(0, BASE)
    from voice.dsp import _approximate_lufs

    lufs = float(_approximate_lufs(y, sr, gate_db=-45.0))
    peak = float(np.max(np.abs(y)))
    duration = float(len(y) / sr)

    # silence ratio (below -50 dBFS)
    rms_f = 20 * np.log10(np.abs(y) + 1e-12)
    silence = float(np.mean(rms_f < -50) * 100)

    return {
        "file": os.path.basename(path),
        "duration_s": round(duration, 2),
        "sr": sr,
        "f0_median_hz": round(f0, 1),
        "low_band_pct": round(low_pct, 1),
        "centroid_hz": round(centroid, 1),
        "lufs": round(lufs, 1),
        "peak": round(peak, 3),
        "silence_pct": round(silence, 1),
    }


def main():
    profile = json.load(open(os.path.join(BASE, "config", "voice_profile.json")))
    targets = {
        "f0_range": (profile["pitch"]["f0_p5"], profile["pitch"]["f0_p75"]),
        "low_band_range": (54.0, 74.0),
        "lufs_target": float(
            json.load(open(os.path.join(BASE, "config", "ultron_voice.json")))
            .get("processing", {})
            .get("loudness", {})
            .get("target_lufs", -22.5)
        ),
    }

    samples = sorted(
        glob.glob(os.path.join(BASE, "samples", "*.wav"))
        + glob.glob(os.path.join(BASE, "samples", "single_*.wav"))
    )
    if not samples:
        print("No samples found. Run `python -m voice.generate` first.")
        return

    print(f"{'file':<50} {'dur':>5} {'F0med':>7} {'low%':>6} {'LUFS':>6} {'peak':>6} {'silenc%':>7}  verdict")
    print("-" * 100)
    fails = 0
    for s in samples:
        m = measure(s)
        ok = True
        lo, hi = targets["f0_range"]
        notes = []
        if not lo <= m["f0_median_hz"] <= hi:
            ok = False
            notes.append(f"F0 {m['f0_median_hz']} outside [{lo}-{hi}]")
        if not targets["low_band_range"][0] <= m["low_band_pct"] <= targets["low_band_range"][1]:
            ok = False
            notes.append(f"low-band {m['low_band_pct']}% outside profile")
        if abs(m["lufs"] - targets["lufs_target"]) > 1.5:
            ok = False
            notes.append(f"LUFS {m['lufs']} vs target {targets['lufs_target']}")
        if m["peak"] >= 0.999:
            ok = False
            notes.append("clipped")
        if m["silence_pct"] > 55:
            ok = False
            notes.append("excessive silence")
        verdict = "PASS" if ok else "FAIL: " + ", ".join(notes)
        if not ok:
            fails += 1
        print(
            f"{m['file']:<50} {m['duration_s']:>5} {m['f0_median_hz']:>7} "
            f"{m['low_band_pct']:>6} {m['lufs']:>6} {m['peak']:>6} "
            f"{m['silence_pct']:>7}  {verdict}"
        )
    print("-" * 100)
    print(f"{len(samples) - fails}/{len(samples)} samples within profile targets")
    return fails


if __name__ == "__main__":
    raise SystemExit(main())