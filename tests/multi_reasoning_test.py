from ultron.knowledge.storage.graph_store import GraphStore
from ultron.reasoning.traversal import GraphTraversal


store = GraphStore(
    "ultron_learning.db"
)


reasoner = GraphTraversal(
    store
)


path = reasoner.find_path(
    "Force",
    "Velocity",
    depth=4
)


print(path)
