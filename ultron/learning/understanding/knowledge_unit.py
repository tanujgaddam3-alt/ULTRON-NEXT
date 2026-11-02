from dataclasses import dataclass, field


@dataclass
class KnowledgeUnit:
    """
    Universal learned knowledge structure.
    """

    topics: list[str] = field(default_factory=list)

    concepts: list[str] = field(default_factory=list)

    definitions: list[str] = field(default_factory=list)

    relationships: list[str] = field(default_factory=list)

    examples: list[str] = field(default_factory=list)

    rules: list[str] = field(default_factory=list)

    metadata: dict = field(default_factory=dict)
