import pytest

from ultron.agents.models import AgentResult
from ultron.agents.registry import AgentRegistry
from ultron.agents.workflow.executor import AgentWorkflowExecutor
from ultron.agents.workflow.model import AgentWorkflow, AgentWorkflowStep


class FixedAgent:
    def __init__(self, name, result=None, error=None):
        self.name = name
        self.result = result or AgentResult(
            success=True,
            agent=name,
            output=f"{name} finished",
        )
        self.error = error
        self.calls = []

    def execute(self, task):
        self.calls.append(task)
        if self.error is not None:
            raise self.error
        return self.result


def test_executor_respects_dependencies_and_blocks_unapproved_dispatch():
    registry = AgentRegistry()
    failing = FixedAgent(
        "first",
        result=AgentResult(
            success=False,
            agent="first",
            output="first failed",
        ),
    )
    dependent = FixedAgent("dependent")
    independent = FixedAgent("independent")
    for agent in (failing, dependent, independent):
        registry.register(agent)

    workflow = AgentWorkflow(
        goal="Run dependent and independent work",
        steps=[
            AgentWorkflowStep(
                order=2,
                agent="dependent",
                objective="Run after first",
                depends_on=[1],
            ),
            AgentWorkflowStep(
                order=1,
                agent="first",
                objective="Run first",
            ),
            AgentWorkflowStep(
                order=3,
                agent="independent",
                objective="Run independently",
            ),
        ],
    )

    results = AgentWorkflowExecutor(registry).execute(workflow)

    by_order = {result.metadata["step_order"]: result for result in results}
    assert workflow.status == "blocked"
    assert by_order[1].metadata["status"] == "blocked"
    assert by_order[1].metadata["reason"] == "trusted_operation_grant_unavailable"
    assert by_order[2].metadata["status"] == "blocked"
    assert by_order[3].metadata["status"] == "blocked"
    assert failing.calls == []
    assert dependent.calls == []
    assert independent.calls == []
    assert all(
        result.metadata["workflow_id"] == workflow.id
        for result in results
    )


def test_executor_does_not_claim_completion_without_trusted_grant():
    registry = AgentRegistry()
    agent = FixedAgent("worker")
    registry.register(agent)
    workflow = AgentWorkflow(
        goal="Complete both steps",
        steps=[
            AgentWorkflowStep(
                order=1,
                agent="worker",
                objective="First",
            ),
            AgentWorkflowStep(
                order=2,
                agent="worker",
                objective="Second",
                depends_on=[1],
            ),
        ],
    )

    results = AgentWorkflowExecutor(registry).execute(workflow)

    assert workflow.status == "blocked"
    assert [result.metadata["status"] for result in results] == [
        "blocked",
        "blocked",
    ]
    assert agent.calls == []


@pytest.mark.parametrize(
    "steps, message",
    [
        (
            [
                AgentWorkflowStep(
                    order=1,
                    agent="worker",
                    objective="First",
                    depends_on=[2],
                ),
                AgentWorkflowStep(
                    order=2,
                    agent="worker",
                    objective="Second",
                    depends_on=[1],
                ),
            ],
            "cycle",
        ),
        (
            [
                AgentWorkflowStep(
                    order=1,
                    agent="worker",
                    objective="Missing prerequisite",
                    depends_on=[99],
                ),
            ],
            "unknown step",
        ),
    ],
)
def test_executor_rejects_invalid_dependencies_before_dispatch(steps, message):
    registry = AgentRegistry()
    worker = FixedAgent("worker")
    registry.register(worker)
    workflow = AgentWorkflow(goal="Invalid workflow", steps=steps)

    with pytest.raises(ValueError, match=message):
        AgentWorkflowExecutor(registry).execute(workflow)

    assert worker.calls == []
    assert workflow.status == "created"


def test_executor_does_not_call_agent_without_trusted_grant():
    registry = AgentRegistry()
    worker = FixedAgent("worker", error=RuntimeError("sensitive detail"))
    registry.register(worker)
    workflow = AgentWorkflow(
        goal="Handle agent failure",
        steps=[
            AgentWorkflowStep(
                order=1,
                agent="worker",
                objective="Fail",
            ),
        ],
    )

    results = AgentWorkflowExecutor(registry).execute(workflow)

    assert workflow.status == "blocked"
    assert results[0].success is False
    assert results[0].metadata["status"] == "blocked"
    assert results[0].metadata["reason"] == "trusted_operation_grant_unavailable"
    assert "sensitive detail" not in results[0].output
    assert worker.calls == []


def test_executor_marks_missing_agent_unavailable_not_completed():
    workflow = AgentWorkflow(
        goal="Missing agent",
        steps=[
            AgentWorkflowStep(
                order=1,
                agent="not_registered",
                objective="Cannot run",
            ),
        ],
    )

    results = AgentWorkflowExecutor(AgentRegistry()).execute(workflow)

    assert workflow.status == "blocked"
    assert results[0].metadata["status"] == "unavailable"


def test_executor_preserves_explicit_blocked_agent_status():
    registry = AgentRegistry()
    worker = FixedAgent(
        "worker",
        result=AgentResult(
            success=False,
            agent="worker",
            output="Work could not proceed.",
            metadata={"status": "blocked"},
        ),
    )
    registry.register(worker)
    workflow = AgentWorkflow(
        goal="Blocked work",
        steps=[
            AgentWorkflowStep(
                order=1,
                agent="worker",
                objective="Blocked",
            ),
        ],
    )

    results = AgentWorkflowExecutor(registry).execute(workflow)

    assert workflow.status == "blocked"
    assert results[0].metadata["status"] == "blocked"


def test_executor_blocks_all_steps_without_trusted_authorizer():
    registry = AgentRegistry()
    worker = FixedAgent("worker")
    registry.register(worker)
    workflow = AgentWorkflow(
        goal="Must not dispatch without authorization",
        steps=[
            AgentWorkflowStep(
                order=1,
                agent="worker",
                objective="Do work",
            ),
        ],
    )

    results = AgentWorkflowExecutor(registry).execute(workflow)

    assert workflow.status == "blocked"
    assert results[0].metadata["reason"] == "trusted_operation_grant_unavailable"
    assert worker.calls == []


def test_executor_does_not_accept_arbitrary_authorization_callback():
    registry = AgentRegistry()
    worker = FixedAgent("worker")
    registry.register(worker)
    workflow = AgentWorkflow(
        goal="Authorization failure",
        steps=[
            AgentWorkflowStep(
                order=1,
                agent="worker",
                objective="Do work",
            ),
        ],
    )

    with pytest.raises(TypeError):
        AgentWorkflowExecutor(
            registry,
            dispatch_authorizer=lambda _agent, _task: True,
        )
