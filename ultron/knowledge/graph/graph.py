from typing import ClassVar

from ultron.knowledge.entity import Entity


class KnowledgeGraph:
    """
    Runtime knowledge graph.

    Stores nodes and relationships
    and supports graph queries.
    """

    def __init__(self):

        self.nodes = set()

        self.entities = self.nodes

        self.edges = []

        # compatibility with older knowledge system
        self.relationships = self.edges

    def add_entity(self, entity):

        name = entity.name if hasattr(entity, "name") else entity

        self.nodes.add(name)

    def add_relationship(self, relationship):

        source = relationship.source_id
        target = relationship.target_id
        relation = relationship.relation

        self.add_edge(source, relation, target)

    def add_edge(
        self,
        source: str,
        relation: str,
        target: str,
        confidence: float = 1.0,
    ):

        self.nodes.add(source)
        self.nodes.add(target)

        self.edges.append(
            {
                "source": source,
                "relation": relation,
                "target": target,
                "confidence": confidence,
            }
        )

    def neighbors(
        self,
        entity: str,
    ):

        results: ClassVar = []

        for edge in self.edges:
            if edge["source"] == entity:
                results.append(Entity(name=edge["target"]))

            elif edge["target"] == entity:
                results.append(Entity(name=edge["source"]))

            return results

    def get_entity(
        self,
        entity: str,
    ):
        """
        Retrieve an entity from the graph.
        """

        if entity in self.nodes:
            return {
                "entity": entity,
                "neighbors": self.neighbors(entity),
            }

        return None
