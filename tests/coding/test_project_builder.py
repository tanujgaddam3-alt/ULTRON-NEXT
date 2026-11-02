from ultron.agents.coding.project_builder import ProjectBuilder
from ultron.improvement.validator.quality_checker import QualityChecker


class FakeLanguageSelection:
    class Selected:
        language = "Python"

    selected = Selected()


class FakeLanguageSelector:
    def select(
        self,
        request,
        *,
        project_root=None,
        preferred_languages=None,
    ):
        return FakeLanguageSelection()


class FakeCodingAgent:
    def __init__(self):
        self.language_selector = FakeLanguageSelector()
        self.calls = []

    def execute(self, task):
        self.calls.append(task)

        path = task.context["target_file"]

        if path.endswith("__init__.py"):
            code = ""

        elif path.endswith("test_main.py"):
            code = """
def test_main():
    assert True
"""

        elif path.endswith("main.py"):
            code = """
def main():
    return "hello"


if __name__ == "__main__":
    main()
"""

        else:
            code = "# ULTRON project\n"

        class Result:
            success = True
            output = code

        return Result()


def test_project_builder_creates_python_project():
    agent = FakeCodingAgent()

    builder = ProjectBuilder(
        coding_agent=agent,
        quality_checker=QualityChecker(),
    )

    result = builder.build(
        "Create a Python hello application",
        project_name="Hello App",
    )

    assert result.success is True
    assert result.project_name == "hello-app"
    assert result.file_count == 4

    paths = {
        project_file.path
        for project_file in result.files
    }

    assert "src/main.py" in paths
    assert "tests/test_main.py" in paths
    assert "README.md" in paths


def test_project_builder_passes_project_context_to_each_file():
    agent = FakeCodingAgent()

    builder = ProjectBuilder(
        coding_agent=agent,
    )

    result = builder.build(
        "Create a Python automation tool",
        project_name="automation",
    )

    assert result.success is True
    assert len(agent.calls) == result.file_count

    for task in agent.calls:
        assert task.context["project_name"] == "automation"
        assert task.context["selected_language"] == "Python"
        assert "project_manifest" in task.context
        assert "target_file" in task.context


def test_project_builder_rejects_empty_request():
    agent = FakeCodingAgent()

    builder = ProjectBuilder(
        coding_agent=agent,
    )

    result = builder.build("   ")

    assert result.success is False
    assert "Project request cannot be empty." in result.errors
    assert agent.calls == []


def test_project_builder_cleans_markdown_fences():
    builder = ProjectBuilder(
        coding_agent=FakeCodingAgent(),
    )

    assert builder._clean_generated_content(
        """```python
def hello():
    return "hello"
```"""
    ) == """def hello():
    return "hello"
"""


def test_project_builder_normalizes_project_name():
    builder = ProjectBuilder(
        coding_agent=FakeCodingAgent(),
    )

    assert builder._normalize_project_name(
        "My Awesome Project!"
    ) == "my-awesome-project"


def test_project_builder_infers_project_name():
    builder = ProjectBuilder(
        coding_agent=FakeCodingAgent(),
    )

    name = builder._infer_project_name(
        "Create a Python file organizer application"
    )

    assert name == "python-file-organizer"


def test_project_builder_handles_generation_failure():
    class FailingAgent(FakeCodingAgent):
        def execute(self, task):
            self.calls.append(task)

            class Result:
                success = False
                output = "generation failed"

            return Result()

    agent = FailingAgent()

    builder = ProjectBuilder(
        coding_agent=agent,
    )

    result = builder.build(
        "Create a Python application",
    )

    assert result.success is False
    assert result.file_count == 4
    assert result.files[0].validation["passed"] is False
    assert result.errors


def test_project_builder_does_not_execute_generated_code():
    agent = FakeCodingAgent()

    builder = ProjectBuilder(
        coding_agent=agent,
    )

    result = builder.build(
        "Create a Python application",
    )

    assert result.success is True
    assert result.metadata["file_count"] == 4
