class CommandRouter:
    """
    Routes commands to correct handlers.
    """

    def __init__(
        self,
    ):

        self.handlers = {}

    def register(
        self,
        action,
        handler,
    ):

        self.handlers[action] = handler

    def route(
        self,
        command,
    ):

        handler = self.handlers.get(command.action)

        if not handler:
            return {
                "status": "no_handler",
                "action": command.action,
            }

        return handler.execute(command)
