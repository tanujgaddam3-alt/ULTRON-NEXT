from ultron.infrastructure.container import container
from ultron.learning.understanding.validator import KnowledgeValidator


result = container.teacher_orchestrator.teach_subject(
    subject="Physics",
    pdfs=[
        "tests/learning_data/test_physics.md"
    ],
)


print("Subject:", result["subject"])
print("Documents processed:", result["documents"])

for item in result["results"]:
    knowledge = item["knowledge"]

    print(
        "Stored:",
        len(knowledge["stored"]),
        "items"
    )

    print(
        "Metadata:",
        knowledge["metadata"]
    )


print(
    "\nKnowledge stored:"
)

print(
    container.knowledge.recall(
        "Physics"
    )
)


print(
    "\nProgress:"
)

print(
    container.progress.get(
        "Physics"
    )
)
validator = KnowledgeValidator()


for item in result.knowledge:

    confidence = validator.validate_definition(
        item.text
    )

    if confidence > 0.5:

        print(
            item.text
        )

        print(
            "Confidence:",
            confidence
        )
