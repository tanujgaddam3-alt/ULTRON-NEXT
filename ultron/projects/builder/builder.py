from dataclasses import dataclass, field
from typing import Any

from ultron.security.permission.rules import deny_when_authorization_unavailable


@dataclass(frozen=True)
class ProjectFile:
    path: str
    content: str
    artifact_type: str = "source"


@dataclass(frozen=True)
class ProjectDirectory:
    path: str


@dataclass(frozen=True)
class ProjectScaffold:
    project_type: str
    directories: tuple[ProjectDirectory, ...] = ()
    files: tuple[ProjectFile, ...] = ()


class ProjectBuilder:
    """
    Builds concrete project workspaces from structured project metadata.

    ProjectManager owns project creation and persistence.
    FileService owns project-aware file creation and artifact registration.
    ProjectBuilder owns scaffold selection and build orchestration.
    """

    def __init__(
        self,
        project_manager,
        file_service,
    ):
        self.projects = project_manager
        self.files = file_service

    def build(
        self,
        name: str,
        project_type: str | None = None,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:

        deny_when_authorization_unavailable("build_project")
        name = self._validate_name(name)

        normalized_type = self._normalize_type(project_type)

        project = self.projects.get(name)

        if project is not None:
            raise ValueError(
                f"Project '{name}' already exists."
            )

        from ultron.projects.model import Project

        project = self.projects.create(
            Project(
                name=name,
                description=description,
                project_type=normalized_type,
                metadata=metadata or {},
            )
        )

        scaffold = self._scaffold_for(normalized_type)

        created_directories = [
            directory.path
            for directory in scaffold.directories
        ]

        for directory in scaffold.directories:
            self._create_directory(
                name,
                directory.path,
            )

        artifacts = []

        for file in scaffold.files:
            content = file.content.format(
                project_name=name,
                description=description,
            )

            result = self.files.create_project_file(
                project_name=name,
                filename=file.path,
                content=content,
            )

            artifacts.append(
                {
                    "name": file.path,
                    "path": result["file"],
                    "artifact_type": file.artifact_type,
                }
            )

        return {
            "success": True,
            "project": name,
            "project_type": normalized_type,
            "description": description,
            "directories": created_directories,
            "files": artifacts,
            "artifact_count": len(artifacts),
        }

    @staticmethod
    def _validate_name(name: str) -> str:
        if not isinstance(name, str):
            raise TypeError("Project name must be a string.")

        name = name.strip()

        if not name:
            raise ValueError("Project name cannot be empty.")

        if name in {".", ".."}:
            raise ValueError("Invalid project name.")

        if "/" in name or "\\" in name:
            raise ValueError(
                "Project name cannot contain path separators."
            )

        return name

    @staticmethod
    def _normalize_type(project_type: str | None) -> str:
        if not project_type:
            return "generic"

        normalized = project_type.strip().lower()

        aliases = {
            "py": "python",
            "python3": "python",
            "fast-api": "fastapi",
            "fast_api": "fastapi",
        }

        return aliases.get(
            normalized,
            normalized,
        )

    def _scaffold_for(
        self,
        project_type: str,
    ) -> ProjectScaffold:

        if project_type == "python":
            return self._python_scaffold()

        if project_type == "fastapi":
            return self._fastapi_scaffold()

        return self._generic_scaffold()

    @staticmethod
    def _generic_scaffold() -> ProjectScaffold:
        return ProjectScaffold(
            project_type="generic",
            files=(
                ProjectFile(
                    path="README.md",
                    content=(
                        "# {project_name}\n\n"
                        "{description}\n"
                    ),
                    artifact_type="documentation",
                ),
            ),
        )

    @staticmethod
    def _python_scaffold() -> ProjectScaffold:
        return ProjectScaffold(
            project_type="python",
            directories=(
                ProjectDirectory("src"),
                ProjectDirectory("tests"),
            ),
            files=(
                ProjectFile(
                    path="src/main.py",
                    content=(
                        '"""Application entry point."""\n\n'
                        "\n"
                        "def main() -> None:\n"
                        '    print("Hello from ULTRON.")\n'
                        "\n"
                        "\n"
                        'if __name__ == "__main__":\n'
                        "    main()\n"
                    ),
                    artifact_type="source",
                ),
                ProjectFile(
                    path="tests/test_main.py",
                    content=(
                        "def test_project_bootstrap():\n"
                        "    assert True\n"
                    ),
                    artifact_type="test",
                ),
                ProjectFile(
                    path="pyproject.toml",
                    content=(
                        "[build-system]\n"
                        'requires = ["setuptools>=68"]\n'
                        'build-backend = '
                        '"setuptools.build_meta"\n'
                        "\n"
                        "[project]\n"
                        'name = "{project_name}"\n'
                        'version = "0.1.0"\n'
                        'description = "{description}"\n'
                        'requires-python = ">=3.11"\n'
                    ),
                    artifact_type="configuration",
                ),
                ProjectFile(
                    path="README.md",
                    content=(
                        "# {project_name}\n\n"
                        "{description}\n"
                    ),
                    artifact_type="documentation",
                ),
                ProjectFile(
                    path=".gitignore",
                    content=(
                        "__pycache__/\n"
                        "*.py[cod]\n"
                        ".venv/\n"
                        ".pytest_cache/\n"
                        "*.egg-info/\n"
                    ),
                    artifact_type="configuration",
                ),
            ),
        )

    @staticmethod
    def _fastapi_scaffold() -> ProjectScaffold:
        return ProjectScaffold(
            project_type="fastapi",
            directories=(
                ProjectDirectory("app"),
                ProjectDirectory("tests"),
            ),
            files=(
                ProjectFile(
                    path="app/__init__.py",
                    content="",
                    artifact_type="source",
                ),
                ProjectFile(
                    path="app/main.py",
                    content=(
                        "from fastapi import FastAPI\n"
                        "\n"
                        "app = FastAPI()\n"
                        "\n"
                        "\n"
                        '@app.get("/")\n'
                        "def root():\n"
                        '    return {"message": "ULTRON API"}\n'
                    ),
                    artifact_type="source",
                ),
                ProjectFile(
                    path="tests/test_main.py",
                    content=(
                        "from fastapi.testclient import TestClient\n"
                        "\n"
                        "from app.main import app\n"
                        "\n"
                        "client = TestClient(app)\n"
                        "\n"
                        "\n"
                        "def test_root():\n"
                        '    response = client.get("/")\n'
                        "    assert response.status_code == 200\n"
                    ),
                    artifact_type="test",
                ),
                ProjectFile(
                    path="pyproject.toml",
                    content=(
                        "[build-system]\n"
                        'requires = ["setuptools>=68"]\n'
                        'build-backend = '
                        '"setuptools.build_meta"\n'
                        "\n"
                        "[project]\n"
                        'name = "{project_name}"\n'
                        'version = "0.1.0"\n'
                        'description = "{description}"\n'
                        'requires-python = ">=3.11"\n'
                        'dependencies = [\n'
                        '    "fastapi",\n'
                        '    "uvicorn[standard]",\n'
                        "]\n"
                    ),
                    artifact_type="configuration",
                ),
                ProjectFile(
                    path="README.md",
                    content=(
                        "# {project_name}\n\n"
                        "{description}\n\n"
                        "## Run\n\n"
                        "```bash\n"
                        "uvicorn app.main:app --reload\n"
                        "```\n"
                    ),
                    artifact_type="documentation",
                ),
                ProjectFile(
                    path=".gitignore",
                    content=(
                        "__pycache__/\n"
                        "*.py[cod]\n"
                        ".venv/\n"
                        ".pytest_cache/\n"
                    ),
                    artifact_type="configuration",
                ),
            ),
        )

    def _create_directory(
        self,
        project_name: str,
        directory: str,
    ) -> None:
        from pathlib import Path

        project = self.projects.get(project_name)

        if project is None:
            raise ValueError(
                f"Project '{project_name}' not found."
            )

        projects_root = Path("Projects").resolve()
        root = Path("Projects") / project.name
        resolved_root = root.resolve()
        target = (root / directory).resolve()
        try:
            resolved_root.relative_to(projects_root)
            target.relative_to(resolved_root)
        except ValueError:
            raise ValueError(
                "Unsafe project directory."
            ) from None

        target.mkdir(
            parents=True,
            exist_ok=True,
        )
