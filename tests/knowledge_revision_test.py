from ultron.knowledge.storage.knowledge_store import KnowledgeStore


store = KnowledgeStore(
    "revision.db"
)


store.add(

"Gravity",

"Heavier objects fall faster",

0.8,

"textbook"

)


store.update_confidence(

"Heavier objects fall faster",

0.3

)


print("updated")
