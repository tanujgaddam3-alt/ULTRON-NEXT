from typing import ClassVar

from dataclasses import dataclass


@dataclass
class DocumentMetadata:
    title: str

    subject: str

    pages: int

    chapters: list[str]


class DocumentAnalyzer:
    """
    Analyzes educational documents.
    """

    def analyze(
        self,
        path: str,
        subject: str,
    ):

        import fitz

        pdf = fitz.open(path)

        return DocumentMetadata(
            title=path.split("/")[-1],
            subject=subject,
            pages=len(pdf),
            chapters=[],
        )
