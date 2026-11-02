from .models import TradingStrategy



class StrategyGenerator:
    """
    Generates strategy candidates.
    """


    def generate(self):

        strategies = []


        windows = [
            (10,50),
            (20,50),
            (30,100),
            (50,200),
        ]


        for short, long in windows:

            strategies.append(
                TradingStrategy(
                    name=f"SMA_{short}_{long}",
                    short_window=short,
                    long_window=long,
                )
            )


        return strategies
