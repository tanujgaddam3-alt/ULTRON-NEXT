from dataclasses import dataclass, field
from typing import ClassVar
from uuid import uuid4


@dataclass
class TextChunk:
    """
    Represents a processed knowledge chunk.
    """

    content: str

    metadata: dict = field(default_factory=dict)

    id: str = field(default_factory=lambda: str(uuid4()))


class TextProcessor:
    """
    Cleans and structures extracted documents.
    """

    def __init__(
        self,
        chunk_size: int = 500,
    ):

        self.chunk_size = chunk_size

    def clean(
        self,
        text: str,
    ):

        text = text.replace("\n\n", "\n")

        return text.strip()

    def split(
        self,
        text: str,
    ) -> list[str]:

        words = text.split()

        chunks: ClassVar = []

        for index in range(
            0,
            len(words),
            self.chunk_size,
        ):
            chunk = " ".join(words[index : index + self.chunk_size])

            chunks.append(chunk)

        return chunks

    def process(
        self,
        text: str,
    ) -> list[TextChunk]:

        cleaned = self.clean(text)

        chunks = self.split(cleaned)

        return [
            TextChunk(
                content=chunk,
                metadata={
                    "chunk_index": index,
                },
            )
            for index, chunk in enumerate(chunks)
        ]
