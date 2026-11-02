from ultron.learning.feedback.model import Feedback
from ultron.learning.feedback.processor import FeedbackProcessor


feedback = Feedback(

    concept="Gravity",

    old_statement=
    "Heavier objects fall faster",

    correction=
    "Objects fall with same acceleration without air resistance",

    confidence_change=-0.5

)


result = FeedbackProcessor().process(
    feedback
)


print(result)
