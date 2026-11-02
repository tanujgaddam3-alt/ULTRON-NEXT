from pathlib import Path


class FileReader:
    """
    Reads source files safely.
    """

    def read(
        self,
        path: str,
    ) -> str:

        file = Path(path)

        if not file.exists():
            raise FileNotFoundError(path)

        return file.read_text(encoding="utf-8")
