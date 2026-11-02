import sys

from ultron.core.learning_session import LearningSession

from ultron.learning.reader.manager import DocumentReaderManager
from ultron.learning.understanding.engine import UnderstandingEngine

from ultron.knowledge.graph.builder import KnowledgeGraphBuilder
from ultron.knowledge.storage.graph_store import GraphStore
from ultron.knowledge.intelligence.semantic_parser import SemanticParser


def create_session():

    reader = DocumentReaderManager()

    understanding = UnderstandingEngine()

    graph_builder = KnowledgeGraphBuilder()

    memory = GraphStore(
        "ultron_learning.db"
    )


    print("Reader:", type(reader))
    print("Understanding:", type(understanding))
    print("Graph Builder:", type(graph_builder))
    print("Memory:", type(memory))


    return LearningSession(

        DocumentReaderManager(),

        UnderstandingEngine(),

        SemanticParser(),

        KnowledgeGraphBuilder(),

        GraphStore(
            "ultron_learning.db"
        ))


if __name__ == "__main__":


    if len(sys.argv) < 2:

        print(
            "Usage: python learn.py <file_path>"
        )

        sys.exit(1)



    file_path = sys.argv[1]


    session = create_session()


    result = session.learn(
        file_path
    )


    print("\nRESULT")

    print(result)