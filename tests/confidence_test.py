from ultron.knowledge.confidence.scorer import ConfidenceScorer


scorer = ConfidenceScorer()


print(
    scorer.calculate(
        source_type="textbook",
        repetitions=3,
        has_explanation=True
    )
)
