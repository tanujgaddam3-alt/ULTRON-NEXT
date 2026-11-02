from ultron.cognition.schema.repository import SchemaRepository


class SchemaPerformanceLearner:
    """
    Improves schemas using execution experience.
    """

    def __init__(
        self,
        repository: SchemaRepository,
    ):
        self.repository = repository


    def update(
        self,
        schema_id: str,
        success: bool,
    ):

        schema = self.repository.get(
            schema_id
        )

        if schema is None:
            return None


        current = schema.confidence


        if success:
            schema.confidence = min(
                current + 0.01,
                1.0,
            )

        else:
            schema.confidence = max(
                current - 0.05,
                0.1,
            )


        self.repository.add(
            schema
        )
        print(
    "SCHEMA LEARNING:",
    schema.name,
    "SUCCESS" if success else "FAILED",
    "CONFIDENCE:",
    schema.confidence,
)

        return schema
