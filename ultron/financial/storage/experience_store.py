from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any


class FinancialExperienceStore:
    """
    Persistent financial experience storage.

    Stores structured financial analysis and decision data in SQLite.
    Complex Python values are serialized to JSON at the persistence
    boundary so callers can work with native dictionaries/lists.

    The store is safe to use across execution threads, including
    Streamlit's rerun/thread model.
    """

    def __init__(
        self,
        path: str = "ultron_financial_memory.db",
    ):
        self.path = path

        # SQLite connections are protected by a lock because the same
        # store may be accessed from multiple application threads.
        self._lock = threading.RLock()

        self.connection = sqlite3.connect(
            self.path,
            check_same_thread=False,
            timeout=30.0,
        )

        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 30000")

        self._create()

    def _create(self) -> None:
        with self._lock:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS experiences(
                    id INTEGER PRIMARY KEY,
                    company TEXT NOT NULL,
                    analysis TEXT NOT NULL,
                    decision TEXT,
                    timestamp TEXT NOT NULL
                )
                """
            )

            self.connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_experiences_company_id
                ON experiences(company, id DESC)
                """
            )

            self.connection.commit()

    @staticmethod
    def _serialize(value: Any) -> str | None:
        """
        Convert a Python value into a SQLite-safe JSON string.
        """
        if value is None:
            return None

        return json.dumps(
            value,
            ensure_ascii=False,
            default=str,
        )

    @staticmethod
    def _deserialize(value: str | None) -> Any:
        """
        Restore JSON data to native Python structures.
        """
        if value is None:
            return None

        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return value

    def remember(
        self,
        company: str,
        analysis: Any,
        decision: Any = None,
    ) -> None:
        """
        Persist a financial experience.

        Both analysis and decision may be dictionaries, lists,
        primitives, or other JSON-serializable structures.
        """

        if not isinstance(company, str) or not company.strip():
            raise ValueError("Company symbol must be a non-empty string.")

        with self._lock:
            self.connection.execute(
                """
                INSERT INTO experiences(
                    company,
                    analysis,
                    decision,
                    timestamp
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    company.strip().upper(),
                    self._serialize(analysis),
                    self._serialize(decision),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

            self.connection.commit()

    def search(
        self,
        company: str,
    ) -> list[dict[str, Any]]:
        """
        Retrieve stored experiences for a company.

        Returns structured dictionaries instead of raw SQLite rows.
        """

        if not isinstance(company, str) or not company.strip():
            return []

        with self._lock:
            cursor = self.connection.execute(
                """
                SELECT
                    id,
                    company,
                    analysis,
                    decision,
                    timestamp
                FROM experiences
                WHERE company = ?
                ORDER BY id DESC
                """,
                (company.strip().upper(),),
            )

            rows = cursor.fetchall()

        return [
            {
                "id": row[0],
                "company": row[1],
                "analysis": self._deserialize(row[2]),
                "decision": self._deserialize(row[3]),
                "timestamp": row[4],
            }
            for row in rows
        ]

    def close(self) -> None:
        """
        Close the SQLite connection cleanly.
        """
        with self._lock:
            if self.connection is not None:
                self.connection.close()
                self.connection = None
