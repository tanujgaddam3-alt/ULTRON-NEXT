class GraphMetrics:
    """
    Calculates knowledge graph statistics.
    """

    def analyze(
        self,
        graph,
    ):

        entity_count = len(graph.entities)

        relationship_count = len(graph.relationships)

        confidence_values = [relation.confidence for relation in graph.relationships]

        average_confidence = (
            sum(confidence_values) / len(confidence_values)
            if confidence_values
            else 0.0
        )

        return {
            "entities": entity_count,
            "relationships": relationship_count,
            "average_confidence": average_confidence,
            "knowledge_density": self._density(
                entity_count,
                relationship_count,
            ),
        }

    def _density(
        self,
        entities,
        relationships,
    ):

        if entities == 0:
            return 0.0

        return relationships / entities
