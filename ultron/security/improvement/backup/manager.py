from datetime import UTC, datetime
from uuid import uuid4


class BackupSnapshot:
    """
    Represents a system backup point.
    """

    def __init__(
        self,
        state,
    ):

        self.id = str(uuid4())

        self.state = state

        self.created_at = datetime.now(UTC)


class BackupManager:
    """
    Creates and restores backups.
    """

    def __init__(self):

        self.snapshots = []

    def create(
        self,
        state,
    ):

        snapshot = BackupSnapshot(state)

        self.snapshots.append(snapshot)

        return snapshot

    def restore(
        self,
        snapshot_id,
    ):

        for snapshot in self.snapshots:
            if snapshot.id == snapshot_id:
                return {
                    "status": "restored",
                    "state": snapshot.state,
                }

        return {
            "status": "not_found",
        }
