from __future__ import annotations

import json
import math
import os
import re
import selectors
import shutil
import subprocess
import struct
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont

from ultron.domain.content import (
    ContentCreativeEditPlan,
    ContentEditPlanAudio,
    ContentCreativeEditPlanRecord,
    ContentCreativeEditPlanV2,
)

from .limits import MAX_SOURCE_BYTES


class ClipRenderTimeout(TimeoutError):
    """The renderer did not finish within its bounded execution window."""


@dataclass(frozen=True)
class MediaInfo:
    duration_seconds: float
    has_video: bool
    has_audio: bool
    video_width: int | None = None
    video_height: int | None = None
    video_fps: float | None = None
    audio_sample_rate: int | None = None
    audio_channels: int | None = None
    audio_duration_seconds: float | None = None
    video_duration_seconds: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    audio_peak_dbfs: float | None = None


@dataclass(frozen=True, slots=True)
class RenderCaption:
    start_seconds: float
    end_seconds: float
    text: str
    sanitize_markup: bool = True


@dataclass(frozen=True, slots=True)
class SourceEditSegment:
    source_in_seconds: float
    source_out_seconds: float
    source_asset_id: str | None = None


class FFmpegLocalClipRenderer:
    """Bounded local FFmpeg renderer for the registered content store.

    V2 audio uses a -1.5 dBFS sample-peak limiter and allows at most 0.5 dB
    of decoded AAC overshoot during validation.
    """

    SCENE_TRANSITION_SECONDS = 0.25
    SCENE_MOTION = "restrained_alternating_zoom"
    SCENE_TRANSITION = "crossfade"
    MAX_SOURCE_DURATION_SECONDS = 300.0
    MAX_CLIP_DURATION_SECONDS = 60.0
    MAX_VIDEO_WIDTH = 1920
    MAX_VIDEO_HEIGHT = 1920
    MAX_VIDEO_FPS = 60.0
    MAX_RUNTIME_SECONDS = 60.0
    MAX_SOURCE_BYTES = MAX_SOURCE_BYTES
    MAX_EXTRACTED_AUDIO_BYTES = 16 * 1024 * 1024
    MAX_PROBE_OUTPUT_BYTES = 64 * 1024
    MAX_SCENE_ANALYSIS_OUTPUT_BYTES = 1024 * 1024
    MAX_PCM_VALIDATION_BYTES = 16 * 1024 * 1024
    V2_AUDIO_PEAK_TARGET_DBFS = -1.5
    V2_AUDIO_PEAK_VALIDATION_TOLERANCE_DB = 0.5
    SCENE_CHANGE_THRESHOLD = 0.30
    DURATION_TOLERANCE_SECONDS = 0.75
    SYNC_TOLERANCE_SECONDS = 0.25
    MAX_CAPTION_LINE_CHARACTERS = 36
    MAX_CAPTION_LINES = 4
    MAX_CAPTION_SEGMENT_CHARACTERS = (
        MAX_CAPTION_LINE_CHARACTERS * MAX_CAPTION_LINES
    )
    CAPTION_FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    CAPTION_FONT_SIZE = 36
    CAPTION_SAFE_LEFT = 64
    CAPTION_SAFE_RIGHT = 64
    CAPTION_SAFE_BOTTOM = 220
    CAPTION_SAFE_MAX_WIDTH = 720 - CAPTION_SAFE_LEFT - CAPTION_SAFE_RIGHT

    def __init__(
        self,
        *,
        managed_staging_directory: Path,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
    ) -> None:
        if (
            not isinstance(managed_staging_directory, Path)
            or managed_staging_directory.is_symlink()
            or not managed_staging_directory.is_dir()
        ):
            raise ValueError("A real managed clip staging directory is required.")
        self.managed_staging_directory = managed_staging_directory.resolve(strict=True)
        self.ffmpeg = self._resolve_executable(ffmpeg)
        self.ffprobe = self._resolve_executable(ffprobe)
        self.last_quality_revision_count = 0

    def _validate_managed_path(self, media_path: Path, *, kind: str) -> None:
        patterns = {
            "source": r"\.clip-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}-source-[A-Za-z0-9_-]+\.media",
            "output": r"\.clip-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}-output-[A-Za-z0-9_-]+\.mp4",
            "script_audio": r"\.clip-script-[A-Fa-f0-9-]{36}-audio-[A-Fa-f0-9]{32}\.wav",
            "script_scene": r"\.clip-script-[A-Fa-f0-9-]{36}-scene-[0-9]{2}-[A-Fa-f0-9]{32}\.png",
        }
        if (
            not isinstance(media_path, Path)
            or media_path.is_symlink()
            or media_path.parent.resolve(strict=True) != self.managed_staging_directory
            or not re.fullmatch(patterns[kind], media_path.name)
            or not media_path.is_file()
        ):
            raise ValueError("Media paths must be files created in managed clip staging.")

    @staticmethod
    def _run_bounded_stdout(
        command: list[str],
        *,
        timeout: float,
        max_output_bytes: int,
    ) -> tuple[int, bytes]:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError as exc:
            raise RuntimeError("The local media probe could not be started.") from exc
        if process.stdout is None:
            process.kill()
            process.wait()
            raise RuntimeError("The local media probe output pipe was unavailable.")

        output = bytearray()
        deadline = time.monotonic() + timeout
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    process.wait()
                    raise ClipRenderTimeout("Media probing exceeded its time limit.")
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(
                        key.fd,
                        min(8192, max_output_bytes + 1 - len(output)),
                    )
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output.extend(chunk)
                    if len(output) > max_output_bytes:
                        process.kill()
                        process.wait()
                        raise ValueError("Media probe metadata exceeds its output limit.")
            remaining = max(0.0, deadline - time.monotonic())
            try:
                return_code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                raise ClipRenderTimeout("Media probing exceeded its time limit.") from exc
            return return_code, bytes(output)
        finally:
            selector.close()
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()

    @staticmethod
    def _resolve_executable(executable: str) -> str:
        resolved = shutil.which(executable)
        if resolved is None:
            raise RuntimeError(f"Required local media executable is unavailable: {executable}")
        path = Path(resolved).resolve(strict=True)
        if not path.is_file() or not os.access(path, os.X_OK):
            raise RuntimeError(f"Required local media executable is not runnable: {executable}")
        return str(path)

    @staticmethod
    def _positive_number(value: Any, label: str) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Media {label} is missing or invalid.") from exc
        if not math.isfinite(result) or result <= 0:
            raise ValueError(f"Media {label} is missing or invalid.")
        return result

    @staticmethod
    def _frame_rate(value: Any) -> float | None:
        if not isinstance(value, str) or "/" not in value:
            return None
        numerator, denominator = value.split("/", 1)
        try:
            rate = float(numerator) / float(denominator)
        except (ValueError, ZeroDivisionError):
            return None
        return rate if math.isfinite(rate) and rate > 0 else None

    def _probe(self, media_path: Path, *, kind: str) -> MediaInfo:
        self._validate_managed_path(media_path, kind=kind)
        command = [
            self.ffprobe,
            "-v",
            "error",
            "-max_alloc",
            "33554432",
            "-probesize",
            "1048576",
            "-analyzeduration",
            "5000000",
            "-show_entries",
            (
                "format=duration:"
                "stream=codec_type,codec_name,width,height,avg_frame_rate,sample_rate,"
                "channels,duration"
            ),
            "-of",
            "json",
            str(media_path),
        ]
        try:
            return_code, stdout = self._run_bounded_stdout(
                command,
                timeout=10,
                max_output_bytes=self.MAX_PROBE_OUTPUT_BYTES,
            )
        except ClipRenderTimeout:
            raise
        except ValueError:
            raise
        if return_code != 0:
            raise ValueError("Media probe could not inspect the registered source.")
        try:
            info = json.loads(stdout)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Media probe returned malformed metadata.") from exc
        if not isinstance(info, dict) or not isinstance(info.get("streams"), list):
            raise ValueError("Media probe returned incomplete stream metadata.")
        format_info = info.get("format")
        if not isinstance(format_info, dict):
            raise ValueError("Media probe returned no container metadata.")
        duration = self._positive_number(format_info.get("duration"), "duration")
        if duration > self.MAX_SOURCE_DURATION_SECONDS:
            raise ValueError("Source media exceeds the configured duration limit.")

        streams = info["streams"]
        video = [stream for stream in streams if stream.get("codec_type") == "video"]
        audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
        if len(video) > 1 or len(audio) > 1 or not video and not audio:
            raise ValueError("Only one video and one audio stream are supported.")
        video_width = video_height = None
        video_fps = None
        video_duration = None
        video_codec = None
        if video:
            video_width = int(video[0].get("width", 0))
            video_height = int(video[0].get("height", 0))
            video_codec = video[0].get("codec_name")
            video_fps = self._frame_rate(video[0].get("avg_frame_rate"))
            max_video_width = (
                self.MAX_VIDEO_WIDTH if kind == "source" else 1280
            )
            max_video_height = (
                self.MAX_VIDEO_HEIGHT if kind == "source" else 1280
            )
            if (
                video_width <= 0
                or video_height <= 0
                or video_width > max_video_width
                or video_height > max_video_height
                or video_fps is None
                or video_fps > self.MAX_VIDEO_FPS
            ):
                raise ValueError("Video stream exceeds supported dimensions or frame rate.")
            value = video[0].get("duration")
            video_duration = (
                self._positive_number(value, "video duration") if value else None
            )
        audio_rate = audio_channels = None
        audio_duration = None
        audio_codec = None
        if audio:
            audio_rate = int(audio[0].get("sample_rate", 0))
            audio_channels = int(audio[0].get("channels", 0))
            audio_codec = audio[0].get("codec_name")
            if audio_rate <= 0 or audio_rate > 48_000 or audio_channels not in {1, 2}:
                raise ValueError("Audio stream exceeds supported sample rate or channels.")
            value = audio[0].get("duration")
            audio_duration = (
                self._positive_number(value, "audio duration") if value else None
            )
        return MediaInfo(
            duration_seconds=duration,
            has_video=bool(video),
            has_audio=bool(audio),
            video_width=video_width,
            video_height=video_height,
            video_fps=video_fps,
            audio_sample_rate=audio_rate,
            audio_channels=audio_channels,
            audio_duration_seconds=audio_duration,
            video_duration_seconds=video_duration,
            video_codec=video_codec,
            audio_codec=audio_codec,
        )

    def inspect(self, media_path: Path) -> MediaInfo:
        self._validate_managed_path(media_path, kind="source")
        if (
            not 0 < media_path.stat().st_size <= self.MAX_SOURCE_BYTES
        ):
            raise ValueError(
                f"Source media must be non-empty and at most "
                f"{self.MAX_SOURCE_BYTES // (1024 * 1024)} MiB."
            )
        return self._probe(media_path, kind="source")

    def inspect_source_video(self, media_path: Path) -> MediaInfo:
        info = self.inspect(media_path)
        if not info.has_video or not info.has_audio:
            raise ValueError("Source footage must contain both video and audio streams.")
        return info

    def inspect_video_source(self, media_path: Path) -> MediaInfo:
        """Inspect registered video while allowing an absent audio stream."""
        info = self.inspect(media_path)
        if not info.has_video:
            raise ValueError("CRE source footage must contain a video stream.")
        return info

    def extract_pcm_audio(self, media_path: Path) -> bytes:
        info = self.inspect_source_video(media_path)
        if info.duration_seconds > self.MAX_SOURCE_DURATION_SECONDS:
            raise ValueError("Source footage exceeds the configured duration limit.")
        command = [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-xerror",
            "-threads",
            "2",
            "-max_alloc",
            "33554432",
            "-probesize",
            "1048576",
            "-analyzeduration",
            "5000000",
            "-i",
            str(media_path),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            "pipe:1",
        ]
        try:
            return_code, audio = self._run_bounded_stdout(
                command,
                timeout=self.MAX_RUNTIME_SECONDS,
                max_output_bytes=self.MAX_EXTRACTED_AUDIO_BYTES,
            )
        except ClipRenderTimeout:
            raise
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError("Audio extraction from the registered source failed.") from exc
        if return_code != 0:
            raise RuntimeError("FFmpeg failed to extract source audio.")
        if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise ValueError("FFmpeg returned an invalid PCM WAV container.")
        finalized = bytearray(audio)
        finalized[4:8] = (len(finalized) - 8).to_bytes(4, "little")
        offset = 12
        data_chunk_found = False
        while offset + 8 <= len(finalized):
            chunk_name = finalized[offset : offset + 4]
            chunk_size = int.from_bytes(finalized[offset + 4 : offset + 8], "little")
            data_start = offset + 8
            if chunk_name == b"data":
                actual_data_size = len(finalized) - data_start
                if actual_data_size > 0xFFFFFFFF:
                    raise ValueError("Extracted PCM WAV exceeds the RIFF size limit.")
                finalized[offset + 4 : offset + 8] = actual_data_size.to_bytes(4, "little")
                data_chunk_found = True
                break
            if chunk_size == 0xFFFFFFFF or data_start + chunk_size > len(finalized):
                raise ValueError("FFmpeg returned malformed WAV chunk metadata.")
            offset = data_start + chunk_size + (chunk_size & 1)
        if not data_chunk_found:
            raise ValueError("FFmpeg returned PCM WAV without an audio data chunk.")
        return bytes(finalized)

    def detect_scene_changes(self, media_path: Path) -> tuple[float, ...]:
        """Return FFmpeg-detected visual cut timestamps; this is not semantic analysis."""
        info = self.inspect_video_source(media_path)
        if info.duration_seconds > self.MAX_SOURCE_DURATION_SECONDS:
            raise ValueError("Source footage exceeds the configured duration limit.")
        command = [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-threads",
            "2",
            "-filter_threads",
            "2",
            "-max_alloc",
            "33554432",
            "-probesize",
            "1048576",
            "-analyzeduration",
            "5000000",
            "-i",
            str(media_path),
            "-map",
            "0:v:0",
            "-vf",
            (
                f"select='gt(scene,{self.SCENE_CHANGE_THRESHOLD:.2f})',"
                "metadata=print:file=-"
            ),
            "-an",
            "-sn",
            "-dn",
            "-f",
            "null",
            "-",
        ]
        try:
            return_code, output = self._run_bounded_stdout(
                command,
                timeout=self.MAX_RUNTIME_SECONDS,
                max_output_bytes=self.MAX_SCENE_ANALYSIS_OUTPUT_BYTES,
            )
        except ClipRenderTimeout:
            raise
        except Exception as exc:
            raise RuntimeError("Local FFmpeg scene analysis failed.") from exc
        if return_code != 0:
            raise RuntimeError("FFmpeg failed to analyze source scene changes.")
        timestamps = (
            float(value)
            for value in re.findall(
                rb"\bpts_time:([+-]?(?:\d+(?:\.\d*)?|\.\d+))",
                output,
            )
        )
        boundaries = tuple(
            value
            for value in timestamps
            if math.isfinite(value)
            and 0.05 < value < info.duration_seconds - 0.05
        )
        if (
            len(boundaries) > 18_000
            or any(left >= right for left, right in zip(boundaries, boundaries[1:]))
        ):
            raise ValueError("FFmpeg returned invalid scene-change timestamps.")
        return boundaries

    def render(
        self,
        source_path: Path,
        *,
        start_seconds: float,
        end_seconds: float,
        staged_output: Path,
        vertical: bool = False,
        captions: tuple[RenderCaption, ...] = (),
    ) -> tuple[MediaInfo, MediaInfo]:
        self.last_quality_revision_count = 0
        if (
            not math.isfinite(start_seconds)
            or not math.isfinite(end_seconds)
            or start_seconds < 0
            or end_seconds <= start_seconds
        ):
            raise ValueError("Requested clip interval must be finite and positive.")
        requested_duration = end_seconds - start_seconds
        if requested_duration > self.MAX_CLIP_DURATION_SECONDS:
            raise ValueError("Requested clip exceeds the configured duration limit.")

        source_info = self.inspect(source_path)
        if end_seconds > source_info.duration_seconds:
            raise ValueError("Requested clip interval exceeds source media duration.")
        self._validate_managed_path(staged_output, kind="output")
        if vertical and not captions:
            raise ValueError("Vertical short-form rendering requires transcript captions.")
        caption_file = self._write_captions(captions, start_seconds, end_seconds)

        command = [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-xerror",
            "-y",
            "-threads",
            "2",
            "-max_alloc",
            "67108864",
            "-probesize",
            "1048576",
            "-analyzeduration",
            "5000000",
            "-filter_threads",
            "2",
        ]
        if vertical and not source_info.has_video:
            command.extend(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=0x14212e:s=720x1280:r=30:d={requested_duration:.6f}",
                    "-ss",
                    f"{start_seconds:.6f}",
                    "-i",
                    str(source_path),
                ]
            )
            video_input = 0
            audio_input = 1
        elif vertical:
            command.extend(["-ss", f"{start_seconds:.6f}", "-i", str(source_path)])
            video_input = 0
            audio_input = 0
        else:
            command.extend(["-i", str(source_path)])
            video_input = 0
            audio_input = 0
        if not vertical:
            command.extend(["-ss", f"{start_seconds:.6f}"])
        command.extend(
            [
                "-t",
                f"{requested_duration:.6f}",
                "-map",
                (
                    f"{video_input}:v:0?"
                    if not source_info.has_video and not vertical
                    else f"{video_input}:v:0"
                ),
                "-map",
                f"{audio_input}:a:0?"
                if source_info.has_audio
                else f"{video_input}:a:0?",
                "-sn",
                "-dn",
            ]
        )
        if source_info.has_video or vertical:
            video_filter = (
                "scale=720:1280:force_original_aspect_ratio=increase:"
                "force_divisible_by=2,"
                "crop=720:1280:(iw-ow)/2:(ih-oh)/2"
                if vertical
                else "scale=1280:720:force_original_aspect_ratio=decrease:"
                "force_divisible_by=2"
            )
            if vertical and not source_info.has_video:
                duration_expression = f"{requested_duration:.6f}"
                video_filter += (
                    ",drawbox=x=0:y=0:w=720:h=12:color=0x43d6c5:t=fill"
                    ",drawbox=x=48:y=224:w=624:h=680:color=0x1d2d40:t=fill"
                    ",drawbox=x=48:y=224:w=10:h=680:color=0x43d6c5:t=fill"
                    ",drawbox=x=64:y=1110:w=592:h=6:color=0x34485d:t=fill"
                    f",drawbox=x=64:y=1110:w='592*t/{duration_expression}':"
                    "h=6:color=0x43d6c5:t=fill"
                )
                if caption_file is not None:
                    subtitle_path = self._escape_filter_path(str(caption_file))
                    video_filter += f",ass=filename='{subtitle_path}'"
            command.extend(
                [
                    "-vf",
                    video_filter,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-crf",
                    "28",
                    "-pix_fmt",
                    "yuv420p",
                    "-fps_mode",
                    "cfr",
                    "-r",
                    "30",
                ]
            )
        if source_info.has_audio:
            command.extend(
                [
                    "-af",
                    "loudnorm=I=-16:TP=-1.5:LRA=11",
                    "-ar",
                    "48000",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "96k",
                ]
            )
        command.extend(
            [
                "-avoid_negative_ts",
                "make_zero",
                "-movflags",
                "+faststart",
                "-fs",
                str(16 * 1024 * 1024),
                "-f",
                "mp4",
                str(staged_output),
            ]
        )
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=self.MAX_RUNTIME_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClipRenderTimeout("Clip rendering exceeded its time limit.") from exc
        except OSError as exc:
            raise RuntimeError("The local media renderer could not be started.") from exc
        finally:
            if caption_file is not None:
                caption_file.unlink(missing_ok=True)
        if result.returncode != 0:
            raise RuntimeError("FFmpeg failed to render the requested clip.")

        size = staged_output.stat().st_size
        if size <= 0 or size > 16 * 1024 * 1024:
            raise ValueError("Rendered clip is empty or exceeds the output size limit.")
        output_info = self._probe(staged_output, kind="output")
        self._validate_output(
            source_info,
            output_info,
            requested_duration=requested_duration,
            require_video=vertical,
            vertical=vertical,
        )
        if source_info.has_audio:
            source_audio_peak = self._audio_peak(
                source_path,
                start_seconds=start_seconds,
                duration_seconds=requested_duration,
            )
            output_audio_peak = self._audio_peak(staged_output)
            if source_audio_peak > 0 and output_audio_peak == 0:
                raise ValueError(
                    "Rendered audio is silent although the selected source audio is not."
                )
        self._decode(staged_output)
        quality = self.inspect_render_quality(staged_output)
        if quality["status"] == "DEFECTS_DETECTED":
            self._correct_timeline_defects(
                staged_output,
                quality,
                source_info=source_info,
            )
            revised = self.inspect_render_quality(staged_output)
            if revised["status"] != "PASSED":
                raise ValueError("Rendered clip remains defective after one bounded revision.")
            self.last_quality_revision_count = 1
            output_info = self._probe(staged_output, kind="output")
            self._validate_output(
                source_info,
                output_info,
                requested_duration=output_info.duration_seconds,
                require_video=vertical,
                vertical=vertical,
            )
        return source_info, output_info

    def render_multi_source_timeline(
        self,
        source_paths: dict[str, Path],
        *,
        segments: tuple[SourceEditSegment, ...],
        staged_output: Path,
        vertical: bool = False,
        audio_decisions: tuple[ContentEditPlanAudio, ...] | None = None,
    ) -> tuple[dict[str, MediaInfo], MediaInfo]:
        """Render ordered segments from multiple owner-registered source assets."""
        self.last_quality_revision_count = 0
        if not isinstance(source_paths, dict) or not source_paths:
            raise ValueError("Multi-source rendering requires registered source paths.")
        if (
            not isinstance(segments, tuple)
            or not 2 <= len(segments) <= 100
            or any(type(segment) is not SourceEditSegment for segment in segments)
        ):
            raise ValueError("A multi-source edit timeline must contain 2 to 100 segments.")
        if audio_decisions is not None and (
            not isinstance(audio_decisions, tuple)
            or len(audio_decisions) != len(segments)
            or any(type(decision) is not ContentEditPlanAudio for decision in audio_decisions)
        ):
            raise ValueError("Explicit audio decisions must match the video timeline.")
        source_infos: dict[str, MediaInfo] = {}
        for asset_id, path in source_paths.items():
            if not isinstance(asset_id, str) or not asset_id.strip():
                raise ValueError("Every multi-source segment must identify its source asset.")
            source_infos[asset_id] = self.inspect(path)

        total_duration = 0.0
        filters: list[str] = []
        video_labels: list[str] = []
        audio_labels: list[str] = []
        input_index_by_asset: dict[str, int] = {}
        command_inputs: list[str] = []
        for input_index, (asset_id, path) in enumerate(source_paths.items(), start=0):
            input_index_by_asset[asset_id] = input_index
            command_inputs.extend(["-i", str(path)])

        has_selected_segment_audio = any(
            source_infos[segment.source_asset_id].has_audio
            for segment in segments
            if segment.source_asset_id in source_infos
        )
        for index, segment in enumerate(segments):
            asset_id = segment.source_asset_id
            if asset_id is None or asset_id not in source_infos:
                raise ValueError("A segment source asset is missing or not in the project.")
            source_info = source_infos[asset_id]
            if not source_info.has_video:
                raise ValueError("Every video timeline source must contain video.")
            duration = segment.source_out_seconds - segment.source_in_seconds
            if (
                not math.isfinite(segment.source_in_seconds)
                or not math.isfinite(segment.source_out_seconds)
                or segment.source_in_seconds < 0
                or segment.source_out_seconds <= segment.source_in_seconds
                or segment.source_out_seconds > source_info.duration_seconds
            ):
                raise ValueError("Multi-source timeline contains an invalid source interval.")
            total_duration += duration
            if total_duration > self.MAX_CLIP_DURATION_SECONDS:
                raise ValueError("Multi-source edit timeline exceeds the configured duration limit.")
            input_index = input_index_by_asset[asset_id]
            video_label = f"v{index}"
            filters.append(
                f"[{input_index}:v:0]trim=start={segment.source_in_seconds:.6f}:end={segment.source_out_seconds:.6f},"
                f"setpts=PTS-STARTPTS,fps=30,scale=1280:720:force_original_aspect_ratio=decrease,"
                f"pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1[{video_label}]"
            )
            video_labels.append(f"[{video_label}]")
            if audio_decisions is not None:
                decision = audio_decisions[index]
                if decision.mode == "silence":
                    audio_label = f"a{index}"
                    filters.append(
                        "anullsrc=channel_layout=stereo:sample_rate=48000,"
                        f"atrim=duration={duration:.6f},"
                        f"asetpts=PTS-STARTPTS[{audio_label}]"
                    )
                    audio_labels.append(f"[{audio_label}]")
                else:
                    audio_asset_id = decision.source_asset_id
                    audio_source_info = source_infos.get(audio_asset_id or "")
                    if (
                        audio_asset_id not in input_index_by_asset
                        or audio_source_info is None
                        or not audio_source_info.has_audio
                        or decision.source_in_seconds is None
                        or decision.source_out_seconds is None
                        or decision.source_out_seconds
                        > audio_source_info.duration_seconds
                        or abs(
                            decision.source_out_seconds
                            - decision.source_in_seconds
                            - duration
                        )
                        > 1e-6
                    ):
                        raise ValueError(
                            "Explicit audio source or timing does not match registered media."
                        )
                    audio_input_index = input_index_by_asset[audio_asset_id]
                    audio_label = f"a{index}"
                    filters.append(
                        f"[{audio_input_index}:a:0]atrim="
                        f"start={decision.source_in_seconds:.6f}:"
                        f"end={decision.source_out_seconds:.6f},"
                        "asetpts=PTS-STARTPTS,aresample=48000,"
                        "aformat=sample_fmts=fltp:channel_layouts=stereo,"
                        f"volume={decision.gain_db:.6f}dB,"
                        f"apad=whole_dur={duration:.6f},"
                        f"atrim=duration={duration:.6f}[{audio_label}]"
                    )
                    audio_labels.append(f"[{audio_label}]")
            elif source_info.has_audio:
                audio_label = f"a{index}"
                filters.append(
                    f"[{input_index}:a:0]atrim=start={segment.source_in_seconds:.6f}:end={segment.source_out_seconds:.6f},"
                    f"asetpts=PTS-STARTPTS,aresample=48000,apad=whole_dur={duration:.6f},atrim=duration={duration:.6f}[{audio_label}]"
                )
                audio_labels.append(f"[{audio_label}]")
            elif has_selected_segment_audio:
                audio_label = f"a{index}"
                filters.append(
                    f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                    f"atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[{audio_label}]"
                )
                audio_labels.append(f"[{audio_label}]")

        if audio_decisions is not None:
            interleaved_concat_inputs = "".join(
                f"[v{index}][a{index}]" for index in range(len(segments))
            )
            limiter_peak = 10 ** (self.V2_AUDIO_PEAK_TARGET_DBFS / 20)
            filters.append(
                interleaved_concat_inputs
                + f"concat=n={len(segments)}:v=1:a=1[vout][aout];"
                f"[aout]alimiter=limit={limiter_peak:.6f}:attack=5:release=50:"
                "level=false[aoutlimited]"
            )
        elif has_selected_segment_audio and len(audio_labels) == len(segments):
            interleaved_concat_inputs = "".join(
                f"[v{index}][a{index}]" for index in range(len(segments))
            )
            filters.append(
                interleaved_concat_inputs + f"concat=n={len(segments)}:v=1:a=1[vout][aout];"
                "[aout]loudnorm=I=-16:TP=-1.5:LRA=11[aoutnorm]"
            )
        else:
            filters.append("".join(video_labels) + f"concat=n={len(segments)}:v=1:a=0[vout]")

        if not vertical:
            filters.append("[vout]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2[vfinal]")
        else:
            filters.append("[vout]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280[vfinal]")
        self._validate_managed_path(staged_output, kind="output")
        command = [
            self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-y",
            "-threads", "2", "-filter_threads", "2", "-max_alloc", "67108864",
            *command_inputs, "-filter_complex", ";".join(filters), "-map", "[vfinal]",
        ]
        if audio_decisions is not None:
            command.extend([
                "-map", "[aoutlimited]", "-ar", "48000", "-ac", "2",
                "-c:a", "aac", "-b:a", "96k",
            ])
        elif has_selected_segment_audio and len(audio_labels) == len(segments):
            command.extend(["-map", "[aoutnorm]", "-ar", "48000", "-c:a", "aac", "-b:a", "96k"])
        command.extend([
            "-sn", "-dn", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-pix_fmt", "yuv420p", "-fps_mode", "cfr", "-r", "30",
            "-avoid_negative_ts", "make_zero", "-movflags", "+faststart",
            "-fs", str(16 * 1024 * 1024), "-f", "mp4", str(staged_output),
        ])
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, check=False, timeout=self.MAX_RUNTIME_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise ClipRenderTimeout("Multi-source timeline rendering exceeded its time limit.") from exc
        except OSError as exc:
            raise RuntimeError("The local multi-source renderer could not be started.") from exc
        if result.returncode != 0:
            raise RuntimeError("FFmpeg failed to render the multi-source timeline.")
        self._decode(staged_output)
        output_info = self._probe(staged_output, kind="output")
        self._validate_output(
            source_infos[next(iter(source_infos))],
            output_info,
            requested_duration=total_duration,
            require_video=True,
            vertical=vertical,
            expected_audio_present=True if audio_decisions is not None else None,
        )
        if audio_decisions is not None:
            peak_dbfs = self._measure_decoded_audio_peak_dbfs(staged_output)
            maximum_decoded_peak = (
                self.V2_AUDIO_PEAK_TARGET_DBFS
                + self.V2_AUDIO_PEAK_VALIDATION_TOLERANCE_DB
            )
            if peak_dbfs is not None and peak_dbfs > maximum_decoded_peak:
                raise ValueError(
                    "Rendered audio exceeds the -1.5 dBFS peak target tolerance."
                )
            output_info = replace(output_info, audio_peak_dbfs=peak_dbfs)
        quality = self.inspect_render_quality(staged_output)
        if quality["status"] != "PASSED":
            raise ValueError("Rendered multi-source clip has unresolved technical defects.")
        return source_infos, output_info

    def render_creative_edit_plan(
        self,
        source_paths: dict[str, Path],
        *,
        plan: ContentCreativeEditPlanRecord,
        staged_output: Path,
        vertical: bool = False,
    ) -> tuple[dict[str, MediaInfo], MediaInfo]:
        """Validate and dispatch supported structured plan versions."""
        if not isinstance(plan, (ContentCreativeEditPlan, ContentCreativeEditPlanV2)):
            raise TypeError("A validated structured CRE plan is required.")
        constraints = plan.output_constraints
        common_unsupported = (
            constraints.frame_rate_fps != 30
            or constraints.max_width != 1280
            or constraints.max_height != 1280
            or constraints.audio_sample_rate_hz != 48_000
            or constraints.max_duration_seconds > self.MAX_CLIP_DURATION_SECONDS
            or len(plan.segments) < 2
            or any(
                segment.transition_before != (
                    "start" if index == 0 else "hard_cut"
                )
                for index, segment in enumerate(plan.segments)
            )
        )
        if common_unsupported:
            raise ValueError("Creative plan requests unsupported renderer behavior.")
        if isinstance(plan, ContentCreativeEditPlan):
            if plan.audio_policy != "preserve_segment_source_audio":
                raise ValueError(
                    "Creative plan requests unsupported renderer behavior (audio)."
                )
            audio_decisions = None
        else:
            if (
                plan.audio_policy != "explicit_segment_audio"
                or plan.peak_protection_policy
                != "sample_peak_limit_minus_1_5_dbfs"
            ):
                raise ValueError(
                    "Creative plan requests unsupported renderer behavior (audio)."
                )
            audio_decisions = tuple(segment.audio for segment in plan.segments)
        segments = tuple(
            SourceEditSegment(
                source_in_seconds=segment.source_in_seconds,
                source_out_seconds=segment.source_out_seconds,
                source_asset_id=segment.source_asset_id,
            )
            for segment in plan.segments
        )
        return self.render_multi_source_timeline(
            source_paths,
            segments=segments,
            staged_output=staged_output,
            vertical=vertical,
            audio_decisions=audio_decisions,
        )

    def render_edit_timeline(
        self,
        source_path: Path,
        *,
        segments: tuple[SourceEditSegment, ...],
        staged_output: Path,
        vertical: bool = False,
        captions: tuple[RenderCaption, ...] = (),
    ) -> tuple[MediaInfo, MediaInfo]:
        self.last_quality_revision_count = 0
        if (
            not isinstance(segments, tuple)
            or not 2 <= len(segments) <= 100
            or any(type(segment) is not SourceEditSegment for segment in segments)
        ):
            raise ValueError("An edit timeline must contain 2 to 100 source segments.")
        source_info = self.inspect(source_path)
        previous_out = -1.0
        total_duration = 0.0
        for segment in segments:
            if (
                not math.isfinite(segment.source_in_seconds)
                or not math.isfinite(segment.source_out_seconds)
                or segment.source_in_seconds < 0
                or segment.source_out_seconds <= segment.source_in_seconds
                or segment.source_in_seconds < previous_out
                or segment.source_out_seconds > source_info.duration_seconds
            ):
                raise ValueError("Edit timeline contains invalid or unordered source intervals.")
            previous_out = segment.source_out_seconds
            total_duration += segment.source_out_seconds - segment.source_in_seconds
        if total_duration > self.MAX_CLIP_DURATION_SECONDS:
            raise ValueError("Edit timeline exceeds the configured clip duration limit.")
        if not source_info.has_video:
            raise ValueError("A multi-segment source edit requires source video.")
        self._validate_managed_path(staged_output, kind="output")

        timeline_captions: list[RenderCaption] = []
        output_offset = 0.0
        for segment in segments:
            for caption in captions:
                start = max(caption.start_seconds, segment.source_in_seconds)
                end = min(caption.end_seconds, segment.source_out_seconds)
                if end > start:
                    timeline_captions.append(
                        RenderCaption(
                            start_seconds=output_offset + start - segment.source_in_seconds,
                            end_seconds=output_offset + end - segment.source_in_seconds,
                            text=caption.text,
                            sanitize_markup=caption.sanitize_markup,
                        )
                    )
            output_offset += segment.source_out_seconds - segment.source_in_seconds
        translated_captions = tuple(timeline_captions)
        caption_file = (
            self._write_captions(translated_captions, 0.0, total_duration)
            if vertical
            else None
        )

        filters: list[str] = []
        video_labels: list[str] = []
        audio_labels: list[str] = []
        for index, segment in enumerate(segments):
            source_in = f"{segment.source_in_seconds:.6f}"
            source_out = f"{segment.source_out_seconds:.6f}"
            video_label = f"v{index}"
            filters.append(
                f"[0:v:0]trim=start={source_in}:end={source_out},"
                f"setpts=PTS-STARTPTS,fps=30[{video_label}]"
            )
            video_labels.append(f"[{video_label}]")
            if source_info.has_audio:
                audio_label = f"a{index}"
                filters.append(
                    f"[0:a:0]atrim=start={source_in}:end={source_out},"
                    f"asetpts=PTS-STARTPTS,aresample=48000[{audio_label}]"
                )
                audio_labels.append(f"[{audio_label}]")
        if source_info.has_audio:
            filters.append(
                "".join(
                    label
                    for pair in zip(video_labels, audio_labels)
                    for label in pair
                )
                + (
                    f"concat=n={len(segments)}:v=1:a=1[vout][acat];"
                    "[acat]loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
                )
            )
        else:
            filters.append(
                "".join(video_labels)
                + f"concat=n={len(segments)}:v=1:a=0[vout]"
            )

        video_filter = (
            "scale=720:1280:force_original_aspect_ratio=increase:"
            "force_divisible_by=2,crop=720:1280:(iw-ow)/2:(ih-oh)/2"
            if vertical
            else "scale=1280:720:force_original_aspect_ratio=decrease:"
            "force_divisible_by=2"
        )
        if caption_file is not None:
            video_filter += (
                f",ass=filename='{self._escape_filter_path(str(caption_file))}'"
            )
        filters.append(f"[vout]{video_filter}[vfinal]")

        command = [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-xerror",
            "-y",
            "-threads",
            "2",
            "-filter_threads",
            "2",
            "-max_alloc",
            "67108864",
            "-probesize",
            "1048576",
            "-analyzeduration",
            "5000000",
            "-i",
            str(source_path),
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vfinal]",
        ]
        if source_info.has_audio:
            command.extend(
                [
                    "-map",
                    "[aout]",
                    "-ar",
                    "48000",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "96k",
                ]
            )
        command.extend(
            [
                "-sn",
                "-dn",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "28",
                "-pix_fmt",
                "yuv420p",
                "-fps_mode",
                "cfr",
                "-r",
                "30",
                "-avoid_negative_ts",
                "make_zero",
                "-movflags",
                "+faststart",
                "-fs",
                str(16 * 1024 * 1024),
                "-f",
                "mp4",
                str(staged_output),
            ]
        )
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=self.MAX_RUNTIME_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClipRenderTimeout("Timeline rendering exceeded its time limit.") from exc
        except OSError as exc:
            raise RuntimeError("The local timeline renderer could not be started.") from exc
        finally:
            if caption_file is not None:
                caption_file.unlink(missing_ok=True)
        if result.returncode != 0:
            raise RuntimeError("FFmpeg failed to render the edit timeline.")

        size = staged_output.stat().st_size
        if size <= 0 or size > 16 * 1024 * 1024:
            raise ValueError("Rendered timeline is empty or exceeds the output size limit.")
        output_info = self._probe(staged_output, kind="output")
        self._validate_output(
            source_info,
            output_info,
            requested_duration=total_duration,
            require_video=True,
            vertical=vertical,
        )
        if source_info.has_audio:
            source_audio_peak = self._audio_peak(
                source_path,
                start_seconds=segments[0].source_in_seconds,
                duration_seconds=(
                    segments[-1].source_out_seconds
                    - segments[0].source_in_seconds
                ),
            )
            output_audio_peak = self._audio_peak(staged_output)
            if source_audio_peak > 0 and output_audio_peak == 0:
                raise ValueError(
                    "Rendered timeline audio is silent although source audio is not."
                )
        self._decode(staged_output)
        quality = self.inspect_render_quality(staged_output)
        if quality["status"] == "DEFECTS_DETECTED":
            self._correct_timeline_defects(
                staged_output,
                quality,
                source_info=source_info,
            )
            revised = self.inspect_render_quality(staged_output)
            if revised["status"] != "PASSED":
                raise ValueError("Rendered timeline remains defective after one bounded revision.")
            self.last_quality_revision_count = 1
            output_info = self._probe(staged_output, kind="output")
            self._validate_output(
                source_info,
                output_info,
                requested_duration=output_info.duration_seconds,
                require_video=True,
                vertical=vertical,
            )
        return source_info, output_info

    def _correct_timeline_defects(
        self,
        media_path: Path,
        quality: dict[str, object],
        *,
        source_info: MediaInfo,
    ) -> None:
        """Remove one bounded set of detected black/frozen intervals, then revalidate."""
        raw_ranges = quality["blackRanges"] + quality["frozenRanges"]  # type: ignore[operator]
        if not raw_ranges:
            raise ValueError("A deterministic defect revision requires timestamped ranges.")
        duration = self._probe(media_path, kind="output").duration_seconds
        ranges: list[tuple[float, float]] = []
        for raw in raw_ranges:
            if not isinstance(raw, list) or len(raw) != 2:
                continue
            start, end = float(raw[0]), float(raw[1])
            if math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration:
                ranges.append((start, end))
        if not ranges:
            raise ValueError("Quality inspection produced no valid defect ranges to revise.")
        ranges.sort()
        merged: list[tuple[float, float]] = []
        for start, end in ranges:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        kept: list[tuple[float, float]] = []
        cursor = 0.0
        for start, end in merged:
            if start > cursor:
                kept.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < duration:
            kept.append((cursor, duration))
        if not kept or sum(end - start for start, end in kept) <= 0.1:
            raise ValueError("Defect correction would remove the complete rendered artifact.")

        backup = media_path.with_name(
            media_path.name.replace("-output-", "-source-").replace(".mp4", ".media")
        )
        os.replace(media_path, backup)
        filters: list[str] = []
        video_labels: list[str] = []
        audio_labels: list[str] = []
        for index, (start, end) in enumerate(kept):
            video_label = f"rv{index}"
            filters.append(
                f"[0:v:0]trim=start={start:.6f}:end={end:.6f},"
                f"setpts=PTS-STARTPTS[{video_label}]"
            )
            video_labels.append(f"[{video_label}]")
            if source_info.has_audio:
                audio_label = f"ra{index}"
                filters.append(
                    f"[0:a:0]atrim=start={start:.6f}:end={end:.6f},"
                    f"asetpts=PTS-STARTPTS,aresample=48000[{audio_label}]"
                )
                audio_labels.append(f"[{audio_label}]")
        if source_info.has_audio:
            filters.append(
                "".join(label for pair in zip(video_labels, audio_labels) for label in pair)
                + f"concat=n={len(kept)}:v=1:a=1[rvout][raout];"
                "[raout]loudnorm=I=-16:TP=-1.5:LRA=11[rafinal]"
            )
        else:
            filters.append(
                "".join(video_labels) + f"concat=n={len(kept)}:v=1:a=0[rvout]"
            )
        filters.append("[rvout]null[vfinal]")
        command = [
            self.ffmpeg,
            "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
            "-y", "-threads", "2", "-filter_threads", "2",
            "-i", str(backup), "-filter_complex", ";".join(filters),
            "-map", "[vfinal]",
        ]
        if source_info.has_audio:
            command.extend(["-map", "[rafinal]", "-ar", "48000", "-c:a", "aac", "-b:a", "96k"])
        command.extend([
            "-sn", "-dn", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-pix_fmt", "yuv420p", "-fps_mode", "cfr", "-r", "30",
            "-movflags", "+faststart", "-f", "mp4", str(media_path),
        ])
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=self.MAX_RUNTIME_SECONDS,
            )
        finally:
            backup.unlink(missing_ok=True)
        if result.returncode != 0:
            raise ValueError("The bounded quality revision could not be rendered.")

    def inspect_render_quality(self, media_path: Path) -> dict[str, object]:
        """Detect objective black-frame and frozen-frame defects in an output."""
        self._validate_managed_path(media_path, kind="output")
        filters = (
            "blackdetect=d=0.50:pix_th=0.10",
            "freezedetect=n=-60dB:d=1.0",
        )
        result = subprocess.run(
            [
                self.ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-v",
                "info",
                "-i",
                str(media_path),
                "-vf",
                ",".join(filters),
                "-an",
                "-f",
                "null",
                "-",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            timeout=self.MAX_RUNTIME_SECONDS,
        )
        if result.returncode != 0:
            raise ValueError("Rendered media quality inspection could not decode the output.")
        log = result.stderr.decode("utf-8", errors="replace")
        black_ranges = [
            (float(match.group(1)), float(match.group(2)))
            for match in re.finditer(
                r"black_start:(\d+(?:\.\d+)?)\s+black_end:(\d+(?:\.\d+)?)",
                log,
            )
        ]
        frozen_ranges = [
            (float(match.group(1)), float(match.group(2)))
            for match in re.finditer(
                r"freeze_start:(\d+(?:\.\d+)?)\s+freeze_end:(\d+(?:\.\d+)?)",
                log,
            )
        ]
        defects = {
            "blackFrames": len(black_ranges),
            "frozenFrames": len(frozen_ranges),
            "blackRanges": [list(range_) for range_ in black_ranges],
            "frozenRanges": [list(range_) for range_ in frozen_ranges],
            "revisionCount": self.last_quality_revision_count,
            "status": "PASSED" if not black_ranges and not frozen_ranges else "DEFECTS_DETECTED",
        }
        return defects

    def render_script_preview(
        self,
        captions: tuple[RenderCaption, ...],
        *,
        duration_seconds: float,
        staged_output: Path,
    ) -> MediaInfo:
        if (
            not math.isfinite(duration_seconds)
            or duration_seconds <= 0
            or duration_seconds > self.MAX_CLIP_DURATION_SECONDS
        ):
            raise ValueError("Script preview duration is outside the configured limit.")
        self._validate_managed_path(staged_output, kind="output")
        caption_file = self._write_captions(captions, 0.0, duration_seconds)
        if caption_file is None:
            raise ValueError("A script preview requires visible captions.")
        subtitle_path = self._escape_filter_path(str(caption_file))
        command = [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-xerror",
            "-y",
            "-threads",
            "2",
            "-max_alloc",
            "67108864",
            "-filter_threads",
            "2",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x14212e:s=720x1280:r=30:d={duration_seconds:.6f}",
            "-vf",
            (
                "drawbox=x=0:y=0:w=720:h=12:color=0x43d6c5:t=fill,"
                "drawbox=x=48:y=224:w=624:h=680:color=0x1d2d40:t=fill,"
                "drawbox=x=48:y=224:w=10:h=680:color=0x43d6c5:t=fill,"
                f"ass=filename='{subtitle_path}'"
            ),
            "-an",
            "-sn",
            "-dn",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-fps_mode",
            "cfr",
            "-r",
            "30",
            "-movflags",
            "+faststart",
            "-fs",
            str(4 * 1024 * 1024),
            "-f",
            "mp4",
            str(staged_output),
        ]
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=self.MAX_RUNTIME_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClipRenderTimeout("Script preview rendering exceeded its time limit.") from exc
        except OSError as exc:
            raise RuntimeError("The local script preview renderer could not be started.") from exc
        finally:
            caption_file.unlink(missing_ok=True)
        if result.returncode != 0:
            raise RuntimeError("FFmpeg failed to render the script preview.")
        size = staged_output.stat().st_size
        if size <= 0 or size > 4 * 1024 * 1024:
            raise ValueError("Script preview is empty or exceeds the output size limit.")
        output_info = self._probe(staged_output, kind="output")
        self._validate_output(
            MediaInfo(duration_seconds, False, False),
            output_info,
            requested_duration=duration_seconds,
            require_video=True,
            vertical=True,
        )
        if output_info.has_audio:
            raise ValueError("Script preview unexpectedly contains an audio stream.")
        self._decode(staged_output)
        return output_info

    def inspect_script_audio(self, audio_path: Path, *, script_id: str) -> MediaInfo:
        self._validate_managed_path(audio_path, kind="script_audio")
        if (
            audio_path.name.split("-audio-", 1)[0]
            != f".clip-script-{script_id}"
            or not 0 < audio_path.stat().st_size <= 4 * 1024 * 1024
        ):
            raise ValueError("Narration audio is outside the managed preview bounds.")
        info = self._probe(audio_path, kind="script_audio")
        if (
            info.has_video
            or not info.has_audio
            or info.audio_codec != "pcm_s16le"
            or info.audio_sample_rate != 24_000
            or info.audio_channels != 1
            or info.audio_duration_seconds is None
            or info.audio_duration_seconds > self.MAX_CLIP_DURATION_SECONDS
        ):
            raise ValueError("Narration audio does not match the supported local format.")
        return info

    def render_narrated_script_preview(
        self,
        scene_paths: tuple[Path, ...],
        scene_durations: tuple[float, ...],
        audio_path: Path,
        *,
        script_id: str,
        duration_seconds: float,
        captions: tuple[RenderCaption, ...],
        staged_output: Path,
    ) -> MediaInfo:
        if (
            not isinstance(scene_paths, tuple)
            or not 1 <= len(scene_paths) <= 12
            or not isinstance(scene_durations, tuple)
            or len(scene_durations) != len(scene_paths)
            or not math.isfinite(duration_seconds)
            or duration_seconds <= 0
            or duration_seconds > self.MAX_CLIP_DURATION_SECONDS
            or any(
                not math.isfinite(duration)
                or duration <= 0
                or duration > self.MAX_CLIP_DURATION_SECONDS
                for duration in scene_durations
            )
            or abs(sum(scene_durations) - duration_seconds) > 0.02
        ):
            raise ValueError("Narrated scene timing is outside the configured limit.")
        self._validate_managed_path(staged_output, kind="output")
        if staged_output.name.split("-output-", 1)[0] != f".clip-script-{script_id}":
            raise ValueError("Narrated preview output is outside its managed request.")
        for scene_number, scene_path in enumerate(scene_paths, start=1):
            self._validate_managed_path(scene_path, kind="script_scene")
            if (
                scene_path.name.split("-scene-", 1)[0]
                != f".clip-script-{script_id}"
                or f"-scene-{scene_number:02d}-" not in scene_path.name
                or not 0 < scene_path.stat().st_size <= 2 * 1024 * 1024
            ):
                raise ValueError("A scene diagram is outside the managed preview bounds.")
            try:
                with Image.open(scene_path) as image:
                    if image.format != "PNG" or image.size != (720, 1280):
                        raise ValueError(
                            "A local scene diagram has an unexpected image format or size."
                        )
                    image.verify()
            except OSError as exc:
                raise ValueError("A local scene diagram is not a valid PNG image.") from exc
        audio_info = self.inspect_script_audio(audio_path, script_id=script_id)
        if abs(audio_info.duration_seconds - duration_seconds) > 0.02:
            raise ValueError("Narration timing differs from its measured duration.")
        caption_file = self._write_captions(captions, 0.0, duration_seconds)
        if caption_file is None:
            raise ValueError("A narrated script preview requires synchronized captions.")
        subtitle_path = self._escape_filter_path(str(caption_file))
        command = [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-xerror",
            "-y",
            "-threads",
            "2",
            "-max_alloc",
            "67108864",
            "-filter_threads",
            "2",
        ]
        scene_filters: list[str] = []
        for index, (scene_path, scene_duration) in enumerate(
            zip(scene_paths, scene_durations)
        ):
            transition_extension = (
                self.SCENE_TRANSITION_SECONDS
                if 0 < index < len(scene_paths) - 1
                else self.SCENE_TRANSITION_SECONDS / 2
                if len(scene_paths) > 1
                else 0.0
            )
            command.extend(
                [
                    "-loop",
                    "1",
                    "-framerate",
                    "30",
                    "-t",
                    f"{scene_duration + transition_extension:.6f}",
                    "-i",
                    str(scene_path),
                ]
            )
            zoom = (
                "min(1.0+on*0.00015,1.05)"
                if index % 2 == 0
                else "max(1.05-on*0.00015,1.0)"
            )
            scene_filters.append(
                f"[{index}:v]scale=720:1280:"
                "force_original_aspect_ratio=decrease:force_divisible_by=2,"
                "pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=0x14212e,"
                f"zoompan=z='{zoom}':"
                "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                "d=1:s=720x1280:fps=30,"
                f"setsar=1,format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[scene{index}]"
            )
        transition_filters: list[str] = []
        video_label = "scene0"
        if len(scene_paths) > 1:
            for index in range(1, len(scene_paths)):
                next_label = f"transition{index}"
                offset = (
                    sum(scene_durations[:index])
                    - self.SCENE_TRANSITION_SECONDS / 2
                )
                transition_filters.append(
                    f"[{video_label}][scene{index}]"
                    f"xfade=transition=fade:"
                    f"duration={self.SCENE_TRANSITION_SECONDS:.3f}:"
                    f"offset={offset:.6f}[{next_label}]"
                )
                video_label = next_label
        command.extend(
            [
                "-i",
                str(audio_path),
                "-filter_complex",
                ";".join(
                    (
                        *scene_filters,
                        *transition_filters,
                        (
                            f"[{video_label}]"
                            f"ass=filename='{subtitle_path}'[vout]"
                        ),
                    )
                ),
                "-map",
                "[vout]",
                "-map",
                f"{len(scene_paths)}:a:0",
                "-t",
                f"{duration_seconds:.6f}",
                "-sn",
                "-dn",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "28",
                "-pix_fmt",
                "yuv420p",
                "-fps_mode",
                "cfr",
                "-r",
                "30",
                "-c:a",
                "aac",
                "-b:a",
                "96k",
                "-movflags",
                "+faststart",
                "-fs",
                str(4 * 1024 * 1024),
                "-f",
                "mp4",
                str(staged_output),
            ]
        )
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=self.MAX_RUNTIME_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClipRenderTimeout(
                "Narrated script preview rendering exceeded its time limit."
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                "The local narrated preview renderer could not be started."
            ) from exc
        finally:
            caption_file.unlink(missing_ok=True)
        if result.returncode != 0:
            raise RuntimeError("FFmpeg failed to render the narrated script preview.")
        size = staged_output.stat().st_size
        if size <= 0 or size > 4 * 1024 * 1024:
            raise ValueError("Narrated script preview exceeds the output size limit.")
        output_info = self._probe(staged_output, kind="output")
        self._validate_output(
            audio_info,
            output_info,
            requested_duration=duration_seconds,
            require_video=True,
            vertical=True,
        )
        if (
            output_info.video_width != 720
            or output_info.video_height != 1280
            or output_info.video_codec != "h264"
            or output_info.audio_codec != "aac"
            or output_info.audio_sample_rate != 24_000
            or output_info.audio_channels != 1
            or output_info.audio_duration_seconds is None
            or output_info.video_duration_seconds is None
            or abs(output_info.audio_duration_seconds - duration_seconds) > 0.25
            or abs(output_info.video_duration_seconds - duration_seconds) > 0.25
            or self._audio_peak(staged_output) == 0
        ):
            raise ValueError("Narrated preview failed its technical audio/video checks.")
        self._decode(staged_output)
        return output_info

    def _audio_peak(
        self,
        media_path: Path,
        *,
        start_seconds: float | None = None,
        duration_seconds: float | None = None,
    ) -> int:
        command = [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-xerror",
            "-threads",
            "2",
        ]
        if start_seconds is not None:
            command.extend(["-ss", f"{start_seconds:.6f}"])
        command.extend(["-i", str(media_path)])
        if duration_seconds is not None:
            command.extend(["-t", f"{duration_seconds:.6f}"])
        command.extend(
            [
                "-map",
                "0:a:0",
                "-vn",
                "-sn",
                "-dn",
                "-acodec",
                "pcm_s16le",
                "-f",
                "s16le",
                "pipe:1",
            ]
        )
        return_code, samples = self._run_bounded_stdout(
            command,
            timeout=self.MAX_RUNTIME_SECONDS,
            max_output_bytes=self.MAX_PCM_VALIDATION_BYTES,
        )
        if return_code != 0 or not samples or len(samples) % 2:
            raise ValueError("Audio signal validation could not decode the selected stream.")
        return max(abs(sample) for (sample,) in struct.iter_unpack("<h", samples))

    def inspect_script_preview_audio(
        self,
        media_path: Path,
        scene_durations: tuple[float, ...],
    ) -> dict[str, Any]:
        self._validate_managed_path(media_path, kind="output")
        command = [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-xerror",
            "-threads",
            "2",
            "-i",
            str(media_path),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-acodec",
            "pcm_s16le",
            "-f",
            "s16le",
            "pipe:1",
        ]
        return_code, samples = self._run_bounded_stdout(
            command,
            timeout=self.MAX_RUNTIME_SECONDS,
            max_output_bytes=self.MAX_PCM_VALIDATION_BYTES,
        )
        if return_code != 0 or not samples or len(samples) % 2:
            raise ValueError("Narrated MP4 audio could not be measured after encoding.")
        all_sample_square_sum = sum(
            sample * sample for (sample,) in struct.iter_unpack("<h", samples)
        )
        sample_count = len(samples) // 2
        overall_rms = math.sqrt(all_sample_square_sum / sample_count)
        if overall_rms <= 0:
            raise ValueError("Narrated MP4 audio is silent after encoding.")
        overall_rms_dbfs = 20 * math.log10(overall_rms / 32768)
        if overall_rms_dbfs < -45.0:
            raise ValueError("Narrated MP4 audio is effectively silent after encoding.")

        boundary_gaps: list[dict[str, Any]] = []
        elapsed = 0.0
        gap_seconds = 0.15
        window_seconds = 0.05
        for scene_duration in scene_durations[:-1]:
            if not math.isfinite(scene_duration) or scene_duration <= gap_seconds:
                raise ValueError("Narrated scene boundary timing is invalid.")
            center = elapsed + scene_duration - gap_seconds / 2
            start_sample = round((center - window_seconds / 2) * 24_000)
            end_sample = round((center + window_seconds / 2) * 24_000)
            if end_sample > sample_count:
                raise ValueError(
                    "Narrated MP4 ended before a scene-boundary check completed."
                )
            window = samples[start_sample * 2 : end_sample * 2]
            window_count = len(window) // 2
            if not window_count:
                raise ValueError("Narrated scene-boundary audio window is empty.")
            window_square_sum = sum(
                sample * sample for (sample,) in struct.iter_unpack("<h", window)
            )
            window_rms = math.sqrt(window_square_sum / window_count)
            window_rms_dbfs = (
                20 * math.log10(window_rms / 32768)
                if window_rms
                else None
            )
            if window_rms_dbfs is not None and window_rms_dbfs > -40.0:
                raise ValueError(
                    "Narrated MP4 scene-boundary silence was not preserved after encoding."
                )
            boundary_gaps.append(
                {
                    "centerSeconds": round(center, 3),
                    "windowMilliseconds": round(window_seconds * 1000),
                    "rmsDbfs": (
                        round(window_rms_dbfs, 2)
                        if window_rms_dbfs is not None
                        else None
                    ),
                    "state": "INTENTIONAL_SILENCE_VERIFIED",
                }
            )
            elapsed += scene_duration
        return {
            "rmsDbfs": round(overall_rms_dbfs, 2),
            "sceneBoundaryGaps": boundary_gaps,
        }

    def _write_captions(
        self,
        captions: tuple[RenderCaption, ...],
        start_seconds: float,
        end_seconds: float,
    ) -> Path | None:
        if not captions:
            return None
        if not isinstance(captions, tuple) or len(captions) > 500:
            raise ValueError("Transcript captions exceed the configured limit.")
        entries: list[str] = []
        previous_end = -1.0
        total_characters = 0
        for caption in captions:
            if (
                type(caption) is not RenderCaption
                or not math.isfinite(caption.start_seconds)
                or not math.isfinite(caption.end_seconds)
                or caption.start_seconds < previous_end
                or caption.end_seconds <= caption.start_seconds
                or caption.start_seconds < start_seconds
                or caption.end_seconds > end_seconds + 0.000001
                or round(caption.end_seconds * 100) <= round(caption.start_seconds * 100)
                or not isinstance(caption.text, str)
                or not caption.text.strip()
            ):
                raise ValueError("Transcript caption timing or text is invalid.")
            previous_end = caption.end_seconds
            text = (
                re.sub(r"<[^>]*>", "", caption.text)
                if caption.sanitize_markup
                else caption.text
            )
            text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            if not text:
                raise ValueError("Transcript caption text is empty after validation.")
            if len(text) > self.MAX_CAPTION_SEGMENT_CHARACTERS:
                raise ValueError(
                    "Transcript caption segment exceeds the readable layout limit."
                )
            lines, maximum_line_width = self._caption_layout(text)
            total_characters += len(text)
            if total_characters > 20_000:
                raise ValueError("Transcript caption text exceeds the configured limit.")
            safe_text = "\n".join(lines)
            safe_text = safe_text.replace("\\", "\\\\")
            safe_text = safe_text.replace("{", r"\{").replace("}", r"\}")
            safe_text = safe_text.replace("\n", r"\N")
            entries.append(
                "Dialogue: 0,"
                f"{self._format_ass_time(caption.start_seconds - start_seconds)},"
                f"{self._format_ass_time(caption.end_seconds - start_seconds)},"
                f"Default,,0,0,0,,{safe_text}"
            )
        ass_content = "\n".join(
            (
                "[Script Info]",
                "ScriptType: v4.00+",
                "PlayResX: 720",
                "PlayResY: 1280",
                "ScaledBorderAndShadow: yes",
                "",
                "[V4+ Styles]",
                "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
                "OutlineColour,BackColour,Bold,Italic,BorderStyle,Outline,"
                "Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
                "Style: Default,DejaVu Sans,36,&H00FFFFFF,&H000000FF,"
                "&H000B1520,&H500B1520,1,0,3,8,0,2,64,64,220,1",
                "",
                "[Events]",
                "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,"
                "Effect,Text",
                *entries,
                "",
            )
        )
        caption_file = self.managed_staging_directory / f".captions-{uuid4().hex}.ass"
        descriptor = os.open(
            caption_file,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(ass_content)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            caption_file.unlink(missing_ok=True)
            raise
        return caption_file

    def _caption_layout(self, text: str) -> tuple[list[str], int]:
        try:
            font = ImageFont.truetype(
                str(self.CAPTION_FONT_PATH),
                size=self.CAPTION_FONT_SIZE,
            )
        except OSError as exc:
            raise RuntimeError(
                "The installed font required for caption validation is unavailable."
            ) from exc
        measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            fragments = [word]
            if (
                measure.textbbox((0, 0), word, font=font)[2]
                > self.CAPTION_SAFE_MAX_WIDTH
                or len(word) > self.MAX_CAPTION_LINE_CHARACTERS
            ):
                fragments = []
                fragment = ""
                for character in word:
                    candidate = f"{fragment}{character}"
                    if (
                        measure.textbbox((0, 0), candidate, font=font)[2]
                        <= self.CAPTION_SAFE_MAX_WIDTH
                        and len(candidate) <= self.MAX_CAPTION_LINE_CHARACTERS
                    ):
                        fragment = candidate
                    else:
                        if not fragment:
                            raise ValueError(
                                "Caption contains a glyph wider than the safe area."
                            )
                        fragments.append(fragment)
                        fragment = character
                if fragment:
                    fragments.append(fragment)
            for fragment in fragments:
                candidate = f"{current} {fragment}".strip()
                if (
                    measure.textbbox((0, 0), candidate, font=font)[2]
                    <= self.CAPTION_SAFE_MAX_WIDTH
                    and len(candidate) <= self.MAX_CAPTION_LINE_CHARACTERS
                ):
                    current = candidate
                    continue
                if current:
                    lines.append(current)
                current = fragment
        if current:
            lines.append(current)
        if not lines or len(lines) > self.MAX_CAPTION_LINES:
            raise ValueError(
                "Transcript caption segment exceeds the readable layout limit."
            )
        widths = [measure.textbbox((0, 0), line, font=font)[2] for line in lines]
        return lines, max(widths)

    @staticmethod
    def _format_ass_time(seconds: float) -> str:
        centiseconds = round(seconds * 100)
        hours, remainder = divmod(centiseconds, 360_000)
        minutes, remainder = divmod(remainder, 6_000)
        whole_seconds, centiseconds = divmod(remainder, 100)
        return f"{hours}:{minutes:02}:{whole_seconds:02}.{centiseconds:02}"

    @staticmethod
    def _escape_filter_path(path: str) -> str:
        return (
            path.replace("\\", "\\\\")
            .replace(":", "\\:")
            .replace(",", "\\,")
            .replace("'", "\\'")
            .replace("[", "\\[")
            .replace("]", "\\]")
        )

    def _validate_output(
        self,
        source: MediaInfo,
        output: MediaInfo,
        *,
        requested_duration: float,
        require_video: bool = False,
        vertical: bool = False,
        expected_audio_present: bool | None = None,
    ) -> None:
        if (
            (require_video and not output.has_video)
            or (not require_video and output.has_video != source.has_video)
            or output.has_audio
            != (
                source.has_audio
                if expected_audio_present is None
                else expected_audio_present
            )
        ):
            raise ValueError("Rendered clip stream types differ from the source.")
        if abs(output.duration_seconds - requested_duration) > self.DURATION_TOLERANCE_SECONDS:
            raise ValueError("Rendered clip duration is outside the accepted tolerance.")
        for stream_duration in (
            output.audio_duration_seconds,
            output.video_duration_seconds,
        ):
            if (
                stream_duration is not None
                and abs(stream_duration - requested_duration)
                > self.DURATION_TOLERANCE_SECONDS
            ):
                raise ValueError("Rendered media stream duration is outside tolerance.")
        if output.has_video and output.has_audio:
            if (
                output.video_duration_seconds is not None
                and output.audio_duration_seconds is not None
                and abs(
                    output.video_duration_seconds - output.audio_duration_seconds
                )
                > max(
                    self.SYNC_TOLERANCE_SECONDS,
                    2 / (output.video_fps or 30.0),
                )
            ):
                raise ValueError("Rendered audio and video streams are out of sync.")
        if output.has_video and (
            output.video_width is None
            or output.video_height is None
            or output.video_width > (720 if vertical else 1280)
            or output.video_height > (1280 if vertical else 720)
            or (vertical and output.video_height <= output.video_width)
        ):
            raise ValueError("Rendered video exceeds the configured output dimensions.")

    def _measure_decoded_audio_peak_dbfs(
        self, staged_output: Path
    ) -> float | None:
        command = [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(staged_output),
            "-map",
            "0:a:0",
            "-f",
            "s16le",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-",
        ]
        return_code, pcm = self._run_bounded_stdout(
            command,
            timeout=self.MAX_RUNTIME_SECONDS,
            max_output_bytes=self.MAX_PCM_VALIDATION_BYTES,
        )
        if return_code != 0 or len(pcm) < 2 or len(pcm) % 2:
            raise ValueError("Rendered audio could not be decoded for peak validation.")
        peak = max(
            abs(sample[0]) for sample in struct.iter_unpack("<h", pcm)
        )
        if peak == 0:
            return None
        return 20 * math.log10(peak / 32768)

    def _decode(self, staged_output: Path) -> None:
        command = [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(staged_output),
            "-f",
            "null",
            "-",
        ]
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=self.MAX_RUNTIME_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClipRenderTimeout("Rendered clip decode validation timed out.") from exc
        except OSError as exc:
            raise RuntimeError("The local clip decoder could not be started.") from exc
        if result.returncode != 0:
            raise ValueError("Rendered clip failed independent decode validation.")
