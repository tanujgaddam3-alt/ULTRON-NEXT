from pathlib import Path


class PatchApplier:
    """Fail-closed patch boundary until trusted exact-patch approval exists."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()

    def apply(self, patch) -> dict[str, object]:
        del patch
        return {
            "success": False,
            "error": (
                "Trusted exact-patch authorization is unavailable; "
                "workspace mutation is disabled."
            ),
        }
