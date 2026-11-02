class KnowledgeUpdateEngine:
    """
    Applies feedback changes to memory.
    """

    def update_confidence(self, old_confidence, change):

        new_confidence = old_confidence + change

        new_confidence = max(new_confidence, 0)

        new_confidence = min(new_confidence, 1)

        return new_confidence

    def create_revision(self, feedback):

        return {
            "concept": feedback.concept,
            "replaced": feedback.old_statement,
            "new_knowledge": feedback.correction,
            "reason": "User feedback",
            "confidence": 1.0,
        }
