from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .models import (
    FinancialPlan,
    FinancialPlanStep,
    FinancialStepStatus,
)
from .validator import FinancialPlanValidator


StepHandler = Callable[
    [FinancialPlanStep, "FinancialExecutionContext"],
    Any,
]


@dataclass
class FinancialExecutionContext:
    """
    Runtime context shared by financial plan step handlers.

    The context provides a stable place for execution state and
    intermediate values without coupling the planner to a specific
    market provider, intelligence engine, or broker.
    """

    values: dict[str, Any] = field(default_factory=dict)

    def set(self, key: str, value: Any) -> None:
        key = key.strip()

        if not key:
            raise ValueError("Context key is required.")

        self.values[key] = value

    def get(
        self,
        key: str,
        default: Any = None,
    ) -> Any:
        return self.values.get(key, default)

    def require(self, key: str) -> Any:
        key = key.strip()

        if not key:
            raise ValueError("Context key is required.")

        if key not in self.values:
            raise KeyError(
                f"Required execution context value is missing: {key}"
            )

        return self.values[key]


@dataclass
class FinancialExecutionResult:
    """
    Structured result produced after executing a financial plan.
    """

    plan: FinancialPlan
    context: FinancialExecutionContext
    completed: bool
    failed: bool
    stopped: bool

    @property
    def successful(self) -> bool:
        return (
            self.completed
            and not self.failed
            and not self.stopped
        )

    @property
    def failed_steps(self) -> list[FinancialPlanStep]:
        return [
            step
            for step in self.plan.steps
            if step.status is FinancialStepStatus.FAILED
        ]

    @property
    def completed_steps(self) -> list[FinancialPlanStep]:
        return [
            step
            for step in self.plan.steps
            if step.status is FinancialStepStatus.COMPLETED
        ]


class FinancialPlanExecutor:
    """
    Executes validated financial plans in dependency order.

    Planning remains separate from execution. Step handlers are injected
    so this layer can orchestrate market data, analysis, risk, strategy,
    backtesting, evaluation, and eventually paper execution without
    hard-coding any provider or broker.
    """

    def __init__(
        self,
        handlers: Mapping[str, StepHandler] | None = None,
        validator: FinancialPlanValidator | None = None,
    ) -> None:
        self.handlers: dict[str, StepHandler] = dict(
            handlers or {}
        )
        self.validator = validator or FinancialPlanValidator()

    def register_handler(
        self,
        action: str,
        handler: StepHandler,
    ) -> None:
        action = action.strip()

        if not action:
            raise ValueError("Action is required.")

        if not callable(handler):
            raise TypeError("Handler must be callable.")

        self.handlers[action] = handler

    def execute(
        self,
        plan: FinancialPlan,
        context: FinancialExecutionContext | None = None,
    ) -> FinancialExecutionResult:
        if not isinstance(plan, FinancialPlan):
            raise TypeError("FinancialPlan is required.")

        if not self.validator.validate(plan):
            raise ValueError("Financial plan is invalid.")

        context = context or FinancialExecutionContext()

        by_order = {
            step.order: step
            for step in plan.steps
        }

        stopped = False
        failed = False

        for step in plan.steps:
            if stopped:
                self._skip(
                    step,
                    "Execution stopped after a previous failure.",
                )
                continue

            dependency_failure = self._dependency_failure(
                step,
                by_order,
            )

            if dependency_failure is not None:
                self._skip(
                    step,
                    dependency_failure,
                )
                stopped = True
                failed = True
                continue

            handler = self.handlers.get(step.action)

            if handler is None:
                self._fail(
                    step,
                    (
                        "No handler registered for action: "
                        f"{step.action}"
                    ),
                )
                stopped = True
                failed = True
                continue

            step.status = FinancialStepStatus.RUNNING

            try:
                step.result = handler(
                    step,
                    context,
                )
                step.error = None
                step.status = FinancialStepStatus.COMPLETED

                context.set(f"step:{step.order}", step.result)
                context.set(f"action:{step.action}", step.result)

            except Exception as exc:
                step.result = None
                step.error = (
                    f"{type(exc).__name__}: {exc}"
                )
                step.status = FinancialStepStatus.FAILED
                stopped = True
                failed = True

        completed = plan.completed

        return FinancialExecutionResult(
            plan=plan,
            context=context,
            completed=completed,
            failed=failed,
            stopped=stopped,
        )

    @staticmethod
    def _dependency_failure(
        step: FinancialPlanStep,
        by_order: dict[int, FinancialPlanStep],
    ) -> str | None:
        for dependency in step.depends_on:
            dependency_step = by_order[dependency]

            if dependency_step.status is FinancialStepStatus.FAILED:
                return (
                    f"Dependency {dependency} failed; "
                    f"step {step.order} cannot execute."
                )

            if dependency_step.status is FinancialStepStatus.SKIPPED:
                return (
                    f"Dependency {dependency} was skipped; "
                    f"step {step.order} cannot execute."
                )

            if dependency_step.status is not FinancialStepStatus.COMPLETED:
                return (
                    f"Dependency {dependency} is not complete; "
                    f"step {step.order} cannot execute."
                )

        return None

    @staticmethod
    def _skip(
        step: FinancialPlanStep,
        reason: str,
    ) -> None:
        step.status = FinancialStepStatus.SKIPPED
        step.error = reason
        step.result = None

    @staticmethod
    def _fail(
        step: FinancialPlanStep,
        reason: str,
    ) -> None:
        step.status = FinancialStepStatus.FAILED
        step.error = reason
        step.result = None
