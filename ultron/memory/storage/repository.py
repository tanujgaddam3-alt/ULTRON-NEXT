import sqlite3
from pathlib import Path


class MemoryRepository:
    """
    Persistent storage for ULTRON memory.
    """

    def __init__(
        self,
        path="data/ultron_memory.db",
    ):

        self.path = Path(path)

        self.path.parent.mkdir(
            exist_ok=True
        )

        self.connection = sqlite3.connect(
            self.path
        )

        self._create_table()


    def _create_table(self):

        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memories
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL
            )
            """
        )

        self.connection.commit()


    def save(
        self,
        category: str,
        key: str,
        value: str,
    ):

        self.connection.execute(
            """
            INSERT INTO memories
            (
                category,
                key,
                value
            )
            VALUES
            (?, ?, ?)
            """,
            (
                category,
                key,
                value,
            ),
        )

        self.connection.commit()


    def search(
        self,
        key: str,
    ):

        cursor = self.connection.execute(
            """
            SELECT key,value
            FROM memories
            WHERE key LIKE ?
            """,
            (
                f"%{key}%",
            ),
        )

        return [
            {
                "key": row[0],
                "value": row[1],
            }
            for row in cursor.fetchall()
        ]
