class CodeRelevanceScorer:
    """
    Scores source files against an improvement problem.
    """

    def score(
        self,
        file_path: str,
        code: str,
        problem: str,
    ):

        score = 0

        text = file_path.lower() + " " + code.lower()

        problem_words = problem.lower().replace(".", "").split()

        # Match important error terms
        for word in problem_words:
            if len(word) > 4 and word in text:
                score += 2

        # Python structure hints

        if "class" in code:
            score += 1

        if "def build" in code:
            score += 3

        if "knowledgegraph" in text:
            score += 5

        if "knowledgeunit" in text:
            score += 5

        if "builder" in file_path.lower():
            score += 5

        if "graph" in file_path.lower():
            score += 5

        if "test" in file_path.lower():
            score -= 10
        if "builder_v1" in file_path.lower():
            score -= 10

        if file_path.endswith("knowledge/graph/builder.py"):
            score += 15

        return score
