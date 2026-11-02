from ultron.learning.reader.manager import DocumentReaderManager
from ultron.learning.understanding.chunking.knowledge_chunker import KnowledgeChunker
from ultron.learning.understanding.analyzers.markdown_section_parser import MarkdownSectionParser
from ultron.learning.understanding.analyzers.section_classifier import SectionClassifier
from ultron.learning.understanding.normalizer import KnowledgeNormalizer


doc = DocumentReaderManager().read(
    "tests/learning_data/test_physics.md"
)


chunks = KnowledgeChunker().chunk(
    doc.content
)


parser = MarkdownSectionParser()

classifier = SectionClassifier()

normalizer = KnowledgeNormalizer()


for chunk in chunks:

    if chunk["type"] == "concept":

        sections = parser.parse(
            chunk["content"]
        )

        data = classifier.classify(
            sections
        )

        clean = normalizer.normalize(
            data
        )

        print(clean)

        break
