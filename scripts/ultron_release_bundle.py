#!/usr/bin/env python3
"""Build and verify an explicitly allowlisted, reproducible release bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Sequence


MANIFEST_NAME = "BUNDLE-MANIFEST.json"
_RELEASE_ID = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_FORBIDDEN_COMPONENTS = {
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    ".backups",
    ".ultron_patch_backups",
    "build",
    "cache",
    "caches",
    "data",
    "dist",
    "logs",
    "node_modules",
    "reference_staging",
    "venv",
}
_FORBIDDEN_SUFFIXES = (
    ".db",
    ".db-shm",
    ".db-wal",
    ".sqlite",
    ".sqlite3",
    ".sqlite-shm",
    ".sqlite-wal",
    ".pem",
    ".key",
)


class BundleError(ValueError):
    """A release bundle is unsafe, malformed, or fails verification."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(raw: str) -> PurePosixPath:
    candidate = PurePosixPath(raw)
    if (
        not raw
        or "\\" in raw
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or any(part.lower() in _FORBIDDEN_COMPONENTS for part in candidate.parts)
        or candidate.name.lower().startswith(".env")
        or candidate.name.lower().endswith(_FORBIDDEN_SUFFIXES)
    ):
        raise BundleError(f"Forbidden or unsafe release path: {raw!r}")
    return candidate


def _read_allowlist(root: Path, list_path: Path) -> list[tuple[PurePosixPath, Path]]:
    try:
        lines = list_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise BundleError("Cannot read the explicitly supplied release file list.") from error
    entries: list[tuple[PurePosixPath, Path]] = []
    seen: set[str] = set()
    for line in lines:
        relative = _safe_relative_path(line.strip())
        key = relative.as_posix()
        if key in seen:
            raise BundleError(f"Duplicate release path: {key}")
        seen.add(key)
        source = root.joinpath(*relative.parts)
        current = root
        if any(
            (current := current / part).is_symlink()
            for part in relative.parts
        ):
            raise BundleError(f"Symlink release paths are not allowed: {key}")
        try:
            resolved = source.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as error:
            raise BundleError(f"Release path is missing or escapes the root: {key}") from error
        if source.is_symlink() or not resolved.is_file():
            raise BundleError(f"Only regular, non-symlink release files are allowed: {key}")
        entries.append((relative, resolved))
    if not entries:
        raise BundleError("The release file list is empty.")
    return sorted(entries, key=lambda item: item[0].as_posix())


def build_bundle(
    root: Path,
    file_list: Path,
    output: Path,
    release_id: str,
    *,
    source_revision: str | None = None,
    supported_cre_schemas: Sequence[int] = (),
    config_schema_version: int | None = None,
    rollback_release_id: str | None = None,
    rollback_instructions: str | None = None,
) -> dict:
    root = root.resolve(strict=True)
    if not _RELEASE_ID.fullmatch(release_id):
        raise BundleError("Release ID must use letters, digits, dot, underscore, or hyphen.")
    checksum_path = output.with_name(output.name + ".sha256")
    if output.exists() or checksum_path.exists():
        raise BundleError("Refusing to overwrite an existing bundle.")
    entries = _read_allowlist(root, file_list)
    files = []
    payloads: list[tuple[str, bytes, int]] = []
    for relative, source in entries:
        data = source.read_bytes()
        mode = stat.S_IMODE(source.stat().st_mode)
        files.append({
            "path": relative.as_posix(),
            "size": len(data),
            "sha256": _sha256(data),
            "executable": bool(mode & 0o111),
        })
        payloads.append((relative.as_posix(), data, mode))
    if source_revision is not None and not re.fullmatch(r"[A-Za-z0-9._/-]{1,160}", source_revision):
        raise BundleError("Source revision is malformed.")
    if any(type(schema) is not int or schema < 1 for schema in supported_cre_schemas):
        raise BundleError("Supported CRE schemas must be positive integers.")
    if len(set(supported_cre_schemas)) != len(supported_cre_schemas):
        raise BundleError("Supported CRE schemas must not contain duplicates.")
    if config_schema_version is not None and type(config_schema_version) is not int:
        raise BundleError("Configuration schema version must be an integer.")
    if rollback_release_id is not None and not _RELEASE_ID.fullmatch(rollback_release_id):
        raise BundleError("Rollback release ID is malformed.")
    if rollback_instructions is not None and not rollback_instructions.strip():
        raise BundleError("Rollback instructions must not be empty.")

    manifest = {
        "format": 1,
        "releaseId": release_id,
        "files": files,
        "sourceRevision": source_revision,
        "supportedCreSchemas": sorted(supported_cre_schemas),
        "configSchemaVersion": config_schema_version,
        "rollbackReleaseId": rollback_release_id,
        "rollbackInstructions": rollback_instructions,
    }
    manifest_bytes = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, data, mode in payloads:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | mode) << 16
                archive.writestr(info, data)
            info = zipfile.ZipInfo(MANIFEST_NAME, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, manifest_bytes)
        os.chmod(temporary, 0o600)
        verify_bundle(temporary)
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    archive_hash = _sha256_file(output)
    checksum_descriptor = os.open(
        checksum_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(checksum_descriptor, "w", encoding="ascii") as checksum_stream:
        checksum_stream.write(f"{archive_hash}  {output.name}\n")
        checksum_stream.flush()
        os.fsync(checksum_stream.fileno())
    return {"archive": str(output), "sha256": archive_hash, "manifest": manifest}


def verify_bundle(archive_path: Path) -> dict:
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            if archive.testzip() is not None:
                raise BundleError("Bundle CRC verification failed.")
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or MANIFEST_NAME not in names:
                raise BundleError("Bundle has duplicate paths or no manifest.")
            if any(info.is_dir() for info in infos):
                raise BundleError("Directory entries are not permitted in release bundles.")
            manifest = json.loads(archive.read(MANIFEST_NAME))
            if (
                not isinstance(manifest, dict)
                or manifest.get("format") != 1
                or not _RELEASE_ID.fullmatch(manifest.get("releaseId", ""))
                or not isinstance(manifest.get("files"), list)
            ):
                raise BundleError("Bundle manifest is malformed.")
            expected: dict[str, dict] = {}
            for item in manifest["files"]:
                if not isinstance(item, dict):
                    raise BundleError("Bundle manifest entry is malformed.")
                relative = _safe_relative_path(item.get("path", ""))
                name = relative.as_posix()
                if name in expected:
                    raise BundleError("Bundle manifest contains duplicate paths.")
                if (
                    not isinstance(item.get("size"), int)
                    or item["size"] < 0
                    or not isinstance(item.get("sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                    or not isinstance(item.get("executable"), bool)
                ):
                    raise BundleError(f"Bundle manifest entry is invalid: {name}")
                expected[name] = item
            if set(names) != set(expected) | {MANIFEST_NAME}:
                raise BundleError("Bundle members do not exactly match the manifest.")
            source_revision = manifest.get("sourceRevision")
            if source_revision is not None and (
                not isinstance(source_revision, str)
                or not re.fullmatch(r"[A-Za-z0-9._/-]{1,160}", source_revision)
            ):
                raise BundleError("Bundle source revision is malformed.")
            schemas = manifest.get("supportedCreSchemas", [])
            if not isinstance(schemas, list) or any(type(schema) is not int or schema < 1 for schema in schemas) or len(set(schemas)) != len(schemas):
                raise BundleError("Bundle CRE compatibility metadata is malformed.")
            config_version = manifest.get("configSchemaVersion")
            if config_version is not None and type(config_version) is not int:
                raise BundleError("Bundle configuration schema version is malformed.")
            rollback_id = manifest.get("rollbackReleaseId")
            if rollback_id is not None and not _RELEASE_ID.fullmatch(rollback_id):
                raise BundleError("Bundle rollback release ID is malformed.")
            rollback_instructions = manifest.get("rollbackInstructions")
            if rollback_instructions is not None and (
                not isinstance(rollback_instructions, str) or not rollback_instructions.strip()
            ):
                raise BundleError("Bundle rollback instructions are malformed.")
            for name, item in expected.items():
                data = archive.read(name)
                if len(data) != item["size"] or _sha256(data) != item["sha256"]:
                    raise BundleError(f"Bundle member failed manifest verification: {name}")
            return manifest
    except BundleError:
        raise
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, KeyError, TypeError) as error:
        raise BundleError("Bundle cannot be read or verified.") from error


def restore_bundle(archive_path: Path, destination: Path) -> dict:
    manifest = verify_bundle(archive_path)
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            for item in manifest["files"]:
                relative = _safe_relative_path(item["path"])
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                data = archive.read(relative.as_posix())
                descriptor = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o755 if item["executable"] else 0o644,
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
        for item in manifest["files"]:
            restored_path = destination.joinpath(
                *_safe_relative_path(item["path"]).parts
            )
            data = restored_path.read_bytes()
            if len(data) != item["size"] or _sha256(data) != item["sha256"]:
                raise BundleError(f"Restored member failed verification: {item['path']}")
    except (OSError, zipfile.BadZipFile, KeyError, TypeError) as error:
        raise BundleError("Restored bundle could not be verified.") from error
    return {"destination": str(destination), "verifiedFiles": len(manifest["files"])}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    build = commands.add_parser("build")
    build.add_argument("--root", type=Path, required=True)
    build.add_argument("--files-from", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--release-id", required=True)
    build.add_argument("--source-revision")
    build.add_argument("--supported-cre-schema", action="append", type=int, default=[])
    build.add_argument("--config-schema-version", type=int)
    build.add_argument("--rollback-release-id")
    build.add_argument("--rollback-instructions")
    verify = commands.add_parser("verify")
    verify.add_argument("--archive", type=Path, required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.action == "build":
        result = build_bundle(
            arguments.root,
            arguments.files_from,
            arguments.output,
            arguments.release_id,
            source_revision=arguments.source_revision,
            supported_cre_schemas=arguments.supported_cre_schema,
            config_schema_version=arguments.config_schema_version,
            rollback_release_id=arguments.rollback_release_id,
            rollback_instructions=arguments.rollback_instructions,
        )
        print(json.dumps(result, sort_keys=True))
    elif arguments.action == "verify":
        print(json.dumps(verify_bundle(arguments.archive), sort_keys=True))
    else:
        print(json.dumps(
            restore_bundle(arguments.archive, arguments.destination),
            sort_keys=True,
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
