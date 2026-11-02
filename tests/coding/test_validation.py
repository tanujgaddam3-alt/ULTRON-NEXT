from ultron.improvement.validator.quality_checker import (
    QualityChecker,
)
from ultron.improvement.validator.test_runner import (
    TestRunner,
)


def test_quality_checker_accepts_valid_python():
    checker = QualityChecker()

    result = checker.check_code(
        """
def add(a, b):
    return a + b
""",
        language="Python",
    )

    assert result["passed"] is True
    assert result["score"] > 0
    assert result["metrics"]["function_count"] == 1


def test_quality_checker_rejects_invalid_python():
    checker = QualityChecker()

    result = checker.check_code(
        """
def broken(
    return 1
""",
        language="Python",
    )

    assert result["passed"] is False
    assert result["score"] == 0
    assert any(
        "invalid syntax" in issue.lower()
        for issue in result["issues"]
    )


def test_quality_checker_rejects_empty_code():
    checker = QualityChecker()

    result = checker.check_code(
        "",
        language="Python",
    )

    assert result["passed"] is False
    assert "Generated code is empty" in result["issues"]


def test_quality_checker_rejects_placeholder_code():
    checker = QualityChecker()

    result = checker.check_code(
        """
def build():
    # TODO: implement
    pass
""",
        language="Python",
    )

    assert result["passed"] is False


def test_quality_checker_handles_markdown_fence():
    checker = QualityChecker()

    result = checker.check_code(
        """```python
def hello():
    return "hello"
```""",
        language="Python",
    )

    assert result["passed"] is True
    assert result["metrics"]["has_markdown_fence"] is True


def test_test_runner_accepts_sequence_command():
    runner = TestRunner()

    result = runner.run(
        [
            "python",
            "-c",
            "print('validation-ok')",
        ]
    )

    assert result["success"] is True
    assert result["return_code"] == 0
    assert "validation-ok" in result["output"]


def test_test_runner_reports_failure():
    runner = TestRunner()

    result = runner.run(
        [
            "python",
            "-c",
            "raise SystemExit(7)",
        ]
    )

    assert result["success"] is False
    assert result["return_code"] == 7


def test_test_runner_handles_timeout():
    runner = TestRunner(
        default_timeout=1,
    )

    result = runner.run(
        [
            "python",
            "-c",
            "import time; time.sleep(3)",
        ]
    )

    assert result["success"] is False
    assert result["error"] == (
        "Test execution timeout"
    )


def test_test_runner_preserves_environment_and_arguments():
    runner = TestRunner()

    result = runner.run(
        [
            "python",
            "-c",
            (
                "import os, sys; "
                "print(os.environ['ULTRON_TEST_VALUE']); "
                "print(repr(sys.argv[1:])); "
            ),
            "alpha beta",
            "gamma",
        ],
        env={"ULTRON_TEST_VALUE": "from-env"},
    )

    assert result["success"] is True
    assert result["return_code"] == 0
    assert "from-env" in result["output"]
    assert "alpha beta" in result["output"]
    assert "gamma" in result["output"]


def test_test_runner_preserves_unrelated_executables():
    runner = TestRunner()

    result = runner.run(
        [
            "/bin/echo",
            "runner-ok",
        ]
    )

    assert result["success"] is True
    assert result["command"][0] == "/bin/echo"
    assert "runner-ok" in result["output"]


def test_test_runner_reports_missing_executable():
    runner = TestRunner()

    result = runner.run(
        [
            "definitely_missing_python_binary_123456",
            "--version",
        ]
    )

    assert result["success"] is False
    assert result["return_code"] is None
    assert result["error"] == "FileNotFoundError"
