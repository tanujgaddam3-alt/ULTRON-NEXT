from dataclasses import dataclass, field
from enum import Enum


class ImprovementStatus(Enum):

    PROPOSED = "proposed"

    TESTING = "testing"

    ACCEPTED = "accepted"

    REJECTED = "rejected"



@dataclass
class ImprovementProposal:
    """
    Represents a possible system improvement.
    """

    target: str

    description: str = ""

    reason: str = ""

    suggestion: str = ""

    confidence: float = 0.0

    status: str = "proposed"

    evidence: dict = field(
        default_factory=dict
    )



@dataclass
class Patch:
    """
    Represents a generated improvement patch.
    """

    target_file: str

    description: str

    changes: str = ""

    risk: str = "LOW"

    original_code: str = ""

    replacement_code: str = ""

    operation: str = "modify"

    files: list[dict] = field(
        default_factory=list
    )

    metadata: dict = field(
        default_factory=dict
    )