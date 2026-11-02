from dataclasses import dataclass


@dataclass
class KnowledgeEdge:
    source: str

    relation: str

    target: str

    explanation: str = ""

    confidence: float = 1.0

    source_document: str = ""
