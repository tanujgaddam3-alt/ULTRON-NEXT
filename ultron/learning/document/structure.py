from dataclasses import dataclass, field


@dataclass
class Topic:
    name: str

    content: str = ""


@dataclass
class Chapter:
    name: str

    content: str = ""

    topics: list[Topic] = field(default_factory=list)


@dataclass
class SubjectDocument:
    subject: str

    chapters: list[Chapter] = field(default_factory=list)
