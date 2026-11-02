from __future__ import annotations

import base64
import hashlib
import http.client
import io
import ipaddress
import json
import math
import re
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import Field, ValidationError, field_validator
from PIL import Image

from ultron.domain.content import ContentRecord
from ultron.domain.content_visual_intelligence import (
    VisualCapability,
    VisualConfidence,
    VisualEventObservation,
    VisualFrameAnalysis,
    VisualFrameEvidence,
    VisualInferenceDevice,
    VisualInferenceResources,
    VisualModelCapabilities,
    VisualObjectObservation,
    VisualVideoAnalysis,
)


_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_MAX_MODEL_RESPONSE_BYTES = 64 * 1024
_MIN_AVAILABLE_MEMORY_BYTES = 2 * 1024**3


class VisualProviderError(RuntimeError):
    """A local visual provider could not produce a validated result."""


class VisualResourceLimitError(VisualProviderError):
    """Inference was refused or aborted because the memory floor was reached."""


@dataclass(frozen=True)
class VisualFrameInput:
    evidence: VisualFrameEvidence
    image_bytes: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.image_bytes, bytes) or not self.image_bytes:
            raise ValueError("Visual frame input must contain non-empty image bytes.")
        if len(self.image_bytes) > _MAX_IMAGE_BYTES:
            raise ValueError("Visual frame image exceeds the configured byte limit.")
        if hashlib.sha256(self.image_bytes).hexdigest() != self.evidence.frame_sha256:
            raise ValueError("Visual frame image hash does not match its evidence record.")


class VisualUnderstandingProvider(Protocol):
    """Provider-neutral image understanding boundary for CRE."""

    def report_capabilities(self) -> VisualModelCapabilities: ...

    def analyze_frame(self, frame: VisualFrameInput) -> VisualFrameAnalysis: ...


@dataclass(frozen=True)
class SampledVideoFrames:
    asset_id: str
    asset_sha256: str
    duration_seconds: float
    frames: tuple[VisualFrameInput, ...]


class _ProviderObject(ContentRecord):
    label: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1000)
    spatial_relation: str | None = Field(default=None, max_length=256)

    @field_validator("label", "description", "spatial_relation")
    @classmethod
    def trim_provider_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip() and value != "":
            raise ValueError("Provider output text must not be blank.")
        return value.strip()


class _ProviderFrameResponse(ContentRecord):
    scene_description: str = Field(min_length=1, max_length=4000)
    objects: tuple[_ProviderObject, ...] = Field(default=(), max_length=32)
    actions: tuple[str, ...] = Field(default=(), max_length=16)
    events: tuple[str, ...] = Field(default=(), max_length=16)
    uncertainties: tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("scene_description")
    @classmethod
    def trim_scene_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Provider scene description must not be blank.")
        return value.strip()

    @field_validator("actions", "events", "uncertainties")
    @classmethod
    def trim_nonblank_strings(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("Provider output contains blank text.")
        return tuple(value.strip() for value in values)


class OllamaVisionProvider:
    """Loopback-only Ollama adapter; model output is normalized before return."""

    provider_id = "ollama"

    def __init__(
        self,
        model_id: str,
        *,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 180.0,
        context_tokens: int = 2048,
        output_tokens: int = 384,
        minimum_available_memory_bytes: int = _MIN_AVAILABLE_MEMORY_BYTES,
    ) -> None:
        if not model_id or len(model_id) > 256:
            raise ValueError("A valid local Ollama model identifier is required.")
        if (
            not math.isfinite(timeout_seconds)
            or not 1 <= timeout_seconds <= 600
            or type(context_tokens) is not int
            or not 256 <= context_tokens <= 8192
            or type(output_tokens) is not int
            or not 16 <= output_tokens <= 2048
            or type(minimum_available_memory_bytes) is not int
            or minimum_available_memory_bytes < _MIN_AVAILABLE_MEMORY_BYTES
        ):
            raise ValueError("Ollama visual inference limits are invalid.")
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.hostname is None
        ):
            raise ValueError("Ollama visual inference must use a loopback HTTP endpoint.")
        try:
            resolved = {
                ipaddress.ip_address(item[4][0].split("%", 1)[0])
                for item in socket.getaddrinfo(
                    parsed.hostname,
                    parsed.port or 80,
                    type=socket.SOCK_STREAM,
                )
            }
        except (OSError, ValueError) as exc:
            raise ValueError("Ollama endpoint host could not be verified as loopback.") from exc
        if not resolved or any(not address.is_loopback for address in resolved):
            raise ValueError("Ollama visual inference must resolve only to loopback.")
        self.model_id = model_id
        self._base_url = base_url.rstrip("/")
        self._host = parsed.hostname
        self._port = parsed.port or 80
        self._timeout_seconds = timeout_seconds
        self._context_tokens = context_tokens
        self._output_tokens = output_tokens
        self._minimum_available_memory_bytes = minimum_available_memory_bytes

    def report_capabilities(self) -> VisualModelCapabilities:
        model_info = self._get_json("/api/show", {"model": self.model_id})
        tags = self._get_json("/api/tags")
        advertised = model_info.get("capabilities")
        if not isinstance(advertised, list) or "vision" not in advertised:
            raise VisualProviderError(
                "The configured Ollama model does not advertise image input."
            )
        models = tags.get("models")
        if not isinstance(models, list):
            raise VisualProviderError("Ollama returned an invalid local model inventory.")
        model_record = next(
            (
                item
                for item in models
                if isinstance(item, dict)
                and item.get("name") == self.model_id
            ),
            None,
        )
        digest = model_record.get("digest") if model_record else None
        if not isinstance(digest, str) or not digest:
            raise VisualProviderError(
                "Ollama did not provide an installed model digest."
            )
        return VisualModelCapabilities(
            provider_id=self.provider_id,
            model_id=self.model_id,
            model_version=digest,
            capabilities=(
                VisualCapability.IMAGE_UNDERSTANDING,
                VisualCapability.SCENE_DESCRIPTION,
                VisualCapability.OBJECT_RECOGNITION,
                VisualCapability.ACTION_OBSERVATION,
                VisualCapability.STRUCTURED_OUTPUT,
            ),
            max_image_dimension=1024,
            max_sequence_frames=1,
        )

    def analyze_frame(self, frame: VisualFrameInput) -> VisualFrameAnalysis:
        if frame.evidence.width > 1024 or frame.evidence.height > 1024:
            raise ValueError("Input image dimensions exceed the local model limit.")
        capabilities = self.report_capabilities()
        start = time.monotonic()
        memory_before, swap_before = _read_memory_snapshot()
        if memory_before < self._minimum_available_memory_bytes:
            raise VisualResourceLimitError(
                "Visual inference refused because available memory is below the safety floor."
            )
        request = {
            "model": self.model_id,
            "prompt": (
                "Analyze this image and return JSON only with exactly these keys: "
                "scene_description (string), objects (array of objects with label, "
                "description, and optional spatial_relation), actions (array of "
                "short strings), events (array of short strings), uncertainties "
                "(array of strings). Keep the description and lists concise. "
                "Describe only visible evidence. Do not invent timestamps, identities, "
                "intent, or confidence values. If unsure, state that in uncertainties."
            ),
            "images": [base64.b64encode(frame.image_bytes).decode("ascii")],
            "stream": False,
            "format": "json",
            "think": False,
            "keep_alive": "5m",
            "options": {
                "num_ctx": self._context_tokens,
                "num_predict": self._output_tokens,
                "temperature": 0,
            },
        }
        response_body, memory_min = self._post_json_with_memory_guard(
            "/api/generate",
            request,
            memory_before=memory_before,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        try:
            wrapper = json.loads(response_body)
            if not isinstance(wrapper, dict):
                raise ValueError("Ollama response must be a JSON object.")
            if wrapper.get("done") is not True:
                raise ValueError("Ollama did not complete the visual inference.")
            if wrapper.get("done_reason") == "length":
                raise ValueError("Ollama visual response reached the output token limit.")
            model_text = wrapper.get("response")
            if not isinstance(model_text, str) or len(model_text) > _MAX_MODEL_RESPONSE_BYTES:
                raise ValueError("Ollama visual response is missing or oversized.")
            parsed = _ProviderFrameResponse.model_validate_json(model_text)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise VisualProviderError(
                f"Ollama returned an invalid or incomplete visual response: {exc}"
            ) from exc
        device, device_evidence = self._reported_device()
        memory_after, swap_after = _read_memory_snapshot()
        if memory_after < self._minimum_available_memory_bytes:
            raise VisualResourceLimitError(
                "Visual inference completed below the configured memory safety floor."
            )
        memory_min = min(memory_min, memory_after)
        resources = VisualInferenceResources(
            duration_ms=elapsed_ms,
            device=device,
            device_evidence=device_evidence,
            mem_available_before_bytes=memory_before,
            mem_available_min_bytes=memory_min,
            mem_available_after_bytes=memory_after,
            swap_free_before_bytes=swap_before,
            swap_free_after_bytes=swap_after,
        )
        confidence = VisualConfidence()
        raw_output_sha256 = hashlib.sha256(model_text.encode("utf-8")).hexdigest()
        return VisualFrameAnalysis(
            evidence=frame.evidence,
            scene_description=parsed.scene_description,
            raw_output_sha256=raw_output_sha256,
            objects=tuple(
                VisualObjectObservation(
                    label=item.label,
                    description=item.description,
                    spatial_relation=item.spatial_relation,
                    confidence=confidence,
                )
                for item in parsed.objects
            ),
            actions=tuple(
                VisualEventObservation(description=item, confidence=confidence)
                for item in parsed.actions
            ),
            events=tuple(
                VisualEventObservation(
                    description=item,
                    event_type="event_observation",
                    confidence=confidence,
                )
                for item in parsed.events
            ),
            uncertainties=parsed.uncertainties,
            provider_id=self.provider_id,
            model_id=capabilities.model_id,
            model_version=capabilities.model_version,
            resources=resources,
        )

    def _post_json_with_memory_guard(
        self,
        path: str,
        payload: dict[str, object],
        *,
        memory_before: int,
    ) -> tuple[bytes, int]:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        connection = http.client.HTTPConnection(
            self._host,
            self._port,
            timeout=self._timeout_seconds,
        )
        finished = threading.Event()
        result: dict[str, object] = {}

        def send_request() -> None:
            try:
                connection.request(
                    "POST",
                    path,
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                if response.status < 200 or response.status >= 300:
                    response.read(1024)
                    raise VisualProviderError(
                        f"Ollama returned HTTP {response.status} for visual inference."
                    )
                response_body = response.read(_MAX_MODEL_RESPONSE_BYTES + 1)
                if len(response_body) > _MAX_MODEL_RESPONSE_BYTES:
                    raise VisualProviderError("Ollama response exceeded its byte limit.")
                result["body"] = response_body
            except Exception as exc:
                result["error"] = exc
            finally:
                finished.set()

        worker = threading.Thread(target=send_request, daemon=True)
        worker.start()
        minimum_available = memory_before
        deadline = time.monotonic() + self._timeout_seconds
        while not finished.wait(0.2):
            current_memory, _ = _read_memory_snapshot()
            minimum_available = min(minimum_available, current_memory)
            if current_memory < self._minimum_available_memory_bytes:
                self._abort_connection(connection)
                finished.wait(2)
                raise VisualResourceLimitError(
                    "Visual inference aborted because available memory crossed the safety floor."
                )
            if time.monotonic() >= deadline:
                self._abort_connection(connection)
                finished.wait(2)
                raise VisualProviderError("Ollama visual inference timed out.")
        worker.join(timeout=1)
        if "error" in result:
            error = result["error"]
            if isinstance(error, VisualProviderError):
                raise error
            raise VisualProviderError(f"Ollama visual inference failed: {error}") from error
        response_body = result.get("body")
        if not isinstance(response_body, bytes):
            raise VisualProviderError("Ollama visual inference returned no response body.")
        memory_after_request, _ = _read_memory_snapshot()
        minimum_available = min(minimum_available, memory_after_request)
        if memory_after_request < self._minimum_available_memory_bytes:
            raise VisualResourceLimitError(
                "Visual inference completed below the configured memory safety floor."
            )
        return response_body, minimum_available

    @staticmethod
    def _abort_connection(connection: http.client.HTTPConnection) -> None:
        sock = connection.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        connection.close()

    def _reported_device(self) -> tuple[VisualInferenceDevice, str | None]:
        try:
            response = self._get_json("/api/ps")
        except VisualProviderError:
            return VisualInferenceDevice.UNKNOWN, None
        models = response.get("models")
        if not isinstance(models, list):
            return VisualInferenceDevice.UNKNOWN, None
        item = next(
            (
                model
                for model in models
                if isinstance(model, dict)
                and model.get("name") == self.model_id
            ),
            None,
        )
        if item is None:
            return VisualInferenceDevice.UNKNOWN, None
        size = item.get("size")
        size_vram = item.get("size_vram")
        if (
            not isinstance(size, int)
            or not isinstance(size_vram, int)
            or size < 0
            or size_vram < 0
        ):
            return VisualInferenceDevice.UNKNOWN, None
        evidence = f"Ollama /api/ps reports size_vram={size_vram} of size={size} bytes."
        if size_vram == 0:
            return VisualInferenceDevice.CPU, evidence
        if size_vram >= size:
            return VisualInferenceDevice.GPU, evidence
        return VisualInferenceDevice.MIXED, evidence

    def _get_json(
        self,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        data = (
            None
            if payload is None
            else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
            method="POST" if data is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                raw = response.read(_MAX_MODEL_RESPONSE_BYTES + 1)
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise VisualProviderError(f"Local Ollama request failed: {exc}") from exc
        if len(raw) > _MAX_MODEL_RESPONSE_BYTES:
            raise VisualProviderError("Local Ollama metadata response exceeded its byte limit.")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VisualProviderError("Local Ollama returned invalid JSON metadata.") from exc
        if not isinstance(value, dict):
            raise VisualProviderError("Local Ollama metadata must be a JSON object.")
        return value


class VideoFrameSampler:
    """Extracts bounded, timestamped frames from hash-verified video bytes."""

    MAX_SOURCE_BYTES = 256 * 1024 * 1024
    MAX_SOURCE_DURATION_SECONDS = 300.0
    MAX_FRAME_COUNT = 24
    MAX_IMAGE_DIMENSION = 1024
    MAX_FRAME_BYTES = _MAX_IMAGE_BYTES
    FFMPEG_TIMEOUT_SECONDS = 30

    def __init__(self, *, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    def sample_video_frames(
        self,
        source_bytes: bytes,
        *,
        asset_id: str,
        expected_asset_sha256: str,
        timestamps_seconds: tuple[float, ...],
    ) -> SampledVideoFrames:
        if not isinstance(source_bytes, bytes) or not source_bytes:
            raise ValueError("Video source must be non-empty bytes.")
        if len(source_bytes) > self.MAX_SOURCE_BYTES:
            raise ValueError("Video source exceeds the configured byte limit.")
        if not asset_id or len(asset_id) > 128:
            raise ValueError("A valid source asset identifier is required.")
        if (
            not isinstance(expected_asset_sha256, str)
            or not _SHA256_RE.fullmatch(expected_asset_sha256)
            or hashlib.sha256(source_bytes).hexdigest() != expected_asset_sha256
        ):
            raise ValueError("Video bytes do not match the registered source digest.")
        if (
            not isinstance(timestamps_seconds, tuple)
            or not timestamps_seconds
            or len(timestamps_seconds) > self.MAX_FRAME_COUNT
            or any(
                not math.isfinite(timestamp) or timestamp < 0
                for timestamp in timestamps_seconds
            )
            or any(
                left >= right
                for left, right in zip(
                    timestamps_seconds,
                    timestamps_seconds[1:],
                )
            )
        ):
            raise ValueError("Requested frame timestamps are invalid.")

        with tempfile.TemporaryDirectory(prefix="ultron-cre-visual-") as directory:
            source_path = Path(directory) / "authorized-source.bin"
            source_path.write_bytes(source_bytes)
            source_path.chmod(0o600)
            duration = self._probe_video_duration(source_path)
            if duration > self.MAX_SOURCE_DURATION_SECONDS:
                raise ValueError("Video source exceeds the configured duration limit.")
            if any(timestamp >= duration for timestamp in timestamps_seconds):
                raise ValueError("Requested frame timestamp lies outside source duration.")
            frames = tuple(
                self._extract_frame(
                    source_path,
                    asset_id=asset_id,
                    asset_sha256=expected_asset_sha256,
                    timestamp_seconds=timestamp,
                )
                for timestamp in timestamps_seconds
            )
        return SampledVideoFrames(
            asset_id=asset_id,
            asset_sha256=expected_asset_sha256,
            duration_seconds=duration,
            frames=frames,
        )

    def _probe_video_duration(self, source_path: Path) -> float:
        command = [
            self.ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source_path),
        ]
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=10,
            )
        except subprocess.TimeoutExpired as exc:
            raise VisualProviderError("FFprobe timed out while probing the source.") from exc
        except OSError as exc:
            raise VisualProviderError("FFprobe could not be started.") from exc
        if result.returncode != 0:
            raise VisualProviderError("FFprobe could not read the supplied video bytes.")
        try:
            duration = float(result.stdout.decode("ascii").strip())
        except (UnicodeDecodeError, ValueError) as exc:
            raise VisualProviderError("FFprobe returned an invalid source duration.") from exc
        if not math.isfinite(duration) or duration <= 0:
            raise VisualProviderError("Video duration must be finite and positive.")
        return duration

    def _extract_frame(
        self,
        source_path: Path,
        *,
        asset_id: str,
        asset_sha256: str,
        timestamp_seconds: float,
    ) -> VisualFrameInput:
        command = [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-threads",
            "1",
            "-filter_threads",
            "1",
            "-ss",
            f"{timestamp_seconds:.6f}",
            "-i",
            str(source_path),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-frames:v",
            "1",
            "-vf",
            (
                "scale=w='min(1024,iw)':h='min(1024,ih)':"
                "force_original_aspect_ratio=decrease"
            ),
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "pipe:1",
        ]
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=self.FFMPEG_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise VisualProviderError("FFmpeg timed out while extracting a frame.") from exc
        except OSError as exc:
            raise VisualProviderError("FFmpeg could not be started.") from exc
        if result.returncode != 0:
            raise VisualProviderError("FFmpeg failed to extract a requested video frame.")
        image_bytes = result.stdout
        if not image_bytes or len(image_bytes) > self.MAX_FRAME_BYTES:
            raise VisualProviderError("Extracted frame is empty or exceeds its byte limit.")
        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                width, height = image.size
                if (
                    width <= 0
                    or height <= 0
                    or max(width, height) > self.MAX_IMAGE_DIMENSION
                    or width * height > self.MAX_IMAGE_DIMENSION**2
                ):
                    raise ValueError("Extracted frame dimensions exceed the configured limit.")
                image.verify()
        except (OSError, ValueError) as exc:
            raise VisualProviderError("FFmpeg produced an invalid image frame.") from exc
        frame_sha256 = hashlib.sha256(image_bytes).hexdigest()
        frame_identity = hashlib.sha256(
            (
                f"{asset_id}|{asset_sha256}|{timestamp_seconds:.6f}|"
                f"{frame_sha256}"
            ).encode("utf-8")
        ).hexdigest()
        evidence = VisualFrameEvidence(
            frame_id=f"vf-{frame_identity[:32]}",
            asset_id=asset_id,
            asset_sha256=asset_sha256,
            timestamp_seconds=timestamp_seconds,
            frame_sha256=frame_sha256,
            width=width,
            height=height,
        )
        return VisualFrameInput(evidence=evidence, image_bytes=image_bytes)


class VisualIntelligenceService:
    """Normalizes provider observations while keeping frame order explicit."""

    def __init__(
        self,
        provider: VisualUnderstandingProvider,
        *,
        sampler: VideoFrameSampler | None = None,
    ) -> None:
        self.provider = provider
        self.sampler = sampler or VideoFrameSampler()

    def analyze_video(
        self,
        source_bytes: bytes,
        *,
        asset_id: str,
        expected_asset_sha256: str,
        timestamps_seconds: tuple[float, ...],
    ) -> VisualVideoAnalysis:
        sampled = self.sampler.sample_video_frames(
            source_bytes,
            asset_id=asset_id,
            expected_asset_sha256=expected_asset_sha256,
            timestamps_seconds=timestamps_seconds,
        )
        capabilities = self.provider.report_capabilities()
        if VisualCapability.IMAGE_UNDERSTANDING not in capabilities.capabilities:
            raise VisualProviderError(
                "The configured provider does not support image understanding."
            )
        analyses_list: list[VisualFrameAnalysis] = []
        for frame in sampled.frames:
            if (
                frame.evidence.width > capabilities.max_image_dimension
                or frame.evidence.height > capabilities.max_image_dimension
            ):
                raise VisualProviderError(
                    "A sampled frame exceeds the configured provider image limit."
                )
            analysis = self.provider.analyze_frame(frame)
            if (
                analysis.evidence != frame.evidence
                or analysis.provider_id != capabilities.provider_id
                or analysis.model_id != capabilities.model_id
                or analysis.model_version != capabilities.model_version
            ):
                raise VisualProviderError(
                    "The provider returned an analysis with mismatched source or model identity."
                )
            analyses_list.append(analysis)
        analyses = tuple(analyses_list)
        return VisualVideoAnalysis(
            asset_id=sampled.asset_id,
            asset_sha256=sampled.asset_sha256,
            duration_seconds=sampled.duration_seconds,
            frames=analyses,
            temporal_method="ordered_timestamped_frame_observations_v1",
            cross_frame_reasoning_performed=False,
        )


def select_frame_timestamps(
    duration_seconds: float,
    *,
    max_frames: int = 12,
    scene_boundaries_seconds: tuple[float, ...] = (),
) -> tuple[float, ...]:
    if (
        not math.isfinite(duration_seconds)
        or duration_seconds <= 0
        or duration_seconds > VideoFrameSampler.MAX_SOURCE_DURATION_SECONDS
        or type(max_frames) is not int
        or not 1 <= max_frames <= VideoFrameSampler.MAX_FRAME_COUNT
        or len(scene_boundaries_seconds) > 1_000
        or any(
            not math.isfinite(boundary)
            or boundary <= 0
            or boundary >= duration_seconds
            for boundary in scene_boundaries_seconds
        )
        or any(
            left >= right
            for left, right in zip(
                scene_boundaries_seconds,
                scene_boundaries_seconds[1:],
            )
        )
    ):
        raise ValueError("Frame sampling configuration is invalid.")
    uniform_count = max(1, max_frames // 2)
    candidates = {
        duration_seconds * (index + 0.5) / uniform_count
        for index in range(uniform_count)
    }
    for boundary in scene_boundaries_seconds:
        candidates.update(
            (
                max(0.001, boundary - 0.25),
                boundary,
                min(duration_seconds - 0.001, boundary + 0.25),
            )
        )
    ordered = sorted(
        value
        for value in candidates
        if 0 <= value < duration_seconds
    )
    deduplicated: list[float] = []
    for timestamp in ordered:
        rounded = round(timestamp, 6)
        if not deduplicated or rounded - deduplicated[-1] > 0.05:
            deduplicated.append(rounded)
    if len(deduplicated) <= max_frames:
        return tuple(deduplicated)
    if max_frames == 1:
        return (deduplicated[len(deduplicated) // 2],)
    indexes = tuple(
        round(index * (len(deduplicated) - 1) / (max_frames - 1))
        for index in range(max_frames)
    )
    return tuple(deduplicated[index] for index in indexes)


def _read_memory_snapshot() -> tuple[int, int]:
    try:
        values: dict[str, int] = {}
        with open("/proc/meminfo", encoding="ascii") as source:
            for line in source:
                key, raw_value = line.split(":", maxsplit=1)
                if key in {"MemAvailable", "SwapFree"}:
                    values[key] = int(raw_value.split()[0]) * 1024
        if "MemAvailable" not in values or "SwapFree" not in values:
            raise ValueError("Required Linux memory counters are missing.")
        return values["MemAvailable"], values["SwapFree"]
    except (OSError, ValueError) as exc:
        raise VisualResourceLimitError(
            "Cannot verify Linux memory state; visual inference is refused."
        ) from exc
