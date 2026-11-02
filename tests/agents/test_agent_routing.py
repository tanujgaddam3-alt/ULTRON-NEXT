import pytest

from ultron.agents.base import BaseAgent
from ultron.agents.coding.agent import CodingAgent
from ultron.agents.models import AgentResult, AgentTask
from ultron.agents.orchestrator import AgentOrchestrator
from ultron.agents.planning.agent import PlanningAgent
from ultron.agents.registry import AgentRegistry
from ultron.agents.research.agent import ResearchAgent
from ultron.agents.security.agent import SecurityAgent


class DeclaredAgent(BaseAgent):
    name = "declared"
    capabilities = ["code_generation", "code_validation"]

    def execute(self, task):
        raise AssertionError("This test only exercises capability matching.")


class UnspecifiedAgent(BaseAgent):
    name = "unspecified"
    capabilities = []

    def execute(self, task):
        raise AssertionError("This test only exercises capability matching.")


class DispatchAgent(BaseAgent):
    name = "dispatch"
    capabilities = ["code_generation"]

    def __init__(self):
        self.calls = []

    def execute(self, task):
        self.calls.append(task)
        return AgentResult(
            success=True,
            agent=self.name,
            output="Generated.",
        )


def test_agent_support_requires_every_requested_capability():
    agent = DeclaredAgent()

    assert agent.supports(
        AgentTask(
            goal="Generate and validate code",
            required_capabilities=["code_generation", "code_validation"],
        )
    )
    assert not agent.supports(
        AgentTask(
            goal="Generate and deploy",
            required_capabilities=["code_generation", "deployment"],
        )
    )


def test_generic_tasks_do_not_match_agents_with_no_declared_capabilities():
    task = AgentTask(goal="Handle this task")

    assert DeclaredAgent().supports(task)
    assert not UnspecifiedAgent().supports(task)
    assert not PlanningAgent().supports(task)
    assert not ResearchAgent().supports(task)
    assert not SecurityAgent().supports(task)


@pytest.mark.parametrize(
    ("agent_type", "reason"),
    [
        (PlanningAgent, "planning_implementation_unavailable"),
        (ResearchAgent, "research_source_unavailable"),
        (SecurityAgent, "security_analyzer_unavailable"),
    ],
)
def test_unimplemented_specialists_report_blocked_instead_of_success(
    agent_type,
    reason,
):
    result = agent_type().execute(AgentTask(goal="Perform specialist work"))

    assert result.success is False
    assert result.metadata["status"] == "blocked"
    assert result.metadata["reason"] == reason
    assert "completed" not in result.output.lower()


def test_coding_agent_declares_its_local_code_validation_capability():
    assert CodingAgent().supports(
        AgentTask(
            goal="Generate and validate code",
            required_capabilities=["code_generation", "code_validation"],
        )
    )


def test_orchestrator_blocks_dispatch_without_trusted_authorizer():
    registry = AgentRegistry()
    agent = DispatchAgent()
    registry.register(agent)
    orchestrator = AgentOrchestrator(registry)

    result = orchestrator.dispatch(
        AgentTask(
            goal="Generate code",
            required_capabilities=["code_generation"],
        )
    )

    assert result.success is False
    assert result.metadata["status"] == "blocked"
    assert result.metadata["reason"] == "trusted_operation_grant_unavailable"
    assert agent.calls == []


def test_orchestrator_does_not_accept_arbitrary_authorization_callbacks():
    registry = AgentRegistry()
    agent = DispatchAgent()
    registry.register(agent)
    with pytest.raises(TypeError):
        AgentOrchestrator(
            registry,
            dispatch_authorizer=lambda _agent, _task: True,
        )

    result = AgentOrchestrator(registry).dispatch(
        AgentTask(
            goal="Generate code",
            required_capabilities=["code_generation"],
        )
    )
    assert result.success is False
    assert result.metadata["reason"] == "trusted_operation_grant_unavailable"
    assert agent.calls == []
