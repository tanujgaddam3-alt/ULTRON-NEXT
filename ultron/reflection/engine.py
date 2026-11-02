from ultron.execution.result import ExecutionResult
from ultron.learning.events import LearningEvent
from ultron.learning.experience.models import ExperienceRecord
from ultron.learning.experience.store import ExperienceStore
from ultron.reflection.analyzer import ReflectionAnalyzer
from ultron.reflection.evaluator import SelfEvaluator
from ultron.reflection.result import ReflectionResult


class ReflectionEngine:
    """
    Reflects on completed execution.

    Produces:
    - ReflectionResult
    - Learning events
    - Experience records
    """

    def __init__(
        self,
        event_bus=None,
        experience_store: ExperienceStore | None = None,
    ):
        self.analyzer = ReflectionAnalyzer()

        self.event_bus = event_bus

        self.experience_store = (
            experience_store or ExperienceStore()
        )

        self.evaluator = SelfEvaluator()

    def reflect(
        self,
        execution: ExecutionResult,
    ) -> ReflectionResult:

        reflection = self.analyzer.analyze(
            execution
        )

        evaluation = self.evaluator.evaluate(
            execution,
            reflection,
        )

        reflection.evaluation_score = evaluation[
            "score"
        ]

        reflection.quality = evaluation[
            "quality"
        ]

        reflection.recommendation = evaluation[
            "recommendation"
        ]

        self._store_experience(
            execution,
            reflection,
        )

        self._emit_learning(
            reflection
        )

        return reflection

    def _store_experience(
        self,
        execution: ExecutionResult,
        reflection: ReflectionResult,
    ):
        record = execution.record

        action = "unknown"

        if execution.data:
            action = execution.data[0].get(
                "action",
                "unknown",
            )

        experience = ExperienceRecord(
            situation=str(execution),
            action=action,
            result=str(reflection),
            success=record.success,
            lesson=reflection.lesson,
            context={
                "source": "reflection",
                "completed_steps": (
                    record.completed_steps
                ),
                "message": record.message,
            },
        )

        self.experience_store.save(
            experience
        )

    def _emit_learning(
        self,
        reflection: ReflectionResult,
    ):
        if not self.event_bus:
            return

        topic = getattr(
            reflection,
            "topic",
            "execution",
        )

        summary = getattr(
            reflection,
            "summary",
            str(reflection),
        )

        event = LearningEvent(
            concept=topic,
            description=summary,
            metadata={
                "source": "reflection",
            },
        )

        self.event_bus.publish(
            event,
        )
