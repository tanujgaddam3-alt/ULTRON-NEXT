from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from ultron.domain.decision import Decision
from ultron.domain.perception import Perception
from ultron.domain.plan import Plan
from ultron.domain.understanding import Understanding
from ultron.world.models import WorldSnapshot


class CognitiveContext(BaseModel):
    """
    Shared context passed through the entire cognitive pipeline.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    world: WorldSnapshot | None = None

    perception: Perception | None = None

    understanding: Understanding | None = None

    reasoning: object | None = None

    decision: Decision | None = None

    plan: Plan | None = None

    execution_result: object | None = None

    metadata: dict[str, object] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
