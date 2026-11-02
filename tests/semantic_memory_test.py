from ultron.knowledge.storage.graph_store import GraphStore


store = GraphStore(
    "semantic_test.db"
)


graph={

"nodes":[
"Force",
"Acceleration"
],


"edges":[

{
"source":"Force",

"relation":"causes",

"target":"Acceleration",

"explanation":
"Force produces acceleration according to Newton's Second Law",

"confidence":0.95,

"source_document":
"physics.md"

}

]

}


store.add_graph(graph)


print(
store.query_node(
"Force"
)
)
