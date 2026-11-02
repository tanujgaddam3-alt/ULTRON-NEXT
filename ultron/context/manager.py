from ultron.context.model import ContextState


class ContextManager:
    """
    Maintains active context.
    """

    def __init__(self):

        self.context = ContextState()

    def update(
        self,
        user_input: str,
        goal: str = "",
    ):

        self.context.user_input = user_input

        if goal:
            self.context.current_goal = goal

        self.context.conversation.append(user_input)

        return self.context

    def add_memory(
        self,
        memory: dict,
    ):

        self.context.memories.append(memory)

    def get(
        self,
    ) -> ContextState:

        return self.context
