import pandas as pd


class MarketAnalyzer:
    """
    Performs real market data analysis.
    """


    def analyze(
        self,
        history,
    ):

        if history.empty:

            return {
                "success": False,
                "reason": "No market history available",
            }


        close = history["Close"]


        current = float(
            close.iloc[-1]
        )


        sma_20 = float(
            close
            .rolling(20)
            .mean()
            .iloc[-1]
        )


        sma_50 = float(
            close
            .rolling(50)
            .mean()
            .iloc[-1]
        )


        volatility = float(
            close
            .pct_change()
            .std()
        )


        if current > sma_20 > sma_50:

            trend = "bullish"

        elif current < sma_20 < sma_50:

            trend = "bearish"

        else:

            trend = "neutral"



        return {

            "success": True,

            "current_price": current,

            "sma_20": sma_20,

            "sma_50": sma_50,

            "trend": trend,

            "volatility": volatility,

        }
