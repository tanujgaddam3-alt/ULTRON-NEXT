from ultron.knowledge.storage.graph_store import GraphStore
from ultron.cognition.engine import CognitiveEngine


store = GraphStore(
    "test_reasoning.db"
)


engine = CognitiveEngine(
    store
)


result = engine.answer(
    "Why does force affect velocity?"
)


print("\nANALYSIS")
print(result["analysis"])


print("\nREASONING")
print(result["reasoning"])


print("\nANSWER")
print(result["answer"])
