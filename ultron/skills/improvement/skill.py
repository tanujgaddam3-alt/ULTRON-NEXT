from ultron.skills.base import Skill
from ultron.security.permission.rules import deny_when_authorization_unavailable


class ImprovementSkill(Skill):
    """
    Autonomous self-improvement capability.

    Flow:

        Self Awareness
            ↓
        Capability Gap Detection
            ↓
        ImprovementManager
            ↓
        Code Generation
            ↓
        Quality Validation
            ↓
        Safety Validation
            ↓
        Backup
            ↓
        Patch Application
            ↓
        Tests
            ↓
        Capability Registration
            ↓
        Verification
            ↓
        Re-analysis
    """

    name = "improvement"

    description = (
        "Autonomously analyzes ULTRON, implements "
        "missing capabilities, validates changes, "
        "and verifies the resulting system."
    )

    version = "6.0"

    actions = [
        "improve_self",
    ]

    def __init__(
        self,
        improvement_service,
        self_awareness=None,
        agent_orchestrator=None,
        workflow_engine=None,
        workflow_executor=None,
    ):
        self.improvement_service = improvement_service
        self.self_awareness = self_awareness
        self.agent_orchestrator = agent_orchestrator
        self.workflow_engine = workflow_engine
        self.workflow_executor = workflow_executor

    def execute(
        self,
        action: str,
        **kwargs,
    ):
        deny_when_authorization_unavailable("improve_self")
        if action != "improve_self":
            raise ValueError(
                f"Unsupported action: {action}"
            )

        if not self.self_awareness:
            return {
                "success": False,
                "message": "Self awareness unavailable.",
            }

        if not self.improvement_service:
            return {
                "success": False,
                "message": "Improvement service unavailable.",
            }

        report_before = self.self_awareness.analyze()

        recommendations = list(
            report_before.recommendations or []
        )

        if not recommendations:
            return {
                "success": True,
                "type": "improvement_complete",
                "message": "No capability gaps detected.",
                "before": report_before,
                "after": report_before,
                "improvements": [],
            }

        max_improvements = int(
            kwargs.get("max_improvements", 1)
        )

        improvements = []

        for item in recommendations[:max_improvements]:

            capability = item.get(
                "capability",
                "unknown",
            )

            reason = item.get(
                "reason",
                "Capability gap detected.",
            )

            print(
                f"[ULTRON SELF-IMPROVEMENT] "
                f"Implementing: {capability}"
            )

            try:
                result = (
                    self.improvement_service.manager
                    .create_improvement(
                        capability=capability,
                        reason=reason,
                    )
                )

            except Exception as error:
                result = {
                    "accepted": False,
                    "reason": (
                        "Self-improvement execution failed: "
                        f"{error}"
                    ),
                }

            improvements.append(
                {
                    "capability": capability,
                    "reason": reason,
                    "result": result,
                }
            )

            if not result.get("accepted", False):
                break

        report_after = self.self_awareness.analyze()

        remaining = {
            item.get("capability")
            for item in (
                report_after.recommendations or []
            )
        }

        resolved = []

        for item in improvements:
            capability = item["capability"]

            resolved.append(
                {
                    "capability": capability,
                    "resolved": (
                        capability not in remaining
                    ),
                }
            )

        success = bool(improvements) and all(
            item["result"].get("accepted", False)
            for item in improvements
        )

        return {
            "success": success,
            "type": "autonomous_self_improvement",
            "before": report_before,
            "improvements": improvements,
            "verification": resolved,
            "after": report_after,
            "remaining_gaps": list(remaining),
        }

    def validate(
        self,
        action: str,
        **kwargs,
    ):
        return action in self.actions
