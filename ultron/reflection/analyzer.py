from ultron.execution.result import ExecutionResult
from ultron.reflection.result import ReflectionResult


class ReflectionAnalyzer:
    """
    Analyzes execution outcomes and extracts lessons.
    """

    def analyze(
        self,
        execution: ExecutionResult,
    ) -> ReflectionResult:

        record = execution.record

        if record.success:
            lesson = f"Execution succeeded. Completed steps: {record.completed_steps}."

            confidence = 1.0

            return ReflectionResult(
                success=True,
                lesson=lesson,
                confidence=confidence,
            )

        lesson = f"Execution failed. Reason: {record.message}"

        return ReflectionResult(
            success=False,
            lesson=lesson,
            confidence=0.5,
        )
