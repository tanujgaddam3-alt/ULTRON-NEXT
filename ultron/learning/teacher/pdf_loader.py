from typing import ClassVar

import pymupdf


class PDFLoader:
    """
    Loads educational PDFs into text documents.
    """

    def load(
        self,
        path: str,
        subject: str,
    ) -> dict:

        document = pymupdf.open(path)

        pages: ClassVar = []

        for page in document:
            pages.append(page.get_text())

        content = "\n".join(pages)

        return {
            "content": content,
            "source": {
                "type": "education",
                "subject": subject,
                "file": path,
            },
        }
