import ast
import re
from pathlib import Path


class QualityChecker:
    """
    Deterministic quality validation for generated and improvement code.

    The checker intentionally performs static validation only.
    It never executes generated code.
    """

    def check(
        self,
        patch,
    ) -> dict:
        issues: list[str] = []

        operation = getattr(
            patch,
            "operation",
            "modify",
        )

        if operation == "create":
            if not getattr(patch, "files", None):
                issues.append(
                    "Missing files for creation patch"
                )
        elif not getattr(patch, "target_file", None):
            issues.append(
                "Missing target file"
            )

        if not getattr(patch, "description", None):
            issues.append(
                "Missing description"
            )

        changes = getattr(
            patch,
            "changes",
            "",
        )

        if "delete" in changes.lower():
            issues.append(
                "Deletion requires approval"
            )

        code = getattr(
            patch,
            "replacement_code",
            "",
        )

        code_result = self.check_code(
            code,
            language="Python",
        )

        issues.extend(code_result["issues"])

        return {
            "passed": not issues,
            "score": code_result["score"] if code else 0.0,
            "issues": issues,
            "metrics": code_result["metrics"],
        }

    def check_code(
        self,
        code: str,
        *,
        language: str = "Python",
    ) -> dict:
        """
        Validate generated source without executing it.

        Currently Python receives full AST validation.
        Other languages receive deterministic structural checks
        until language-specific parsers are added.
        """

        issues: list[str] = []
        warnings: list[str] = []

        if not isinstance(code, str):
            return {
                "passed": False,
                "score": 0.0,
                "issues": [
                    "Generated code must be a string"
                ],
                "warnings": [],
                "metrics": {},
            }

        normalized = self._strip_markdown_fence(
            code.strip()
        )

        if not normalized:
            issues.append(
                "Generated code is empty"
            )

        if self._looks_like_llm_placeholder(normalized):
            issues.append(
                "Generated output appears to be a placeholder rather than implementation code"
            )

        metrics = {
            "characters": len(normalized),
            "lines": len(normalized.splitlines()) if normalized else 0,
            "has_markdown_fence": normalized != code.strip(),
        }

        language_name = language.strip().lower()

        if language_name == "python":
            try:
                tree = ast.parse(normalized)
            except SyntaxError as exc:
                issues.append(
                    "Generated Python has invalid syntax: "
                    f"{exc.msg} at line {exc.lineno}"
                )
            except Exception as exc:
                issues.append(
                    "Python parsing failed: "
                    f"{type(exc).__name__}: {exc}"
                )
            else:
                metrics.update(
                    self._python_metrics(tree)
                )

                if not metrics["has_executable_nodes"]:
                    issues.append(
                        "Generated Python contains no executable statements"
                    )

                if metrics["function_count"] == 0 and metrics["class_count"] == 0:
                    warnings.append(
                        "Generated Python contains no functions or classes"
                    )
        else:
            if len(normalized) < 20:
                warnings.append(
                    "Generated source is unusually short"
                )

        score = self._score(
            issues=issues,
            warnings=warnings,
            metrics=metrics,
        )

        return {
            "passed": not issues,
            "score": score,
            "issues": issues,
            "warnings": warnings,
            "metrics": metrics,
        }

    @staticmethod
    def _strip_markdown_fence(
        code: str,
    ) -> str:
        """
        Remove a surrounding markdown code fence.

        LLMs love putting Python inside ```python because apparently
        code needs a little ceremonial packaging before entering the world.
        """

        match = re.fullmatch(
            r"\s*```(?:[A-Za-z0-9_+#.-]+)?\s*\n?(.*?)\n?\s*```\s*",
            code,
            flags=re.DOTALL,
        )

        if match:
            return match.group(1).strip()

        return code

    @staticmethod
    def _looks_like_llm_placeholder(
        code: str,
    ) -> bool:
        lowered = code.lower()

        placeholders = (
            "your code here",
            "implement here",
            "implementation goes here",
            "todo: implement",
            "pass  # implement",
            "coming soon",
            "placeholder implementation",
        )

        return any(
            marker in lowered
            for marker in placeholders
        )

    @staticmethod
    def _python_metrics(
        tree: ast.AST,
    ) -> dict:
        nodes = list(
            ast.walk(tree)
        )

        return {
            "ast_nodes": len(nodes),
            "function_count": sum(
                isinstance(
                    node,
                    (ast.FunctionDef, ast.AsyncFunctionDef),
                )
                for node in nodes
            ),
            "class_count": sum(
                isinstance(
                    node,
                    ast.ClassDef,
                )
                for node in nodes
            ),
            "import_count": sum(
                isinstance(
                    node,
                    (ast.Import, ast.ImportFrom),
                )
                for node in nodes
            ),
            "has_executable_nodes": any(
                isinstance(
                    node,
                    (
                        ast.Expr,
                        ast.Assign,
                        ast.AnnAssign,
                        ast.AugAssign,
                        ast.Return,
                        ast.Raise,
                        ast.If,
                        ast.For,
                        ast.While,
                        ast.Try,
                        ast.With,
                        ast.Match,
                        ast.Assert,
                    ),
                )
                for node in nodes
            ),
        }

    @staticmethod
    def _score(
        *,
        issues: list[str],
        warnings: list[str],
        metrics: dict,
    ) -> float:
        if issues:
            return 0.0

        score = 100.0

        score -= min(
            15.0,
            len(warnings) * 5.0,
        )

        if metrics.get("lines", 0) > 1000:
            score -= 10.0

        return round(
            max(0.0, score),
            2,
        )