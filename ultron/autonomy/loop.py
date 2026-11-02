from __future__ import annotations

from uuid import uuid4


class AutonomyLoop:
    """
    Bounded autonomous agent cycle.

    Lifecycle:

        observe
          ↓
        select goal
          ↓
        plan
          ↓
        policy gate
          ↓
        execute
          ↓
        reflect
          ↓
        update goal state
          ↓
        complete / retry

    One call to run_cycle() performs one bounded cycle.

    Goal state is immutable. Lifecycle transitions therefore create
    replacement Goal instances through Goal's domain methods and
    persist them through GoalStore.update().
    """

    def __init__(
        self,
        monitor,
        goal_store,
        priority_engine,
        planner,
        policy,
        execution=None,
        reflection=None,
    ):
        self.monitor = monitor
        self.goal_store = goal_store
        self.priority = priority_engine
        self.planner = planner
        self.policy = policy
        self.execution = execution
        self.reflection = reflection

        self.cycle_count = 0

    def run_cycle(
        self,
        context: dict,
    ) -> dict:
        self.cycle_count += 1

        cycle_id = str(uuid4())

        observations = self.monitor.observe(context)

        goals = self.goal_store.active()

        if not goals:
            return {
                "status": "idle",
                "cycle_id": cycle_id,
                "observations": observations,
                "goals": [],
            }

        ranked = self.priority.rank(goals)

        if not ranked:
            return {
                "status": "idle",
                "cycle_id": cycle_id,
                "observations": observations,
                "goals": [],
            }

        selected_goal = ranked[0]

        plan = self.planner.plan(selected_goal)

        permission = self.policy.check("create_task")

        if permission.value != "allow":
            return {
                "status": "waiting_permission",
                "cycle_id": cycle_id,
                "goal": selected_goal,
                "plan": plan,
                "permission": permission.value,
                "observations": observations,
            }

        # Planning-only mode remains supported when execution is not wired.
        if self.execution is None:
            return {
                "status": "planned",
                "cycle_id": cycle_id,
                "goal": selected_goal,
                "observations": observations,
                "plan": plan,
            }

        execution_result = self.execution.execute(plan)

        reflection_result = None

        if self.reflection is not None:
            reflection_result = self.reflection.reflect(
                execution_result
            )

        completed = self._execution_completed(
            execution_result,
            plan,
        )

        if completed:
            updated_goal = selected_goal.complete()

            self.goal_store.update(updated_goal)

            status = "completed"

        else:
            # A failed execution does not automatically destroy the goal.
            # Keeping it active allows the next bounded cycle to re-plan.
            updated_goal = selected_goal.activate()

            self.goal_store.update(updated_goal)

            status = "execution_failed"

        return {
            "status": status,
            "cycle_id": cycle_id,
            "goal": updated_goal,
            "observations": observations,
            "plan": plan,
            "execution": execution_result,
            "reflection": reflection_result,
        }

    @staticmethod
    def _execution_completed(
        execution_result,
        plan,
    ) -> bool:
        """
        Determine whether execution completed every step
        in the supplied plan.

        ExecutionEngine returns an ExecutionResult whose
        completion information lives in ExecutionResult.record.
        """

        if execution_result is None:
            return False

        record = getattr(
            execution_result,
            "record",
            None,
        )

        if record is None:
            return False

        success = getattr(
            record,
            "success",
            False,
        )

        completed_steps = getattr(
            record,
            "completed_steps",
            0,
        )

        steps = getattr(
            plan,
            "steps",
            None,
        )

        if steps is None:
            return False

        expected_steps = len(steps)

        return (
            success
            and expected_steps > 0
            and completed_steps == expected_steps
        )