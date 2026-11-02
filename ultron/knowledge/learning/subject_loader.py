from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class Subject:
    """
    Represents a learning subject.
    """

    name: str

    topics: list[str] = field(default_factory=list)

    progress: float = 0.0

    metadata: dict = field(default_factory=dict)

    id: str = field(default_factory=lambda: str(uuid4()))


class SubjectLoader:
    """
    Loads and manages learning subjects.
    """

    def __init__(
        self,
    ):

        self.subjects = {}

    def create(
        self,
        name: str,
        topics=None,
    ):

        subject = Subject(
            name=name,
            topics=topics or [],
        )

        self.subjects[subject.id] = subject

        return subject

    def get(
        self,
        subject_id: str,
    ):

        return self.subjects.get(subject_id)
