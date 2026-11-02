from ultron.domain.execution import ExecutionRecord
from ultron.execution.result import ExecutionResult
from ultron.learning.experience_recorder import ExperienceRecorder
from ultron.reflection.result import ReflectionResult


def test_experience_creation():

    recorder = ExperienceRecorder()

    execution = ExecutionResult(
        record=ExecutionRecord(
            success=True,
            completed_steps=1,
            message="Done",
        )
    )

    reflection = ReflectionResult(
        success=True,
        lesson="Successful execution.",
        confidence=1.0,
    )

    experience = recorder.record(
        execution,
        reflection,
    )

    assert experience.task_id == execution.record.id
    assert experience.lesson == "Successful execution."
    assert experience.confidence == 1.0