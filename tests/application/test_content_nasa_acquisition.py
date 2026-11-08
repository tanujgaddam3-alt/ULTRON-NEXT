from __future__ import annotations

import hashlib
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ultron.application.content.nasa import (
    NASAImagesAcquisitionAdapter,
    NASAVideoCandidate,
    create_nasa_trusted_issuer,
    register_nasa_trusted_issuer,
)
from ultron.application.content.rights_verification import (
    EvidenceAssuranceLevel,
    RightsVerificationDecision,
    TrustedIssuerRegistry,
)


def _candidate(*, nasa_id: str = "GSFC_20240101_TEST") -> NASAVideoCandidate:
    return NASAVideoCandidate(
        nasa_id=nasa_id,
        title="NASA scientific visualization",
        center="GSFC",
        description="A NASA scientific visualization.",
        metadata_url=f"https://images-api.nasa.gov/search?nasa_id={nasa_id}",
        canonical_url=f"https://images-api.nasa.gov/search?nasa_id={nasa_id}",
        asset_url=f"https://images-assets.nasa.gov/video/{nasa_id}/{nasa_id}~large.mp4",
        copyright_notice="For copyright and restrictions refer to nasa.gov",
        photographer="NASA",
    )


def test_nasa_issuer_registry_is_explicit_and_revocable() -> None:
    with tempfile.TemporaryDirectory() as directory:
        registry = TrustedIssuerRegistry(persistence_path=Path(directory) / "issuers.json")
        now = datetime.now(UTC)
        issuer = register_nasa_trusted_issuer(registry, now=now - timedelta(minutes=1))
        assert registry.get(issuer.issuer_id, now=now) is not None

        revoked = registry.revoke(issuer.issuer_id, reason="guidance revoked", now=now)
        assert revoked.revoked
        assert registry.get(issuer.issuer_id, now=now) is None


def test_nasa_origin_verification_approves_editorial_usage_without_advertising_claims() -> None:
    with tempfile.TemporaryDirectory() as directory:
        registry = TrustedIssuerRegistry(persistence_path=Path(directory) / "issuers.json")
        now = datetime.now(UTC)
        register_nasa_trusted_issuer(registry, now=now - timedelta(minutes=1))
        adapter = NASAImagesAcquisitionAdapter(issuer_registry=registry)
        candidate = _candidate()
        metadata = {
            "AVAIL:NASAID": candidate.nasa_id,
            "AVAIL:Owner": "NASA",
            "AVAIL:Center": "GSFC",
            "AVAIL:Description": "NASA scientific visualization.",
            "QuickTime:Copyright": (
                "For copyright and restrictions refer to "
                "http://www.nasa.gov/multimedia/guidelines/index.html"
            ),
        }
        raw = b"nasa metadata snapshot"
        result = adapter.verify_candidate(
            candidate,
            metadata,
            {"metadata_sha256": hashlib.sha256(raw).hexdigest()},
            retrieved_at=now,
        )
        assert result.decision == RightsVerificationDecision.APPROVED
        assert result.reasons == ("complete_verified_rights_evidence",)
        assert all(
            evidence.assurance_level == EvidenceAssuranceLevel.A3_INSTITUTIONAL
            for evidence in result.license_evidence + result.rights_holder_evidence + result.authority_evidence
        )


@pytest.mark.parametrize(
    ("description", "reason"),
    [
        ("video includes an interview with an identifiable NASA employee", "third_party_material_restrictions_found"),
        ("NASA and ESA partner mission animation", "third_party_material_restrictions_found"),
        ("third-party copyrighted soundtrack", "third_party_material_restrictions_found"),
        ("mission control crew footage", "third_party_material_restrictions_found"),
    ],
)
def test_nasa_restrictions_remain_blocked(description: str, reason: str) -> None:
    with tempfile.TemporaryDirectory() as directory:
        registry = TrustedIssuerRegistry(persistence_path=Path(directory) / "issuers.json")
        now = datetime.now(UTC)
        register_nasa_trusted_issuer(registry, now=now - timedelta(minutes=1))
        adapter = NASAImagesAcquisitionAdapter(issuer_registry=registry)
        candidate = NASAVideoCandidate(
            nasa_id="GSFC_20240101_RESTRICTED",
            title=description,
            center="GSFC",
            description=description,
            metadata_url="https://images-api.nasa.gov/search?nasa_id=GSFC_20240101_RESTRICTED",
            canonical_url="https://images-api.nasa.gov/search?nasa_id=GSFC_20240101_RESTRICTED",
            asset_url="https://images-assets.nasa.gov/video/GSFC_20240101_RESTRICTED/test~large.mp4",
            copyright_notice=None,
            photographer=None,
        )
        metadata = {
            "AVAIL:NASAID": candidate.nasa_id,
            "AVAIL:Owner": "NASA",
            "AVAIL:Center": "GSFC",
            "AVAIL:Description": description,
            "QuickTime:Copyright": "http://www.nasa.gov/multimedia/guidelines/index.html",
        }
        raw = b"nasa restricted metadata snapshot"
        result = adapter.verify_candidate(
            candidate,
            metadata,
            {"metadata_sha256": hashlib.sha256(raw).hexdigest()},
            retrieved_at=now,
        )
        assert result.decision == RightsVerificationDecision.REJECTED
        assert reason in result.reasons


def test_nasa_asset_identity_mismatch_is_rejected() -> None:
    adapter = NASAImagesAcquisitionAdapter()
    candidate = _candidate(nasa_id="GSFC_REAL")
    result = adapter.verify_candidate(
        candidate,
        {
            "AVAIL:NASAID": "GSFC_DIFFERENT",
            "AVAIL:Owner": "NASA",
            "AVAIL:Center": "GSFC",
            "QuickTime:Copyright": "http://www.nasa.gov/multimedia/guidelines/index.html",
        },
        {"metadata_sha256": hashlib.sha256(b"changed").hexdigest()},
        retrieved_at=datetime.now(UTC),
    )
    assert result.decision == RightsVerificationDecision.REJECTED
    assert result.reasons == ("asset_identity_mismatch",)
