from typing import ClassVar


class SubsectionParser:
    """
    Splits concept blocks into
    labeled subsections.
    """

    def extract(
        self,
        block: str,
    ):

        sections: ClassVar = {}

        current = "general"

        sections[current] = []

        for line in block.splitlines():
            line = line.strip()

            if not line:
                continue

            # Detect headings like:
            # CONSTANT_ACCELERATION_EQUATIONS:

            if line.endswith(":"):
                current = line[:-1].strip().lower()

                sections[current] = []

                continue

            sections[current].append(line)

        return sections
