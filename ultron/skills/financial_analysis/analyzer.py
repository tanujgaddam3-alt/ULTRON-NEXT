class FinancialAnalyzer:
    """
    Converts financial data into intelligence.
    """


    def analyze(
        self,
        company,
        market,
    ):

        score = 0

        reasons = []


        # Profitability

        margin = (
            company.get(
                "profit_margin"
            )
            or 0
        )


        if margin > 0.15:

            score += 20

            reasons.append(
                "Strong profit margin"
            )



        # Growth

        growth = (
            company.get(
                "revenue_growth"
            )
            or 0
        )


        if growth > 0.05:

            score += 20

            reasons.append(
                "Positive revenue growth"
            )



        # Debt

        debt = (
            company.get(
                "debt_to_equity"
            )
            or 0
        )


        if debt < 100:

            score += 15

            reasons.append(
                "Manageable debt"
            )



        # Market trend

        if (
            market.get("trend")
            ==
            "bullish"
        ):

            score += 15

            reasons.append(
                "Positive technical trend"
            )


        elif (
            market.get("trend")
            ==
            "neutral"
        ):

            score += 8

            reasons.append(
                "Neutral market condition"
            )



        return {

            "score": score,

            "rating":
                self._rating(score),

            "reasons": reasons,

        }



    def _rating(
        self,
        score
    ):

        if score >= 70:

            return "strong"

        if score >= 40:

            return "moderate"

        return "weak"
