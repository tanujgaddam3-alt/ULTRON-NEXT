from typing import ClassVar

from ultron.knowledge.models import Fact


class FactExtractor:
    """
    Converts extracted relationships
    into structured facts.

    Future upgrades:
    - fact verification
    - source comparison
    - contradiction detection
    """

    def extract(
        self,
        relationships,
    ) -> list[Fact]:

        if not relationships:
            return []

        facts: ClassVar = []

        for relation in relationships:
            facts.append(
                Fact(
                    subject=relation.source_id,
                    predicate=relation.relation,
                    object=relation.target_id,
                    content=(
                        f"{relation.source_id} {relation.relation} {relation.target_id}"
                    ),
                    confidence=(relation.confidence),
                )
            )

        return facts
