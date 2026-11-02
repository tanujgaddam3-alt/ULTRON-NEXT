class FinancialDecisionEngine:
    """
    Produces final financial decisions.
    """


    def decide(
        self,
        reasoning,
        regime,
        strategy,
    ):

        confidence = (
            reasoning.get(
                "confidence",
                0.5
            )
        )


        risks = (
            reasoning.get(
                "risks",
                []
            )
        )


        if confidence >= 0.75 and len(risks) == 0:

            decision = "consider"


        elif confidence >= 0.5:

            decision = "watch"


        else:

            decision = "avoid"


        return {

            "decision":
                decision,

            "confidence":
                confidence,

            "market_regime":
                regime,

            "strategy":
                strategy,

            "reasoning":
                reasoning,

        }
