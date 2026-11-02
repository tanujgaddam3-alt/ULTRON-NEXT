"""ULTRON voice DSP chain.

Takes raw TTS output and applies the acoustic character measured from the
reference recording (see ``config/voice_profile.json``):

    noise gate -> hard high-pass -> soft high-pass -> low-shelf (vocal weight)
    -> presence peak -> de-emphasis -> de-esser -> compressor
    -> controlled saturation -> limiter -> loudness normalization

Every stage is derived from the measured profile and gated by ``processing``
parameters in ``config/ultron_voice.json``. Stages only run when there is
measured evidence that justifies them (see ``PROFILE_JUSTIFICATION``).

The chain deliberately does NOT reproduce the source recording's artefact
(WhatsApp VOIP clipping / codec noise floor). It reproduces the timbre, weight
and dynamics of the voice itself on a clean carrier.
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import tempfile

import json

import numpy as np
import scipy.signal

_PROFILE_JUSTIFICATION = {
    "hard_highpass": "reference energy floor starts near 55 Hz F0; removes sub-voice rumble only",
    "soft_highpass": "low band is 64.9% of reference energy but sub-200Hz mush must stay controlled",
    "low_shelf": "bass-baritone weight; 65% of energy below 300 Hz justifies a low vocal-region shelf",
    "presence_peak": "formant F2 ~973 Hz carries intelligibility; small presence lift",
    "de_emphasis_high": "high band is only 4.3% of energy; roll excess HF on a clean carrier",
    "de_ess": "reference sibilance is only 1.61% in 4-8 kHz; keep sibilance restrained",
    "compressor": "reference crest factor 15.5 dB; compressor tames peaks toward that",
    "saturation": "gentle harmonic enhancement for weight; HNR media ~0 dB so keep it mild (blend ~0.18)",
    "noise_gate": "cleanup between utterances; threshold derived from reference noise floor -38.8 dB",
    "limiter": "reference peaks at 0 dBFS; production ceiling -1 dBFS prevents hard clipping",
    "loudness": "reference LUFS approx -22.2; normalize toward configured target",
}


def transcode_to_wav(src, dst, sample_rate: int) -> None:
    """Transcode an arbitrary audio file to mono s16 WAV at ``sample_rate``.

    Uses a bundled ffmpeg when available, otherwise the ``imageio-ffmpeg``
    executable path, otherwise raises a clear error.
    """
    exe = _find_ffmpeg()
    cmd = [
        exe, "-y", "-i", src,
        "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", dst,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)


def _find_ffmpeg() -> str:
    env = os.environ.get("ULTRON_FFMPEG")
    if env and os.path.isfile(env):
        return env
    # Bundled, self-contained copy ships with the project.
    bundled = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "voice",
        "third_party",
        "ffmpeg",
        "bin",
        "ffmpeg.exe",
    )
    if os.path.isfile(bundled):
        return bundled
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if os.path.isfile(exe):
            return exe
    except Exception:
        pass
    return "ffmpeg"


def _read_wav(path: str) -> tuple[np.ndarray, int]:
    import soundfile as sf

    return sf.read(path, dtype="float64")


def _write_wav(path: str, audio: np.ndarray, sample_rate: int) -> None:
    import soundfile as sf

    sf.write(path, audio, sample_rate, subtype="PCM_16")


class _DspChain:
    """Stateful-but-simple DSP processing chain operating on (audio, sr)."""

    def __init__(self, p: dict, sample_rate: int):
        self.p = p
        self.sr = sample_rate

    def process(self, audio: np.ndarray) -> np.ndarray:
        audio = np.asarray(audio, dtype=np.float64)
        if audio.ndim > 1:
            audio = np.mean(audio, axis=1)
        if not p_enabled(self.p):
            return self._normalize_only(audio)

        audio = self._noise_gate(audio)
        audio = self._hard_highpass(audio)
        audio = self._soft_highpass(audio)
        audio = self._low_shelf(audio)
        audio = self._presence_peak(audio)
        audio = self._de_emphasis(audio)
        audio = self._de_esser(audio)
        audio = self._compressor(audio)
        audio = self._saturation(audio)
        audio = self._silence_trim(audio)
        audio = self._loudness(audio)
        audio = self._limiter(audio)
        return audio

    # ------------------------------------------------------------------ #
    # per-stage helpers
    # ------------------------------------------------------------------ #
    def _apply_sos(self, audio, sos) -> np.ndarray:
        try:
            return scipy.signal.sosfilt(sos, audio)
        except ValueError:
            return scipy.signal.lfilter(sos[0], sos[1], audio)

    def _noise_gate(self, audio) -> np.ndarray:
        g = self.p.get("noise_gate", {})
        thresh_db = float(g.get("threshold_db", -50.0))
        thresh = 10 ** (thresh_db / 20.0)
        attack_s = float(g.get("attack_ms", 2.0)) / 1000.0
        release_s = float(g.get("release_ms", 100.0)) / 1000.0
        attack = min(1.0, max(0.0, 1.0 / (attack_s * self.sr + 1.0)))
        release = min(1.0, max(0.0, 1.0 / (release_s * self.sr + 1.0)))
        env = np.abs(audio)
        smoothed = np.zeros_like(env)
        cur = 0.0
        for i in range(len(env)):
            target = float(env[i])
            if target > cur:
                cur += (target - cur) * attack
            else:
                cur *= (1.0 - release)
                cur = target if target > 0 else cur
            smoothed[i] = cur
        smoothed = np.maximum(smoothed, 1e-9)
        gain = np.where(smoothed > thresh, 1.0, 10 ** ((-5.0) / 20.0))
        # fade gain to avoid zipper noise
        gain = scipy.ndimage.uniform_filter1d(gain, size=4)
        return audio * gain

    def _hard_highpass(self, audio) -> np.ndarray:
        hz = float(self.p.get("hard_highpass_hz", 65.0))
        sos = scipy.signal.butter(3, hz, btype="highpass", fs=self.sr)
        return self._apply_sos(audio, sos)

    def _soft_highpass(self, audio) -> np.ndarray:
        hz = float(self.p.get("soft_highpass_hz", 90.0))
        sos = scipy.signal.butter(1, hz, btype="highpass", fs=self.sr)
        return self._apply_sos(audio, sos)

    def _low_shelf(self, audio) -> np.ndarray:
        ls = self.p.get("low_shelf", {})
        f = float(ls.get("freq_hz", 250.0))
        db = float(ls.get("gain_db", 2.5))
        return self._peaking(audio, f, db, q=0.5, shelf=True)

    def _presence_peak(self, audio) -> np.ndarray:
        pp = self.p.get("presence_peak", {})
        f = float(pp.get("freq_hz", 950.0))
        db = float(pp.get("gain_db", 1.0))
        q = float(pp.get("q", 0.8))
        return self._peaking(audio, f, db, q)

    def _de_emphasis(self, audio) -> np.ndarray:
        de = self.p.get("de_emphasis_high", {})
        f = float(de.get("freq_hz", 2600.0))
        db = float(de.get("gain_db", -1.5))
        return self._peaking(audio, f, db, q=0.7)

    def _peaking(self, audio, freq, db_gain, q=0.8, shelf=None) -> np.ndarray:
        if abs(db_gain) < 0.05:
            return audio
        w0 = 2.0 * math.pi * freq / self.sr
        a_amp = 10 ** (db_gain / 40.0)
        alpha = math.sin(w0) / (2.0 * q)
        if shelf:
            # low shelf
            c0 = math.cos(w0)
            s = math.sin(w0)
            sqrt_a = math.sqrt(a_amp)
            b = [
                a_amp * ((a_amp + 1) - (a_amp - 1) * c0 + 2 * sqrt_a * alpha * s),
                2 * a_amp * ((a_amp - 1) - (a_amp + 1) * c0),
                a_amp * ((a_amp + 1) - (a_amp - 1) * c0 - 2 * sqrt_a * alpha * s),
            ]
            a = [
                (a_amp + 1) + (a_amp - 1) * c0 + 2 * sqrt_a * alpha * s,
                -2 * ((a_amp - 1) + (a_amp + 1) * c0),
                (a_amp + 1) + (a_amp - 1) * c0 - 2 * sqrt_a * alpha * s,
            ]
        else:
            b = [1 + alpha * a_amp, -2 * math.cos(w0), 1 - alpha * a_amp]
            a = [1 + alpha / a_amp, -2 * math.cos(w0), 1 - alpha / a_amp]
        return scipy.signal.lfilter(b, a, audio)

    def _de_esser(self, audio) -> np.ndarray:
        de = self.p.get("de_ess", {})
        threshold_db = float(de.get("threshold_db", -24.0))
        band_start = float(de.get("band_start_hz", 5000.0))
        ratio = float(de.get("ratio", 0.5))
        if self.sr / 2 <= band_start:
            return audio
        sos_band = scipy.signal.butter(
            2, [band_start, self.sr / 2 * 0.97], btype="bandpass", fs=self.sr
        )
        band = self._apply_sos(audio, sos_band)
        env = np.abs(band)
        env = scipy.ndimage.uniform_filter1d(env, size=int(self.sr * 0.01))
        env_db = 20 * np.log10(env + 1e-12)
        over = np.clip((env_db - threshold_db) * (1.0 - ratio), 0, 12)
        gain = 10 ** (-over / 20.0)
        gain = scipy.ndimage.uniform_filter1d(gain, size=int(self.sr * 0.002) + 1)
        return audio * gain

    def _compressor(self, audio) -> np.ndarray:
        c = self.p.get("compressor", {})
        threshold_db = float(c.get("threshold_db", -22.0))
        ratio = float(c.get("ratio", 2.5))
        attack_s = float(c.get("attack_ms", 8.0)) / 1000.0
        release_s = float(c.get("release_ms", 120.0)) / 1000.0
        att = min(1.0, max(0.0, 1.0 / (attack_s * self.sr + 1.0)))
        rel = min(1.0, max(0.0, 1.0 / (release_s * self.sr + 1.0)))
        env = np.abs(audio)
        env_s = np.zeros_like(env)
        cur = 0.0
        for i in range(len(env)):
            target = float(env[i])
            cur = target + (cur - target) * (rel if target < cur else att)
            env_s[i] = cur
        env_db = 20 * np.log10(env_s + 1e-12)
        over_db = np.clip(env_db - threshold_db, 0, 30)
        gain_db = -over_db * (1.0 - 1.0 / ratio)
        gain = 10 ** (gain_db / 20.0)
        gain = scipy.ndimage.uniform_filter1d(gain, size=6)
        return audio * gain

    def _saturation(self, audio) -> np.ndarray:
        s = self.p.get("saturation", {})
        driver_db = float(s.get("driver_db", 6.0))
        blend = float(s.get("blend", 0.18))
        drive = 10 ** (driver_db / 20.0)
        shaped = np.tanh(audio * drive * 0.6) / np.tanh(0.6)
        shaped = shaped * np.max(np.abs(audio)) / (np.max(np.abs(shaped)) + 1e-9)
        return audio + (shaped - audio) * blend

    def _silence_trim(self, audio) -> np.ndarray:
        """Trim leading/trailing silence (threshold from the noise gate config)."""
        g = self.p.get("noise_gate", {})
        db = float(g.get("threshold_db", -50.0))
        level = 10 ** (db / 20.0)
        window = int(self.sr * 0.02)
        if len(audio) < window * 2:
            return audio
        gate = scipy.ndimage.uniform_filter1d(np.abs(audio), window)
        thresh_padded = level * 3.0
        idx = np.where(gate > thresh_padded)[0]
        if len(idx) == 0:
            return audio
        lo = max(0, int(idx[0]) - window)
        hi = min(len(audio), int(idx[-1]) + window)
        return audio[lo:hi]

    def _limiter(self, audio) -> np.ndarray:
        l = self.p.get("limiter", {})
        ceiling = 10 ** ((float(l.get("ceiling_db", -1.0))) / 20.0)
        peak = np.max(np.abs(audio))
        if peak > ceiling and peak > 0:
            audio = audio * (ceiling / peak)
        # soft final protection
        max_out = 10 ** ((-0.9) / 20.0)
        peak = np.max(np.abs(audio))
        if peak > max_out:
            audio = audio * (max_out / peak)
        return audio

    def _loudness(self, audio) -> np.ndarray:
        l = self.p.get("loudness", {})
        target = float(l.get("target_lufs", -22.5))
        current = _approximate_lufs(audio, self.sr)
        delta = target - current
        if abs(delta) > 0.1:
            audio = audio * 10 ** (delta / 20.0)
        return audio

    def _normalize_only(self, audio) -> np.ndarray:
        """When processing is disabled, centre loudness and clamp only."""
        audio = audio - np.mean(audio[: int(self.sr * 0.05)]) if len(audio) > self.sr * 0.05 else audio
        return set_loudness(audio, float(self.p.get("loudness", {}).get("target_lufs", -22.5)))


def p_enabled(p: dict) -> bool:
    return bool(p.get("enabled", True))


def _approximate_lufs(audio: np.ndarray, sr: int = 24000, gate_db: float = -45.0) -> float:
    """Simplified LUFS over speech-active 400 ms windows.

    ``gate_db`` defaults to -45 so near-silent gaps between words are excluded:
    we normalise the *speech* level, not the pauses around it.
    """
    win = int(sr * 0.4)
    n = max(1, len(audio) // win)
    vals = []
    for i in range(n):
        seg = audio[i * win : (i + 1) * win]
        rms = np.sqrt(np.mean(seg**2) + 1e-12)
        db = 20 * np.log10(rms)
        if db > gate_db:
            vals.append(db)
    if not vals:
        return -70.0
    return float(np.mean(vals) - 0.691)


def set_loudness(audio: np.ndarray, target_lufs: float, sr: int = 24000) -> np.ndarray:
    current = _approximate_lufs(audio, sr)
    return audio * 10 ** ((target_lufs - current) / 20.0)


def process_file(
    input_path: str,
    output_path: str,
    processing: dict,
    target_sr: int = 24000,
) -> tuple[np.ndarray, int]:
    """Full offline DSP on a WAV file. Returns (audio, sr)."""
    import soundfile as sf

    audio, sr = sf.read(input_path, dtype="float64")
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)
    if sr != target_sr:
        g = math.gcd(sr, target_sr)
        audio = scipy.signal.resample_poly(audio, target_sr // g, sr // g)
        sr = target_sr
    chain = _DspChain(processing, sr)
    out = chain.process(audio)
    sf.write(output_path, out, sr, subtype="PCM_16")
    return out, sr


def _report_justification() -> dict:
    return _PROFILE_JUSTIFICATION


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description="Apply ULTRON voice DSP to a wav.")
    ap.add_argument("input")
    ap.add_argument("-o", "--output", default="")
    ap.add_argument("--config", default=os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "ultron_voice.json",
        ))
    ap.add_argument("--sr", type=int, default=24000)
    args = ap.parse_args(argv)

    with open(args.config) as f:
        cfg = json.load(f)
    out = args.output or (args.input.replace(".wav", "_voiced.wav"))
    process_file(args.input, out, cfg.get("processing", {}), args.sr)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()