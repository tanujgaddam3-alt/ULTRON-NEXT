from ultron.learning.reader.manager import DocumentReaderManager
from ultron.learning.understanding.chunking.knowledge_chunker import KnowledgeChunker
from ultron.learning.understanding.analyzers.section_parser import InternalSectionParser


doc = DocumentReaderManager().read(
    "tests/learning_data/test_physics.md"
)


chunks = KnowledgeChunker().chunk(
    doc.content
)


parser = InternalSectionParser()


for chunk in chunks:

    if chunk["type"] == "concept":

        result = parser.parse(
            chunk["content"]
        )

        print(result)

        break
