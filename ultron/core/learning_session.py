class LearningSession:
    def __init__(self, reader, understanding, semantic_parser, graph_builder, memory):

        self.reader = reader

        self.understanding = understanding

        self.semantic_parser = semantic_parser

        self.graph_builder = graph_builder

        self.memory = memory

    def learn(self, file_path):

        print("[1] Reading document...")

        document = self.reader.read(file_path)

        print("[2] Understanding...")

        knowledge = self.understanding.understand(document)

        print("[3] Semantic extraction...")

        semantic_units = self.semantic_parser.parse(document.content)

        print("[4] Creating graph...")

        graph = self.graph_builder.build(semantic_units)

        print("[5] Storing memory...")

        self.memory.add_graph(graph)

        return {
            "document": file_path,
            "concepts": len(semantic_units),
            "nodes": len(graph["nodes"]),
            "edges": len(graph["edges"]),
        }
