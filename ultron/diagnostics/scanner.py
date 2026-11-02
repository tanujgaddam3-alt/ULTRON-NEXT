import ast
import subprocess
from pathlib import Path
from typing import ClassVar


class ModuleScanner:
    """
    Static module health scanner.
    Does not execute ULTRON code.
    """

    def scan(self, root="ultron"):

        results: ClassVar = {}

        for file in Path(root).rglob("*.py"):
            module = str(file)

            try:
                ast.parse(file.read_text(encoding="utf-8"))

                results[module] = {"status": "valid"}

            except Exception as e:
                results[module] = {"status": "failed", "error": str(e)}

        return results


class DependencyScanner:
    """
    Scans Python files and extracts imports.
    Helps detect dependency structure.
    """

    def scan(self, root="ultron"):

        results: ClassVar = []

        for file in Path(root).rglob("*.py"):
            try:
                tree = ast.parse(file.read_text(encoding="utf-8"))

                imports: ClassVar = []

                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for item in node.names:
                            imports.append(item.name)

                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imports.append(node.module)

                results.append(
                    {
                        "file": str(file),
                        "imports": imports,
                    }
                )

            except (OSError, UnicodeError, SyntaxError) as e:
                results.append(
                    {
                        "file": str(file),
                        "error": str(e),
                    }
                )

        return results


class TestScanner:
    """
    Runs ULTRON test suite.
    """

    def run(self):

        result = subprocess.run(
            ["pytest", "-q"],
            capture_output=True,
            text=True,
            check=False,
        )

        return {
            "passed": result.returncode == 0,
            "output": result.stdout + result.stderr,
        }


class FileScanner:
    """
    Detects empty python files.
    """

    def scan(self, root="ultron"):

        empty: ClassVar = []

        for file in Path(root).rglob("*.py"):
            if file.stat().st_size == 0:
                empty.append(str(file))

        return empty


class SyntaxScanner:
    """
    Checks Python syntax validity.
    """

    def scan(self, root="ultron"):

        result: ClassVar = []

        for file in Path(root).rglob("*.py"):
            try:
                ast.parse(file.read_text(encoding="utf-8"))

                result.append(
                    {
                        "file": str(file),
                        "status": "valid",
                    }
                )

            except (OSError, UnicodeError, SyntaxError) as e:
                result.append(
                    {
                        "file": str(file),
                        "status": "invalid",
                        "error": str(e),
                    }
                )

        return result
