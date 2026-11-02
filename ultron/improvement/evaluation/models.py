from dataclasses import dataclass


@dataclass
class ImprovementEvaluation:

    capability: str

    before_score: float

    after_score: float

    improvement_gain: float

    success: bool

    reason: str
