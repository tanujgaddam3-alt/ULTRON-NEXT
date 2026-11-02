import json
import sqlite3

from ultron.improvement.domain.models import (
    ImprovementProposal,
)


class ImprovementRepository:
    """
    Persistent storage for improvement history.
    """

    def __init__(
        self,
        database="improvements.db",
    ):

        self.connection = sqlite3.connect(database)

        self._create_table()

    def _create_table(
        self,
    ):

        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS improvements(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target TEXT NOT NULL,
                data TEXT NOT NULL
            )
            """
        )

        self.connection.commit()

    def save(
        self,
        proposal: ImprovementProposal,
    ):

        self.connection.execute(
            """
            INSERT INTO improvements(
                target,
                data
            )
            VALUES (?,?)
            """,
            (
                proposal.target,
                json.dumps(
                    proposal.to_dict()
                    if hasattr(proposal, "to_dict")
                    else proposal.__dict__,
                    default=str,
                ),
            ),
        )

        self.connection.commit()

        return proposal

    def list_all(
        self,
    ):

        rows = self.connection.execute(
            """
            SELECT data
            FROM improvements
            ORDER BY id DESC
            """
        ).fetchall()

        return [json.loads(row[0]) for row in rows]

    def find_by_target(
        self,
        target: str,
    ):

        rows = self.connection.execute(
            """
            SELECT data
            FROM improvements
            WHERE target=?
            """,
            (target,),
        ).fetchall()

        return [json.loads(row[0]) for row in rows]
