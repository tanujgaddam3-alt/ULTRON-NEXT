from dataclasses import dataclass


@dataclass
class LearningResult:
    subject: str

    documents: int

    results: list

    knowledge: list
