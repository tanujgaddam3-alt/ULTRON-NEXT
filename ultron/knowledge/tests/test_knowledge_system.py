from ultron.knowledge.graph import KnowledgeGraph
from ultron.knowledge.intelligence.graph_metrics import GraphMetrics


def test_knowledge_metrics():

    graph = KnowledgeGraph()

    metrics = GraphMetrics()

    result = metrics.analyze(graph)

    assert result["entities"] == 0

    assert result["relationships"] == 0
