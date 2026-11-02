import socket
import subprocess

from ultron.core.logging.logger import (
    get_logger,
)
from ultron.world.models import WorldSnapshot
from ultron.world.observers.application import (
    ApplicationObserver,
)
from ultron.world.observers.filesystem import (
    FileSystemObserver,
)
from ultron.world.observers.health import (
    ObserverHealth,
)
from ultron.world.observers.process import (
    ProcessObserver,
)
from ultron.world.observers.project import (
    ProjectObserver,
)
from ultron.world.observers.system import (
    SystemObserver,
)


class ObserverManager:
    """
    Coordinates all world observers.

    Each observer works independently.
    Failure of one observer should not
    break the complete world update cycle.
    """

    def __init__(self):

        self.system = SystemObserver()

        self.project = ProjectObserver()

        self.process = ProcessObserver()

        self.application = ApplicationObserver()

        self.filesystem = FileSystemObserver()

        self.logger = get_logger("ObserverManager")

        self.health = ObserverHealth()

    def safe_observe(
        self,
        observer,
        default,
    ):
        """
        Execute observer safely.

        If an observer fails:
        - log error
        - return fallback value
        - continue world update
        """

        try:
            if callable(observer) and not hasattr(observer, "observe"):
                return observer()
            return observer.observe()

        except Exception as error:
            name = observer.__class__.__name__

            self.health.observers[name] = "failed"

            self.health.errors.append(str(error))

            self.health.healthy = False

        self.logger.exception(f"{name} failed")

        return default


    @staticmethod
    def _internet_connected() -> bool:
        """
        Determine whether the host currently has network connectivity.

        A short TCP connection is used instead of assuming connectivity
        from configuration or cached state.
        """

        try:
            with socket.create_connection(
                ("1.1.1.1", 53),
                timeout=1.0,
            ):
                return True
        except OSError:
            return False

    @staticmethod
    def _docker_running() -> bool:
        """
        Determine whether the Docker daemon is reachable.
        """

        try:
            result = subprocess.run(
                ["docker", "info"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
                check=False,
            )
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def observe(self):

        snapshot = WorldSnapshot()

        # System observation

        snapshot.system = self.safe_observe(
            self.system,
            snapshot.system,
        )

        # Project observation

        snapshot.project = self.safe_observe(
            self.project,
            snapshot.project,
        )

        # Process observation

        processes = self.safe_observe(
            self.process,
            [],
        )

        snapshot.entities.extend(processes)

        # Application observation

        applications = self.safe_observe(
            lambda: self.application.observe(processes),
            [],
        )

        snapshot.entities.extend(applications)

        # Runtime observation
        #
        # Reuse the application entities already derived from the
        # authoritative process observation. No second process scan.

        snapshot.runtime.active_applications = [
            application.name
            for application in applications
            if getattr(application, "entity_type", "") == "application"
        ]

        snapshot.runtime.internet_connected = self._internet_connected()

        snapshot.runtime.docker_running = self._docker_running()

        # File system observation

        files = self.safe_observe(
            self.filesystem,
            [],
        )

        snapshot.entities.extend(files)

        return snapshot
