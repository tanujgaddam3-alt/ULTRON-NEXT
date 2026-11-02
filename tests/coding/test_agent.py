from ultron.agents.coding.agent import CodingAgent
from ultron.agents.models import AgentTask


class FakeLLMProvider:
    def __init__(self, response="generated code"):
        self.response = response
        self.calls = []

    def generate(self, prompt, context=None):
        self.calls.append(
            {
                "prompt": prompt,
                "context": context,
            }
        )

        return self.response


class FailingLLMProvider:
    def generate(self, prompt, context=None):
        raise RuntimeError("LLM unavailable")


def test_coding_agent_generates_using_selected_language():
    provider = FakeLLMProvider(
        response="print('hello from ULTRON')"
    )

    agent = CodingAgent(
        llm_provider=provider,
    )

    result = agent.execute(
        AgentTask(
            goal="Create a Python automation tool",
        )
    )

    assert result.success is True
    assert result.agent == "coding"
    assert result.output == "print('hello from ULTRON')"
    assert "code_generation" in result.capabilities_used

    metadata = result.metadata["language_selection"]

    assert metadata["selected"]["language"] == "Python"
    assert metadata["selected"]["available"] is True
    assert metadata["confidence"] == 1.0

    assert len(provider.calls) == 1

    prompt = provider.calls[0]["prompt"]

    assert "Create a Python automation tool" in prompt
    assert "Selected language: Python" in prompt
    assert "Do not execute commands or modify files." in prompt


def test_coding_agent_passes_context_to_llm():
    provider = FakeLLMProvider()

    agent = CodingAgent(
        llm_provider=provider,
    )

    result = agent.execute(
        AgentTask(
            goal="Create a browser frontend",
            context={
                "project_root": ".",
                "preferred_languages": [
                    "TypeScript"
                ],
                "project_context": {
                    "framework": "react",
                },
            },
        )
    )

    assert result.success is True

    call_context = provider.calls[0]["context"]

    assert call_context["goal"] == (
        "Create a browser frontend"
    )

    assert call_context["selected_language"] == "TypeScript"

    assert call_context["task_context"]["project_root"] == "."

    assert call_context["task_context"]["project_context"] == {
        "framework": "react"
    }


def test_coding_agent_fails_cleanly_without_llm():
    agent = CodingAgent()

    result = agent.execute(
        AgentTask(
            goal="Create a Python application",
        )
    )

    assert result.success is False
    assert result.agent == "coding"
    assert result.metadata["error"] == (
        "llm_provider_unavailable"
    )


def test_coding_agent_handles_llm_failure():
    agent = CodingAgent(
        llm_provider=FailingLLMProvider(),
    )

    result = agent.execute(
        AgentTask(
            goal="Create a Python application",
        )
    )

    assert result.success is False
    assert result.agent == "coding"
    assert result.metadata["error"] == "RuntimeError"
    assert "LLM unavailable" in result.output


def test_coding_agent_rejects_empty_goal():
    provider = FakeLLMProvider()

    agent = CodingAgent(
        llm_provider=provider,
    )

    result = agent.execute(
        AgentTask(
            goal="   ",
        )
    )

    assert result.success is False
    assert result.metadata["error"] == "empty_goal"
    assert provider.calls == []


def test_coding_agent_repairs_invalid_generated_code():
    class RepairProvider:
        def __init__(self):
            self.calls = []

        def generate(self, prompt, context=None):
            self.calls.append(
                {
                    "prompt": prompt,
                    "context": context,
                }
            )

            if len(self.calls) == 1:
                return "def broken("

            return """
def hello():
    return "hello"
"""

    provider = RepairProvider()

    agent = CodingAgent(
        llm_provider=provider,
        max_repair_attempts=2,
    )

    result = agent.execute(
        AgentTask(
            goal="Create a Python hello function",
        )
    )

    assert result.success is True
    assert len(provider.calls) == 2

    assert result.metadata["validation"]["passed"] is True

    assert (
        result.metadata["generation"]["repair_attempts"]
        == 1
    )


def test_coding_agent_rejects_code_after_repair_limit():
    class AlwaysBrokenProvider:
        def __init__(self):
            self.calls = 0

        def generate(self, prompt, context=None):
            self.calls += 1
            return "def broken("

    provider = AlwaysBrokenProvider()

    agent = CodingAgent(
        llm_provider=provider,
        max_repair_attempts=2,
    )

    result = agent.execute(
        AgentTask(
            goal="Create a Python application",
        )
    )

    assert result.success is False

    assert result.metadata["error"] == (
        "generated_code_failed_validation"
    )

    assert provider.calls == 3