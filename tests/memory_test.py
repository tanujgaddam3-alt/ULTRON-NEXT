from ultron.knowledge.storage.graph_store import GraphStore


graph={

"nodes":[
"Velocity",
"Displacement",
"Time"
],


"edges":[

{
"source":"Velocity",
"relation":"depends on",
"target":"Displacement"
},

{
"source":"Velocity",
"relation":"depends on",
"target":"Time"
}

]

}


store=GraphStore()

store.add_graph(graph)


print(
store.query_node(
"Velocity"
)
)
