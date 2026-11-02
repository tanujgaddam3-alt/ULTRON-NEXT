from dataclasses import dataclass, field


@dataclass
class LearningProgress:
    subject: str

    completed_topics: list[str] = field(default_factory=list)

    confidence: float = 0.0

    weak_topics: list[str] = field(default_factory=list)
