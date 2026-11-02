from dataclasses import dataclass, field


@dataclass
class Curriculum:
    subject: str

    topics: list[str] = field(default_factory=list)

    level: str = "foundation"

    description: str = ""
