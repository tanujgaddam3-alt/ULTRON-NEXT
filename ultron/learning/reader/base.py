from abc import ABC, abstractmethod


class DocumentReader(ABC):
    """
    Base interface for all document readers.

    Every reader:
    PDF
    DOCX
    Markdown
    TXT
    PPTX

    must implement read().
    """

    @abstractmethod
    def read(
        self,
        path: str,
    ):
        raise NotImplementedError
