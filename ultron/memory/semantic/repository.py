import sqlite3
import threading
from typing import ClassVar

from ultron.memory.semantic.models import (
    SemanticMemoryItem,
)


class SemanticMemoryRepository:
    """
    Persistent storage for semantic memories.
    """

    def __init__(
        self,
        database="semantic_memory.db",
    ):

        self.connection = sqlite3.connect(
            database,
            check_same_thread=False,
        )
        self._lock = threading.RLock()

        self._create_table()

    def _create_table(
        self,
    ):

        with self._lock:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS semantic_memory(
                    concept TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                )
                """
            )
            self.connection.commit()

    def save(
        self,
        memory: SemanticMemoryItem,
    ):

        with self._lock:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO semantic_memory
                VALUES (?,?)
                """,
                (
                    memory.concept,
                    memory.model_dump_json(),
                ),
            )
            self.connection.commit()

        return memory

    def get(
        self,
        concept: str,
    ) -> SemanticMemoryItem | None:

        with self._lock:
            row = self.connection.execute(
                """
                SELECT data
                FROM semantic_memory
                WHERE concept=?
                """,
                (concept,),
            ).fetchone()

        if row is None:
            return None

        return SemanticMemoryItem.model_validate_json(row[0])

    def search(
        self,
        query: str,
    ):

        with self._lock:
            rows = self.connection.execute(
                """
                SELECT data
                FROM semantic_memory
                """
            ).fetchall()

        query = query.lower()

        results: ClassVar = []

        for row in rows:
            memory = SemanticMemoryItem.model_validate_json(row[0])

            if query in (memory.concept.lower() or memory.description.lower()):
                results.append(memory)

        return results
