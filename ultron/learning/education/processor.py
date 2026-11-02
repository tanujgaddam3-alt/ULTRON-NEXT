from ultron.learning.document.analyzer import (
    DocumentAnalyzer,
)
from ultron.learning.document.chapter_detector import (
    ChapterDetector,
)
from ultron.learning.document.chunks import (
    KnowledgeChunker,
)
from ultron.learning.document.topic_extractor import (
    TopicExtractor,
)


class EducationalProcessor:
    """
    Converts educational documents
    into structured knowledge.
    """

    def __init__(
        self,
        knowledge_manager,
    ):

        self.knowledge = knowledge_manager

        from ultron.learning.teacher.document_loader import DocumentLoader

        self.loader = DocumentLoader()

        self.analyzer = DocumentAnalyzer()

        self.chapter_detector = ChapterDetector()

        self.topic_extractor = TopicExtractor()

        self.chunker = KnowledgeChunker()

    def process_pdf(
        self,
        path: str,
        subject: str,
    ):

        document = self.loader.load(
            path,
        )
        document["subject"] = subject

        result = self.knowledge.learn(document)

        return {
            "subject": subject,
            "file": path,
            "knowledge": result,
        }
