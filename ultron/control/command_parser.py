from ultron.control.command_models import (
    Command,
    CommandLevel,
)


class CommandParser:
    """
    Converts natural language into commands.
    """

    def parse(
        self,
        text: str,
    ):

        text = text.lower()

        if "open" in text:
            return Command(
                action="open_application",
                target=text.replace("open", "").strip(),
                level=CommandLevel.NORMAL,
                confidence=0.8,
            )

        if "secure" in text:
            return Command(
                action="security_mode",
                level=CommandLevel.CRITICAL,
                confidence=0.9,
            )

        if "monitor" in text:
            return Command(
                action="system_monitor",
                level=CommandLevel.SYSTEM,
                confidence=0.8,
            )

        return Command(
            action="unknown",
            level=CommandLevel.INFORMATION,
            confidence=0.2,
        )
