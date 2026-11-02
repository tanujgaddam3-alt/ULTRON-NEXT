from ultron.security.improvement.models import (
    ImprovementRequest,
)


class ImprovementSecurityBridge:
    """
    Converts improvement proposals into
    secure improvement requests.
    """

    def create_request(
        self,
        proposal,
        risk_level="LOW",
    ):

        return ImprovementRequest(
            component=proposal.target,
            description=proposal.suggestion,
            risk_level=risk_level,
        )
