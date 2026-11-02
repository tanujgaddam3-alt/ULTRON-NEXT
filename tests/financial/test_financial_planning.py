from ultron.financial.planning import (
    FinancialPlanValidator,
    FinancialPlanner,
    FinancialStepStatus,
)


def test_creates_multi_step_plan():
    plan = FinancialPlanner().create_plan(
        "Determine whether the asset is attractive",
        "BTC-USD",
        asset_class="crypto",
    )

    assert plan.symbol == "BTC-USD"
    assert len(plan.steps) >= 7

    actions = [step.action for step in plan.steps]

    expected = {
        "get_company_data",
        "get_market_data",
        "get_financial_analysis",
        "get_historical_data",
        "analyze_regime",
        "assess_risk",
        "generate_strategy",
        "backtest_strategy",
        "reason",
        "make_decision",
        "persist_experience",
        "evaluate_plan",
    }

    assert expected.issubset(actions)
    assert actions[-1] == "evaluate_plan"


def test_plan_dependencies_are_ordered():
    plan = FinancialPlanner().create_plan(
        "Analyze asset",
        "USD/INR",
        asset_class="forex",
    )

    assert FinancialPlanValidator().validate(plan)

    for step in plan.steps:
        assert all(
            dependency < step.order
            for dependency in step.depends_on
        )


def test_backtest_can_be_disabled():
    plan = FinancialPlanner().create_plan(
        "Analyze asset",
        "RELIANCE.NS",
        run_backtest=False,
    )

    actions = [step.action for step in plan.steps]

    assert "backtest_strategy" not in actions
    assert actions[-1] == "evaluate_plan"


def test_invalid_objective_rejected():
    try:
        FinancialPlanner().create_plan("", "BTC-USD")
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError")


def test_initial_steps_are_pending():
    plan = FinancialPlanner().create_plan(
        "Analyze asset",
        "BTC-USD",
    )

    assert all(
        step.status is FinancialStepStatus.PENDING
        for step in plan.steps
    )
