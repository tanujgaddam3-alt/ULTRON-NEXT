from __future__ import annotations

from pathlib import Path
from typing import Any

from ultron.agents.coding.agent import CodingAgent
from ultron.agents.models import AgentTask
from ultron.security.permission.rules import deny_when_authorization_unavailable
from ultron.skills.base import Skill


class FileSkill(Skill):
    """
    Production filesystem skill.

    Responsibilities:
    - create project files
    - read files
    - write files
    - list directories
    - generate source code when a write request contains a
      natural-language request but no explicit content
    - validate generated code through CodingAgent
    - return structured execution results

    Generated source is never executed by this skill.
    """

    name = "file"
    description = (
        "Creates, reads, writes and manages files, including "
        "AI-assisted source-code generation."
    )
    version = "3.0"

    actions = [
        "create_file",
        "read_file",
        "write_file",
        "list_directory",
    ]

    def __init__(
        self,
        file_service,
        coding_agent: CodingAgent | None = None,
    ) -> None:
        if file_service is None:
            raise ValueError("file_service is required.")

        self.file_service = file_service
        self.coding_agent = coding_agent

    def set_coding_agent(
        self,
        coding_agent: CodingAgent | None,
    ) -> None:
        """
        Attach or replace the coding-generation capability.

        This supports container initialization where filesystem
        services may be constructed before the LLM provider.
        """
        self.coding_agent = coding_agent

    def execute(
        self,
        action: str,
        **kwargs: Any,
    ):
        self._require_authorization(action)
        if action == "create_file":
            return self._create_file(**kwargs)

        if action == "read_file":
            return self._read_file(**kwargs)

        if action == "write_file":
            return self._write_file(**kwargs)

        if action == "list_directory":
            return self.list_directory(kwargs.get("path"))

        raise ValueError(f"Unsupported action: {action}")

    def _create_file(
        self,
        **kwargs: Any,
    ):
        self._require_authorization("create_file")
        project_name = kwargs.get("project_name")
        filename = kwargs.get("filename") or kwargs.get("file_name")
        content = kwargs.get("content")

        if not project_name:
            raise ValueError(
                "project_name is required for create_file."
            )

        if not filename:
            raise ValueError(
                "filename is required for create_file."
            )

        if content is None:
            request = kwargs.get("request")

            if request:
                content = self._generate_content(
                    filename=filename,
                    request=request,
                )
            else:
                content = ""

        return self.file_service.create_project_file(
            project_name=project_name,
            filename=filename,
            content=content,
        )

    def _read_file(
        self,
        **kwargs: Any,
    ):
        self._require_authorization("read_file")
        path = kwargs.get("path")

        if not path:
            raise ValueError("path is required for read_file.")

        return {
            "success": True,
            "content": self._read_path(path),
        }

    def _write_file(
        self,
        **kwargs: Any,
    ):
        self._require_authorization("write_file")
        path = kwargs.get("path") or kwargs.get("file_name")
        content = kwargs.get("content")
        request = kwargs.get("request")

        if not path:
            raise ValueError(
                "path or file_name is required for write_file."
            )

        if content is None:
            if not request:
                raise ValueError(
                    "write_file requires either content or request."
                )

            content = self._generate_content(
                filename=Path(path).name,
                request=request,
            )

        if not isinstance(content, str):
            raise TypeError(
                "Generated or supplied file content must be a string."
            )

        if not content.strip():
            raise ValueError(
                f"Refusing to write empty content to '{path}'."
            )

        return self.write_file(
            path=path,
            content=content,
        )

    def _generate_content(
        self,
        filename: str,
        request: str,
    ) -> str:
        """
        Generate source code from the user's natural-language request.

        CodingAgent performs language selection, generation, validation,
        and bounded repair. Generated code is returned but never executed.
        """

        if self.coding_agent is None:
            raise RuntimeError(
                "Code generation is unavailable: CodingAgent is not configured."
            )

        task = AgentTask(
            goal=request,
            required_capabilities=[
                "code_generation",
                "code_validation",
            ],
            constraints={
                "filename": filename,
                "do_not_execute_generated_code": True,
                "production_quality": True,
            },
            context={
                "filename": filename,
                "operation": "write_file",
                "request": request,
            },
        )

        result = self.coding_agent.execute(task)

        if not result.success:
            raise RuntimeError(
                "Code generation failed: "
                f"{result.output or 'unknown generation error'}"
            )

        code = result.output

        if not isinstance(code, str) or not code.strip():
            raise RuntimeError(
                "CodingAgent reported success but returned no generated code."
            )

        return code

    def write_file(
        self,
        path: str,
        content: str,
    ) -> dict[str, Any]:
        """
        Write already-generated content to disk.

        This method deliberately does not generate or execute code.
        """
        self._require_authorization("write_file")
        if not path:
            raise ValueError("File path cannot be empty.")

        if not isinstance(content, str):
            raise TypeError("File content must be a string.")

        target = Path(path)

        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        target.write_text(
            content,
            encoding="utf-8",
        )

        return {
            "success": True,
            "message": f"{target} updated.",
            "path": str(target),
            "bytes": len(content.encode("utf-8")),
            "lines": len(content.splitlines()),
        }

    def _read_path(
        self,
        path: str,
    ) -> str:
        self._require_authorization("read_file")
        target = Path(path)

        if not target.exists():
            raise FileNotFoundError(
                f"File not found: {target}"
            )

        if not target.is_file():
            raise IsADirectoryError(
                f"Path is not a file: {target}"
            )

        return target.read_text(
            encoding="utf-8",
        )

    def list_directory(
        self,
        path: str | None = None,
    ) -> dict[str, Any]:
        self._require_authorization("list_directory")
        directory = Path(path or ".")

        if not directory.exists():
            raise FileNotFoundError(
                f"Directory not found: {directory}"
            )

        if not directory.is_dir():
            raise NotADirectoryError(
                f"Path is not a directory: {directory}"
            )

        return {
            "success": True,
            "path": str(directory),
            "files": [
                item.name
                for item in directory.iterdir()
            ],
        }

    @staticmethod
    def _require_authorization(action: str) -> None:
        deny_when_authorization_unavailable(action)
