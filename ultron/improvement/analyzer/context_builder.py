from typing import ClassVar

from ultron.improvement.analyzer.relevance_scorer import CodeRelevanceScorer
from ultron.improvement.analyzer.source_reader import SourceReader


class ContextBuilder:
    """
    Builds relevant source context.
    """

    def __init__(
        self,
        locator,
        scorer=None,
    ):

        self.locator = locator

        self.reader = SourceReader()

        self.scorer = scorer or CodeRelevanceScorer()

    def build(
        self,
        component: str,
        problem: str,
    ):

        files = self.locator.locate(
            component,
            problem,
        )

        ranked: ClassVar = []

        for file in files:
            code = self.reader.read(file)

            score = self.scorer.score(
                file,
                code,
                problem,
            )

            ranked.append(
                {
                    "file": file,
                    "score": score,
                    "code": code,
                }
            )

        ranked.sort(
            key=lambda x: x["score"],
            reverse=True,
        )

        return {
            "component": component,
            "problem": problem,
            "files": ranked[:5],
        }
