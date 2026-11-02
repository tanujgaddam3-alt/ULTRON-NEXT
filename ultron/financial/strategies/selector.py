class StrategySelector:
    """
    Selects strategy families based on market regime.
    """


    def select(
        self,
        regime,
    ):

        if regime == "bull":

            return [
                "trend_following",
                "momentum",
                "breakout",
            ]


        elif regime == "bear":

            return [
                "defensive",
                "short_bias",
                "risk_reduction",
            ]


        elif regime == "high_volatility":

            return [
                "low_exposure",
                "hedging",
                "cash_preservation",
            ]


        else:

            return [
                "mean_reversion",
                "range_trading",
            ]
