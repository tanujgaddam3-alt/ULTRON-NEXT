class RevisionStore:
    def __init__(self, store):

        self.store = store

    def revise(self, revision):

        return {
            "action": "memory_updated",
            "concept": revision["concept"],
            "removed": revision["replaced"],
            "added": revision["new_knowledge"],
            "confidence": revision["confidence"],
        }
