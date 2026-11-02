from ultron.improvement.models import (
    ImprovementProposal,
)


class CodeAnalyzer:
    """
    Analyzes failures and creates
    improvement proposals.
    """

    def __init__(
        self,
        advisor=None,
    ):
        self.advisor = advisor

    def analyze_failure(
        self,
        component: str,
        failure: str,
    ) -> ImprovementProposal:

        suggestion = "Generate a safer implementation to resolve the failure."

        if self.advisor:
            suggestion = self.advisor.analyze(
                component,
                failure,
            )

        return ImprovementProposal(
    target=component,

    description=(
        f"Add or improve {component} capability."
    ),

    reason=failure,

    suggestion=suggestion,

    confidence=(
        0.8 if self.advisor else 0.5
    ),

    evidence={
        "failure": failure,
        "component": component,
        "source": "code_analyzer",
    },
)
