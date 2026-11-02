from ultron.cognition.memory.experience_record import (
    ExperienceRecord,
)


class ExperienceLogger:
    """
    Stores and retrieves ULTRON experiences.
    """

    def __init__(
        self,
    ):

        self.experiences: list[ExperienceRecord] = []

    def record(
        self,
        system: str,
        action: str,
        outcome: str,
        success: bool,
        score: float,
        context=None,
    ):

        experience = ExperienceRecord(
            system=system,
            action=action,
            outcome=outcome,
            success=success,
            score=score,
            context=context or {},
        )

        self.experiences.append(experience)

        return experience

    def get_all(
        self,
    ):

        return self.experiences

    def get_by_system(
        self,
        system: str,
    ):

        return [exp for exp in self.experiences if exp.system == system]

    def success_rate(
        self,
        system: str,
    ):

        records = self.get_by_system(system)

        if not records:
            return 0.0

        successful = sum(1 for record in records if record.success)

        return successful / len(records)
