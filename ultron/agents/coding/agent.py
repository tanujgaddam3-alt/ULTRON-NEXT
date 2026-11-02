from __future__ import annotations

import logging
from typing import Any, ClassVar

from ultron.agents.base import BaseAgent
from ultron.agents.models import AgentResult, AgentTask
from ultron.agents.coding.intelligence import LanguageSelector
from ultron.improvement.validator.quality_checker import QualityChecker


logger = logging.getLogger(__name__)


class CodingAgent(BaseAgent):
    """
    Production coding agent.

    Responsibilities:
    - Select an appropriate programming language.
    - Build a deterministic engineering prompt.
    - Generate code through the configured LLM provider.
    - Validate generated code.
    - Request bounded repairs when validation fails.
    - Return structured metadata describing the generation process.

    The agent never executes generated code and never modifies project files.
    """

    name = "coding"

    capabilities: ClassVar[list[str]] = [
        "code_generation",
        "code_validation",
    ]

    def __init__(
        self,
        improvement_service=None,
        llm_provider=None,
        language_selector: LanguageSelector | None = None,
        quality_checker: QualityChecker | None = None,
        max_repair_attempts: int = 2,
    ) -> None:
        self.improvement_service = improvement_service
        self.llm_provider = llm_provider
        self.language_selector = (
            language_selector or LanguageSelector()
        )
        self.quality_checker = (
            quality_checker or QualityChecker()
        )

        if max_repair_attempts < 0:
            raise ValueError(
                "max_repair_attempts cannot be negative."
            )

        self.max_repair_attempts = max_repair_attempts

    def execute(
        self,
        task: AgentTask,
    ) -> AgentResult:
        """
        Generate and validate code for an AgentTask.
        """

        try:
            if not isinstance(task.goal, str) or not task.goal.strip():
                return self._failure(
                    output="Coding task goal cannot be empty.",
                    error="empty_goal",
                )

            goal = task.goal.strip()

            if self.llm_provider is None:
                return self._failure(
                    output="Coding generation unavailable: LLM provider is not configured.",
                    error="llm_provider_unavailable",
                )

            task_context = dict(task.context or {})

            project_root = task_context.get(
                "project_root"
            )

            preferred_languages = task_context.get(
                "preferred_languages"
            )

            selection = self.language_selector.select(
                goal,
                project_root=project_root,
                preferred_languages=preferred_languages,
            )

            if selection.selected is None:
                return self._failure(
                    output=selection.explanation,
                    error="language_selection_failed",
                    metadata={
                        "language_selection": {
                            "confidence": selection.confidence,
                            "explanation": selection.explanation,
                        }
                    },
                )

            selected = selection.selected
            language = selected.language

            generation_context = {
                "goal": goal,
                "selected_language": language,
                "task_context": task_context,
                "language_requirements": (
                    self._requirements_metadata(
                        selection.requirements
                    )
                ),
            }

            prompt = self._build_generation_prompt(
                goal=goal,
                language=language,
                task_context=task_context,
                selection=selection,
            )

            code = self.llm_provider.generate(
                prompt,
                generation_context,
            )

            if not isinstance(code, str):
                code = str(code)

            code = self._clean_generated_content(code)

            validation = self._validate_generated_code(
                code,
                language,
            )

            repair_attempts = 0

            while (
                not validation["passed"]
                and repair_attempts < self.max_repair_attempts
            ):
                repair_attempts += 1

                repair_prompt = self._build_repair_prompt(
                    goal=goal,
                    language=language,
                    code=code,
                    validation=validation,
                    attempt=repair_attempts,
                    task_context=task_context,
                )

                repair_context = {
                    **generation_context,
                    "repair_attempt": repair_attempts,
                    "validation": validation,
                }

                code = self.llm_provider.generate(
                    repair_prompt,
                    repair_context,
                )

                if not isinstance(code, str):
                    code = str(code)

                validation = self._validate_generated_code(
                    code,
                    language,
                )

            metadata = {
                "language_selection": self._selection_metadata(
                    selection
                ),
                "validation": validation,
                "repair_attempts": repair_attempts,
                "max_repair_attempts": (
                    self.max_repair_attempts
                ),
                "generation": {
                    "repair_attempts": repair_attempts,
                    "max_repair_attempts": (
                        self.max_repair_attempts
                    ),
                    "validated": validation["passed"],
                },
            }

            if not validation["passed"]:
                return self._failure(
                    output=(
                        "Generated code failed quality validation "
                        f"after {repair_attempts} repair attempt(s)."
                    ),
                    error="generated_code_failed_validation",
                    metadata=metadata,
                )

            return AgentResult(
                success=True,
                agent=self.name,
                output=code,
                capabilities_used=[
                    "code_generation",
                ],
                metadata=metadata,
            )

        except Exception as exc:
            logger.exception(
                "CodingAgent execution failed."
            )

            return self._failure(
                output=f"Coding generation failed: {exc}",
                error=type(exc).__name__,
                metadata={
                    "error_message": str(exc),
                },
            )

    def _validate_goal(
        self,
        goal: str,
    ) -> str:
        if not isinstance(goal, str):
            raise ValueError(
                "Coding task goal must be a string."
            )

        normalized = goal.strip()

        if not normalized:
            raise ValueError(
                "Coding task goal cannot be empty."
            )

        return normalized
    def _clean_generated_content(
        self,
        content: str,
    ) -> str:
        """
        Normalize LLM output before validation and storage.

        Removes Markdown code fences commonly returned by LLMs
        while preserving the actual generated source code.
        """

        text = (content or "").strip()

        if not text:
            return ""

        lines = text.splitlines()

        # Remove opening Markdown fence.
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]

        # Remove closing Markdown fence.
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        return "\n".join(lines).strip()

    

    def _validate_generated_code(
        self,
        code: str,
        language: str,
    ) -> dict[str, Any]:
        """
        Validate generated source without executing it.

        Delegates deterministic source validation to QualityChecker
        and always returns the structured validation result expected
        by the CodingAgent execution pipeline.
        """

        if not isinstance(code, str):
            return {
                "passed": False,
                "score": 0.0,
                "issues": [
                    "Generated code must be a string."
                ],
                "warnings": [],
                "metrics": {},
            }

        try:
            result = self.quality_checker.check_code(
                code,
                language=language,
            )
        except Exception as exc:
            self.logger.exception(
                "Generated code validation failed."
            )

            return {
                "passed": False,
                "score": 0.0,
                "issues": [
                    "Code validation failed: "
                    f"{type(exc).__name__}: {exc}"
                ],
                "warnings": [],
                "metrics": {},
            }

        if not isinstance(result, dict):
            return {
                "passed": False,
                "score": 0.0,
                "issues": [
                    "QualityChecker returned an invalid validation result."
                ],
                "warnings": [],
                "metrics": {},
            }

        return result
    def _build_generation_prompt(
        self,
        *,
        goal: str,
        language: str,
        task_context: dict[str, Any],
        selection,
    ) -> str:
        requirements = self._requirements_metadata(
            selection.requirements
        )

        return f"""
You are ULTRON's coding engine.

Generate production-quality source code for the following engineering task.

Task:
{goal}

Selected language: {language}

Engineering requirements:
{requirements}

Project context:
{task_context}

Rules:
1. Produce complete, maintainable implementation code.
2. Prefer clear architecture and strong separation of concerns.
3. Handle realistic errors and edge cases.
4. Use appropriate typing and validation where supported.
5. Avoid placeholder implementations.
6. Do not use TODO markers as substitutes for implementation.
7. Do not explain the code unless explanation is explicitly requested.
8. Return the source code itself.
9. Do not execute commands or modify files.
10. Do not invent unavailable dependencies when standard-library or established alternatives are sufficient.
11. Follow the selected language's idioms and production conventions.
""".strip()

    def _build_repair_prompt(
        self,
        *,
        goal: str,
        language: str,
        code: str,
        validation: dict[str, Any],
        attempt: int,
        task_context: dict[str, Any],
    ) -> str:
        issues = validation.get(
            "issues",
            [],
        )

        metrics = validation.get(
            "metrics",
            {},
        )

        return f"""
You are ULTRON's code repair engine.

The previous generated implementation failed quality validation.

Original task:
{goal}

Language:
{language}

Repair attempt:
{attempt}

Project context:
{task_context}

Validation issues:
{issues}

Validation metrics:
{metrics}

Previous implementation:
{code}

Repair requirements:
1. Return the complete corrected source code.
2. Preserve the original task intent.
3. Fix every reported validation issue.
4. Do not merely suppress or hide the problem.
5. Do not replace implementation with placeholders.
6. Do not add TODO markers as a workaround.
7. Keep the implementation production-quality.
8. Return source code only.
9. Do not execute commands or modify files.
""".strip()

    def _requirements_metadata(
        self,
        requirements,
    ) -> dict[str, Any]:
        if requirements is None:
            return {}

        if hasattr(
            requirements,
            "model_dump",
        ):
            return requirements.model_dump()

        if hasattr(
            requirements,
            "__dict__",
        ):
            return dict(requirements.__dict__)

        return {
            "value": str(requirements)
        }

    def _candidate_metadata(
        self,
        candidate,
    ) -> dict[str, Any]:
        """
        Serialize a LanguageCandidate safely.

        Important:
        confidence belongs to LanguageSelection, not LanguageCandidate.
        """

        if candidate is None:
            return {}

        metadata: dict[str, Any] = {}

        for attribute in (
            "language",
            "score",
            "reasons",
            "available",
        ):
            if hasattr(candidate, attribute):
                value = getattr(
                    candidate,
                    attribute,
                )

                if isinstance(
                    value,
                    tuple,
                ):
                    value = list(value)

                metadata[attribute] = value

        return metadata

    def _selection_metadata(
        self,
        selection,
    ) -> dict[str, Any]:
        return {
            "selected": self._candidate_metadata(
                selection.selected
            ),
            "alternatives": [
                self._candidate_metadata(
                    candidate
                )
                for candidate in (
                    selection.alternatives or ()
                )
            ],
            "requirements": (
                self._requirements_metadata(
                    selection.requirements
                )
            ),
            "confidence": selection.confidence,
            "explanation": selection.explanation,
        }

    def _failure(
        self,
        *,
        output: str,
        error: str,
        metadata: dict[str, Any] | None = None,
    ) -> AgentResult:
        return AgentResult(
            success=False,
            agent=self.name,
            output=output,
            capabilities_used=[
                "code_generation",
            ],
            metadata={
                "error": error,
                **(metadata or {}),
            },
        )
