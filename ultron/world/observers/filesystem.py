import os
import time
from typing import ClassVar

from ultron.world.entity import WorldEntity


class FileSystemObserver:
    """
    Observes important filesystem information.

    Production safeguards:
    - ignored directories
    - file limit
    - depth limit
    - execution timing
    """

    WATCH_EXTENSIONS = {
        ".py",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
    }

    IGNORED_DIRECTORIES = {
        ".git",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        ".idea",
    }

    MAX_FILES = 200

    MAX_DEPTH = 5

    def observe(
        self,
        directory=None,
    ):

        if directory is None:
            directory = os.getcwd()

        entities: ClassVar = []

        start_time = time.time()

        try:
            base_depth = directory.count(os.sep)

            for root, dirs, files in os.walk(directory):
                # Remove ignored folders
                dirs[:] = [d for d in dirs if d not in self.IGNORED_DIRECTORIES]

                current_depth = root.count(os.sep) - base_depth

                if current_depth > self.MAX_DEPTH:
                    dirs[:] = []
                    continue

                for file in files:
                    if len(entities) >= self.MAX_FILES:
                        return entities

                    _, ext = os.path.splitext(file)

                    if ext in self.WATCH_EXTENSIONS:
                        path = os.path.join(
                            root,
                            file,
                        )

                        entities.append(
                            WorldEntity(
                                name=file,
                                entity_type="file",
                                properties={
                                    "path": path,
                                    "extension": ext,
                                },
                            )
                        )

        except Exception:
            return entities

        finally:
            elapsed = time.time() - start_time

        return entities
