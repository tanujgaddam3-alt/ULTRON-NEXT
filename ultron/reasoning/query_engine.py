from typing import ClassVar


class QueryEngine:
    """
    Retrieves knowledge from graph memory.
    """

    def __init__(self, store):

        self.store = store

    def find(self, concept):

        results = self.store.query_node(concept)

        knowledge: ClassVar = []

        for row in results:
            knowledge.append({"concept": row[1], "relation": row[2], "target": row[3]})

        return knowledge
