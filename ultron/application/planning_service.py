class PlanningService:
    """
    Coordinates planning.
    """

    def __init__(self, planning_engine):
        self.engine = planning_engine

    def plan(self, decision):
        return self.engine.plan(decision)
