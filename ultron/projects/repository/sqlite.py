import sqlite3

from ultron.projects.model import Project
from ultron.projects.repository.base import (
    ProjectRepository,
)
from ultron.security.permission.rules import deny_when_authorization_unavailable


class SQLiteProjectRepository(ProjectRepository):
    """
    SQLite implementation of project storage.
    """

    def __init__(
        self,
        database="ultron_projects.db",
    ):

        self.connection = sqlite3.connect(database)

        self._create_table()

    def _create_table(self):

        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS projects(
                name TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )

        self.connection.commit()

    def save(
        self,
        project: Project,
    ) -> Project:
        deny_when_authorization_unavailable("create_project")

        self.connection.execute(
            """
            INSERT OR REPLACE INTO projects
            VALUES (?,?)
            """,
            (
                project.name,
                project.model_dump_json(),
            ),
        )

        self.connection.commit()

        return project

    def get(
        self,
        name: str,
    ) -> Project | None:

        deny_when_authorization_unavailable("read_project")
        result = self.connection.execute(
            """
            SELECT data
            FROM projects
            WHERE name=?
            """,
            (name,),
        ).fetchone()

        if result is None:
            return None

        return Project.model_validate_json(result[0])

    def list_all(
        self,
    ) -> list[Project]:

        deny_when_authorization_unavailable("list_projects")
        rows = self.connection.execute("SELECT data FROM projects").fetchall()

        return [Project.model_validate_json(row[0]) for row in rows]

    def delete(
        self,
        name: str,
    ) -> bool:

        deny_when_authorization_unavailable("delete_project")
        cursor = self.connection.execute(
            """
            DELETE FROM projects
            WHERE name=?
            """,
            (name,),
        )

        self.connection.commit()

        return cursor.rowcount > 0
