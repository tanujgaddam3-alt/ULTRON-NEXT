from pathlib import Path

from .loaders.markdown_loader import MarkdownLoader
from .loaders.pdf_loader import PDFLoader


class DocumentLoader:
    def __init__(self):

        self.loaders = {
            ".pdf": PDFLoader(),
            ".md": MarkdownLoader(),
        }

    def load(self, path: str):

        extension = Path(path).suffix.lower()

        loader = self.loaders.get(extension)

        if loader is None:
            raise ValueError(f"Unsupported document type: {extension}")

        return loader.load(path)
