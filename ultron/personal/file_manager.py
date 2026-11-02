from pathlib import Path
from typing import ClassVar


class FileManager:
    """
    Handles safe file operations.
    """

    def __init__(
        self,
        root_directory=None,
    ):

        self.root = Path(root_directory) if root_directory else Path(".")

    def list_files(
        self,
    ):

        return list(self.root.iterdir())

    def find(
        self,
        name,
    ):

        matches: ClassVar = []

        for file in self.root.rglob("*"):
            if file.name == name:
                matches.append(file)

        return matches

    def exists(
        self,
        path,
    ):

        return Path(path).exists()
