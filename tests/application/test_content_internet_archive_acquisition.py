from __future__ import annotations

from pathlib import Path

import pytest

from ultron.application.content.internet_archive import (
    InternetArchiveAcquisitionAdapter,
    InternetArchiveCandidate,
)


def candidate(
    *,
    license_url: str | None = "https://creativecommons.org/publicdomain/zero/1.0/",
):
    return InternetArchiveCandidate(
        identifier="candidate-id",
        title="Candidate Video",
        creator="Candidate Creator",
        license_url=license_url,
        metadata_url="https://archive.org/metadata/candidate-id",
        canonical_url="https://archive.org/details/candidate-id",
    )


def test_cc0_metadata_is_conditionally_held_not_approved():
    adapter = InternetArchiveAcquisitionAdapter()
    evidence = adapter.rights_evidence(candidate())
    assert evidence["decision"] == "CONDITIONAL"
    assert evidence["commercial"] is True
    assert evidence["derivative"] is True
    assert evidence["attribution_required"] is False


@pytest.mark.parametrize(
    "license_url",
    [
        None,
        "https://example.com/license",
        "https://creativecommons.org/licenses/by/4.0/",
    ],
)
def test_unsupported_license_metadata_is_rejected(license_url):
    evidence = InternetArchiveAcquisitionAdapter().rights_evidence(
        candidate(license_url=license_url)
    )
    assert evidence["decision"] == "REJECTED"
    assert evidence["commercial"] is False
    assert evidence["derivative"] is False


def test_video_selection_rejects_oversized_and_unknown_files():
    adapter = InternetArchiveAcquisitionAdapter(max_bytes=1000)
    video = adapter.select_video_file(
        candidate(),
        {
            "files": [
                {"name": "wrong.txt", "size": "100"},
                {"name": "large.mp4", "size": "1001"},
                {"name": "valid.webm", "size": "900"},
            ]
        },
    )
    assert video.name == "valid.webm"
    assert video.size == 900


def test_video_selection_rejects_when_no_supported_file_exists():
    adapter = InternetArchiveAcquisitionAdapter()
    with pytest.raises(ValueError, match="No bounded supported"):
        adapter.select_video_file(
            candidate(),
            {"files": [{"name": "caption.srt", "size": "100"}]},
        )


def test_private_url_redirect_target_is_rejected():
    from ultron.application.content.internet_archive import _validate_public_network_url

    with pytest.raises(ValueError):
        _validate_public_network_url("http://127.0.0.1/file.mp4")
