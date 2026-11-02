import os
import platform
import shutil


class SystemMonitor:
    """
    Provides system information.
    """

    def get_info(self):

        return {
            "os": platform.system(),
            "hostname": platform.node(),
            "python_version": platform.python_version(),
            "cpu_count": os.cpu_count(),
        }

    def get_disk_usage(self):

        usage = shutil.disk_usage("/")

        return {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
        }
