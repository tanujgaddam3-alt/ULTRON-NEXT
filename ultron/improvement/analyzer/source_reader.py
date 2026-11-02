from pathlib import Path


class SourceReader:
    """
    Reads source code for analysis.
    """

    def read(
        self,
        file_path: str,
    ):

        file = Path(file_path)

        if not file.exists():
            return ""

        return file.read_text(encoding="utf-8")
