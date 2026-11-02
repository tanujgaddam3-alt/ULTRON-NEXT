class SectionClassifier:
    """
    Classifies document sections into knowledge categories.
    """

    def classify(
        self,
        sections: dict,
    ):

        result = {
            "definitions": [],
            "formulas": [],
            "examples": [],
            "relationships": [],
            "misconceptions": [],
            "records": [],
            "other": [],
        }

        for title, content in sections.items():
            name = title.lower()

            # Knowledge record has highest priority

            if "knowledge record" in name:
                result["records"].append({"title": title, "content": content})

                continue

            if any(
                word in name
                for word in [
                    "worked example",
                    "example",
                ]
            ):
                result["examples"].append(content)

                continue

            if "misconception" in name:
                result["misconceptions"].append(content)

                continue

            if any(
                word in name
                for word in [
                    "equation",
                    "equations",
                    "formula",
                    "formal",
                ]
            ):
                result["formulas"].append(content)

                continue

            if any(
                word in name
                for word in [
                    "relationship",
                    "meaning",
                    "interpretation",
                ]
            ):
                result["relationships"].append(content)

                continue

            if any(
                word in name
                for word in [
                    "definition",
                    "position",
                    "velocity",
                    "acceleration",
                ]
            ):
                result["definitions"].append({"title": title, "content": content})

                continue

            result["other"].append(content)

        return result
