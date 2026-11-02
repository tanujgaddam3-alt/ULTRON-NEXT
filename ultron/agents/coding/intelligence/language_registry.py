from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil


@dataclass(frozen=True)
class LanguageSpec:
    """
    Machine-readable knowledge about a programming language.

    This registry is deliberately extensible. ULTRON should not need
    its core architecture changed merely because a new language appears.
    """

    name: str
    aliases: tuple[str, ...]
    extensions: tuple[str, ...]
    filenames: tuple[str, ...] = ()

    compiler_commands: tuple[str, ...] = ()
    runtime_commands: tuple[str, ...] = ()
    package_managers: tuple[str, ...] = ()
    build_tools: tuple[str, ...] = ()
    test_tools: tuple[str, ...] = ()
    formatters: tuple[str, ...] = ()
    linters: tuple[str, ...] = ()
    type_checkers: tuple[str, ...] = ()

    paradigms: tuple[str, ...] = ()
    strengths: tuple[str, ...] = ()
    weaknesses: tuple[str, ...] = ()

    performance: float = 0.5
    safety: float = 0.5
    portability: float = 0.5
    ecosystem: float = 0.5
    development_speed: float = 0.5

    @property
    def normalized_name(self) -> str:
        return self.name.lower().strip()


class LanguageRegistry:
    """
    Central language/toolchain knowledge base.

    It knows language characteristics and can inspect the current machine
    to determine which toolchains are actually available.
    """

    def __init__(self, specs: list[LanguageSpec] | None = None):
        self._languages: dict[str, LanguageSpec] = {}

        for spec in specs or self._default_specs():
            self.register(spec)

    def register(self, spec: LanguageSpec) -> None:
        self._languages[spec.normalized_name] = spec

    def get(self, name: str) -> LanguageSpec | None:
        normalized = name.lower().strip()

        if normalized in self._languages:
            return self._languages[normalized]

        for spec in self._languages.values():
            if normalized in {
                alias.lower()
                for alias in spec.aliases
            }:
                return spec

        return None

    def all(self) -> list[LanguageSpec]:
        return list(self._languages.values())

    def detect_from_path(self, path: str | Path) -> LanguageSpec | None:
        path = Path(path)

        filename = path.name.lower()

        for spec in self._languages.values():
            if filename in {
                value.lower()
                for value in spec.filenames
            }:
                return spec

        suffix = path.suffix.lower()

        for spec in self._languages.values():
            if suffix in {
                extension.lower()
                for extension in spec.extensions
            }:
                return spec

        return None

    def detect_project(self, root: str | Path) -> list[LanguageSpec]:
        """
        Detect languages actually represented in a repository.

        Hidden/build directories and dependency trees are intentionally
        ignored.
        """

        root = Path(root)

        if not root.exists():
            return []

        ignored = {
            ".git",
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            "target",
            "build",
            "dist",
            ".idea",
            ".vscode",
        }

        found: dict[str, LanguageSpec] = {}

        try:
            paths = root.rglob("*")
        except OSError:
            return []

        for path in paths:
            if not path.is_file():
                continue

            if any(part in ignored for part in path.parts):
                continue

            spec = self.detect_from_path(path)

            if spec:
                found[spec.normalized_name] = spec

        return list(found.values())

    def available_toolchains(
        self,
        spec: LanguageSpec,
    ) -> dict[str, list[str]]:
        """
        Discover executable toolchains available on the current host.
        """

        categories = {
            "compilers": spec.compiler_commands,
            "runtimes": spec.runtime_commands,
            "package_managers": spec.package_managers,
            "build_tools": spec.build_tools,
            "test_tools": spec.test_tools,
            "formatters": spec.formatters,
            "linters": spec.linters,
            "type_checkers": spec.type_checkers,
        }

        result: dict[str, list[str]] = {}

        for category, commands in categories.items():
            available = []

            for command in commands:
                executable = shutil.which(command)

                if executable:
                    available.append(executable)

            result[category] = available

        return result

    def environment_report(self) -> list[dict]:
        report = []

        for spec in self.all():
            tools = self.available_toolchains(spec)

            available = any(
                tools.values()
            )

            report.append(
                {
                    "language": spec.name,
                    "available": available,
                    "toolchain": tools,
                }
            )

        return report

    @staticmethod
    def _default_specs() -> list[LanguageSpec]:
        return [
            LanguageSpec(
                name="Python",
                aliases=("py",),
                extensions=(".py",),
                filenames=("pyproject.toml", "requirements.txt", "setup.py"),
                compiler_commands=("python",),
                runtime_commands=("python", "python3"),
                package_managers=("pip", "uv", "poetry"),
                build_tools=("build",),
                test_tools=("pytest", "unittest"),
                formatters=("ruff", "black"),
                linters=("ruff", "pylint"),
                type_checkers=("mypy", "pyright"),
                paradigms=("imperative", "object-oriented", "functional"),
                strengths=("rapid development", "AI/ML", "automation", "web"),
                weaknesses=("raw CPU performance", "mobile development"),
                performance=0.65,
                safety=0.65,
                portability=0.90,
                ecosystem=0.98,
                development_speed=0.98,
            ),

            LanguageSpec(
                name="Rust",
                aliases=("rs",),
                extensions=(".rs",),
                filenames=("Cargo.toml",),
                compiler_commands=("rustc",),
                runtime_commands=(),
                package_managers=("cargo",),
                build_tools=("cargo",),
                test_tools=("cargo",),
                formatters=("rustfmt",),
                linters=("clippy",),
                type_checkers=("rustc",),
                paradigms=("systems", "functional", "imperative"),
                strengths=(
                    "performance",
                    "memory safety",
                    "concurrency",
                    "systems programming",
                ),
                weaknesses=("learning curve",),
                performance=0.99,
                safety=0.99,
                portability=0.95,
                ecosystem=0.88,
                development_speed=0.70,
            ),

            LanguageSpec(
                name="Go",
                aliases=("golang",),
                extensions=(".go",),
                filenames=("go.mod",),
                compiler_commands=("go",),
                runtime_commands=("go",),
                package_managers=("go",),
                build_tools=("go",),
                test_tools=("go",),
                formatters=("gofmt",),
                linters=("golangci-lint",),
                type_checkers=("go",),
                paradigms=("imperative", "concurrent"),
                strengths=(
                    "backend services",
                    "concurrency",
                    "deployment",
                    "networking",
                ),
                weaknesses=("generics complexity",),
                performance=0.93,
                safety=0.82,
                portability=0.95,
                ecosystem=0.90,
                development_speed=0.88,
            ),

            LanguageSpec(
                name="C",
                aliases=(),
                extensions=(".c",),
                compiler_commands=("gcc", "clang"),
                runtime_commands=(),
                package_managers=(),
                build_tools=("make", "cmake"),
                test_tools=("ctest",),
                formatters=("clang-format",),
                linters=("clang-tidy",),
                type_checkers=("clang",),
                paradigms=("imperative", "systems"),
                strengths=(
                    "embedded",
                    "systems",
                    "hardware",
                    "performance",
                ),
                weaknesses=("memory safety", "development speed"),
                performance=1.0,
                safety=0.45,
                portability=0.95,
                ecosystem=0.95,
                development_speed=0.55,
            ),

            LanguageSpec(
                name="C++",
                aliases=("cpp", "cplusplus"),
                extensions=(".cpp", ".cc", ".cxx", ".hpp", ".h"),
                compiler_commands=("g++", "clang++"),
                runtime_commands=(),
                package_managers=("vcpkg", "conan"),
                build_tools=("cmake", "make", "ninja"),
                test_tools=("ctest",),
                formatters=("clang-format",),
                linters=("clang-tidy",),
                type_checkers=("clang++",),
                paradigms=(
                    "object-oriented",
                    "generic",
                    "functional",
                    "systems",
                ),
                strengths=(
                    "high performance",
                    "games",
                    "systems",
                    "robotics",
                ),
                weaknesses=("complexity", "memory safety"),
                performance=1.0,
                safety=0.55,
                portability=0.90,
                ecosystem=0.98,
                development_speed=0.60,
            ),

            LanguageSpec(
                name="Java",
                aliases=("jvm",),
                extensions=(".java",),
                filenames=("pom.xml", "build.gradle"),
                compiler_commands=("javac",),
                runtime_commands=("java",),
                package_managers=("mvn", "gradle"),
                build_tools=("mvn", "gradle"),
                test_tools=("mvn", "gradle"),
                formatters=("google-java-format",),
                linters=("checkstyle", "pmd"),
                type_checkers=("javac",),
                paradigms=("object-oriented", "functional"),
                strengths=(
                    "enterprise",
                    "backend",
                    "large systems",
                    "portability",
                ),
                weaknesses=("startup time",),
                performance=0.90,
                safety=0.88,
                portability=0.98,
                ecosystem=0.99,
                development_speed=0.78,
            ),

            LanguageSpec(
                name="Kotlin",
                aliases=("kt",),
                extensions=(".kt", ".kts"),
                filenames=("build.gradle.kts",),
                compiler_commands=("kotlinc",),
                runtime_commands=("kotlin",),
                package_managers=("gradle",),
                build_tools=("gradle",),
                test_tools=("gradle",),
                formatters=("ktlint",),
                linters=("detekt",),
                type_checkers=("kotlinc",),
                paradigms=("object-oriented", "functional"),
                strengths=("Android", "JVM", "backend"),
                weaknesses=("JVM dependency",),
                performance=0.88,
                safety=0.90,
                portability=0.92,
                ecosystem=0.92,
                development_speed=0.90,
            ),

            LanguageSpec(
                name="JavaScript",
                aliases=("js", "node", "nodejs"),
                extensions=(".js", ".mjs", ".cjs"),
                filenames=("package.json",),
                compiler_commands=(),
                runtime_commands=("node", "deno", "bun"),
                package_managers=("npm", "pnpm", "yarn", "bun"),
                build_tools=("npm", "pnpm", "yarn", "vite", "webpack"),
                test_tools=("jest", "vitest", "mocha"),
                formatters=("prettier",),
                linters=("eslint",),
                type_checkers=(),
                paradigms=("functional", "event-driven", "object-oriented"),
                strengths=("web", "full-stack", "real-time"),
                weaknesses=("runtime typing",),
                performance=0.82,
                safety=0.68,
                portability=0.96,
                ecosystem=1.0,
                development_speed=0.96,
            ),

            LanguageSpec(
                name="TypeScript",
                aliases=("ts",),
                extensions=(".ts", ".tsx"),
                filenames=("tsconfig.json",),
                compiler_commands=("tsc",),
                runtime_commands=("node", "deno", "bun"),
                package_managers=("npm", "pnpm", "yarn", "bun"),
                build_tools=("tsc", "vite", "webpack"),
                test_tools=("jest", "vitest", "playwright"),
                formatters=("prettier",),
                linters=("eslint",),
                type_checkers=("tsc",),
                paradigms=("functional", "object-oriented", "event-driven"),
                strengths=("web", "large frontend", "full-stack"),
                weaknesses=("toolchain complexity",),
                performance=0.82,
                safety=0.80,
                portability=0.96,
                ecosystem=1.0,
                development_speed=0.94,
            ),

            LanguageSpec(
                name="C#",
                aliases=("csharp", "dotnet"),
                extensions=(".cs",),
                filenames=("*.csproj", "*.sln"),
                compiler_commands=("dotnet",),
                runtime_commands=("dotnet",),
                package_managers=("dotnet", "nuget"),
                build_tools=("dotnet",),
                test_tools=("dotnet",),
                formatters=("dotnet",),
                linters=("dotnet",),
                type_checkers=("dotnet",),
                paradigms=("object-oriented", "functional"),
                strengths=("enterprise", "game development", "Windows", "backend"),
                weaknesses=("platform ecosystem differences",),
                performance=0.90,
                safety=0.88,
                portability=0.90,
                ecosystem=0.94,
                development_speed=0.88,
            ),

            LanguageSpec(
                name="Swift",
                aliases=(),
                extensions=(".swift",),
                filenames=("Package.swift",),
                compiler_commands=("swiftc",),
                runtime_commands=("swift",),
                package_managers=("swift",),
                build_tools=("swift",),
                test_tools=("swift",),
                formatters=("swift-format",),
                linters=("swiftlint",),
                type_checkers=("swiftc",),
                paradigms=("object-oriented", "functional", "protocol-oriented"),
                strengths=("Apple platforms", "performance", "safety"),
                weaknesses=("Apple ecosystem dependence",),
                performance=0.92,
                safety=0.94,
                portability=0.65,
                ecosystem=0.85,
                development_speed=0.88,
            ),

            LanguageSpec(
                name="Dart",
                aliases=(),
                extensions=(".dart",),
                filenames=("pubspec.yaml",),
                compiler_commands=("dart",),
                runtime_commands=("dart",),
                package_managers=("dart",),
                build_tools=("dart", "flutter"),
                test_tools=("dart", "flutter"),
                formatters=("dart",),
                linters=("dart",),
                type_checkers=("dart",),
                paradigms=("object-oriented", "functional"),
                strengths=("Flutter", "cross-platform UI"),
                weaknesses=("smaller ecosystem",),
                performance=0.82,
                safety=0.84,
                portability=0.96,
                ecosystem=0.78,
                development_speed=0.92,
            ),

            LanguageSpec(
                name="Ruby",
                aliases=("rb",),
                extensions=(".rb",),
                filenames=("Gemfile",),
                compiler_commands=(),
                runtime_commands=("ruby",),
                package_managers=("gem", "bundle"),
                build_tools=("bundle",),
                test_tools=("rspec", "ruby"),
                formatters=("rubocop",),
                linters=("rubocop",),
                type_checkers=("steep",),
                paradigms=("object-oriented", "dynamic"),
                strengths=("web", "rapid development"),
                weaknesses=("CPU performance",),
                performance=0.62,
                safety=0.62,
                portability=0.88,
                ecosystem=0.84,
                development_speed=0.94,
            ),

            LanguageSpec(
                name="PHP",
                aliases=(),
                extensions=(".php",),
                filenames=("composer.json",),
                compiler_commands=(),
                runtime_commands=("php",),
                package_managers=("composer",),
                build_tools=("composer",),
                test_tools=("phpunit",),
                formatters=("php-cs-fixer",),
                linters=("phpstan", "psalm"),
                type_checkers=("phpstan", "psalm"),
                paradigms=("object-oriented", "functional"),
                strengths=("web", "server-side"),
                weaknesses=("legacy ecosystem complexity",),
                performance=0.70,
                safety=0.68,
                portability=0.90,
                ecosystem=0.90,
                development_speed=0.90,
            ),

            LanguageSpec(
                name="R",
                aliases=(),
                extensions=(".r", ".R"),
                compiler_commands=("R",),
                runtime_commands=("Rscript",),
                package_managers=("R",),
                build_tools=(),
                test_tools=("R",),
                formatters=("styler",),
                linters=("lintr",),
                type_checkers=(),
                paradigms=("functional", "statistical"),
                strengths=("statistics", "data science"),
                weaknesses=("general application development",),
                performance=0.55,
                safety=0.65,
                portability=0.85,
                ecosystem=0.88,
                development_speed=0.88,
            ),

            LanguageSpec(
                name="Julia",
                aliases=(),
                extensions=(".jl",),
                compiler_commands=("julia",),
                runtime_commands=("julia",),
                package_managers=("julia",),
                build_tools=("julia",),
                test_tools=("julia",),
                formatters=("JuliaFormatter",),
                linters=(),
                type_checkers=(),
                paradigms=("multiple-dispatch", "functional"),
                strengths=("scientific computing", "numerical computing"),
                weaknesses=("smaller general ecosystem",),
                performance=0.90,
                safety=0.72,
                portability=0.90,
                ecosystem=0.72,
                development_speed=0.90,
            ),

            LanguageSpec(
                name="Lua",
                aliases=(),
                extensions=(".lua",),
                compiler_commands=(),
                runtime_commands=("lua",),
                package_managers=("luarocks",),
                build_tools=(),
                test_tools=(),
                formatters=(),
                linters=("luacheck",),
                type_checkers=(),
                paradigms=("imperative", "functional"),
                strengths=("embedded scripting", "games"),
                weaknesses=("large-system tooling",),
                performance=0.78,
                safety=0.65,
                portability=0.98,
                ecosystem=0.68,
                development_speed=0.92,
            ),

            LanguageSpec(
                name="Scala",
                aliases=(),
                extensions=(".scala",),
                filenames=("build.sbt",),
                compiler_commands=("scalac",),
                runtime_commands=("scala",),
                package_managers=("sbt",),
                build_tools=("sbt",),
                test_tools=("sbt",),
                formatters=("scalafmt",),
                linters=("scalafix",),
                type_checkers=("scalac",),
                paradigms=("functional", "object-oriented"),
                strengths=("JVM", "distributed systems", "functional programming"),
                weaknesses=("complexity",),
                performance=0.88,
                safety=0.86,
                portability=0.96,
                ecosystem=0.82,
                development_speed=0.72,
            ),

            LanguageSpec(
                name="Haskell",
                aliases=("hs",),
                extensions=(".hs",),
                filenames=("*.cabal", "stack.yaml"),
                compiler_commands=("ghc",),
                runtime_commands=("runghc",),
                package_managers=("cabal", "stack"),
                build_tools=("cabal", "stack"),
                test_tools=("cabal", "stack"),
                formatters=("ormolu", "fourmolu"),
                linters=("hlint",),
                type_checkers=("ghc",),
                paradigms=("functional", "pure"),
                strengths=("correctness", "functional programming"),
                weaknesses=("learning curve",),
                performance=0.88,
                safety=0.92,
                portability=0.90,
                ecosystem=0.68,
                development_speed=0.65,
            ),

            LanguageSpec(
                name="Elixir",
                aliases=(),
                extensions=(".ex", ".exs"),
                filenames=("mix.exs",),
                compiler_commands=("elixirc",),
                runtime_commands=("elixir",),
                package_managers=("mix",),
                build_tools=("mix",),
                test_tools=("mix",),
                formatters=("mix",),
                linters=("credo",),
                type_checkers=("dialyzer",),
                paradigms=("functional", "concurrent"),
                strengths=("distributed systems", "fault tolerance", "real-time"),
                weaknesses=("CPU-heavy workloads",),
                performance=0.75,
                safety=0.88,
                portability=0.90,
                ecosystem=0.76,
                development_speed=0.86,
            ),

            LanguageSpec(
                name="Zig",
                aliases=(),
                extensions=(".zig",),
                filenames=("build.zig",),
                compiler_commands=("zig",),
                runtime_commands=(),
                package_managers=("zig",),
                build_tools=("zig",),
                test_tools=("zig",),
                formatters=("zig",),
                linters=("zig",),
                type_checkers=("zig",),
                paradigms=("imperative", "systems"),
                strengths=("systems programming", "performance", "simplicity"),
                weaknesses=("smaller ecosystem",),
                performance=0.98,
                safety=0.78,
                portability=0.96,
                ecosystem=0.62,
                development_speed=0.70,
            ),
        ]
