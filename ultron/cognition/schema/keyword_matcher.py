from __future__ import annotations

from ultron.cognition.schema.matcher import SchemaMatcher
from ultron.cognition.schema.models import ActivatedSchema
from ultron.cognition.schema.repository import SchemaRepository


class KeywordSchemaMatcher(SchemaMatcher):
    """
    Matches schemas using keyword relevance.

    Uses:
    - schema name
    - description
    - concept names
    - concept metadata
    """

    def __init__(
        self,
        repository: SchemaRepository,
    ):
        self.repository = repository

    def match(
    self,
    query: str,
) -> list[ActivatedSchema]:

     words = query.lower().strip().split()

     activated: list[ActivatedSchema] = []

     for schema in self.repository.all():

        score = 0.0

        searchable = " ".join(
            [
                schema.name,
                schema.description,
                *[
                    concept.name
                    for concept in schema.concepts.values()
                ],
                *[
                    str(value)
                    for concept in schema.concepts.values()
                    for value in concept.metadata.values()
                ],
            ]
        ).lower()


        for word in words:

            if word in searchable:
                score += 0.3


        if score > 0:

            activated.append(
                ActivatedSchema(
                    schema=schema,
                    activation=min(score,1.0),
                    predicted_actions=[
                        schema.metadata.get("action")
                    ]
                    if schema.metadata.get("action")
                    else [],
                )
            )


     activated.sort(
        key=lambda item:item.activation,
        reverse=True,
    )

     return activated