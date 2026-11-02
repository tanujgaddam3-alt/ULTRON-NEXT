import re


class CurriculumParser:
    """
    Parses structured learning documents.
    """

    def process(
        self,
        text: str,
    ) -> str:

        text = re.sub(
            r"[*#`]",
            "",
            text,
        )

        text = re.sub(
            r"\n+",
            "\n",
            text,
        )

        return text.strip()
