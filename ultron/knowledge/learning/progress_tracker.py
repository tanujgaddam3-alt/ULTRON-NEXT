from datetime import UTC, datetime


class ProgressTracker:
    """
    Tracks ULTRON learning progress.
    """

    def __init__(
        self,
    ):

        self.progress = {}

    def update(
        self,
        subject_id: str,
        topic: str,
        score: float,
    ):

        if not 0 <= score <= 1:
            raise ValueError("Progress score must be between 0 and 1")

        if subject_id not in self.progress:
            self.progress[subject_id] = {}

        self.progress[subject_id][topic] = {
            "score": score,
            "updated_at": datetime.now(UTC),
        }

    def get(
        self,
        subject_id: str,
    ):

        return self.progress.get(subject_id, {})

    def overall(
        self,
        subject_id: str,
    ):

        topics = self.get(subject_id)

        if not topics:
            return 0.0

        scores = [item["score"] for item in topics.values()]

        return sum(scores) / len(scores)
