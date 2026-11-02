from ultron.knowledge.intelligence.semantic_parser import SemanticParser


parser = SemanticParser()


text = """
Acceleration is the rate of change of velocity.
Probability is a measure of uncertainty.
Force causes acceleration.
"""


result = parser.parse(text)


for item in result:
    print(item)
