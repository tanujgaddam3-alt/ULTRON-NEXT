import sqlite3
import threading
from pathlib import Path
from datetime import datetime


class ConversationHistory:

    def __init__(
        self,
        database="data/conversation_history.db",
    ):

        path = Path(database)

        path.parent.mkdir(
            exist_ok=True
        )

        self.connection = sqlite3.connect(
            path,
            check_same_thread=False,
        )
        self._lock = threading.RLock()

        self._create_table()


    def _create_table(self):

        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                message TEXT,
                timestamp TEXT
            )
            """
        )

        self.connection.commit()


    def add(
        self,
        session_id: str,
        role: str,
        message: str,
    ):

        with self._lock:
            self.connection.execute(
                """
                INSERT INTO messages
                (
                    session_id,
                    role,
                    message,
                    timestamp
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    session_id,
                    role,
                    message,
                    datetime.now().isoformat(),
                ),
            )
            self.connection.commit()


    def get(
        self,
        session_id: str,
        limit: int = 10,
    ):

        with self._lock:
            rows = self.connection.execute(
                """
                SELECT role,message
                FROM messages
                WHERE session_id=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (
                    session_id,
                    limit,
                ),
            ).fetchall()
        return list(reversed(rows))
