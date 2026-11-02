from ultron.projects.builder import ProjectBuilder
from ultron.security.permission.rules import deny_when_authorization_unavailable
from ultron.skills.base import Skill


class ProjectSkill(Skill):
    """
    Handles project lifecycle and project construction operations.
    """

    name = "project"
    description = (
        "Creates, builds, retrieves and lists ULTRON projects."
    )
    version = "2.0"

    actions = [
        "create_project",
        "build_project",
        "get_project",
        "list_projects",
    ]

    def __init__(
        self,
        project_manager,
        file_service=None,
    ):
        self.project_manager = project_manager

        self.builder = None

        if file_service is not None:
            self.builder = ProjectBuilder(
                project_manager=project_manager,
                file_service=file_service,
            )

    def execute(
        self,
        action: str,
        **kwargs,
    ):
        deny_when_authorization_unavailable(action)
        if action == "create_project":
            return self.create_project(kwargs)

        if action == "build_project":
            return self.build_project(kwargs)

        if action == "get_project":
            return self.get_project(kwargs)

        if action == "list_projects":
            return self.list_projects()

        raise ValueError(
            f"Unsupported action: {action}"
        )

    def create_project(
        self,
        data,
    ):
        deny_when_authorization_unavailable("create_project")
        from ultron.projects.model import Project

        project = Project(
            name=data["name"],
            description=data.get(
                "description",
                "",
            ),
            project_type=data.get(
                "project_type"
            ),
        )

        created = self.project_manager.create(
            project
        )

        return {
            "success": True,
            "project": created.name,
        }

    def build_project(
        self,
        data,
    ):
        deny_when_authorization_unavailable("build_project")
        if self.builder is None:
            raise RuntimeError(
                "ProjectBuilder is not configured."
            )

        return self.builder.build(
            name=data["name"],
            project_type=data.get(
                "project_type"
            ),
            description=data.get(
                "description",
                "",
            ),
            metadata=data.get(
                "metadata"
            ),
        )

    def get_project(
        self,
        data,
    ):
        deny_when_authorization_unavailable("read_project")
        project = self.project_manager.get(
            data["name"]
        )

        return {
            "success": project is not None,
            "project": project,
        }

    def list_projects(
        self,
    ):
        deny_when_authorization_unavailable("list_projects")
        projects = self.project_manager.list_projects()

        return {
            "success": True,
            "projects": projects,
        }
