from ultron.learning.feedback.model import Feedback
from ultron.learning.update.engine import KnowledgeUpdateEngine


feedback = Feedback(

    concept="Gravity",

    old_statement=
    "Heavier objects fall faster",

    correction=
    "Objects fall with same acceleration without air resistance",

    confidence_change=-0.5

)


engine = KnowledgeUpdateEngine()


print(
    engine.create_revision(
        feedback
    )
)


print(
    engine.update_confidence(
        0.8,
        -0.5
    )
)
