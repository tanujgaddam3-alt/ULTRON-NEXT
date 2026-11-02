from __future__ import annotations

from uuid import uuid4

from ultron.cognition.schema.graph import KnowledgeGraph
from ultron.cognition.schema.models import (
    Concept,
    Relation,
    Schema,
)
from ultron.cognition.schema.repository import SchemaRepository
from ultron.domain.knowledge import Knowledge


class SchemaLearner:
    """
    Discovers reusable cognitive schemas from knowledge.

    Responsibilities:
    - extract concepts
    - create relationships
    - generate schemas

    Does not:
    - activate schemas
    - match queries
    - manage memory
    """

    def __init__(
        self,
        repository: SchemaRepository,
    ):
        self.repository = repository
        self.graph = KnowledgeGraph()

    def learn(
        self,
        knowledge: list[Knowledge],
    ) -> Schema | None:
        """
        Create a schema from accumulated knowledge.
        """

        if not knowledge:
            return None

        concepts: dict[str, Concept] = {}
        relations: list[Relation] = []

        for item in knowledge:
            concept_id = str(uuid4())

            concept = Concept(
                id=concept_id,
                name=item.key,
                kind="knowledge",
                metadata={
                    "source": item.source,
                    "confidence": item.confidence,
                },
            )

            concepts[concept_id] = concept

            self.graph.add_concept(concept)

        concept_ids = list(concepts.keys())

        for index in range(len(concept_ids) - 1):
            relation = Relation(
                source=concept_ids[index],
                target=concept_ids[index + 1],
                relation="associated_with",
                weight=1.0,
            )

            relations.append(relation)

            self.graph.add_relation(relation)

        schema = Schema(
            id=str(uuid4()),
            name="learned_pattern",
            description="Schema discovered from accumulated knowledge.",
            concepts=concepts,
            relations=relations,
            confidence=sum(item.confidence for item in knowledge) / len(knowledge),
            metadata={
                "source": "schema_learning",
            },
        )

        self.repository.add(schema)

        return schema
