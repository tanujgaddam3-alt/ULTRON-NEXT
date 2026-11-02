import re
from typing import ClassVar


class CurriculumBlockParser:
    """
    Extracts structured blocks from curriculum documents.
    """

    def extract_blocks(self, text: str):

        blocks = {
            "concepts": [],
            "formulas": [],
            "examples": [],
            "misconceptions": [],
            "records": [],
        }

        # Concepts
        blocks["concepts"] = [
            x.strip()
            for x in re.findall(
                r"CONCEPT:\s*(.+)",
                text,
                re.IGNORECASE,
            )
        ]

        # Knowledge Records
        blocks["records"] = [
            x.strip()
            for x in re.findall(
                r"\*\*ULTRON Knowledge Record:\*\*\s*(.*?)(?=\n\*\*|\nCONCEPT:|\Z)",
                text,
                re.DOTALL,
            )
        ]

        # Worked examples
        blocks["examples"] = [
            x.strip()
            for x in re.findall(
                r"\*\*Worked Examples?:\*\*\s*(.*?)(?=\n\*\*ULTRON Knowledge Record:\*\*|\nCONCEPT:|\Z)",
                text,
                re.DOTALL,
            )
        ]

        # Misconceptions
        blocks["misconceptions"] = [
            x.strip()
            for x in re.findall(
                r"MISCONCEPTION:\s*(.*)",
                text,
                re.IGNORECASE,
            )
        ]

        return blocks

    def extract_concept_blocks(self, text):

        import re

        blocks: ClassVar = []

        positions = [
            m.start()
            for m in re.finditer(
                r"CONCEPT:",
                text,
                flags=re.IGNORECASE,
            )
        ]

        for i, start in enumerate(positions):
            end = positions[i + 1] if i + 1 < len(positions) else len(text)

            block = text[start:end]

            # remove example and record sections
            block = re.split(
                r"\*\*Worked Examples?:\*\*|\*\*ULTRON Knowledge Record:\*\*",
                block,
                flags=re.IGNORECASE,
            )[0]

            blocks.append(block.strip())

        return blocks
