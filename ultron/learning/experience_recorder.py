from ultron.domain.experience import Experience
from ultron.execution.result import ExecutionResult
from ultron.reflection.result import ReflectionResult


class ExperienceRecorder:
    """
    Converts execution + reflection into an experience.
    """

    def record(
        self,
        execution: ExecutionResult,
        reflection: ReflectionResult,
    ) -> Experience:

        return Experience(
            task_id=execution.record.id,
            outcome=execution.record.message,
            lesson=reflection.lesson,
            confidence=reflection.confidence,
        )
