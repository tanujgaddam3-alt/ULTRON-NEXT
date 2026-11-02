from typing import ClassVar

from ultron.cognition.reasoning.hypothesis import Hypothesis


class DecisionScorer:
    """
    Assigns final decision scores to hypotheses.

    Uses:
    - confidence
    - priority from inference
    - learned evidence
    """

    def __init__(
        self,
        learning_provider=None,
    ):

        self.learning = learning_provider

    def _learned_score(
        self,
        action,
    ) -> float:

        if not self.learning:
            return 0.0

        try:
            return self.learning.get_action_score(action)

        except Exception:
            return 0.0

    def score(
        self,
        hypotheses,
    ):

        scored: ClassVar = []

        for hypothesis in hypotheses:
            if isinstance(
                hypothesis,
                dict,
            ):
                item = dict(hypothesis)

                reasoning_score = item.get(
                    "priority",
                    item.get(
                        "confidence",
                        0.0,
                    ),
                )

                learned_score = self._learned_score(item.get("action"))

                item["learned_score"] = learned_score

                item["score"] = reasoning_score * 0.7 + learned_score * 0.3

                scored.append(item)

            elif isinstance(
                hypothesis,
                Hypothesis,
            ):
                reasoning_score = (
                    hypothesis.priority
                    if hypothesis.priority is not None
                    else hypothesis.confidence
                )

                learned_score = self._learned_score(hypothesis.action)

                final_score = reasoning_score * 0.7 + learned_score * 0.3

                hypothesis.metadata["learned_score"] = learned_score

                hypothesis.metadata["score"] = final_score

                scored.append(hypothesis)

            else:
                raise TypeError(f"Unsupported hypothesis type: {type(hypothesis)}")

        return scored
