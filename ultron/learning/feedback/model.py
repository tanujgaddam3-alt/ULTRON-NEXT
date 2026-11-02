from dataclasses import dataclass


@dataclass
class Feedback:
    concept: str

    old_statement: str

    correction: str

    confidence_change: float

    source: str = "user"
