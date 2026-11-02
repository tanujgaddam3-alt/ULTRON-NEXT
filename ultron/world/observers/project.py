import os

from ultron.world.models import ProjectInfo


class ProjectObserver:
    def observe(self):

        return ProjectInfo(
            name=os.path.basename(os.getcwd()),
            directory=os.getcwd(),
        )
