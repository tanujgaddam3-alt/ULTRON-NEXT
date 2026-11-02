from uuid import uuid4

from ultron.agents.models import AgentResult, AgentTask
from ultron.agents.registry import AgentRegistry


class AgentWorkflowExecutor:
    """Executes workflow steps sequentially while respecting dependencies."""

    def __init__(
        self,
        registry: AgentRegistry,
    ):
        self.registry = registry

    def execute(self, workflow, context=None):
        ordered_steps = self._topological_steps(workflow.steps)
        results = []
        results_by_order = {}
        context = dict(context or {})

        if not ordered_steps:
            workflow.status = "blocked"
            return results

        for step in ordered_steps:
            execution_id = str(uuid4())
            dependency_results = [
                results_by_order[dependency]
                for dependency in step.depends_on
            ]
            if any(not result.success for result in dependency_results):
                result = AgentResult(
                    success=False,
                    agent=step.agent,
                    output="Blocked because a prerequisite step did not succeed.",
                    metadata={
                        "status": "blocked",
                        "workflow_id": workflow.id,
                        "execution_id": execution_id,
                        "step_order": step.order,
                        "dependency_orders": step.depends_on,
                    },
                )
                results.append(result)
                results_by_order[step.order] = result
                continue

            agent = self.registry.get(step.agent)
            if agent is None:
                result = AgentResult(
                    success=False,
                    agent=step.agent,
                    output="Agent unavailable.",
                    metadata={
                        "status": "unavailable",
                        "workflow_id": workflow.id,
                        "execution_id": execution_id,
                        "step_order": step.order,
                    },
                )
                results.append(result)
                results_by_order[step.order] = result
                continue

            task = AgentTask(
                goal=step.objective,
                constraints={
                    "workflow_id": workflow.id,
                    "step_order": step.order,
                    "depends_on": list(step.depends_on),
                },
                context={
                    **context,
                    "workflow_goal": workflow.goal,
                },
            )

            result = AgentResult(
                success=False,
                agent=step.agent,
                output=(
                    "Agent dispatch blocked: no trusted operation-grant "
                    "integration is available."
                ),
                metadata={
                    "status": "blocked",
                    "reason": "trusted_operation_grant_unavailable",
                    "workflow_id": workflow.id,
                    "execution_id": execution_id,
                    "task_id": task.id,
                    "step_order": step.order,
                },
            )

            results.append(result)
            results_by_order[step.order] = result

        statuses = [result.metadata["status"] for result in results]
        if all(status == "completed" for status in statuses):
            workflow.status = "completed"
        elif any(status == "completed" for status in statuses):
            workflow.status = "partial"
        elif all(status in {"blocked", "unavailable"} for status in statuses):
            workflow.status = "blocked"
        else:
            workflow.status = "failed"

        return results

    @staticmethod
    def _topological_steps(steps):
        remaining = list(steps)
        steps_by_order = {}

        for step in remaining:
            if step.order in steps_by_order:
                raise ValueError("Workflow step orders must be unique.")
            steps_by_order[step.order] = step

        for step in remaining:
            for dependency in step.depends_on:
                if dependency not in steps_by_order:
                    raise ValueError(
                        "Workflow dependency references an unknown step."
                    )
                if dependency == step.order:
                    raise ValueError("Workflow steps cannot depend on themselves.")

        resolved = set()
        ordered = []
        while remaining:
            ready = [
                step
                for step in remaining
                if set(step.depends_on).issubset(resolved)
            ]
            if not ready:
                raise ValueError("Workflow dependencies contain a cycle.")

            for step in ready:
                ordered.append(step)
                resolved.add(step.order)
                remaining.remove(step)

        return ordered
