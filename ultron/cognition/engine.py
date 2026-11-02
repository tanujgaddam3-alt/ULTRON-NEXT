from typing import ClassVar

from ultron.reasoning.explanation_builder import ExplanationBuilder
from ultron.reasoning.question.analyzer import QuestionAnalyzer
from ultron.reasoning.traversal import GraphTraversal


class CognitiveEngine:
    def __init__(self, store):

        self.analyzer = QuestionAnalyzer()

        self.traversal = GraphTraversal(store)

        self.explainer = ExplanationBuilder()

    def answer(self, question):

        analysis = self.analyzer.analyze(question)

        concepts = analysis["concepts"]

        paths: ClassVar = []

        for concept in concepts:
            result = self.traversal.explore(concept, depth=3)

            paths.extend(result)

        explanation = self.explainer.build(paths)

        return {"analysis": analysis, "reasoning": paths, "answer": explanation}
