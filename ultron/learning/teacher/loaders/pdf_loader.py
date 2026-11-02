import pymupdf

from .base import DocumentLoader


class PDFLoader(DocumentLoader):
    def load(self, path: str) -> dict:

        document = pymupdf.open(path)

        text = ""

        for page in document:
            text += page.get_text()

        return {
            "type": "pdf",
            "source": path,
            "content": text,
        }
