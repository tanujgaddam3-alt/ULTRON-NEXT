from pydantic import BaseModel

from ultron.domain.decision import Decision


class DecisionResult(BaseModel):
    decision: Decision | None

    evaluated: int
