from ultron.learning.reader.manager import DocumentReaderManager
from ultron.learning.understanding.chunking.knowledge_chunker import KnowledgeChunker
from ultron.learning.understanding.analyzers.concept_analyzer import ConceptAnalyzer


doc = DocumentReaderManager().read(
    "tests/learning_data/test_physics.md"
)


chunks = KnowledgeChunker().chunk(
    doc.content
)


analyzer = ConceptAnalyzer()


for chunk in chunks:

    if chunk["type"] == "concept":

        result = analyzer.analyze(
            chunk
        )

        print(result)

        break
