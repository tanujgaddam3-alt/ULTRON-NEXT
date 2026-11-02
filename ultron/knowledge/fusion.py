from typing import Any, ClassVar


class KnowledgeFusionEngine:
    """
    Combines extracted knowledge components
    into a unified knowledge structure.

    Responsibilities:
    - merge extraction outputs
    - remove duplicates
    - prepare graph input
    """

    def fuse(
        self,
        extraction_result: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Create unified knowledge package.
        """

        entities = self._unique(extraction_result.get("entities", []))

        concepts = self._unique(extraction_result.get("concepts", []))

        relationships = self._unique(extraction_result.get("relationships", []))

        facts = self._unique(extraction_result.get("facts", []))

        return {
            "entities": entities,
            "concepts": concepts,
            "relationships": relationships,
            "facts": facts,
            "metadata": {
                "entity_count": len(entities),
                "concept_count": len(concepts),
                "relationship_count": len(relationships),
                "fact_count": len(facts),
            },
        }

    def _unique(
        self,
        items: list,
    ) -> list:
        """
        Remove duplicate objects.
        """

        seen = set()
        result: ClassVar = []

        for item in items:
            key = str(item.model_dump() if hasattr(item, "model_dump") else item)

            if key not in seen:
                seen.add(key)

                result.append(item)

        return result
