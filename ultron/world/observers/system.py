import platform
import socket

import psutil

from ultron.world.models import SystemInfo


class SystemObserver:
    def observe(self):
        return SystemInfo(
            os=platform.system(),
            hostname=socket.gethostname(),
            python_version=platform.python_version(),
            cpu_usage=float(psutil.cpu_percent(interval=0.1)),
            memory_usage=float(psutil.virtual_memory().percent),
        )
