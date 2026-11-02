from ultron.cognition.reflection.reflection_record import (
    ReflectionRecord,
)


class ReflectionEngine:
    """
    Converts experiences into lessons.
    """

    def __init__(
        self,
    ):

        self.reflections: list[ReflectionRecord] = []

    def reflect(
        self,
        experience,
    ):

        if experience.success:
            lesson = f"Successful pattern detected: {experience.action}"

            confidence = experience.score

        else:
            lesson = f"Improvement required for: {experience.action}"

            confidence = 1 - experience.score

        reflection = ReflectionRecord(
            experience_id=experience.id,
            observation=experience.outcome,
            lesson=lesson,
            confidence=confidence,
        )

        self.reflections.append(reflection)

        return reflection

    def get_lessons(
        self,
    ):

        return self.reflections
