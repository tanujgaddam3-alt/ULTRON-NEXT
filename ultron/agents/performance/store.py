from ultron.agents.performance.model import (
    AgentPerformance,
)


class PerformanceStore:
    """
    Stores and retrieves agent performance.
    """

    def __init__(self):

        self.records = []

    def save(
        self,
        record: AgentPerformance,
    ):

        self.records.append(record)

    def history(
        self,
        agent: str,
    ):

        return [record for record in self.records if record.agent == agent]

    def average_score(
        self,
        agent: str,
    ) -> float:

        records = self.history(agent)

        if not records:
            return 0.5

        return sum(r.score for r in records) / len(records)
