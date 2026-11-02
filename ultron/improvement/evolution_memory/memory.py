from datetime import datetime


class EvolutionMemory:
    """
    Stores evolution attempts,
    outcomes, and learned patterns.
    """


    def __init__(self):

        self.records = []


    def remember(
        self,
        capability,
        action,
        before,
        after,
        success,
        strategy=None,
        reason=None,
    ):

        self.records.append(

            {

                "capability": capability,

                "action": action,

                "before_score": before,

                "after_score": after,

                "gain": after - before,

                "success": success,

                "strategy": strategy,

                "reason": reason,

                "timestamp": (
                    datetime.now()
                    .isoformat()
                ),

            }

        )


    def history(self):

        return self.records



    def successful_attempts(self):

        return [

            item

            for item in self.records

            if item["success"]

        ]



    def failed_attempts(self):

        return [

            item

            for item in self.records

            if not item["success"]

        ]



    def strategy_performance(
        self,
        strategy,
    ):

        attempts = [

            item

            for item in self.records

            if item["strategy"] == strategy

        ]


        if not attempts:

            return 0


        successes = [

            item

            for item in attempts

            if item["success"]

        ]


        return (
            len(successes)
            /
            len(attempts)
        )