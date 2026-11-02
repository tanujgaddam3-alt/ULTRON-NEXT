from ultron.learning.reader.manager import DocumentReaderManager
from ultron.learning.understanding.engine import UnderstandingEngine
from ultron.knowledge.graph.builder import KnowledgeGraphBuilder
from ultron.knowledge.storage.graph_store import GraphStore
from ultron.reasoning.traversal import GraphTraversal
from ultron.reasoning.explanation_builder import ExplanationBuilder
from ultron.reasoning.question.analyzer import QuestionAnalyzer
from ultron.reasoning.confidence import ConfidenceCalculator

# Load memory created by learning session

store = GraphStore(
    "ultron_learning.db"
)


analyzer = QuestionAnalyzer()

traversal = GraphTraversal(
    store
)

explainer = ExplanationBuilder()

confidence = ConfidenceCalculator()


question = "Why does force affect velocity?"


analysis = analyzer.analyze(
    question
)


print("QUESTION ANALYSIS")
print(analysis)


paths = []


concepts = analysis["concepts"]


if len(concepts) >= 2:

    paths = traversal.find_path(
        concepts[0],
        concepts[1],
        depth=5
    )


print("\nREASONING PATH")
print(paths)


print("\nREASONING PATH")
print(paths)


print("\nANSWER")
print(
    explainer.build(paths)
)
answer = explainer.build(paths)

score = confidence.calculate(paths)


print(answer)

print(
    "Confidence:",
    score
)
