from enum import Enum


class ConversationType(str, Enum):

    CHAT = "chat"

    COMMAND = "command"

    TASK = "task"


class ConversationRouter:

    def route(
        self,
        message: str,
    ):

        text = message.lower().strip()


        command_patterns = [

            "open ",
            "close ",
            "launch ",
            "run ",
            "execute ",
            "start ",
            "stop ",

            "get system info",
            "system info",

            "disk usage",
            "check disk",

            "battery",

        ]


        if any(
            pattern in text
            for pattern in command_patterns
        ):

            return ConversationType.COMMAND



        task_patterns = [

            "learn",

            "analyze",

            "create",

            "build",

            "plan",

            "design",

            # Self improvement triggers
            "improve",

            "upgrade",

            "enhance",

            "optimize",

            "evolve",

            "make yourself better",

            "improve yourself",

            "upgrade yourself",

            "enhance yourself",

        ]


        if any(
            pattern in text
            for pattern in task_patterns
        ):

            return ConversationType.TASK


        return ConversationType.CHAT