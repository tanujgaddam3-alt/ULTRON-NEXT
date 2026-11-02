from ultron.knowledge.storage.graph_store import GraphStore
from ultron.reasoning.query_engine import QueryEngine


store = GraphStore()


engine = QueryEngine(
    store
)


result = engine.find(
    "Velocity"
)


print(result)
