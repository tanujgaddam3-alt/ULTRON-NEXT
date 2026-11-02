from pathlib import Path


class DocumentLoader:
    """
    Loads documents into ULTRON knowledge pipeline.
    """

    SUPPORTED_TYPES = {
        ".txt",
        ".md",
        ".pdf",
    }

    def load(
        self,
        path: str,
    ):

        file = Path(path)

        if not file.exists():
            raise FileNotFoundError(f"Document not found: {path}")

        if file.suffix not in self.SUPPORTED_TYPES:
            raise ValueError(f"Unsupported file type: {file.suffix}")

        if file.suffix in {
            ".txt",
            ".md",
        }:
            return self._load_text(file)

        if file.suffix == ".pdf":
            return self._load_pdf(file)

    def _load_text(
        self,
        file,
    ):

        return file.read_text(encoding="utf-8")

    def _load_pdf(
        self,
        file,
    ):

        try:
            import pypdf

            reader = pypdf.PdfReader(str(file))

            return "\n".join(page.extract_text() or "" for page in reader.pages)

        except ImportError:
            raise RuntimeError("Install pypdf for PDF support")
