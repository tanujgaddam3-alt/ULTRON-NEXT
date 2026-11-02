from ultron.knowledge.storage.graph_store import GraphStore


store = GraphStore(
    "ultron_learning.db"
)


import sqlite3

conn = sqlite3.connect(
    "ultron_learning.db"
)

cur = conn.cursor()


for edge in cur.execute(
    "SELECT source,relation,target FROM edges"
):

    print(
        edge[0],
        "--",
        edge[1],
        "-->",
        edge[2]
    )
