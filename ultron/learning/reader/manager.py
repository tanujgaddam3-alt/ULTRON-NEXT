from pathlib import Path

from ultron.learning.reader.markdown_reader import (
    MarkdownReader,
)


class DocumentReaderManager:
    """
    Selects the correct reader
    based on file type.
    """

    def __init__(self):

        self.readers = {
            ".md": MarkdownReader(),
        }

    def read(
        self,
        path: str,
    ):

        extension = Path(path).suffix.lower()

        reader = self.readers.get(extension)

        if reader is None:
            raise ValueError(f"No reader available for {extension}")

        return reader.read(path)
