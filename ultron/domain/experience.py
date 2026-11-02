from .base import DomainModel


class Experience(DomainModel):
    task_id: str

    outcome: str

    lesson: str

    confidence: float = 1.0
