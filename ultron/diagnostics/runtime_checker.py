class RuntimeChecker:
    def check(self, system):

        try:
            return {"status": "ok", "type": type(system).__name__}

        except Exception as e:
            return {"status": "failed", "error": str(e)}
