from datetime import UTC, datetime


class CognitionAgent:
    """
    Central coordinator for ULTRON cognitive intelligence.
    """

    name = "cognition"

    def __init__(
        self,
        metrics=None,
        experience=None,
        reflection=None,
        optimization=None,
        alignment=None,
    ):

        self.metrics = metrics

        self.experience = experience

        self.reflection = reflection

        self.optimization = optimization

        self.alignment = alignment

    def observe(
        self,
        system,
        event,
        score,
    ):

        if not self.metrics:
            return None

        return self.metrics.record(
            system,
            event,
            score,
        )

    def learn(
        self,
        experience,
    ):

        if not self.reflection:
            return None

        reflection = self.reflection.reflect(experience)

        if self.optimization:
            self.optimization.analyze(reflection)

        return reflection

    def evaluate_alignment(
        self,
        goal,
        behaviour,
        score,
    ):

        if not self.alignment:
            return None

        return self.alignment.evaluate(
            goal,
            behaviour,
            score,
        )

    def status(
        self,
    ):

        return {
            "agent": self.name,
            "online": True,
            "timestamp": datetime.now(UTC),
        }
