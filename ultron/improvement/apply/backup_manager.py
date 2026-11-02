import shutil
from datetime import datetime
from pathlib import Path


class BackupManager:
    """
    Creates backups before applying changes.
    """


    def create_backup(
        self,
        file_path: str,
    ) -> str | None:


        if not file_path:

            return None


        source = Path(
            file_path
        )


        if not source.exists():

            return None


        timestamp = datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )


        backup = source.with_suffix(
            source.suffix +
            f".backup_{timestamp}"
        )


        shutil.copy2(
            source,
            backup,
        )


        return str(backup)