from .models import TradingStrategy


class StrategyEvolution:
    """
    Creates improved strategy variations.

    Evolution is deliberately deterministic so that backtest
    experiments remain reproducible.
    """

    def mutate(
        self,
        strategy: TradingStrategy,
    ) -> list[TradingStrategy]:

        variations = []

        short = strategy.short_window
        long = strategy.long_window

        changes = [
            (-3, -10),
            (-2, -5),
            (1, 5),
            (2, 10),
            (5, 20),
        ]

        seen = set()

        for short_change, long_change in changes:

            new_short = short + short_change
            new_long = long + long_change

            if (
                new_short < 2
                or new_long <= new_short
            ):
                continue

            key = (new_short, new_long)

            if key in seen:
                continue

            seen.add(key)

            variations.append(
                TradingStrategy(
                    name=f"SMA_{new_short}_{new_long}",
                    short_window=new_short,
                    long_window=new_long,
                )
            )

        return variations
