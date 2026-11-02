from ultron.cognition.models.performance_record import (
    PerformanceRecord,
)


class MetricsCollector:
    """
    Collects ULTRON performance metrics.
    """

    def __init__(
        self,
    ):

        self.records = []

    def record(
        self,
        system: str,
        event: str,
        score: float,
    ):

        record = PerformanceRecord(
            system=system,
            event=event,
            score=score,
        )

        self.records.append(record)

        return record

    def get_metrics(
        self,
        system: str = None,
    ):

        if system is None:
            return self.records

        return [record for record in self.records if record.system == system]

    def average_score(
        self,
        system: str,
    ):

        values = [record.score for record in self.records if record.system == system]

        if not values:
            return 0.0

        return sum(values) / len(values)
