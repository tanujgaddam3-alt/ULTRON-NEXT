from dataclasses import dataclass


@dataclass
class CompanySnapshot:

    symbol: str

    name: str

    market_cap: float | None

    profit_margin: float | None

    debt_to_equity: float | None

    pe_ratio: float | None

    revenue: float | None

    revenue_growth: float | None

    return_on_equity: float | None
