from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ultron.agents.coding.agent import CodingAgent
from ultron.agents.models import AgentTask
from ultron.improvement.validator.quality_checker import QualityChecker


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectFile:
    """A generated project file held in memory."""

    path: str
    content: str
    language: str
    validation: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectBuildResult:
    """Complete result of a project-generation operation."""

    success: bool
    project_name: str
    description: str
    files: tuple[ProjectFile, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def file_count(self) -> int:
        return len(self.files)


class ProjectBuilder:
    """
    Orchestrates deterministic multi-file project generation.

    Generated projects remain in memory. This component does not write
    files to disk and never executes generated code.

    Pipeline:

        request
          -> language selection
          -> deterministic manifest
          -> per-file generation
          -> file-type-aware validation
          -> project result
    """

    DEFAULT_MAX_FILES = 20

    _LANGUAGE_EXTENSIONS = {
        "python": ".py",
        "javascript": ".js",
        "typescript": ".ts",
        "java": ".java",
        "kotlin": ".kt",
        "go": ".go",
        "rust": ".rs",
        "c": ".c",
        "cpp": ".cpp",
        "c++": ".cpp",
        "csharp": ".cs",
        "c#": ".cs",
    }

    _EXTENSION_LANGUAGES = {
        ".py": "Python",
        ".js": "JavaScript",
        ".ts": "TypeScript",
        ".java": "Java",
        ".kt": "Kotlin",
        ".go": "Go",
        ".rs": "Rust",
        ".c": "C",
        ".cpp": "C++",
        ".cs": "C#",
    }

    def __init__(
        self,
        coding_agent: CodingAgent,
        quality_checker: QualityChecker | None = None,
        *,
        max_files: int = DEFAULT_MAX_FILES,
    ):
        if coding_agent is None:
            raise ValueError("coding_agent is required.")

        if max_files < 1:
            raise ValueError("max_files must be at least 1.")

        self.coding_agent = coding_agent
        self.quality_checker = (
            quality_checker
            if quality_checker is not None
            else QualityChecker()
        )
        self.max_files = max_files

    def build(
        self,
        request: str,
        *,
        project_name: str | None = None,
        project_root: str | None = None,
        preferred_languages: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> ProjectBuildResult:
        """Generate a complete project without filesystem mutation."""

        if not request or not request.strip():
            return ProjectBuildResult(
                success=False,
                project_name=project_name or "unnamed-project",
                description=request or "",
                errors=("Project request cannot be empty.",),
            )

        name = self._normalize_project_name(
            project_name or self._infer_project_name(request)
        )

        build_context = dict(context or {})

        try:
            language = self._select_language(
                request,
                project_root=project_root,
                preferred_languages=preferred_languages,
            )

            manifest = self._create_manifest(
                request,
                language=language,
                project_name=name,
            )

            files = self._generate_files(
                request,
                manifest,
                language=language,
                project_root=project_root,
                context=build_context,
            )

            errors = tuple(
                f"{project_file.path}: validation failed"
                for project_file in files
                if not project_file.validation.get("passed", True)
            )

            warnings = tuple(
                f"{project_file.path}: "
                + "; ".join(
                    project_file.validation.get("warnings", [])
                )
                for project_file in files
                if project_file.validation.get("warnings")
            )

            return ProjectBuildResult(
                success=bool(files) and not errors,
                project_name=name,
                description=request,
                files=tuple(files),
                errors=errors,
                warnings=warnings,
                metadata={
                    "language": language,
                    "manifest": manifest,
                    "file_count": len(files),
                },
            )

        except Exception as exc:
            logger.exception("Project build failed.")

            return ProjectBuildResult(
                success=False,
                project_name=name,
                description=request,
                errors=(str(exc),),
                metadata={"error": type(exc).__name__},
            )

    def _select_language(
        self,
        request: str,
        *,
        project_root: str | None,
        preferred_languages: list[str] | None,
    ) -> str:
        selector = getattr(
            self.coding_agent,
            "language_selector",
            None,
        )

        if selector is None:
            return "Python"

        selection = selector.select(
            request,
            project_root=project_root,
            preferred_languages=preferred_languages,
        )

        selected = getattr(selection, "selected", None)

        if selected is None:
            raise RuntimeError(
                "No suitable programming language was selected."
            )

        language = getattr(selected, "language", None)

        if not language:
            raise RuntimeError(
                "Language selector returned an invalid language."
            )

        return language

    def _create_manifest(
        self,
        request: str,
        *,
        language: str,
        project_name: str,
    ) -> dict[str, Any]:
        language_lower = language.lower()

        if language_lower == "python":
            files = [
                "src/__init__.py",
                "src/main.py",
                "tests/test_main.py",
                "README.md",
            ]
        elif language_lower in {"javascript", "typescript"}:
            extension = (
                "ts"
                if language_lower == "typescript"
                else "js"
            )
            files = [
                f"src/index.{extension}",
                f"tests/index.test.{extension}",
                "README.md",
            ]
        elif language_lower == "java":
            files = [
                "src/main/Main.java",
                "src/test/MainTest.java",
                "README.md",
            ]
        elif language_lower == "go":
            files = [
                "main.go",
                "main_test.go",
                "README.md",
            ]
        elif language_lower == "rust":
            files = [
                "src/main.rs",
                "tests/main.rs",
                "README.md",
            ]
        else:
            extension = self._LANGUAGE_EXTENSIONS.get(
                language_lower,
                ".txt",
            )
            files = [
                f"src/main{extension}",
                "README.md",
            ]

        return {
            "name": project_name,
            "language": language,
            "request": request,
            "files": files[: self.max_files],
        }

    def _generate_files(
        self,
        request: str,
        manifest: dict[str, Any],
        *,
        language: str,
        project_root: str | None,
        context: dict[str, Any],
    ) -> list[ProjectFile]:
        generated: list[ProjectFile] = []

        for path in manifest["files"]:
            file_language = self._language_for_file(
                path,
                project_language=language,
            )

            file_context = {
                **context,
                "project_root": project_root,
                "project_name": manifest["name"],
                "project_manifest": manifest,
                "target_file": path,
                "selected_language": language,
                "file_language": file_language,
            }

            task = AgentTask(
                goal=(
                    f"Create the file '{path}' as part of the project "
                    f"described below.\n\n"
                    f"Project request:\n{request}"
                ),
                context=file_context,
            )

            result = self.coding_agent.execute(task)

            if not result.success:
                logger.warning(
                    "Generation failed for %s: %s",
                    path,
                    result.output,
                )

                generated.append(
                    ProjectFile(
                        path=path,
                        content="",
                        language=file_language,
                        validation={
                            "passed": False,
                            "score": 0.0,
                            "issues": [result.output],
                            "warnings": [],
                        },
                    )
                )
                continue

            content = self._clean_generated_content(
                result.output
            )

            validation = self._validate_file(
                content,
                path,
                file_language,
            )

            generated.append(
                ProjectFile(
                    path=path,
                    content=content,
                    language=file_language,
                    validation=validation,
                )
            )

        return generated

    def _validate_file(
        self,
        content: str,
        path: str,
        language: str,
    ) -> dict[str, Any]:
        """Validate content according to its actual file type."""

        suffix = self._file_suffix(path)

        if suffix == ".md":
            return self._validate_markdown(content)

        if suffix == ".txt":
            return self._validate_text(content)

        if not content.strip():
            if path.endswith("__init__.py"):
                return {
                    "passed": True,
                    "score": 1.0,
                    "issues": [],
                    "warnings": [
                        "Empty structural file accepted."
                    ],
                }

            return {
                "passed": False,
                "score": 0.0,
                "issues": ["Generated code is empty"],
                "warnings": [],
            }

        return self.quality_checker.check_code(
            content,
            language=language,
        )

    @staticmethod
    def _validate_markdown(content: str) -> dict[str, Any]:
        if not content.strip():
            return {
                "passed": False,
                "score": 0.0,
                "issues": ["Generated documentation is empty"],
                "warnings": [],
                "metrics": {
                    "characters": 0,
                    "lines": 0,
                },
            }

        return {
            "passed": True,
            "score": 100.0,
            "issues": [],
            "warnings": [],
            "metrics": {
                "characters": len(content),
                "lines": len(content.splitlines()),
                "format": "markdown",
            },
        }

    @staticmethod
    def _validate_text(content: str) -> dict[str, Any]:
        if not content.strip():
            return {
                "passed": False,
                "score": 0.0,
                "issues": ["Generated text is empty"],
                "warnings": [],
            }

        return {
            "passed": True,
            "score": 100.0,
            "issues": [],
            "warnings": [],
            "metrics": {
                "characters": len(content),
                "lines": len(content.splitlines()),
                "format": "text",
            },
        }

    @classmethod
    def _language_for_file(
        cls,
        path: str,
        *,
        project_language: str,
    ) -> str:
        suffix = cls._file_suffix(path)

        return cls._EXTENSION_LANGUAGES.get(
            suffix,
            project_language,
        )

    @staticmethod
    def _file_suffix(path: str) -> str:
        match = re.search(
            r"(\.[A-Za-z0-9]+)$",
            path,
        )

        return match.group(1).lower() if match else ""

    @staticmethod
    def _clean_generated_content(content: str) -> str:
        """
        Remove surrounding Markdown fences while preserving one
        trailing newline for generated source artifacts.
        """

        text = (content or "").strip()

        match = re.fullmatch(
            r"```[A-Za-z0-9_+#.-]*\s*\n?(.*?)\n?\s*```",
            text,
            flags=re.DOTALL,
        )

        if match:
            text = match.group(1).strip()

        if not text:
            return ""

        return text + "\n"

    @staticmethod
    def _infer_project_name(request: str) -> str:
        words = re.findall(
            r"[A-Za-z0-9]+",
            request,
        )

        if not words:
            return "ultron-project"

        stop_words = {
            "create",
            "build",
            "make",
            "develop",
            "a",
            "an",
            "the",
            "application",
            "app",
            "project",
            "tool",
            "using",
        }

        useful = [
            word
            for word in words
            if word.lower() not in stop_words
        ]

        if not useful:
            return "ultron-project"

        return "-".join(
            word.lower()
            for word in useful[:4]
        )

    @staticmethod
    def _normalize_project_name(name: str) -> str:
        normalized = re.sub(
            r"[^A-Za-z0-9._-]+",
            "-",
            name.strip().lower(),
        )

        normalized = normalized.strip(".-_")

        return normalized or "ultron-project"
