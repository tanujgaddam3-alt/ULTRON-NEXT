from typing import ClassVar

from ultron.learning.understanding.knowledge_unit import KnowledgeUnit


class KnowledgeBuilder:
    """
    Converts normalized extraction output
    into structured knowledge.
    """

    def build(
        self,
        concept_name,
        data,
    ):

        definitions: ClassVar = []

        for item in data["definitions"]:
            if item.get("content"):
                definitions.append(item["content"])

        return KnowledgeUnit(
            concepts=[concept_name],
            definitions=definitions,
            relationships=data["relationships"],
            examples=data["examples"],
            metadata={"source": "document"},
        )
