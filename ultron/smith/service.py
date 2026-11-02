from ultron.smith.collectors.cognitive import (
    CognitiveMetricsCollector,
)
from ultron.smith.storage.repository import (
    MetricsRepository,
)


class SmithService:
    """
    Central performance intelligence service.
    """

    def __init__(
        self,
        repository=None,
        schema_learner=None,
    ):

        self.collector = CognitiveMetricsCollector()

        self.repository = (
            repository
            if repository
            else MetricsRepository()
        )

        self.schema_learner = schema_learner


    def analyze_cycle(
    self,
    cycle,
):

     metrics = self.collector.collect(
        cycle
    )

     self.repository.save(
        metrics
    )


    # -----------------------------
    # Schema Performance Learning
    # -----------------------------

     if self.schema_learner and cycle.context:

        schemas = cycle.context.schemas

        print(
            "SMITH SCHEMAS:",
            [
                s.schema.name
                for s in schemas
            ]
        )

        for activated_schema in schemas:

            self.schema_learner.update(
                schema_id=activated_schema.schema.id,
                success=metrics.execution_success,
            )


     return metrics

    def history(self):

        return self.repository.all()
    def evaluate_skill_performance(self):

     history = self.repository.all()

     if not history:
        return {
            "status": "no data"
        }


     success = sum(
        1
        for item in history
        if item.execution_success
    )


     total = len(history)


     return {
        "total_cycles": total,
        "successful_cycles": success,
        "success_rate": success / total,
     }