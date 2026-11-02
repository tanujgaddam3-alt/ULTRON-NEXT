from datetime import datetime


class StrategyMemory:
    """
    Stores strategy experiments.
    """


    def __init__(self):

        self.records = []



    def remember(
        self,
        symbol,
        strategy,
        performance,
    ):

        self.records.append(
            {
                "symbol": symbol,

                "strategy": strategy,

                "performance": performance,

                "timestamp":
                    datetime.now()
                    .isoformat(),
            }
        )



    def history(self):

        return self.records
