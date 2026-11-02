from typing import ClassVar

from ultron.knowledge.extraction.concept_relationship_extractor import (
    ConceptRelationshipExtractor,
)
from ultron.knowledge.extraction.definition_extractor import (
    DefinitionExtractor,
)
from ultron.knowledge.extraction.formula_extractor import (
    FormulaExtractor,
)
from ultron.knowledge.extraction.subsection_parser import (
    SubsectionParser,
)


class ConceptKnowledgeBuilder:
    """
    Builds structured knowledge units
    from curriculum concepts.
    """

    def __init__(
        self,
    ):

        self.formula_extractor = FormulaExtractor()

        self.subsection_parser = SubsectionParser()

        self.definition_extractor = DefinitionExtractor()

        self.relationship_extractor = ConceptRelationshipExtractor()

    def build(
        self,
        knowledge_units,
    ):

        all_knowledge: ClassVar = []

        for knowledge_unit in knowledge_units:
            all_knowledge.append(
                {
                    "concept": knowledge_unit.concepts,
                    "definitions": knowledge_unit.definitions,
                    "formulas": [],
                    "relationships": knowledge_unit.relationships,
                    "examples": knowledge_unit.examples,
                    "rules": knowledge_unit.rules,
                    "source": "document",
                }
            )

        return all_knowledge
