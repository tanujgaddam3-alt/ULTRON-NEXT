from __future__ import annotations

import json

import pytest

from ultron.application.content.sources import InternetArchiveAdapter


def test_internet_archive_search_json_produces_persistable_source_documents():
    payload = json.dumps(
        {
            "response": {
                "docs": [
                    {
                        "identifier": "sample-cc0-speech",
                        "title": "Sample CC0 Speech",
                        "creator": ["Test Creator"],
                        "licenseurl": "https://creativecommons.org/publicdomain/zero/1.0/",
                        "description": ["A public-domain test recording."],
                    }
                ]
            }
        }
    ).encode()

    documents = InternetArchiveAdapter().parse(
        "https://archive.org/advancedsearch.php?q=mediatype%3Amovies&rows=1",
        payload,
    )

    assert len(documents) == 1
    document = documents[0]
    assert document.provider == "internet_archive"
    assert document.provider_content_id == "sample-cc0-speech"
    assert document.original_url == "https://archive.org/details/sample-cc0-speech"
    assert document.source_identity.startswith("internet_archive:")
    assert "CC0 1.0" in document.summary or "publicdomain/zero" in document.summary


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"{}",
        b'{"response":{"docs":"not-an-array"}}',
    ],
)
def test_internet_archive_search_rejects_malformed_payloads(payload):
    with pytest.raises(ValueError):
        InternetArchiveAdapter().parse(
            "https://archive.org/advancedsearch.php?q=mediatype%3Amovies",
            payload,
        )
