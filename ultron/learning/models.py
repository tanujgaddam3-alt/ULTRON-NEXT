from dataclasses import dataclass, field


@dataclass
class LearningResult:
    knowledge: list = field(default_factory=list)

    metadata: dict = field(default_factory=dict)

    progress: object | None = None
