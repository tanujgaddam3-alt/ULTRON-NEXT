from pathlib import Path


class KnowledgeExtractionHook:
    """
    Sends file content to the knowledge pipeline.

    This layer intentionally does not
    understand knowledge itself.
    It only prepares documents.
    """

    def __init__(
        self,
        extractor=None,
    ):

        self.extractor = extractor

    def process(
        self,
        path: str,
    ) -> dict:

        file = Path(path)

        if not file.exists():
            raise FileNotFoundError(path)

        content = file.read_text(encoding="utf-8")

        document = {
            "source": str(file),
            "content": content,
            "metadata": {
                "filename": file.name,
                "extension": file.suffix,
            },
        }

        if self.extractor:
            knowledge = self.extractor.extract(document)

            return {
                "success": True,
                "knowledge": knowledge,
            }

        return {
            "success": True,
            "document": document,
        }
