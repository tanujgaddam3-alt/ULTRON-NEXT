from pydantic import Field

from .action import Action
from .base import DomainModel
from .decision import Decision
from .goal import Goal
from .observation import Observation
from .perception import Perception
from .plan import Plan
from .reflection import Reflection
from .task import Task
from .understanding import Understanding


class Workspace(DomainModel):
    """
    Represents ULTRON's current cognitive state.
    """

    goal_stack: list[Goal] = Field(default_factory=list)

    task_stack: list[Task] = Field(default_factory=list)

    active_plan: Plan | None = None

    current_task: Task | None = None

    perception: Perception | None = None

    understanding: Understanding | None = None

    decision: Decision | None = None

    action: Action | None = None

    observation: Observation | None = None

    reflection: Reflection | None = None

    metadata: dict[str, object] = Field(default_factory=dict)
