class MemoryService:
    """
    Coordinates memory access.
    """

    def __init__(self, memory):
        self.memory = memory

    def remember(self, item):
        self.memory.semantic.store(item)

    def recall(self):
        return self.memory.semantic.retrieve_all()
