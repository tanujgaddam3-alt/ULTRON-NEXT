from ultron.cognition.reasoning.result import ReasoningResult
from ultron.decision.policy import DecisionPolicy
from ultron.decision.result import DecisionResult
from ultron.decision.scorer import DecisionScorer
from ultron.decision.selector import DecisionSelector


class DecisionEngine:
    """
    Converts reasoning hypotheses into a single decision.
    """

    def __init__(
        self,
        learning_provider=None,
    ):

        self.policy = DecisionPolicy()

        self.scorer = DecisionScorer(
            learning_provider=learning_provider,
        )

        self.selector = DecisionSelector()

    def decide(self, reasoning):

        if isinstance(reasoning, ReasoningResult):
            hypotheses = reasoning.hypotheses
        else:
            hypotheses = reasoning

        allowed = [h for h in hypotheses if self.policy.allows(h)]

        scored = self.scorer.score(allowed)

        decision = self.selector.select(scored)

        return DecisionResult(
            decision=decision,
            evaluated=len(scored),
        )
