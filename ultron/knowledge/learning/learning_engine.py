class LearningEngine:
    """
    Controls ULTRON learning workflow.
    """

    def __init__(
        self,
        subject_loader=None,
        progress_tracker=None,
    ):

        self.subject_loader = subject_loader

        self.progress_tracker = progress_tracker

    def learn_topic(
        self,
        subject_id: str,
        topic: str,
        score: float,
    ):

        if not self.progress_tracker:
            raise RuntimeError("Progress tracker unavailable")

        self.progress_tracker.update(
            subject_id,
            topic,
            score,
        )

        return {
            "subject": subject_id,
            "topic": topic,
            "score": score,
            "status": self._status(score),
        }

    def status(
        self,
        score,
    ):

        return self._status(score)

    def _status(
        self,
        score,
    ):

        if score >= 0.85:
            return "mastered"

        if score >= 0.60:
            return "developing"

        return "needs_learning"
