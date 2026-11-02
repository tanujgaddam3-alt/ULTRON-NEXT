from ultron.knowledge.entity import Entity
from ultron.knowledge.relationship import Relationship
from ultron.knowledge.graph import KnowledgeGraph


def test_knowledge_graph_flow():

    graph = KnowledgeGraph()


    graph.add_entity(
        Entity(
            name="ULTRON",
        )
    )


    graph.add_entity(
        Entity(
            name="Python",
        )
    )


    graph.add_relationship(
        Relationship(
            source_id="ULTRON",
            target_id="Python",
            relation="uses",
        )
    )


    connected = graph.neighbors(
        "ULTRON"
    )


    assert len(connected) == 1
    assert connected[0].name == "Python"
