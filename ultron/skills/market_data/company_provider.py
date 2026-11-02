from .company import CompanySnapshot



class CompanyDataProvider:


    def get_company(
        self,
        symbol: str,
    ):


        import yfinance as yf

        company = yf.Ticker(symbol)

        info = company.info


        return CompanySnapshot(

            symbol=symbol,

            name=info.get(
                "longName",
                symbol,
            ),

            market_cap=info.get(
                "marketCap"
            ),

            profit_margin=info.get(
                "profitMargins"
            ),

            debt_to_equity=info.get(
                "debtToEquity"
            ),

            pe_ratio=info.get(
                "trailingPE"
            ),

            revenue=info.get(
                "totalRevenue"
            ),

            revenue_growth=info.get(
                "revenueGrowth"
            ),

            return_on_equity=info.get(
                "returnOnEquity"
            ),

        )
