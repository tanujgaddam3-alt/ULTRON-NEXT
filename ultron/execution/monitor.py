class ExecutionMonitor:
    """
    Observes execution progress.
    """

    def started(self):
        return {"status": "started"}

    def completed(self):
        return {"status": "completed"}

    def failed(self, error=None):
        return {"status": "failed", "error": error}
