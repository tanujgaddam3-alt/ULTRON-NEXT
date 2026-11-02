from ultron.cognition.reasoning.state import ReasoningState

from .base import ReasoningStage


class GoalStage(ReasoningStage):
    """
    Derives active goals from the detected intent.
    """

    GOALS = {
        "open_application": "Launch requested application",
        "store_memory": "Persist user information",
        "query_time": "Retrieve current time",
        "query_weather": "Retrieve weather information",
        "search": "Retrieve requested information",
        "unknown": "Clarify user request",
    }

    def run(
        self,
        state: ReasoningState,
    ) -> ReasoningState:

        goal = self.GOALS.get(
            state.intent,
            "Clarify user request",
        )

        state.goals.append(goal)

        state.trace.append(f"Goal created: {goal}")

        return state
