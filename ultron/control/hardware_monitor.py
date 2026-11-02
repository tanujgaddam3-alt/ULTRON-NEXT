import platform
import shutil


class HardwareMonitor:
    """
    Monitors hardware status.
    """

    def cpu_info(
        self,
    ):

        try:
            import os

            return {"cpu_count": os.cpu_count()}

        except Exception:
            return {}

    def storage_info(
        self,
    ):

        usage = shutil.disk_usage("/")

        return {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
        }

    def system_health(
        self,
    ):

        return {
            "platform": platform.system(),
            "cpu": self.cpu_info(),
            "storage": self.storage_info(),
        }
