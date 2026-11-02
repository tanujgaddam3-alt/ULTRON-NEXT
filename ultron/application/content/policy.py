from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ultron.domain.content import (
    AssetState,
    ContentPlatform,
    PlatformDecisionState,
    RightsAction,
    RightsEvaluation,
    RightsGrant,
    RightsState,
    RightsUse,
    SourceAsset,
)


class PlatformPolicyRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    description: str
    official_source: str
    required_value: bool = True

    @field_validator("rule_id", "description", "official_source")
    @classmethod
    def require_rule_text(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 2000:
            raise ValueError("Platform policy rule fields must be bounded and non-empty.")
        return value

    @field_validator("official_source")
    @classmethod
    def require_https_source(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("Policy evidence references must use HTTPS.")
        return value


class PlatformPolicyInputs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: ContentPlatform
    version: str
    rules: tuple[PlatformPolicyRule, ...] = ()
    official_requirements_verified: bool = False
    verified_by: str | None = None
    verified_at: datetime | None = None

    @model_validator(mode="after")
    def validate_verification(self) -> PlatformPolicyInputs:
        if self.official_requirements_verified:
            if (
                not self.verified_by
                or self.verified_at is None
                or not self.rules
                or self.verified_at.tzinfo is None
                or self.verified_at.utcoffset() is None
            ):
                raise ValueError(
                    "Enabled platform policy inputs require a reviewer, time, "
                    "official sources, and at least one rule."
                )
        if len({rule.rule_id for rule in self.rules}) != len(self.rules):
            raise ValueError("Platform policy rule IDs must be unique.")
        return self


class GrantRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    grant: RightsGrant
    claim_state: str
    evidence_decision: str | None
    evidence_integrity_valid: bool
    agreement_status: str = "unverified"
    agreement_integrity_valid: bool = False


class PlatformRightsAdapter(Protocol):
    platform: ContentPlatform
    policy_inputs: PlatformPolicyInputs

    def evaluate(
        self,
        use: RightsUse,
        records: tuple[GrantRecord, ...],
        now: datetime,
    ) -> tuple[RightsState, tuple[str, ...], tuple[str, ...]]: ...

    def evaluate_platform_compliance(
        self,
        use: RightsUse,
    ) -> PlatformDecisionState: ...


class _BasePlatformRightsAdapter:
    platform: ContentPlatform
    policy_inputs: PlatformPolicyInputs

    def __init__(self, policy_inputs: PlatformPolicyInputs | None = None) -> None:
        self.policy_inputs = policy_inputs or PlatformPolicyInputs(
            platform=self.platform,
            version="unverified",
        )
        if self.policy_inputs.platform != self.platform:
            raise ValueError("Platform policy inputs do not match their adapter.")

    def evaluate(
        self,
        use: RightsUse,
        records: tuple[GrantRecord, ...],
        now: datetime,
    ) -> tuple[RightsState, tuple[str, ...], tuple[str, ...]]:
        if use.platform != self.platform:
            return RightsState.BLOCKED, (), ("platform_adapter_mismatch",)

        asset_records = tuple(
            record for record in records if record.grant.asset_id == use.asset_id
        )
        if not asset_records:
            return RightsState.UNKNOWN, (), ("no_verified_grants",)

        same_platform = tuple(
            record
            for record in asset_records
            if record.grant.terms.platform == self.platform
        )
        if not same_platform:
            return (
                RightsState.BLOCKED,
                (),
                ("grant_does_not_cover_requested_platform",),
            )

        if any(not record.evidence_integrity_valid for record in same_platform):
            return (
                RightsState.BLOCKED,
                (),
                ("evidence_integrity_failure",),
            )
        if any(
            record.claim_state in {"ambiguous", "rejected"}
            or record.evidence_decision in {"ambiguous", "rejected"}
            for record in same_platform
        ):
            return (
                RightsState.BLOCKED,
                (),
                ("conflicting_or_rejected_authority_evidence",),
            )

        qualified: list[str] = []
        reasons: set[str] = set()
        expired = False
        revoked = False
        review_required = False
        for record in same_platform:
            grant = record.grant
            terms = grant.terms

            if not record.agreement_integrity_valid:
                reasons.add("agreement_missing_or_integrity_failure")
                continue
            if record.agreement_status == "suspended":
                reasons.add("agreement_suspended")
                continue
            if record.agreement_status == "revoked":
                revoked = True
                reasons.add("agreement_revoked")
                continue
            if record.agreement_status == "expired":
                expired = True
                reasons.add("agreement_expired")
                continue
            if record.agreement_status != "approved":
                review_required = True
                reasons.add("agreement_not_approved")
                continue
            if record.claim_state != "verified" or record.evidence_decision != "verified":
                review_required = True
                reasons.add("authority_evidence_not_verified")
                continue
            if grant.revoked:
                revoked = True
                reasons.add("grant_revoked")
                continue
            if now < terms.valid_from:
                reasons.add("grant_not_yet_valid")
                continue
            if now >= terms.valid_until:
                expired = True
                reasons.add("grant_expired")
                continue
            if terms.account_scope not in {"*", use.account_id}:
                reasons.add("account_or_channel_not_permitted")
                continue
            if "*" not in terms.territories and use.territory not in terms.territories:
                reasons.add("territory_not_permitted")
                continue
            required_actions = set(use.actions)
            if not required_actions.issubset(terms.permitted_actions):
                reasons.add("intended_action_not_permitted")
                continue
            if use.duration_seconds > terms.max_duration_seconds:
                reasons.add("duration_limit_exceeded")
                continue
            if terms.attribution_required and (
                not use.attribution_text
                or terms.attribution_text not in use.attribution_text
            ):
                reasons.add("required_attribution_missing")
                continue
            qualified.append(grant.grant_id)

        if qualified:
            return RightsState.CLEARED, tuple(sorted(qualified)), ()
        if "evidence_integrity_failure" in reasons:
            state = RightsState.BLOCKED
        elif (
            "ambiguous_authority_evidence" in reasons
            or "rejected_authority_evidence" in reasons
        ):
            state = RightsState.BLOCKED
        elif revoked:
            state = RightsState.REVOKED
        elif expired:
            state = RightsState.EXPIRED
        elif review_required:
            state = RightsState.REVIEW_REQUIRED
        else:
            state = RightsState.BLOCKED
        return state, (), tuple(sorted(reasons))

    def evaluate_platform_compliance(
        self,
        use: RightsUse,
    ) -> PlatformDecisionState:
        inputs = self.policy_inputs
        if not inputs.official_requirements_verified:
            return PlatformDecisionState.UNKNOWN
        unknown = False
        for rule in inputs.rules:
            fact = use.platform_policy_facts.get(rule.rule_id)
            if fact is None:
                unknown = True
            elif fact != rule.required_value:
                return PlatformDecisionState.NON_COMPLIANT
        if unknown:
            return PlatformDecisionState.UNKNOWN
        return PlatformDecisionState.COMPLIANT


class YouTubeRightsAdapter(_BasePlatformRightsAdapter):
    """Evaluates explicit YouTube grant scope, not YouTube policy compliance."""

    platform = ContentPlatform.YOUTUBE


class InstagramRightsAdapter(_BasePlatformRightsAdapter):
    """Evaluates explicit Instagram grant scope, not Instagram policy compliance."""

    platform = ContentPlatform.INSTAGRAM


class ContentRightsPolicy:
    """Combines copyright evidence and platform-specific grant scope decisions."""

    VERSION = "content-rights-v1"

    def __init__(
        self,
        *,
        youtube: YouTubeRightsAdapter | None = None,
        instagram: InstagramRightsAdapter | None = None,
    ) -> None:
        self._adapters: dict[ContentPlatform, PlatformRightsAdapter] = {
            ContentPlatform.YOUTUBE: youtube or YouTubeRightsAdapter(),
            ContentPlatform.INSTAGRAM: instagram or InstagramRightsAdapter(),
        }

    def evaluate(
        self,
        *,
        decision_id: str,
        asset: SourceAsset,
        use: RightsUse,
        records: tuple[GrantRecord, ...],
        evidence_states: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> RightsEvaluation:
        evaluated_at = (now or datetime.now(UTC)).astimezone(UTC)
        if asset.asset_id != use.asset_id:
            raise ValueError("Rights use does not reference the supplied asset.")
        if asset.state != AssetState.ACTIVE:
            copyright_state = RightsState.BLOCKED
            platform_rights_state = RightsState.BLOCKED
            reasons = ("asset_not_active",)
            grant_ids: tuple[str, ...] = ()
        elif asset.content_sha256 != use.expected_asset_sha256:
            copyright_state = RightsState.BLOCKED
            platform_rights_state = RightsState.BLOCKED
            reasons = ("asset_hash_changed",)
            grant_ids = ()
        elif any(state in {"ambiguous", "rejected"} for state in evidence_states):
            copyright_state = RightsState.BLOCKED
            platform_rights_state = RightsState.BLOCKED
            reasons = ("conflicting_or_rejected_authority_evidence",)
            grant_ids = ()
        elif evidence_states and any(state == "pending" for state in evidence_states):
            copyright_state = RightsState.REVIEW_REQUIRED
            platform_rights_state = RightsState.REVIEW_REQUIRED
            reasons = ("authority_evidence_not_verified",)
            grant_ids = ()
        elif not records and evidence_states:
            copyright_state = RightsState.UNKNOWN
            platform_rights_state = RightsState.UNKNOWN
            reasons = ("no_verified_grants",)
            grant_ids = ()
        else:
            adapter = self._adapters[use.platform]
            platform_rights_state, grant_ids, reasons = adapter.evaluate(
                use,
                records,
                evaluated_at,
            )
            copyright_state = self._evaluate_copyright_state(
                records,
                use,
                evaluated_at,
                platform_rights_state,
            )

        adapter = self._adapters[use.platform]
        monetization_permission = (
            RightsAction.MONETIZE in use.actions
            and platform_rights_state == RightsState.CLEARED
        )
        return RightsEvaluation(
            decision_id=decision_id,
            asset_id=asset.asset_id,
            use=use,
            copyright_state=copyright_state,
            platform_rights_state=platform_rights_state,
            platform_compliance_state=adapter.evaluate_platform_compliance(use),
            publishing_authorization_state=PlatformDecisionState.NOT_AUTHORIZED,
            monetization_permission=monetization_permission,
            monetization_eligibility_state=PlatformDecisionState.UNKNOWN,
            applicable_grant_ids=grant_ids,
            reason_codes=reasons,
            policy_version=(
                f"{self.VERSION}:{adapter.policy_inputs.version}"
            ),
            evaluated_at=evaluated_at,
        )

    @staticmethod
    def _evaluate_copyright_state(
        records: tuple[GrantRecord, ...],
        use: RightsUse,
        now: datetime,
        platform_state: RightsState,
    ) -> RightsState:
        if not records:
            return RightsState.UNKNOWN
        if any(not record.evidence_integrity_valid for record in records):
            return RightsState.BLOCKED
        if any(
            record.claim_state in {"ambiguous", "rejected"}
            or record.evidence_decision in {"ambiguous", "rejected"}
            for record in records
        ):
            return RightsState.BLOCKED

        verified_records = tuple(
            record
            for record in records
            if record.claim_state == "verified"
            and record.evidence_decision == "verified"
            and not record.grant.revoked
            and record.grant.terms.valid_from <= now < record.grant.terms.valid_until
        )
        if not verified_records:
            if any(
                record.grant.revoked
                for record in records
                if record.claim_state == "verified"
                and record.evidence_decision == "verified"
            ):
                return RightsState.REVOKED
            if any(
                record.grant.terms.valid_until <= now
                for record in records
                if record.claim_state == "verified"
                and record.evidence_decision == "verified"
            ):
                return RightsState.EXPIRED
            if any(
                record.claim_state != "verified"
                or record.evidence_decision != "verified"
                for record in records
            ):
                return RightsState.REVIEW_REQUIRED
            return RightsState.UNKNOWN

        for record in verified_records:
            terms = record.grant.terms
            if (
                use.actions.issubset(terms.permitted_actions)
                and use.duration_seconds <= terms.max_duration_seconds
                and (
                    not terms.attribution_required
                    or (
                        use.attribution_text is not None
                        and terms.attribution_text in use.attribution_text
                    )
                )
            ):
                return RightsState.CLEARED
        if platform_state in {RightsState.EXPIRED, RightsState.REVOKED}:
            return platform_state
        return RightsState.BLOCKED
