from ultron.knowledge.storage.graph_store import GraphStore
from ultron.reasoning.traversal import GraphTraversal


store = GraphStore(
    "ultron_learning.db"
)


reasoner = GraphTraversal(
    store
)


print(
    reasoner.find_path(
        "Concept 2.1.2: Newton's Second Law",
        "Acceleration"
    )
)
