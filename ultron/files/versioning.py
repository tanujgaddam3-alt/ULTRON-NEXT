import shutil
from datetime import UTC, datetime
from pathlib import Path


class FileVersionManager:
    """
    Handles file version history.
    """

    def __init__(
        self,
        version_directory="versions",
    ):

        self.version_directory = Path(version_directory)

    def create_version(
        self,
        file_path: str,
    ) -> dict:

        source = Path(file_path)

        if not source.exists():
            raise FileNotFoundError(file_path)

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

        version_folder = self.version_directory / source.stem

        version_folder.mkdir(
            parents=True,
            exist_ok=True,
        )

        destination = version_folder / f"{source.name}.{timestamp}"

        shutil.copy2(
            source,
            destination,
        )

        return {
            "success": True,
            "version": str(destination),
        }

    def list_versions(
        self,
        filename: str,
    ):

        folder = self.version_directory / Path(filename).stem

        if not folder.exists():
            return []

        return [str(item) for item in folder.iterdir()]
