class VectorStore:
    """
    Abstract vector storage interface.
    """

    def __init__(
        self,
    ):

        self.vectors: dict[str, list] = {}

    def add(
        self,
        identifier: str,
        vector: list,
    ):

        self.vectors[identifier] = vector

    def get(
        self,
        identifier: str,
    ):

        return self.vectors.get(identifier)

    def all(
        self,
    ):

        return self.vectors
