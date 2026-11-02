from ultron.learning.reader.manager import DocumentReaderManager
from ultron.learning.understanding.chunking.knowledge_chunker import KnowledgeChunker
from ultron.learning.understanding.analyzers.markdown_section_parser import MarkdownSectionParser
from ultron.learning.understanding.analyzers.section_classifier import SectionClassifier


doc = DocumentReaderManager().read(
    "tests/learning_data/test_physics.md"
)


chunks = KnowledgeChunker().chunk(
    doc.content
)


parser = MarkdownSectionParser()

classifier = SectionClassifier()


for chunk in chunks:

    if chunk["type"] == "concept":

        sections = parser.parse(
            chunk["content"]
        )

        knowledge = classifier.classify(
            sections
        )

        print(knowledge)

        break
