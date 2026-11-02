import hashlib


class IntegrityVerifier:
    """
    Verifies system integrity.
    """

    def __init__(self):

        self.baselines = {}

    def create_baseline(
        self,
        name: str,
        data: str,
    ):

        self.baselines[name] = self._hash(data)

        return self.baselines[name]

    def verify(
        self,
        name: str,
        data: str,
    ):

        current = self._hash(data)

        original = self.baselines.get(name)

        return {
            "valid": (current == original),
            "current_hash": current,
            "original_hash": original,
        }

    def _hash(
        self,
        data: str,
    ):

        return hashlib.sha256(data.encode()).hexdigest()
