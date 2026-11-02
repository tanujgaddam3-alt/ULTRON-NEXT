from ultron.reflection.engine import ReflectionEngine


class ReflectionService:
    """
    Coordinates reflection.
    """

    def __init__(self, reflection_engine: ReflectionEngine):
        self.engine = reflection_engine

    def reflect(self, execution):
        return self.engine.reflect(execution)
