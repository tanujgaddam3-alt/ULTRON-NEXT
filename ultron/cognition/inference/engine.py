from ultron.cognition.context.models import CognitiveContext
from ultron.cognition.inference.evaluator import HypothesisEvaluator
from ultron.cognition.inference.generator import HypothesisGenerator
from ultron.cognition.inference.ranker import HypothesisRanker
from ultron.cognition.inference.selector import HypothesisSelector


class InferenceEngine:
    """
    Complete inference pipeline.
    """

    def __init__(self):
        self.generator = HypothesisGenerator()
        self.evaluator = HypothesisEvaluator()
        self.ranker = HypothesisRanker()
        self.selector = HypothesisSelector()

    def infer(
        self,
        context: CognitiveContext,
    ):

        hypotheses = self.generator.generate(context)

        self.evaluator.evaluate(hypotheses)

        hypotheses = self.ranker.rank(hypotheses)

        return hypotheses
