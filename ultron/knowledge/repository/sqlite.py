import json
import sqlite3
import threading
from typing import ClassVar

from ultron.knowledge.models import (
    Concept,
    Fact,
    KnowledgeItem,
)
from ultron.knowledge.models.extended import (
    Misconception,
)
from ultron.knowledge.repository.base import (
    KnowledgeRepository,
)


def deserialize_knowledge(data: str) -> KnowledgeItem:
    """
    Convert stored JSON into the correct
    knowledge object type.
    """

    raw = json.loads(data)

    knowledge_type = raw.get("knowledge_type", raw.get("category", "knowledge"))

    # Handle old database records
    # that do not have knowledge_type

    if knowledge_type == "fact" or "subject" in raw:
        return Fact.model_validate(raw)

    if knowledge_type == "concept" or "name" in raw:
        return Concept.model_validate(raw)
    if knowledge_type == "misconception" or "statement" in raw or "correction" in raw:
        return Misconception.model_validate(raw)

    return KnowledgeItem.model_validate(raw)


class SQLiteKnowledgeRepository(KnowledgeRepository):
    """
    SQLite persistence for ULTRON knowledge.

    Supports:
    - KnowledgeItem
    - Fact
    - Concept
    """

    def __init__(
        self,
        database="ultron_knowledge.db",
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
                CREATE TABLE IF NOT EXISTS knowledge(
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                )
                """
            )
            self.connection.commit()

    def save(
        self,
        item: KnowledgeItem,
    ) -> KnowledgeItem:

        identifier = item.id

        with self._lock:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO knowledge
                VALUES (?,?)
                """,
                (
                    identifier,
                    item.model_dump_json(),
                ),
            )
            self.connection.commit()

        return item

    def get(
        self,
        identifier: str,
    ) -> KnowledgeItem | None:

        with self._lock:
            row = self.connection.execute(
                """
                SELECT data
                FROM knowledge
                WHERE id=?
                """,
                (identifier,),
            ).fetchone()

        if row is None:
            return None

        return deserialize_knowledge(row[0])

    def search(
        self,
        query: str,
    ) -> list[KnowledgeItem]:

        with self._lock:
            rows = self.connection.execute(
                """
                SELECT data
                FROM knowledge
                """
            ).fetchall()

        query = query.lower()

        results: ClassVar = []

        for row in rows:
            item = deserialize_knowledge(row[0])

            if query in item.content.lower():
                results.append(item)

        return results

    def list_all(
        self,
    ) -> list[KnowledgeItem]:

        with self._lock:
            rows = self.connection.execute(
                """
                SELECT data
                FROM knowledge
                """
            ).fetchall()

        return [deserialize_knowledge(row[0]) for row in rows]

    def delete(
        self,
        identifier: str,
    ) -> bool:

        with self._lock:
            cursor = self.connection.execute(
                """
                DELETE FROM knowledge
                WHERE id=?
                """,
                (identifier,),
            )
            self.connection.commit()

        return cursor.rowcount > 0
