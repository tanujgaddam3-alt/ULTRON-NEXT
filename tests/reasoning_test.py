from ultron.knowledge.storage.graph_store import GraphStore
from ultron.reasoning.traversal import GraphTraversal


store=GraphStore()


reasoner=GraphTraversal(
    store
)


result=reasoner.explore(
    "Velocity"
)


print(result)
