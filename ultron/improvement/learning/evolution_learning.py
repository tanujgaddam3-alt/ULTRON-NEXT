class EvolutionLearningEngine:
    """
    Learns from previous evolution attempts.
    """


    def __init__(
        self,
        evolution_memory,
    ):

        self.memory = evolution_memory



    def capability_success_rate(
        self,
        capability,
    ):

        records = [

            item

            for item in self.memory.history()

            if item["capability"] == capability

        ]


        if not records:

            return 0


        success = [

            item

            for item in records

            if item["success"]

        ]


        return (
            len(success)
            /
            len(records)
        )



    def best_strategy(
        self,
        capability,
    ):

        records = [

            item

            for item in self.memory.history()

            if item["capability"] == capability

        ]


        if not records:

            return None


        strategies = {}


        for item in records:

            strategy = item["strategy"]


            if strategy not in strategies:

                strategies[strategy] = []


            strategies[strategy].append(
                item["success"]
            )


        scores = {}


        for key, values in strategies.items():

            scores[key] = (
                sum(values)
                /
                len(values)
            )


        return max(
            scores,
            key=scores.get
        )