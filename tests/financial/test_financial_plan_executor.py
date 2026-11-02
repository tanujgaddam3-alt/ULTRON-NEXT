import pytest

from ultron.financial.planning.executor import (
    FinancialPlanExecutor,
)
from ultron.financial.planning.models import (
    FinancialPlan,
    FinancialStepStatus,
)


def make_plan():
    from ultron.financial.planning.planner import FinancialPlanner

    return FinancialPlanner().create_plan(
        "analyze AAPL",
        "AAPL",
        run_backtest=False,
    )


def test_executes_plan_in_dependency_order():
    plan = make_plan()
    calls = []

    def handler(step, context):
        calls.append(step.order)
        return f"result-{step.order}"

    executor = FinancialPlanExecutor(
        handlers={
            step.action: handler
            for step in plan.steps
        }
    )

    result = executor.execute(plan)

    assert result.successful is True
    assert result.completed is True
    assert result.failed is False
    assert calls == [step.order for step in plan.steps]

    for step in plan.steps:
        assert step.status is FinancialStepStatus.COMPLETED
        assert step.result == f"result-{step.order}"
        assert step.error is None


def test_missing_handler_stops_execution():
    plan = make_plan()
    calls = []

    first_action = plan.steps[0].action

    executor = FinancialPlanExecutor(
    handlers={
        first_action: lambda step, context: calls.append(step.order),
    }
)

    result = executor.execute(plan)

    assert result.failed is True
    assert result.stopped is True
    assert calls == [1]

    assert plan.steps[0].status is FinancialStepStatus.COMPLETED
    assert plan.steps[1].status is FinancialStepStatus.FAILED

    for step in plan.steps[2:]:
        assert step.status is FinancialStepStatus.SKIPPED


def test_handler_failure_stops_downstream_steps():
    plan = make_plan()
    calls = []

    def handler(step, context):
        calls.append(step.order)

        if step.order == 3:
            raise RuntimeError("market analysis failed")

        return step.order

    executor = FinancialPlanExecutor(
        handlers={
            step.action: handler
            for step in plan.steps
        }
    )

    result = executor.execute(plan)

    assert result.failed is True
    assert result.stopped is True
    assert calls == [1, 2, 3]

    assert plan.steps[2].status is FinancialStepStatus.FAILED
    assert "market analysis failed" in plan.steps[2].error

    for step in plan.steps[3:]:
        assert step.status is FinancialStepStatus.SKIPPED


def test_register_handler():
    plan = make_plan()
    calls = []

    executor = FinancialPlanExecutor()

    for step in plan.steps:
        executor.register_handler(
            step.action,
            lambda step, context, calls=calls: calls.append(step.order),
        )

    result = executor.execute(plan)

    assert result.successful is True
    assert calls == [step.order for step in plan.steps]


def test_rejects_invalid_plan():
    plan = FinancialPlan(
        objective="test",
        symbol="AAPL",
        steps=[],
    )

    executor = FinancialPlanExecutor()

    with pytest.raises(ValueError, match="invalid"):
        executor.execute(plan)


def test_rejects_non_plan():
    executor = FinancialPlanExecutor()

    with pytest.raises(TypeError, match="FinancialPlan"):
        executor.execute(None)


def test_handler_exception_is_recorded():
    plan = make_plan()

    def handler(step, context):
        if step.order == 1:
            raise ValueError("bad market data")

    executor = FinancialPlanExecutor(
        handlers={
            step.action: handler
            for step in plan.steps
        }
    )

    result = executor.execute(plan)

    step = plan.steps[0]

    assert result.failed is True
    assert step.status is FinancialStepStatus.FAILED
    assert step.result is None
    assert step.error == "ValueError: bad market data"


def test_execution_result_successful_property():
    plan = make_plan()

    executor = FinancialPlanExecutor(
        handlers={
            step.action: lambda step, plan: None
            for step in plan.steps
        }
    )

    result = executor.execute(plan)

    assert result.successful is True
