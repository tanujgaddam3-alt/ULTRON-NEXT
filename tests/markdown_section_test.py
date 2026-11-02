from ultron.learning.reader.manager import DocumentReaderManager
from ultron.learning.understanding.chunking.knowledge_chunker import KnowledgeChunker
from ultron.learning.understanding.analyzers.markdown_section_parser import MarkdownSectionParser


doc = DocumentReaderManager().read(
    "tests/learning_data/test_physics.md"
)


chunks = KnowledgeChunker().chunk(
    doc.content
)


parser = MarkdownSectionParser()


for chunk in chunks:

    if chunk["type"] == "concept":

        sections = parser.parse(
            chunk["content"]
        )

        for key,value in sections.items():

            print("\nSECTION:",key)

            print(
                value[:200]
            )

        break
