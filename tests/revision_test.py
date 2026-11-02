from ultron.knowledge.storage.revision_store import RevisionStore


revision = {

"concept":"Gravity",

"replaced":
"Heavier objects fall faster",

"new_knowledge":
"Objects fall with same acceleration without air resistance",

"confidence":1.0

}


result = RevisionStore(None).revise(
    revision
)


print(result)
