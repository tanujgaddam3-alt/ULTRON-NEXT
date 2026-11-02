import ast
from typing import ClassVar


class DependencyMapper:
    """
    Maps dependencies inside Python source code.
    """

    def analyze(
        self,
        source: str,
    ) -> dict:

        tree = ast.parse(source)

        dependencies: ClassVar = []

        for node in ast.walk(tree):
            if isinstance(
                node,
                ast.Import,
            ):
                for item in node.names:
                    dependencies.append(item.name)

            elif isinstance(
                node,
                ast.ImportFrom,
            ):
                if node.module:
                    dependencies.append(node.module)

            elif isinstance(
                node,
                ast.Call,
            ):
                if isinstance(
                    node.func,
                    ast.Name,
                ):
                    dependencies.append(node.func.id)

        return {"dependencies": list(set(dependencies))}
