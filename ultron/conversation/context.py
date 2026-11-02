class ConversationContext:


    def __init__(
        self,
        memory=None,
        knowledge=None,
    ):

        self.memory = memory

        self.knowledge = knowledge



    def build(
        self,
        message: str
    ):

        context = {}


        if self.memory:

            context["memory"] = (
                self.memory.recall(message)
            )


        if self.knowledge:

            context["knowledge"] = (
                self.knowledge.recall(message)
            )


        return context
