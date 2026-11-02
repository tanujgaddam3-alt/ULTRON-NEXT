from ultron.learning.reader.manager import DocumentReaderManager

from ultron.learning.understanding.extractors.definition_extractor import (
    DefinitionExtractor,
)

from ultron.learning.understanding.validator import (
    KnowledgeValidator,
)


doc = DocumentReaderManager().read(
    "tests/learning_data/test_physics.md"
)


extractor = DefinitionExtractor()

items = extractor.extract(
    doc
)


validator = KnowledgeValidator()


print("VALID DEFINITIONS\n")


for item in items:

    confidence = validator.validate_definition(
        item.text
    )


    if confidence > 0.5:

        print("- ", item.text)

        print(
            "Confidence:",
            confidence
        )

        print()
