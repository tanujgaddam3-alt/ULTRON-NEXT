class ConflictResolver:
    def resolve(self, knowledge_a, knowledge_b):

        if knowledge_a["confidence"] > knowledge_b["confidence"]:
            return knowledge_a

        elif knowledge_b["confidence"] > knowledge_a["confidence"]:
            return knowledge_b

        return {"status": "needs_review"}
