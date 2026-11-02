from __future__ import annotations

import json
import re
from typing import Any

from ultron.improvement.domain.models import Patch
from ultron.agents.models import AgentTask


class PatchGenerator:
    """
    Production patch generator for ULTRON self-improvement.

    Generation priority:

        CodingAgent
            ↓
        Improvement Advisor
            ↓
        Component Generator
            ↓
        Deterministic fallback

    The CodingAgent is the primary implementation engine.

    This class NEVER applies patches.
    It only produces a validated Patch object for the
    downstream improvement pipeline.
    """

    def __init__(
        self,
        advisor=None,
        context_builder=None,
        component_builder=None,
        code_generator=None,
        coding_agent=None,
    ):
        self.advisor = advisor
        self.context_builder = context_builder
        self.component_builder = component_builder
        self.code_generator = code_generator
        self.coding_agent = coding_agent

    # ============================================================
    # PUBLIC API
    # ============================================================

    def generate(self, plan: dict[str, Any]) -> Patch:
        if not isinstance(plan, dict):
            raise TypeError("plan must be a dictionary")

        operation = str(
            plan.get("operation", "modify")
        ).strip().lower()

        if operation == "create":
            return self._generate_creation_patch(plan)

        return self._generate_modify_patch(
            plan,
            plan.get("context") or {},
        )

    # ============================================================
    # CREATION
    # ============================================================

    def _generate_creation_patch(
        self,
        plan: dict[str, Any],
    ) -> Patch:

        capability = str(
            plan.get("target", "unknown")
        ).strip() or "unknown"

        # --------------------------------------------------------
        # PRIMARY: CodingAgent
        # --------------------------------------------------------

        if self.coding_agent is not None:
            patch = self._generate_with_coding_agent(
                plan,
                operation="create",
            )

            if patch is not None:
                return patch

        # --------------------------------------------------------
        # SECONDARY: Component generator
        # --------------------------------------------------------

        blueprint = plan.get("blueprint")

        if blueprint and self.code_generator:
            try:
                files = self.code_generator.generate(
                    blueprint
                )

                if files:
                    return Patch(
                        target_file="",
                        description=(
                            f"Create {capability} component"
                        ),
                        operation="create",
                        files=files,
                        changes=(
                            "Generated component files"
                        ),
                        risk=plan.get(
                            "risk",
                            "LOW",
                        ),
                    )

            except Exception:
                pass

        # --------------------------------------------------------
        # TERTIARY: Advisor
        # --------------------------------------------------------

        if self.advisor:
            try:
                response = self.advisor.generate_patch(
                    {
                        **plan,
                        "operation": "create",
                    }
                )

                parsed = self._parse_response(response)

                if self._has_real_generation(parsed):
                    return Patch(
                        target_file=str(
                            parsed.get("file", "")
                        ),
                        description=parsed.get(
                            "description",
                            f"Create {capability} capability",
                        ),
                        operation="create",
                        files=parsed.get(
                            "files",
                            [],
                        ),
                        changes=parsed.get(
                            "changes",
                            "",
                        ),
                        risk=plan.get(
                            "risk",
                            "LOW",
                        ),
                    )

            except Exception:
                pass

        # --------------------------------------------------------
        # HARD FAILURE
        #
        # Do NOT return an empty patch.
        # Empty generated code was the exact failure we are fixing.
        # --------------------------------------------------------

        raise RuntimeError(
            "No code generation engine produced a usable "
            f"implementation for capability '{capability}'."
        )

    # ============================================================
    # MODIFICATION
    # ============================================================

    def _generate_modify_patch(
        self,
        plan: dict[str, Any],
        context: dict[str, Any],
    ) -> Patch:

        context = (
            context
            if isinstance(context, dict)
            else {}
        )

        # --------------------------------------------------------
        # PRIMARY: CodingAgent
        # --------------------------------------------------------

        if self.coding_agent is not None:
            patch = self._generate_with_coding_agent(
                plan,
                operation="modify",
            )

            if patch is not None:
                return patch

        # --------------------------------------------------------
        # SECONDARY: Advisor
        # --------------------------------------------------------

        if self.advisor:
            try:
                response = self.advisor.generate_patch(
                    {
                        **plan,
                        "context": context,
                    }
                )

                parsed = self._parse_response(response)

                if self._has_real_generation(parsed):
                    return self._patch_from_parsed_response(
                        parsed,
                        plan,
                        context,
                    )

            except Exception:
                pass

        # --------------------------------------------------------
        # Existing source fallback
        # --------------------------------------------------------

        files = context.get("files") or []

        target_file = ""
        original_code = ""

        if files and isinstance(
            files[0],
            dict,
        ):
            target_file = str(
                files[0].get(
                    "file",
                    "",
                )
            ).strip()

            original_code = files[0].get(
                "content",
                "",
            )

            if not isinstance(
                original_code,
                str,
            ):
                original_code = str(
                    original_code
                )

        if not target_file:
            target_file = self._resolve_target_file(
                plan,
                context,
            )

        # --------------------------------------------------------
        # If source exists, never silently replace it with nothing.
        # --------------------------------------------------------

        if original_code.strip():

            return Patch(
                target_file=target_file,
                description=(
                    "Improve implementation based "
                    "on repository analysis"
                ),
                changes=plan.get(
                    "problem",
                    "",
                ),
                original_code=original_code,
                replacement_code=original_code,
                risk=plan.get(
                    "risk",
                    "LOW",
                ),
                operation="modify",
            )

        # --------------------------------------------------------
        # No usable generator and no source.
        #
        # Fail explicitly rather than manufacturing fake code.
        # --------------------------------------------------------

        capability = str(
            plan.get(
                "target",
                "unknown",
            )
        )

        raise RuntimeError(
            "Unable to generate a real modification for "
            f"'{capability}'. CodingAgent and advisor did not "
            "produce usable code and no source context was available."
        )

    # ============================================================
    # CODING AGENT
    # ============================================================

    def _generate_with_coding_agent(
        self,
        plan: dict[str, Any],
        operation: str,
    ) -> Patch | None:

        if self.coding_agent is None:
            return None

        target = str(
            plan.get(
                "target",
                "unknown",
            )
        ).strip()

        problem = str(
            plan.get(
                "problem",
                "Implement the requested capability.",
            )
        ).strip()

        description = str(
            plan.get(
                "description",
                "",
            )
        ).strip()

        context = plan.get(
            "context",
            {},
        )

        if not isinstance(
            context,
            dict,
        ):
            context = {}

        goal = (
            f"Implement a real production-level "
            f"{operation} for ULTRON.\n\n"
            f"Capability: {target}\n"
            f"Problem: {problem}\n"
            f"Description: {description}\n\n"
            "You are ULTRON's implementation coding agent. "
            "Analyze the supplied repository context and produce "
            "actual working code. Do not return placeholders, "
            "mock implementations, empty code, pseudocode, or "
            "explanations instead of code.\n\n"
            "The implementation must integrate with the existing "
            "ULTRON architecture and preserve existing behavior "
            "unless the requested improvement requires a change.\n\n"
            "Return ONLY valid JSON using this structure:\n"
            "{\n"
            '  "file": "path/to/file.py",\n'
            '  "description": "what changed",\n'
            '  "changes": "why and how",\n'
            '  "original": "existing code when modifying",\n'
            '  "replacement": "complete replacement code",\n'
            '  "files": []\n'
            "}\n\n"
            "For a new component, use the files array:\n"
            "[\n"
            '  {"file": "path.py", "content": "real code"}\n'
            "]\n\n"
            "Every generated Python file must contain executable "
            "implementation code."
        )

        task = AgentTask(
            goal=goal,
            required_capabilities=[
                "code_generation",
                "repository_analysis",
                "software_engineering",
            ],
            priority=10,
            constraints={
                "operation": operation,
                "no_placeholders": True,
                "no_empty_code": True,
                "production_level": True,
                "preserve_architecture": True,
            },
            context={
                "plan": plan,
                "repository_context": context,
            },
        )

        try:
            result = self.coding_agent.execute(
                task
            )
        except Exception:
            return None

        response = self._extract_agent_output(
            result
        )

        parsed = self._parse_response(
            response
        )

        if not self._has_real_generation(
            parsed
        ):
            return None

        if operation == "create":
            files = parsed.get(
                "files",
                [],
            )

            if not files:
                file_name = parsed.get(
                    "file"
                )
                content = parsed.get(
                    "replacement"
                )

                if file_name and content:
                    files = [
                        {
                            "file": file_name,
                            "content": content,
                        }
                    ]

            if not files:
                return None

            return Patch(
                target_file=str(
                    parsed.get(
                        "file",
                        "",
                    )
                ),
                description=parsed.get(
                    "description",
                    f"Create {target} capability",
                ),
                operation="create",
                files=files,
                changes=parsed.get(
                    "changes",
                    "",
                ),
                risk=plan.get(
                    "risk",
                    "MEDIUM",
                ),
            )

        return self._patch_from_parsed_response(
            parsed,
            plan,
            context,
        )

    # ============================================================
    # AGENT RESULT EXTRACTION
    # ============================================================

    @staticmethod
    def _extract_agent_output(
        result: Any,
    ) -> Any:

        if result is None:
            return None

        if isinstance(
            result,
            (dict, str),
        ):
            if isinstance(result, dict):
                if "output" in result:
                    return result["output"]

                if "result" in result:
                    nested = result["result"]

                    if isinstance(
                        nested,
                        dict,
                    ):
                        return nested.get(
                            "output",
                            nested,
                        )

            return result

        output = getattr(
            result,
            "output",
            None,
        )

        if output is not None:
            return output

        return str(result)

    # ============================================================
    # VALIDATION
    # ============================================================

    @staticmethod
    def _has_real_generation(
        parsed: dict[str, Any] | None,
    ) -> bool:

        if not isinstance(
            parsed,
            dict,
        ):
            return False

        replacement = parsed.get(
            "replacement",
            "",
        )

        if isinstance(
            replacement,
            str,
        ) and replacement.strip():
            return True

        files = parsed.get(
            "files",
            [],
        )

        if not isinstance(
            files,
            list,
        ):
            return False

        for item in files:
            if not isinstance(
                item,
                dict,
            ):
                continue

            content = item.get(
                "content",
                "",
            )

            if (
                isinstance(
                    content,
                    str,
                )
                and content.strip()
            ):
                return True

        return False

    # ============================================================
    # TARGET RESOLUTION
    # ============================================================

    @staticmethod
    def _resolve_target_file(
        plan: dict[str, Any],
        context: dict[str, Any],
    ) -> str:

        candidates = (
            context.get(
                "target_file"
            ),
            plan.get(
                "target_file"
            ),
            plan.get(
                "file"
            ),
        )

        for candidate in candidates:
            if candidate:
                return str(
                    candidate
                ).strip()

        target = str(
            plan.get(
                "target",
                "unknown",
            )
        ).strip()

        if (
            target
            and target.lower()
            != "unknown"
        ):
            return target

        return (
            "ultron/improvement/"
            "generated_improvement.py"
        )

    # ============================================================
    # PATCH CONVERSION
    # ============================================================

    @staticmethod
    def _patch_from_parsed_response(
        parsed: dict[str, Any],
        plan: dict[str, Any],
        context: dict[str, Any],
    ) -> Patch:

        target_file = parsed.get(
            "file"
        )

        if not target_file:
            target_file = (
                PatchGenerator
                ._resolve_target_file(
                    plan,
                    context,
                )
            )

        replacement = parsed.get(
            "replacement",
            "",
        )

        original = parsed.get(
            "original",
            "",
        )

        # A coding agent may return a single file through files[]
        # instead of replacement.
        if (
            not isinstance(
                replacement,
                str,
            )
            or not replacement.strip()
        ):
            files = parsed.get(
                "files",
                [],
            )

            if (
                isinstance(
                    files,
                    list,
                )
                and files
                and isinstance(
                    files[0],
                    dict,
                )
            ):
                first = files[0]

                if not target_file:
                    target_file = first.get(
                        "file",
                        target_file,
                    )

                replacement = first.get(
                    "content",
                    "",
                )

        if (
            not isinstance(
                replacement,
                str,
            )
            or not replacement.strip()
        ):
            raise RuntimeError(
                "Coding agent returned no executable replacement code."
            )

        return Patch(
            target_file=str(
                target_file
            ),
            description=parsed.get(
                "description",
                "Improve implementation based on analysis",
            ),
            original_code=(
                original
                if isinstance(
                    original,
                    str,
                )
                else str(original)
            ),
            replacement_code=replacement,
            changes=parsed.get(
                "changes",
                plan.get(
                    "problem",
                    "",
                ),
            ),
            risk=plan.get(
                "risk",
                "MEDIUM",
            ),
            operation="modify",
        )

    # ============================================================
    # RESPONSE PARSER
    # ============================================================

    @staticmethod
    def _parse_response(
        response: Any,
    ) -> dict[str, Any] | None:

        if not response:
            return None

        if isinstance(
            response,
            dict,
        ):
            return response

        if not isinstance(
            response,
            str,
        ):
            return None

        text = response.strip()

        # Remove markdown fences.
        fenced = re.search(
            r"```(?:json)?\s*(.*?)\s*```",
            text,
            flags=(
                re.DOTALL
                | re.IGNORECASE
            ),
        )

        if fenced:
            text = fenced.group(
                1
            ).strip()

        # Direct JSON.
        try:
            parsed = json.loads(
                text
            )

            if isinstance(
                parsed,
                dict,
            ):
                return parsed

        except json.JSONDecodeError:
            pass

        # JSON embedded inside explanatory text.
        start = text.find("{")
        end = text.rfind("}")

        if (
            start == -1
            or end == -1
            or end <= start
        ):
            return None

        candidate = text[
            start:end + 1
        ]

        try:
            parsed = json.loads(
                candidate
            )

            if isinstance(
                parsed,
                dict,
            ):
                return parsed

        except json.JSONDecodeError:
            return None

        return None