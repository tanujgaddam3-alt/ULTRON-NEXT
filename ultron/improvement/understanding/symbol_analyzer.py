import ast
from typing import ClassVar


class SymbolAnalyzer:
    """
    Extracts structural information
    from Python source code.
    """

    def analyze(
        self,
        source: str,
    ) -> dict:

        tree = ast.parse(source)

        classes: ClassVar = []
        functions: ClassVar = []
        imports: ClassVar = []

        for node in ast.walk(tree):
            if isinstance(
                node,
                ast.ClassDef,
            ):
                classes.append(node.name)

            elif isinstance(
                node,
                ast.FunctionDef,
            ):
                functions.append(node.name)

            elif isinstance(
                node,
                ast.Import,
            ):
                for item in node.names:
                    imports.append(item.name)

            elif isinstance(
                node,
                ast.ImportFrom,
            ):
                imports.append(node.module)

        return {
            "classes": classes,
            "functions": functions,
            "imports": imports,
        }
