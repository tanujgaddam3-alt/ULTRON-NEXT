from typing import ClassVar

from ultron.knowledge.relationship import Relationship


class KnowledgeInferenceEngine:
    """
    Derives new knowledge from existing
    relationships.
    """

    def __init__(
        self,
        graph,
    ):

        self.graph = graph

    def infer(
        self,
        entity_name: str,
    ) -> list[Relationship]:
        """
        Generate derived relationships.
        """

        derived: ClassVar = []

        direct = self.graph.find_relationships(entity_name)

        for first in direct:
            if first.source_id == entity_name:
                intermediate = first.target_id

            else:
                continue

            next_relations = self.graph.find_relationships(intermediate)

            for second in next_relations:
                if second.source_id == intermediate:
                    derived.append(
                        Relationship(
                            source_id=entity_name,
                            target_id=second.target_id,
                            relation=("inferred_" + second.relation),
                            confidence=(
                                min(
                                    first.confidence,
                                    second.confidence,
                                )
                            ),
                        )
                    )

        return derived
