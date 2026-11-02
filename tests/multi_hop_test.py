from ultron.knowledge.storage.graph_store import GraphStore
from ultron.reasoning.traversal import GraphTraversal


store = GraphStore(
    "test_reasoning.db"
)


graph = {

"nodes":[
    "Force",
    "Acceleration",
    "Velocity"
],


"edges":[

{
"source":"Force",
"relation":"causes",
"target":"Acceleration"
},

{
"source":"Acceleration",
"relation":"changes",
"target":"Velocity"
}

]

}


store.add_graph(graph)


engine = GraphTraversal(
    store
)


result = engine.explore(
    "Force",
    depth=3
)


print(result)
