from dataclasses import dataclass, field

from ultron.improvement.understanding.dependency_mapper import (
    DependencyMapper,
)
from ultron.improvement.understanding.file_reader import (
    FileReader,
)
from ultron.improvement.understanding.symbol_analyzer import (
    SymbolAnalyzer,
)


@dataclass
class CodeUnderstandingReport:
    """
    Complete understanding of a source file.
    """

    file: str

    symbols: dict = field(default_factory=dict)

    dependencies: dict = field(default_factory=dict)

    risk: str = "unknown"


class UnderstandingEngine:
    """
    Coordinates source code analysis.
    """

    def __init__(self):

        self.reader = FileReader()

        self.symbols = SymbolAnalyzer()

        self.dependencies = DependencyMapper()

    def analyze(
        self,
        file_path: str,
    ) -> CodeUnderstandingReport:

        source = self.reader.read(file_path)

        symbols = self.symbols.analyze(source)

        dependencies = self.dependencies.analyze(source)

        return CodeUnderstandingReport(
            file=file_path,
            symbols=symbols,
            dependencies=dependencies,
            risk=self._calculate_risk(
                symbols,
                dependencies,
            ),
        )

    def _calculate_risk(
        self,
        symbols,
        dependencies,
    ):

        count = (
            len(symbols["classes"])
            + len(symbols["functions"])
            + len(dependencies["dependencies"])
        )

        if count > 20:
            return "high"

        if count > 10:
            return "medium"

        return "low"
