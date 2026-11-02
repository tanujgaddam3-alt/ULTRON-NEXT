from ultron.cognition.reasoning.stages.base import ReasoningStage
from ultron.cognition.reasoning.state import ReasoningState


class ReasoningPipeline:
    """
    Executes a sequence of reasoning stages.
    """

    def __init__(self):
        self.stages: list[ReasoningStage] = []

    def add_stage(
        self,
        stage: ReasoningStage,
    ) -> None:
        """
        Register a reasoning stage.
        """
        self.stages.append(stage)

    def run(
        self,
        state: ReasoningState,
    ) -> ReasoningState:
        """
        Execute all reasoning stages in order.
        """

        for stage in self.stages:
            state = stage.run(state)

        return state
