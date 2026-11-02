from ultron.learning.document.chunks.model import (
    KnowledgeChunk,
)


class KnowledgeChunker:
    """
    Converts topics into learning chunks.
    """

    def chunk(
        self,
        subject: str,
        chapter: str,
        topic: str,
        content: str,
    ):

        return KnowledgeChunk(
            subject=subject,
            chapter=chapter,
            topic=topic,
            content=content,
            chunk_type="topic",
        )
