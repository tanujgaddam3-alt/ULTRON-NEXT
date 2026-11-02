from ultron.knowledge.graph.builder import KnowledgeGraphBuilder
from ultron.learning.understanding.knowledge_unit import KnowledgeUnit


unit = KnowledgeUnit(

    concepts=[
        "Velocity"
    ],

    relationships=[
        "Velocity depends on displacement and time"
    ]

)


graph = KnowledgeGraphBuilder().build(
    [unit]
)


print(graph)
