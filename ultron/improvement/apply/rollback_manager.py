import shutil
from pathlib import Path


class RollbackManager:
    """
    Restores previous versions after failed changes.
    """

    def restore(
        self,
        backup_file: str,
        original_file: str,
    ) -> dict:

        backup = Path(backup_file)
        original = Path(original_file)

        if not backup.exists():
            return {
                "success": False,
                "error": ("Backup file not found"),
            }

        try:
            shutil.copy2(
                backup,
                original,
            )

            return {
                "success": True,
                "restored": str(original),
            }

        except Exception as error:
            return {
                "success": False,
                "error": str(error),
            }
