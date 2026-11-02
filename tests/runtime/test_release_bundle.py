import hashlib
import zipfile

import pytest

from scripts.ultron_release_bundle import (
    BundleError,
    build_bundle,
    restore_bundle,
    verify_bundle,
)


def test_explicit_release_bundle_is_reproducible_verifiable_and_restorable(tmp_path):
    root = tmp_path / "source"
    (root / "scripts").mkdir(parents=True)
    (root / "interface").mkdir()
    (root / "scripts/manager.py").write_text("print('manager')\n", encoding="utf-8")
    (root / "interface/index.html").write_text("<title>test</title>\n", encoding="utf-8")
    file_list = tmp_path / "release-files.txt"
    file_list.write_text(
        "interface/index.html\nscripts/manager.py\n",
        encoding="utf-8",
    )

    first = tmp_path / "release-a.zip"
    second = tmp_path / "release-b.zip"
    first_result = build_bundle(root, file_list, first, "isolated-test")
    second_result = build_bundle(root, file_list, second, "isolated-test")

    assert first.read_bytes() == second.read_bytes()
    assert first_result["sha256"] == second_result["sha256"]
    assert hashlib.sha256(first.read_bytes()).hexdigest() == first_result["sha256"]
    assert first.with_name(first.name + ".sha256").read_text().startswith(
        first_result["sha256"]
    )
    manifest = verify_bundle(first)
    assert [item["path"] for item in manifest["files"]] == [
        "interface/index.html",
        "scripts/manager.py",
    ]

    destination = tmp_path / "restored"
    result = restore_bundle(first, destination)
    assert result["verifiedFiles"] == 2
    assert (destination / "scripts/manager.py").read_text() == "print('manager')\n"
    assert (destination / "interface/index.html").read_text() == "<title>test</title>\n"
    assert (destination / "scripts/manager.py").stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize(
    "filename",
    [
        "../outside.py",
        "/absolute.py",
        ".env.production",
        "data/live.db-wal",
        ".venv/bin/python",
        "module/__pycache__/cached.pyc",
        "cache/generated.py",
    ],
)
def test_release_bundle_rejects_forbidden_or_unsafe_paths(tmp_path, filename):
    root = tmp_path / "root"
    root.mkdir()
    listed = tmp_path / "files.txt"
    listed.write_text(filename + "\n", encoding="utf-8")

    with pytest.raises(BundleError):
        build_bundle(root, listed, tmp_path / "unsafe.zip", "test")


def test_release_bundle_verification_rejects_member_tampering(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    source = root / "safe.py"
    source.write_text("original\n", encoding="utf-8")
    file_list = tmp_path / "files.txt"
    file_list.write_text("safe.py\n", encoding="utf-8")
    archive_path = tmp_path / "release.zip"
    build_bundle(root, file_list, archive_path, "tamper-test")

    tampered_path = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive_path) as original, zipfile.ZipFile(
        tampered_path, "w", compression=zipfile.ZIP_STORED
    ) as tampered:
        for info in original.infolist():
            contents = original.read(info.filename)
            if info.filename == "safe.py":
                contents = b"changed\n"
            tampered.writestr(info, contents)

    with pytest.raises(BundleError, match="failed manifest verification"):
        verify_bundle(tampered_path)


def test_release_metadata_is_verified_without_becoming_compatibility_claim(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "app.py").write_text("print('ok')\n", encoding="utf-8")
    file_list = tmp_path / "files.txt"
    file_list.write_text("app.py\n", encoding="utf-8")
    archive = tmp_path / "release.zip"

    build_bundle(
        root,
        file_list,
        archive,
        "v10-candidate",
        source_revision="deadbeef",
        supported_cre_schemas=[10],
        config_schema_version=3,
        rollback_release_id="v10-rollback",
        rollback_instructions="Restore only an independently verified compatible bundle.",
    )

    manifest = verify_bundle(archive)
    assert manifest["sourceRevision"] == "deadbeef"
    assert manifest["supportedCreSchemas"] == [10]
    assert manifest["configSchemaVersion"] == 3
    assert manifest["rollbackReleaseId"] == "v10-rollback"
