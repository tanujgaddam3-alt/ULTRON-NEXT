from datetime import UTC, datetime


class ExecutionResult:
    """
    Stores command execution result.
    """

    def __init__(
        self,
        command,
        success,
        output=None,
    ):

        self.command = command

        self.success = success

        self.output = output

        self.timestamp = datetime.now(UTC)


class ExecutionEngine:
    """
    Executes approved ULTRON commands.
    """

    def __init__(
        self,
    ):

        self.executors = {}

    def register(
        self,
        action,
        executor,
    ):

        self.executors[action] = executor

    def execute(
        self,
        command,
    ):

        executor = self.executors.get(command.action)

        if not executor:
            return ExecutionResult(
                command,
                False,
                "No executor found",
            )

        result = executor.execute(command)

        return ExecutionResult(
            command,
            True,
            result,
        )
