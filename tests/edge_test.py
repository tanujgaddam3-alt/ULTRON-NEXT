from ultron.knowledge.graph.edge import KnowledgeEdge


edge = KnowledgeEdge(

    source="Force",

    relation="causes",

    target="Acceleration",

    explanation=
    "Force produces acceleration according to Newton's Second Law",

    confidence=0.95,

    source_document="physics.md"

)


print(edge)
