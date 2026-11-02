import sqlite3

from ultron.files.repository.base import (
    FileArtifactRepository,
)
from ultron.projects.model import ProjectArtifact
from ultron.security.permission.rules import deny_when_authorization_unavailable


class SQLiteArtifactRepository(FileArtifactRepository):
    """
    SQLite persistence for file artifacts.
    """

    def __init__(
        self,
        database="ultron_artifacts.db",
    ):

        self.connection = sqlite3.connect(database)

        self._create_table()

    def _create_table(self):

        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts(
                name TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )

        self.connection.commit()

    def save(
        self,
        artifact: ProjectArtifact,
    ) -> ProjectArtifact:
        deny_when_authorization_unavailable("register_artifact")

        self.connection.execute(
            """
            INSERT OR REPLACE INTO artifacts
            VALUES (?,?)
            """,
            (
                artifact.name,
                artifact.model_dump_json(),
            ),
        )

        self.connection.commit()

        return artifact

    def get(
        self,
        name: str,
    ) -> ProjectArtifact | None:

        deny_when_authorization_unavailable("read_artifact")
        result = self.connection.execute(
            """
            SELECT data
            FROM artifacts
            WHERE name=?
            """,
            (name,),
        ).fetchone()

        if result is None:
            return None

        return ProjectArtifact.model_validate_json(result[0])

    def list_all(
        self,
    ) -> list[ProjectArtifact]:

        deny_when_authorization_unavailable("list_artifacts")
        rows = self.connection.execute("SELECT data FROM artifacts").fetchall()

        return [ProjectArtifact.model_validate_json(row[0]) for row in rows]

    def delete(
        self,
        name: str,
    ) -> bool:

        deny_when_authorization_unavailable("delete_artifact")
        cursor = self.connection.execute(
            """
            DELETE FROM artifacts
            WHERE name=?
            """,
            (name,),
        )

        self.connection.commit()

        return cursor.rowcount > 0
