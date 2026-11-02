from ultron.domain.execution import ExecutionRecord
from ultron.execution.result import ExecutionResult
from ultron.reflection.engine import ReflectionEngine


def test_reflection():

    engine = ReflectionEngine()

    execution = ExecutionResult(
        record=ExecutionRecord(
            success=True,
            completed_steps=1,
            message="Done",
        )
    )

    result = engine.reflect(execution)

    assert result.success
    assert result.confidence == 1.0