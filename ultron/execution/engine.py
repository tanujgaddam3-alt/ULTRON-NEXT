from ultron.domain.execution import ExecutionRecord
from ultron.execution.executor import Executor
from ultron.execution.monitor import ExecutionMonitor
from ultron.execution.result import ExecutionResult
from ultron.planning.result import PlanningResult


class ExecutionEngine:
    """
    Executes validated plans.
    """

    def __init__(
    self,
    skills=None,
    audit_store=None,
):

        self.executor = Executor(
    skills=skills,
    audit_store=audit_store,
)
        self.monitor = ExecutionMonitor()

    def execute(
        self,
        plan,
    ) -> ExecutionResult:

        if isinstance(
            plan,
            PlanningResult,
        ):
            plan = plan.plan

        self.monitor.started()

        execution_result = self.executor.execute(plan)

        completed = execution_result["completed"]

        success = completed == len(plan.steps)

        if success:
            self.monitor.completed()

            message = "Execution completed successfully."

        else:
            message = "Execution failed."

        return ExecutionResult(
    record=ExecutionRecord(
        success=success,
        completed_steps=completed,
        message=message,
    ),
    data=execution_result["results"],
)