import pytest

from ultron.domain.plan import Plan, PlanStep
from ultron.application.runtime.operation_store import SQLiteOperationStore
from ultron.application.task_service import TaskService
from ultron.domain.enums import TaskStatus
from ultron.execution.engine import ExecutionEngine
from ultron.execution.executor import Executor
from ultron.execution.permission import ExecutionPermission
from ultron.domain.task import Task
from ultron.executive.tasks.manager import TaskManager
from ultron.skills.base import Skill
from ultron.skills.manager import SkillManager
from ultron.security.permission.rules import (
    AUTHORIZATION_REQUIRED_ACTIONS,
    UNTRUSTED_SAFE_ACTIONS,
)
from ultron.skills.system.skill import SystemSkill
from ultron.skills.terminal.skill import TerminalSkill
from ultron.skills.file.skill import FileSkill
from ultron.skills.project.skill import ProjectSkill
from ultron.skills.self_awareness.skill import SelfAwarenessSkill
from ultron.skills.improvement.skill import ImprovementSkill
from ultron.skills.financial.skill import FinancialSkill


def test_execution_engine():
    engine = ExecutionEngine()

    plan = Plan(
        goal="Run a harmless terminal command",
        steps=[
            PlanStep(
                order=1,
                action="run_command",
                parameters={
                    "command": "pwd",
                },
            )
        ],
    )

    result = engine.execute(plan)

    assert result.record.success
    assert result.record.completed_steps == 1
    assert result.record.message == "Execution completed successfully."
    assert result.data[0]["result"]["stdout"].strip()


def test_task_service_executes_real_durable_task_via_application_boundary(tmp_path):
    database = tmp_path / "task-service.db"
    store = SQLiteOperationStore(database)
    task_manager = TaskManager(database=database, operation_store=store)
    task = Task(
        id="app-task-pwd",
        goal_id="app-scope-goal",
        title="pwd",
        description="print working directory",
    )
    task_manager.create_tasks([task])

    skills = SkillManager()
    executor = Executor(skills=skills, audit_store=store)
    service = TaskService(
        task_manager=task_manager,
        executor=executor,
        operation_store=store,
    )

    result = service.execute_next_task()

    assert result is not None
    assert result["task"].status == TaskStatus.COMPLETED
    assert result["verification"]["outcome"] == "CONFIRMED_SUCCESS"
    assert result["operation"]["verificationAudit"]["verified"] is True
    assert task_manager.get_task("app-task-pwd").status == TaskStatus.COMPLETED


def test_task_service_reports_recovery_failure_without_replacing_task_error():
    task = Task(
        id="recovery-error-task",
        goal_id="recovery-error-goal",
        title="pwd",
        attempt_id="recovery-error-attempt",
    )

    class TaskManagerWithFailingRecovery:
        def claim_task(self):
            return task

        def recover_interrupted_task(self, _task_id):
            raise OSError("recovery database unavailable")

    service = TaskService(
        task_manager=TaskManagerWithFailingRecovery(),
        executor=object(),
    )

    def fail_plan(_task):
        raise ValueError("task plan could not be built")

    try:
        service.execute_next_task(plan_builder=fail_plan)
    except ValueError as error:
        assert str(error) == "task plan could not be built"
        assert error.__notes__ == [
            "Task recovery failed (OSError); the persisted task state may require operator inspection."
        ]
    else:
        raise AssertionError("Task plan failure was not propagated.")


def test_executor_does_not_retry_after_skill_may_have_caused_a_side_effect(
    capsys,
):
    class SideEffectThenRaiseSkill(Skill):
        name = "side-effect-then-raise"
        actions = ["run_command"]
        domain = "system"
        action_permissions = {
            "run_command": frozenset({"system.command"}),
        }

        def __init__(self):
            self.calls = 0

        def execute(self, action, **kwargs):
            self.calls += 1
            raise RuntimeError("simulated failure after possible side effect")

    skills = SkillManager()
    skills.registry.unregister("run_command")
    skill = SideEffectThenRaiseSkill()
    skills.register(skill)
    executor = Executor(skills=skills, max_retries=3)
    plan = Plan(
        goal="Exercise uncertain execution handling",
        steps=[
            PlanStep(
                order=1,
                action="run_command",
                parameters={"command": "pwd"},
            )
        ],
    )

    result = executor.execute(plan)

    assert skill.calls == 1
    assert result["success"] is False
    assert result["results"][0]["status"] == "UNCERTAIN"
    assert result["results"][0]["attempts"] == 1
    assert result["recovery"][0]["retry"] is False
    assert "simulated failure after possible side effect" not in capsys.readouterr().out
    assert "simulated failure after possible side effect" not in repr(result)


def test_executor_preserves_uncertainty_when_result_audit_write_fails(tmp_path):
    class SideEffectThenAuditFailureSkill(Skill):
        name = "side-effect-before-audit-failure"
        actions = ["analyze"]
        domain = "market"
        action_permissions = {
            "analyze": frozenset({"market.research.read"}),
        }

        def __init__(self):
            self.calls = 0

        def execute(self, action, **kwargs):
            self.calls += 1
            return {"success": True, "value": "completed"}

    store = SQLiteOperationStore(tmp_path / "execution-audit.db")
    store._connection.execute(
        """
        CREATE TRIGGER reject_execution_result_audit
        BEFORE INSERT ON execution_audit
        WHEN NEW.event = 'EXECUTION_RESULT'
        BEGIN
            SELECT RAISE(ABORT, 'injected result audit failure');
        END
        """
    )
    skills = SkillManager()
    skills.registry.unregister("analyze")
    skill = SideEffectThenAuditFailureSkill()
    skills.register(skill)
    executor = Executor(skills=skills, max_retries=3, audit_store=store)
    plan = Plan(
        goal="Exercise an audit persistence failure after dispatch",
        steps=[
            PlanStep(
                order=1,
                action="analyze",
                parameters={"subject": "controlled test"},
            )
        ],
    )

    result = executor.execute(plan, operation_id="audit-operation")
    stored_audit = store.execution_audit(operation_id="audit-operation")

    assert skill.calls == 1
    assert result["success"] is False
    assert result["results"][0]["status"] == "UNCERTAIN"
    assert result["results"][0]["attempts"] == 1
    assert result["recovery"][0]["retry"] is False
    assert not any(record["event"] == "EXECUTION_RESULT" for record in stored_audit)
    assert any(
        record["event"] == "RECOVERY_DECISION"
        and record["execution_outcome"] == "RECOVERY_STOPPED"
        for record in stored_audit
    )
    store.close()


def test_executor_blocks_unapproved_terminal_commands_before_dispatch():
    class CountingTerminalSkill(Skill):
        name = "counting-terminal"
        actions = ["run_command"]

        def __init__(self):
            self.calls = 0

        def execute(self, action, **kwargs):
            self.calls += 1
            return {"success": True}

    skill_manager = SkillManager()
    skill_manager.registry.unregister("run_command")
    skill = CountingTerminalSkill()
    skill_manager.register(skill)
    executor = Executor(skills=skill_manager)

    for parameters in (
        {"command": "python -c 'print(1)'"},
        {"command": "pytest -q"},
        {"command": "git status"},
        {"command": "pip install example"},
        {"command": "pwd", "cwd": "/tmp"},
    ):
        result = executor.execute(
            Plan(
                goal="Verify pre-dispatch terminal policy",
                steps=[
                    PlanStep(
                        order=1,
                        action="run_command",
                        parameters=parameters,
                    )
                ],
            )
        )
        assert result["results"][0]["status"] == "BLOCKED"

    assert skill.calls == 0
    assert ExecutionPermission().check(
        "run_command",
        command="pwd",
    )


@pytest.mark.parametrize("action", sorted(AUTHORIZATION_REQUIRED_ACTIONS))
def test_protected_actions_are_blocked_before_executor_dispatch(action):
    class RecordingSkill(Skill):
        name = "recording-consequential"
        actions = [action]

        def __init__(self):
            self.calls = []

        def execute(self, requested_action, **kwargs):
            self.calls.append((requested_action, kwargs))
            return {"success": True}

    skills = SkillManager()
    skills.registry.unregister(action)
    skill = RecordingSkill()
    skills.register(skill)
    result = Executor(skills=skills).execute(
        Plan(
            goal=f"Check guarded {action}",
            steps=[PlanStep(order=1, action=action, parameters={"value": 1})],
        )
    )

    assert result["results"][0]["status"] == "BLOCKED"
    assert result["results"][0]["result"]["blocked"] is True
    assert skill.calls == []


@pytest.mark.parametrize("action", sorted(AUTHORIZATION_REQUIRED_ACTIONS))
def test_direct_skill_manager_calls_cannot_bypass_protected_action_gate(
    action,
):
    class RecordingSkill(Skill):
        name = "recording-consequential"
        actions = [action]

        def __init__(self):
            self.calls = []

        def execute(self, requested_action, **kwargs):
            self.calls.append((requested_action, kwargs))
            return {"success": True}

    skills = SkillManager()
    skills.registry.unregister(action)
    skill = RecordingSkill()
    skills.register(skill)

    with pytest.raises(PermissionError, match="trusted authorization"):
        skills.execute(action, value=1)

    assert skill.calls == []


def test_newly_registered_unknown_action_is_blocked_at_both_dispatch_layers():
    class NewlyRegisteredSkill(Skill):
        name = "newly-registered"
        actions = ["newly_registered_side_effect"]

        def __init__(self):
            self.calls = []

        def execute(self, action, **kwargs):
            self.calls.append((action, kwargs))
            return {"success": True}

    skill_manager = SkillManager()
    skill = NewlyRegisteredSkill()
    skill_manager.register(skill)

    direct_error = None
    try:
        skill_manager.execute("newly_registered_side_effect", target="sentinel")
    except PermissionError as error:
        direct_error = error

    executor_result = Executor(skills=skill_manager).execute(
        Plan(
            goal="Try a newly registered action without authorization",
            steps=[
                PlanStep(
                    order=1,
                    action="newly_registered_side_effect",
                    parameters={"target": "sentinel"},
                )
            ],
        )
    )

    assert direct_error is not None
    assert "explicit safe classification" in str(direct_error)
    assert executor_result["results"][0]["status"] == "BLOCKED"
    assert skill.calls == []


def test_container_registered_skill_actions_have_explicit_dispatch_classification():
    registered_skill_types = (
        SystemSkill,
        TerminalSkill,
        FileSkill,
        ProjectSkill,
        SelfAwarenessSkill,
        ImprovementSkill,
        FinancialSkill,
    )
    registered_actions = {
        action
        for skill_type in registered_skill_types
        for action in skill_type.actions
    }

    assert registered_actions == (
        AUTHORIZATION_REQUIRED_ACTIONS & registered_actions
    ) | (UNTRUSTED_SAFE_ACTIONS & registered_actions)
    assert not (
        AUTHORIZATION_REQUIRED_ACTIONS & UNTRUSTED_SAFE_ACTIONS
    )


@pytest.mark.parametrize(
    "action",
    sorted(UNTRUSTED_SAFE_ACTIONS - {"run_command"}),
)
def test_explicit_read_only_actions_remain_available_without_grants(action):
    assert ExecutionPermission().check(action) is True


@pytest.mark.parametrize(
    "action",
    sorted(AUTHORIZATION_REQUIRED_ACTIONS | {"newly_registered_action"}),
)
def test_sensitive_and_unclassified_actions_are_not_executor_allowed(action):
    assert ExecutionPermission().check(action) is False


def test_execution_and_recovery_audit_survive_process_termination(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    workspace = Path(__file__).resolve().parents[1]
    database = tmp_path / "execution-audit.db"
    side_effect = tmp_path / "side-effect-record"
    worker = """
import os
import sys
from pathlib import Path

from ultron.application.runtime.operation_store import SQLiteOperationStore
from ultron.domain.plan import Plan, PlanStep
from ultron.execution.executor import Executor
from ultron.skills.base import Skill
from ultron.skills.manager import SkillManager

database, side_effect = sys.argv[1:]

class SideEffectThenRaise(Skill):
    name = "audit-side-effect-then-raise"
    actions = ["run_command"]
    domain = "system"
    action_permissions = {
        "run_command": frozenset({"system.command"}),
    }

    def execute(self, action, **kwargs):
        Path(side_effect).write_text("effect", encoding="ascii")
        raise RuntimeError("AUDIT_SECRET_MUST_NOT_BE_STORED")

skills = SkillManager()
skills.registry.unregister("run_command")
skills.register(SideEffectThenRaise())
store = SQLiteOperationStore(database)
executor = Executor(skills=skills, max_retries=2, audit_store=store)
result = executor.execute(
    Plan(
        goal="Persist uncertain execution evidence",
        steps=[
            PlanStep(
                order=1,
                action="run_command",
                parameters={
                    "command": "pwd",
                    "password": "AUDIT_SECRET_MUST_NOT_BE_STORED",
                },
            )
        ],
    ),
    task_id="audit-task",
    operation_id="audit-operation",
    correlation_id="audit-correlation",
)
if result["results"][0]["status"] != "UNCERTAIN":
    raise RuntimeError("Expected an uncertain execution result.")
os._exit(0)
"""
    environment = {
        "HOME": str(tmp_path),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(workspace),
    }
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            worker,
            str(database),
            str(side_effect),
        ],
        cwd=workspace,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert "AUDIT_SECRET_MUST_NOT_BE_STORED" not in process.stdout
    assert "AUDIT_SECRET_MUST_NOT_BE_STORED" not in process.stderr
    assert side_effect.read_text(encoding="ascii") == "effect"

    store = SQLiteOperationStore(database)
    records = store.execution_audit(
        task_id="audit-task",
        operation_id="audit-operation",
    )
    store.close()

    assert [record["event"] for record in records] == [
        "DISPATCH_BOUNDARY",
        "RECOVERY_DECISION",
        "EXECUTION_EXCEPTION",
    ]
    assert len({record["attempt_id"] for record in records}) == 1
    assert all(
        record["correlation_id"] == "audit-correlation"
        and record["verification_outcome"] == "NOT_PERFORMED"
        for record in records
    )
    assert records[0]["execution_outcome"] == "DISPATCH_MAY_HAVE_STARTED"
    assert records[1]["execution_outcome"] == "RECOVERY_STOPPED"
    assert records[2]["execution_outcome"] == "UNCERTAIN"
    assert records[1]["evidence"]["retry"] is False
    assert records[2]["failure_classification"] == "RuntimeError"
    for database_file in tmp_path.glob("execution-audit.db*"):
        assert b"AUDIT_SECRET_MUST_NOT_BE_STORED" not in database_file.read_bytes()


def test_task_execution_links_durable_lifecycle_to_unverified_dispatch(tmp_path):
    from datetime import UTC, datetime

    from ultron.application.runtime.operation_verification import (
        LocalReadOnlyOperationVerifier,
    )

    database = tmp_path / "task-execution.db"
    store = SQLiteOperationStore(database)
    tasks = TaskManager(database=database, operation_store=store)
    task = Task(
        id="audited-task",
        goal_id="audited-goal",
        title="Execute and preserve audit",
    )
    tasks.create_tasks([task])
    claimed = tasks.claim_task()
    assert claimed is not None

    skills = SkillManager()
    executor = Executor(skills=skills, audit_store=store)
    result = tasks.execute_claimed_task(
        claimed.id,
        executor,
        Plan(
            goal="Run and verify harmless pwd",
            steps=[
                PlanStep(
                    order=1,
                    action="run_command",
                    parameters={"command": "pwd"},
                )
            ],
        ),
        operation_id="audited-operation",
        correlation_id="audited-correlation",
    )

    assert result["execution"]["success"] is True
    assert result["task"].status == TaskStatus.UNCERTAIN
    audit = store.execution_audit(
        task_id="audited-task",
        operation_id="audited-operation",
    )
    assert audit[-1]["execution_outcome"] == "DISPATCH_RETURNED"
    assert audit[-1]["verification_outcome"] == "NOT_PERFORMED"
    assert audit[-1]["correlation_id"] == "audited-correlation"
    assert audit[-1]["attempt_id"] == claimed.attempt_id
    assert len(tasks.task_events("audited-task")) == 4

    operation = {
        "operationId": "audited-operation",
        "correlationId": "audited-correlation",
        "verb": "execute",
        "commandText": "pwd",
        "expectedEffect": (
            "Return the current working directory without changing state."
        ),
        "parameters": {"subject": "pwd"},
        "lifecycleState": "VERIFIED",
        "createdAt": datetime.now(UTC).isoformat(),
        "updatedAt": datetime.now(UTC).isoformat(),
        "executionResult": {"result": result["execution"]},
    }
    verifier = LocalReadOnlyOperationVerifier()
    operation["requestFingerprint"] = verifier._request_fingerprint(operation)
    verification_evidence = verifier.verify(operation)
    assert verification_evidence is not None
    assert verification_evidence["outcome"] == "CONFIRMED_SUCCESS"
    operation["verificationAudit"] = verification_evidence
    store.create(operation)
    resolved = tasks.resolve_uncertain_task(
        "audited-task",
        claimed.attempt_id,
        operation_id="audited-operation",
    )
    assert resolved.status == TaskStatus.COMPLETED
    resolution_event = tasks.task_events("audited-task")[-1]
    assert resolution_event["verification_outcome"] == "CONFIRMED_SUCCESS"
    assert resolution_event["evidence_fingerprint"]
    assert "verified" not in repr(resolution_event).lower()
    store.close()
    tasks.close()