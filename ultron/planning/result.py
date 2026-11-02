from pydantic import BaseModel

from ultron.domain.plan import Plan


class PlanningResult(BaseModel):
    """
    Result of the planning process.
    """

    plan: Plan
    valid: bool
    
