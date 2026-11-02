from ultron.improvement.domain.models import (
    ImprovementProposal,
)


class ImprovementPlanner:
    """
    Creates implementation plans
    based on improvement strategy.

    Responsibilities:
    - Decide operation type
    - Select implementation approach
    - Estimate risk
    - Prepare execution steps
    """

    def create_plan(
        self,
        proposal: ImprovementProposal,
        context=None,
    ) -> dict:

        context = context or {}

        strategy = context.get(
            "strategy"
        )

        action = getattr(
            strategy,
            "action",
            None,
        )


        operation = self._determine_operation(
            action
        )


        risk = self._estimate_risk(
            proposal.target,
            operation,
        )


        if operation == "create":

            steps = [
                "Design isolated component",
                "Generate required files",
                "Register capability",
                "Run validation tests",
            ]

            strategy_description = (
                "Create isolated component "
                "instead of modifying existing core."
            )


        elif operation == "extend":

            steps = [
                "Inspect existing capability",
                "Design extension",
                "Generate changes",
                "Validate integration",
            ]

            strategy_description = (
                "Extend existing capability safely."
            )


        else:

            steps = [
                "Inspect current implementation",
                "Generate improvement",
                "Run validation tests",
                "Accept or reject change",
            ]

            strategy_description = (
                f"Analyze and improve "
                f"{proposal.target}."
            )


        return {

            "target": proposal.target,

            "problem": proposal.reason,

            "operation": operation,

            "strategy": strategy_description,

            "risk": risk,

            "context": context,

            "metadata": {

                "confidence": proposal.confidence,

                "source": proposal.evidence.get(
                    "source",
                    "unknown",
                ),

            },

            "steps": steps,

        }



    def _determine_operation(
        self,
        action,
    ) -> str:
        """
        Converts strategy decision
        into execution operation.
        """

        strategy_map = {

            "create_new_component":
                "create",

            "extend_existing_component":
                "extend",

            "modify_existing":
                "modify",

            "replace_component":
                "replace",

        }


        return strategy_map.get(
            action,
            "modify",
        )



    def _estimate_risk(
        self,
        target: str,
        operation: str,
    ) -> str:
        """
        Estimates implementation risk.
        """

        critical_components = [

            "container",

            "memory",

            "reasoning",

            "execution",

            "self_awareness",

            "security",

            "kernel",

        ]


        target = target.lower()


        for component in critical_components:

            if component in target:

                return "HIGH"


        if operation == "replace":

            return "HIGH"


        if operation == "modify":

            return "MEDIUM"


        return "LOW"