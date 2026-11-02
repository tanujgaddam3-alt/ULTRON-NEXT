import re


class CurriculumExtractor:
    """
    Extracts structured learning units
    from ULTRON curriculum documents.

    Extracts:
    - modules
    - chapters
    - concepts
    - formulas
    - misconceptions
    - knowledge records
    """

    def extract(self, text: str) -> dict:

        result = {
            "modules": [],
            "chapters": [],
            "concepts": [],
            "formulas": [],
            "misconceptions": [],
            "records": [],
        }

        result["modules"] = re.findall(r"MODULE\s+\d+\s*:\s*(.+)", text, re.IGNORECASE)

        result["chapters"] = re.findall(
            r"CHAPTER\s+\d+\.\d+\s*:\s*(.+)", text, re.IGNORECASE
        )

        result["concepts"] = re.findall(
            r"Concept\s+\d+\.\d+\.\d+\s*:\s*(.+)", text, re.IGNORECASE
        )

        result["misconceptions"] = re.findall(r"MISCONCEPTION:\s*(.+)", text)

        result["formulas"] = [
            line.strip()
            for line in text.splitlines()
            if (
                "=" in line
                and len(line.strip()) < 120
                and not any(
                    word in line.lower()
                    for word in [
                        "find",
                        "calculate",
                        "object",
                        "mass",
                        "ball",
                        "using",
                        "where",
                    ]
                )
            )
        ]

        result["records"] = re.findall(
            r"ULTRON Knowledge Record:(.*?)(?=---|\Z)", text, re.DOTALL
        )

        return result
