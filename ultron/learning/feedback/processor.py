class FeedbackProcessor:
    """
    Updates knowledge based on feedback.
    """

    def process(self, feedback):

        update = {
            "concept": feedback.concept,
            "old": feedback.old_statement,
            "new": feedback.correction,
            "confidence_change": feedback.confidence_change,
            "source": feedback.source,
        }

        return update
