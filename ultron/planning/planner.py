from ultron.domain.action_types import ActionType
from ultron.domain.decision import Decision
from ultron.domain.plan import Plan, PlanStep
from ultron.planning.strategy import PlanningStrategy


class Planner:
    """
    Converts decisions into execution plans.

    Planning strategies:
    - sequential
    - hierarchical
    - reactive

    The planner translates canonical cognitive actions into
    executable skill actions while preserving parameters gathered
    during perception and reasoning.
    """

    def create_plan(
        self,
        decision: Decision,
        strategy: PlanningStrategy = PlanningStrategy.SEQUENTIAL,
    ) -> Plan:

        if strategy == PlanningStrategy.HIERARCHICAL:
            return self._hierarchical_plan(decision)

        if strategy == PlanningStrategy.REACTIVE:
            return self._reactive_plan(decision)

        return self._sequential_plan(decision)

    def _sequential_plan(
        self,
        decision: Decision,
    ) -> Plan:

        if decision.action in {
            ActionType.RESPOND,
            ActionType.ASK_CLARIFICATION,
        }:
            return Plan(
                goal=decision.action.value,
                steps=[],
            )

        step = self._create_step(
            decision.action,
            decision=decision,
        )

        return Plan(
            goal=decision.action.value,
            steps=[step],
        )

    def _hierarchical_plan(
        self,
        decision: Decision,
    ) -> Plan:

        steps = [
            PlanStep(
                order=1,
                action="analyze_goal",
                parameters={},
            ),
            self._create_step(
                decision.action,
                decision=decision,
                order=2,
            ),
        ]

        return Plan(
            goal=decision.action.value,
            steps=steps,
        )

    def _reactive_plan(
        self,
        decision: Decision,
    ) -> Plan:

        steps = [
            PlanStep(
                order=1,
                action="check_environment",
                parameters={},
            ),
            self._create_step(
                decision.action,
                decision=decision,
                order=2,
            ),
        ]

        return Plan(
            goal=decision.action.value,
            steps=steps,
        )

    def _create_step(
        self,
        action,
        decision: Decision | None = None,
        order: int = 1,
    ) -> PlanStep:

        parameters = {}

        # Preserve structured parameters produced upstream.
        if decision is not None:
            parameters.update(
                getattr(
                    decision,
                    "parameters",
                    {},
                )
                or {}
            )

        # -----------------------------------------
        # Application control
        # -----------------------------------------

        if action == ActionType.OPEN_APPLICATION:
            parameters.setdefault(
                "application",
                "code",
            )

        # -----------------------------------------
        # Project creation
        # -----------------------------------------

        elif action == ActionType.BUILD_PROJECT:
            # Cognitive action -> ProjectSkill action.
            #
            # ProjectSkill.execute() expects:
            #     create_project
            #
            # The project name must survive from perception
            # through the decision into the execution plan.

            parameters.setdefault(
                "name",
                parameters.get("project_name"),
            )

            parameters.pop(
                "project_name",
                None,
            )

            parameters.setdefault(
                "description",
                "",
            )

            parameters.setdefault(
                "project_type",
                None,
            )

            return PlanStep(
                order=order,
                action="build_project",
                parameters=parameters,
            )

        return PlanStep(
            order=order,
            action=(
                action.value
                if hasattr(action, "value")
                else str(action)
            ),
            parameters=parameters,
        )
