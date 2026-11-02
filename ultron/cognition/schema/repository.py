from __future__ import annotations

import json
import sqlite3

from ultron.cognition.schema.models import (
    Schema,
    Concept,
    Relation,
)


class SchemaRepository:
    """
    Persistent storage for cognitive schemas.
    """

    def __init__(
        self,
        database="ultron_schema.db",
    ):

        self.connection = sqlite3.connect(
            database
        )

        self._create_table()


    def _create_table(self):

        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schemas (

                id TEXT PRIMARY KEY,

                name TEXT,

                description TEXT,

                concepts TEXT,

                relations TEXT,

                confidence REAL,

                metadata TEXT

            )
            """
        )

        self.connection.commit()


    def add(
        self,
        schema: Schema,
    ) -> None:

        concepts = {}

        for key, concept in schema.concepts.items():

            concepts[key] = {

                "id": concept.id,

                "name": concept.name,

                "kind": concept.kind,

                "metadata": concept.metadata,

            }


        relations = []

        for relation in schema.relations:

            relations.append({

                "source": relation.source,

                "target": relation.target,

                "relation": relation.relation,

                "weight": relation.weight,

            })


        self.connection.execute(
            """
            INSERT OR REPLACE INTO schemas
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,

            (
                schema.id,

                schema.name,

                schema.description,

                json.dumps(concepts),

                json.dumps(relations),

                schema.confidence,

                json.dumps(schema.metadata),

            ),
        )

        self.connection.commit()



    def get(
        self,
        schema_id: str,
    ) -> Schema | None:


        cursor = self.connection.execute(
            """
            SELECT *
            FROM schemas
            WHERE id=?
            """,

            (
                schema_id,
            ),
        )


        row = cursor.fetchone()


        if row is None:

            return None


        return self._deserialize(row)



    def all(
        self,
    ) -> list[Schema]:


        cursor = self.connection.execute(
            """
            SELECT *
            FROM schemas
            """
        )


        return [

            self._deserialize(row)

            for row in cursor.fetchall()

        ]



    def _deserialize(
        self,
        row,
    ) -> Schema:


        concepts_data = json.loads(
            row[3]
        )

        relations_data = json.loads(
            row[4]
        )


        concepts = {}

        for key, value in concepts_data.items():

            concepts[key] = Concept(

                id=value["id"],

                name=value["name"],

                kind=value["kind"],

                metadata=value["metadata"],

            )


        relations = [

            Relation(

                source=item["source"],

                target=item["target"],

                relation=item["relation"],

                weight=item["weight"],

            )

            for item in relations_data

        ]


        return Schema(

            id=row[0],

            name=row[1],

            description=row[2],

            concepts=concepts,

            relations=relations,

            confidence=row[5],

            metadata=json.loads(row[6]),

        )



    def remove(
        self,
        schema_id: str,
    ):

        self.connection.execute(
            """
            DELETE FROM schemas
            WHERE id=?
            """,

            (
                schema_id,
            ),
        )

        self.connection.commit()



    def exists(
        self,
        schema_id: str,
    ) -> bool:

        return self.get(schema_id) is not None



    def clear(
        self,
    ):

        self.connection.execute(
            "DELETE FROM schemas"
        )

        self.connection.commit()