from .model import LearningProgress


class ProgressTracker:
    """
    Tracks ULTRON learning progress.
    """

    def __init__(self):

        self.progress = {}

    def create_subject(
        self,
        subject: str,
    ):

        self.progress[subject] = LearningProgress(subject=subject)

    def update(
        self,
        subject: str,
        topic: str,
        confidence: float,
    ):

        if subject not in self.progress:
            self.create_subject(subject)

        state = self.progress[subject]

        if topic not in state.completed_topics:
            state.completed_topics.append(topic)

        state.confidence = confidence

    def get(
        self,
        subject: str,
    ):

        return self.progress.get(subject)
