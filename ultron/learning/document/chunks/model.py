from dataclasses import dataclass, field


@dataclass
class KnowledgeChunk:
    subject: str

    chapter: str

    topic: str

    content: str

    chunk_type: str = "general"

    metadata: dict = field(default_factory=dict)
