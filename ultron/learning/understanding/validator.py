class KnowledgeValidator:
    """
    Validates extracted knowledge candidates.
    """

    def validate_definition(
        self,
        definition: str,
    ) -> float:

        score = 1.0

        text = definition.lower()

        # Reject questions

        if "?" in definition:
            return 0.0

        question_words = [
            "explain",
            "why",
            "how",
            "what",
            "find",
            "calculate",
        ]

        if any(word in text for word in question_words):
            return 0.0

        # Reject examples/problems

        bad_patterns = [
            "problem setup",
            "solution strategy",
            "worked example",
            "key results",
            "this is a",
        ]

        if any(pattern in text for pattern in bad_patterns):
            score -= 0.7

        # Prefer actual definition language

        good_patterns = [
            "is defined as",
            "refers to",
            "is the",
            "is a measure of",
            "is a force",
        ]

        if any(pattern in text for pattern in good_patterns):
            score += 0.2

        return max(min(score, 1.0), 0.0)
