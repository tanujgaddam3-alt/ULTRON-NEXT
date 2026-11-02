from ultron.domain.workspace import Workspace


class Blackboard:
    """
    Shared cognitive workspace.

    Every subsystem reads and writes here.
    """

    def __init__(self):
        self._workspace = Workspace()

    @property
    def workspace(self) -> Workspace:
        return self._workspace

    def update(self, **kwargs):
        self._workspace = self._workspace.model_copy(update=kwargs)

    def clear(self):
        self._workspace = Workspace()
