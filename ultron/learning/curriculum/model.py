from dataclasses import dataclass, field


@dataclass
class Subject:
    name: str

    description: str = ""

    documents: list[str] = field(default_factory=list)


@dataclass
class Curriculum:
    name: str

    subjects: list[Subject] = field(default_factory=list)
