from __future__ import annotations

from collections import defaultdict

from ultron.cognition.schema.models import Concept, Relation


class KnowledgeGraph:
    """
    Stores concepts and the relationships between them.
    """

    def __init__(self):
        self._concepts: dict[str, Concept] = {}
        self._relations: list[Relation] = []
        self._adjacency: dict[str, list[Relation]] = defaultdict(list)

    def add_concept(
        self,
        concept: Concept,
    ) -> None:
        """
        Add or replace a concept.
        """
        self._concepts[concept.id] = concept

    def get_concept(
        self,
        concept_id: str,
    ) -> Concept | None:
        return self._concepts.get(concept_id)

    def add_relation(
        self,
        relation: Relation,
    ) -> None:
        """
        Add a relationship between two concepts.
        """

        if relation.source not in self._concepts:
            raise KeyError(f"Unknown source concept '{relation.source}'.")

        if relation.target not in self._concepts:
            raise KeyError(f"Unknown target concept '{relation.target}'.")

        self._relations.append(relation)
        self._adjacency[relation.source].append(relation)

    def neighbors(
        self,
        concept_id: str,
    ) -> list[Relation]:
        """
        Return all outgoing relationships.
        """
        return list(self._adjacency.get(concept_id, []))

    def concepts(
        self,
    ) -> list[Concept]:
        return list(self._concepts.values())

    def relations(
        self,
    ) -> list[Relation]:
        return list(self._relations)

    def clear(
        self,
    ) -> None:
        self._concepts.clear()
        self._relations.clear()
        self._adjacency.clear()
