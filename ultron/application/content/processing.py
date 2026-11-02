from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import BinaryIO, Protocol

from .limits import MAX_SOURCE_BYTES


class ContentBlobStore(Protocol):
    @property
    def clip_staging_directory(self) -> Path: ...

    def put_input(self, key: str, stream: BinaryIO) -> tuple[str, int]: ...

    def read_verified_input(self, key: str, expected_sha256: str) -> bytes: ...

    def put_output(self, job_id: str, content: bytes) -> tuple[str, str, int]: ...

    def read_output(self, key: str, expected_sha256: str) -> bytes: ...

    def stage_clip_source(self, render_id: str, content: bytes) -> Path: ...

    def create_clip_staging_path(self, render_id: str) -> Path: ...

    def put_clip_output(self, render_id: str, staged_path: Path) -> tuple[str, str, int]: ...

    def read_clip_output(self, key: str, expected_sha256: str) -> bytes: ...

    def delete_clip_output(self, key: str) -> None: ...

    def delete_clip_staging(self, staged_path: Path) -> None: ...

    def delete_input(self, key: str) -> None: ...

    def delete_output(self, key: str) -> None: ...

    def cleanup_orphans(
        self,
        referenced_output_keys: frozenset[str],
        protected_clip_render_ids: frozenset[str] = frozenset(),
    ) -> None: ...


class ContentProcessor(Protocol):
    def process(self, content: bytes, *, max_output_bytes: int) -> bytes: ...


class LocalTemporaryContentStore:
    """Bounded owner-only local storage for registered CRE media and artifacts."""

    MAX_INPUT_BYTES = MAX_SOURCE_BYTES
    MAX_OUTPUT_BYTES = 64 * 1024
    MAX_CLIP_OUTPUT_BYTES = 16 * 1024 * 1024
    _KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self._root.is_symlink() or not self._root.is_dir():
            raise ValueError("Content storage root must be a real directory.")
        os.chmod(self._root, 0o700)
        self._root = self._root.resolve(strict=True)
        for name in ("inputs", "outputs", ".staging"):
            directory = self._root / name
            directory.mkdir(mode=0o700, exist_ok=True)
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError("Managed content subdirectories must be real directories.")
            os.chmod(directory, 0o700)
        self._lock = threading.RLock()

    @property
    def clip_staging_directory(self) -> Path:
        return self._root / ".staging"

    def _path(self, key: str, *, namespace: str) -> Path:
        if (
            not isinstance(key, str)
            or not self._KEY_RE.fullmatch(key)
            or ".." in key
        ):
            raise ValueError("Content storage requires an opaque registered key.")
        if namespace not in {"inputs", "outputs"}:
            raise ValueError("Unsupported managed content namespace.")
        directory = self._root / namespace
        path = directory / key
        if path.parent != directory:
            raise ValueError("Content storage key escaped its root.")
        return path

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write(
        self,
        key: str,
        content: bytes,
        max_bytes: int,
        *,
        namespace: str,
    ) -> tuple[str, int]:
        if not isinstance(content, bytes) or len(content) > max_bytes:
            raise ValueError("Content exceeds the configured storage size limit.")
        destination = self._path(key, namespace=namespace)
        digest = hashlib.sha256(content).hexdigest()
        staged_path: Path | None = None
        linked = False
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".stage-",
                dir=self._root / ".staging",
                delete=False,
            ) as staged:
                staged_path = Path(staged.name)
                os.chmod(staged_path, 0o600)
                staged.write(content)
                staged.flush()
                os.fsync(staged.fileno())
            with self._lock:
                os.link(staged_path, destination)
                linked = True
                staged_path.unlink()
                self._sync_directory(destination.parent)
                self._sync_directory(self._root / ".staging")
            return digest, len(content)
        except Exception as primary:
            try:
                if staged_path is not None:
                    staged_path.unlink(missing_ok=True)
                if linked:
                    destination.unlink(missing_ok=True)
            except Exception as cleanup:
                raise RuntimeError(
                    f"Content storage failed ({primary}); staged-file cleanup failed "
                    f"({cleanup})."
                ) from primary
            raise

    def put_input(self, key: str, stream: BinaryIO) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        chunks: list[bytes] = []
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            if not isinstance(chunk, bytes):
                raise TypeError("Content input must be a binary stream.")
            size += len(chunk)
            if size > self.MAX_INPUT_BYTES:
                raise ValueError("Content input exceeds the configured size limit.")
            digest.update(chunk)
            chunks.append(chunk)
        stored_digest, stored_size = self._write(
            key,
            b"".join(chunks),
            self.MAX_INPUT_BYTES,
            namespace="inputs",
        )
        if stored_digest != digest.hexdigest() or stored_size != size:
            self.delete_input(key)
            raise RuntimeError("Stored content failed its input hash verification.")
        return stored_digest, stored_size

    def read_verified_input(self, key: str, expected_sha256: str) -> bytes:
        path = self._path(key, namespace="inputs")
        with self._lock:
            if path.is_symlink() or not path.is_file():
                raise FileNotFoundError("Registered content bytes are unavailable.")
            with path.open("rb") as source:
                content = source.read(self.MAX_INPUT_BYTES + 1)
        if len(content) > self.MAX_INPUT_BYTES:
            raise ValueError("Stored content exceeds the configured size limit.")
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise ValueError("Stored content does not match the registered SHA-256.")
        return content

    def put_output(self, job_id: str, content: bytes) -> tuple[str, str, int]:
        key = f"output-{job_id}"
        digest, size = self._write(
            key,
            content,
            self.MAX_OUTPUT_BYTES,
            namespace="outputs",
        )
        return key, digest, size

    def read_output(self, key: str, expected_sha256: str) -> bytes:
        if not key.startswith("output-"):
            raise ValueError("Output reads require a registered output key.")
        path = self._path(key, namespace="outputs")
        with self._lock:
            if path.is_symlink() or not path.is_file():
                raise FileNotFoundError("Processing output is unavailable.")
            with path.open("rb") as source:
                content = source.read(self.MAX_OUTPUT_BYTES + 1)
        if len(content) > self.MAX_OUTPUT_BYTES:
            raise ValueError("Stored output exceeds the configured size limit.")
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise ValueError("Stored output does not match its registered SHA-256.")
        return content

    def create_clip_staging_path(self, render_id: str) -> Path:
        if not self._KEY_RE.fullmatch(render_id) or ".." in render_id:
            raise ValueError("Clip render ID must be a bounded opaque identifier.")
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".clip-{render_id}-output-",
            suffix=".mp4",
            dir=self._root / ".staging",
            delete=False,
        ) as staged:
            os.chmod(staged.name, 0o600)
            return Path(staged.name)

    def stage_clip_source(self, render_id: str, content: bytes) -> Path:
        if not self._KEY_RE.fullmatch(render_id) or ".." in render_id:
            raise ValueError("Clip render ID must be a bounded opaque identifier.")
        if not isinstance(content, bytes) or not content or len(content) > self.MAX_INPUT_BYTES:
            raise ValueError("Registered clip source must be non-empty and at most 32 MiB.")
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".clip-{render_id}-source-",
            suffix=".media",
            dir=self._root / ".staging",
            delete=False,
        ) as staged:
            os.chmod(staged.name, 0o600)
            staged.write(content)
            staged.flush()
            os.fsync(staged.fileno())
            return Path(staged.name)

    def put_clip_output(
        self,
        render_id: str,
        staged_path: Path,
    ) -> tuple[str, str, int]:
        if (
            not isinstance(staged_path, Path)
            or staged_path.parent != self._root / ".staging"
            or not re.fullmatch(
                r"\.clip-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}-output-[A-Za-z0-9_-]+\.mp4",
                staged_path.name,
            )
            or staged_path.suffix != ".mp4"
            or staged_path.is_symlink()
            or not staged_path.is_file()
        ):
            raise ValueError("Clip output must be a managed staging file.")
        size = staged_path.stat().st_size
        if size <= 0 or size > self.MAX_CLIP_OUTPUT_BYTES:
            raise ValueError("Rendered clip exceeds the configured output size limit.")
        content = staged_path.read_bytes()
        key = f"clip-{render_id}"
        digest, stored_size = self._write(
            key,
            content,
            self.MAX_CLIP_OUTPUT_BYTES,
            namespace="outputs",
        )
        if stored_size != size:
            self.delete_clip_output(key)
            raise RuntimeError("Stored clip size did not match its staged file.")
        return key, digest, stored_size

    def read_clip_output(self, key: str, expected_sha256: str) -> bytes:
        if not key.startswith("clip-"):
            raise ValueError("Clip reads require a registered clip output key.")
        path = self._path(key, namespace="outputs")
        with self._lock:
            if path.is_symlink() or not path.is_file():
                raise FileNotFoundError("Rendered clip is unavailable.")
            with path.open("rb") as source:
                content = source.read(self.MAX_CLIP_OUTPUT_BYTES + 1)
        if len(content) > self.MAX_CLIP_OUTPUT_BYTES:
            raise ValueError("Stored clip exceeds the configured output size limit.")
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise ValueError("Stored clip does not match its registered SHA-256.")
        return content

    def delete_clip_output(self, key: str) -> None:
        if not key.startswith("clip-"):
            raise ValueError("Clip deletion requires a registered clip output key.")
        self._delete(key, namespace="outputs")

    def delete_clip_staging(self, staged_path: Path) -> None:
        if (
            not isinstance(staged_path, Path)
            or staged_path.parent != self._root / ".staging"
            or not re.fullmatch(
                r"\.clip-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}-(?:source-[A-Za-z0-9_-]+\.media|output-[A-Za-z0-9_-]+\.mp4)",
                staged_path.name,
            )
        ):
            raise ValueError("Clip cleanup requires a managed staging file.")
        with self._lock:
            if staged_path.is_symlink():
                raise RuntimeError("Refusing to delete a symbolic-link staging file.")
            if staged_path.exists():
                staged_path.unlink()
                self._sync_directory(self._root / ".staging")

    def delete_input(self, key: str) -> None:
        self._delete(key, namespace="inputs")

    def delete_output(self, key: str) -> None:
        self._delete(key, namespace="outputs")

    def _delete(self, key: str, *, namespace: str) -> None:
        path = self._path(key, namespace=namespace)
        with self._lock:
            if path.is_symlink():
                raise RuntimeError("Refusing to delete a symbolic-link content object.")
            if path.exists():
                path.unlink()
                self._sync_directory(path.parent)

    def cleanup_orphans(
        self,
        referenced_output_keys: frozenset[str],
        protected_clip_render_ids: frozenset[str] = frozenset(),
    ) -> None:
        protected_clip_output_keys = frozenset(
            f"clip-{render_id}" for render_id in protected_clip_render_ids
        )
        with self._lock:
            for path in (self._root / ".staging").iterdir():
                if path.is_symlink():
                    raise RuntimeError("Unexpected symbolic link in content staging.")
                match = re.fullmatch(
                    r"\.clip-(.+)-(?:source-[A-Za-z0-9_-]+\.media|output-[A-Za-z0-9_-]+\.mp4)",
                    path.name,
                )
                if match is not None and match.group(1) in protected_clip_render_ids:
                    continue
                path.unlink()
                self._sync_directory(self._root / ".staging")
            for path in (self._root / "outputs").iterdir():
                if path.is_symlink():
                    raise RuntimeError("Unexpected symbolic link in content outputs.")
                if (
                    path.name not in referenced_output_keys
                    and path.name not in protected_clip_output_keys
                ):
                    path.unlink()
                    self._sync_directory(self._root / "outputs")


class SyntheticContentProcessor:
    """Deterministic synthetic-byte transform; it does not decode media."""

    INPUT_PREFIX = b"ULTRON-SYNTHETIC:"
    OUTPUT_PREFIX = b"ULTRON-SYNTHETIC-RESULT:"

    def process(self, content: bytes, *, max_output_bytes: int) -> bytes:
        if not content.startswith(self.INPUT_PREFIX):
            raise ValueError("Input is not a supported synthetic test payload.")
        output = self.OUTPUT_PREFIX + hashlib.sha256(content).hexdigest().encode("ascii")
        if len(output) > max_output_bytes:
            raise ValueError("Synthetic processor output exceeds its size limit.")
        return output
