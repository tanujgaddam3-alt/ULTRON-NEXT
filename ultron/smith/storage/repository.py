import sqlite3

from ultron.smith.models.metrics import (
    CognitiveMetrics,
)


class MetricsRepository:
    """
    Persistent storage for Smith performance history.
    """

    def __init__(
        self,
        database="ultron_smith.db",
    ):

        self.connection = sqlite3.connect(
            database
        )

        self._create_table()


    def _create_table(self):

        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS cognitive_metrics (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                reasoning_confidence REAL,

                decision_confidence REAL,

                execution_success INTEGER,

                reflection_score REAL,

                learning_generated INTEGER

            )
            """
        )

        self.connection.commit()


    def save(
        self,
        metrics: CognitiveMetrics,
    ):

        self.connection.execute(
            """
            INSERT INTO cognitive_metrics (

                reasoning_confidence,
                decision_confidence,
                execution_success,
                reflection_score,
                learning_generated

            )

            VALUES (?, ?, ?, ?, ?)

            """,

            (
                metrics.reasoning_confidence,

                metrics.decision_confidence,

                int(metrics.execution_success),

                metrics.reflection_score,

                int(metrics.learning_generated),
            ),
        )

        self.connection.commit()

        return metrics



    def all(
        self,
    ) -> list[CognitiveMetrics]:

        cursor = self.connection.execute(
            """
            SELECT

            reasoning_confidence,
            decision_confidence,
            execution_success,
            reflection_score,
            learning_generated

            FROM cognitive_metrics

            ORDER BY id

            """
        )


        records = []


        for row in cursor.fetchall():

            records.append(
                CognitiveMetrics(
                    reasoning_confidence=row[0],
                    decision_confidence=row[1],
                    execution_success=bool(row[2]),
                    reflection_score=row[3],
                    learning_generated=bool(row[4]),
                )
            )


        return records



    def latest(
        self,
    ) -> CognitiveMetrics | None:

        cursor = self.connection.execute(
            """
            SELECT

            reasoning_confidence,
            decision_confidence,
            execution_success,
            reflection_score,
            learning_generated

            FROM cognitive_metrics

            ORDER BY id DESC

            LIMIT 1

            """
        )


        row = cursor.fetchone()


        if row is None:
            return None


        return CognitiveMetrics(
            reasoning_confidence=row[0],
            decision_confidence=row[1],
            execution_success=bool(row[2]),
            reflection_score=row[3],
            learning_generated=bool(row[4]),
        )