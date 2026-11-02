from ultron.core.learning_session import LearningSession

from ultron.learning.reader.manager import DocumentReaderManager
from ultron.learning.understanding.engine import UnderstandingEngine



from ultron.knowledge.graph.builder import KnowledgeGraphBuilder
from ultron.knowledge.storage.graph_store import GraphStore
from ultron.knowledge.intelligence.semantic_parser import SemanticParser

reader = DocumentReaderManager()

understanding = UnderstandingEngine()
semantic_parser = SemanticParser()


graph_builder = KnowledgeGraphBuilder()

memory = GraphStore(
    "ultron_learning.db"
)


session = LearningSession(

    reader,

    understanding,

    semantic_parser,
    

    graph_builder,

    memory

)


result = session.learn(
    "tests/learning_data/test_physics.md"
)


print("\nRESULT")
print(result)
