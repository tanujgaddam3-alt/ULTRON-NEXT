from typing import ClassVar


class GraphTraversal:
    def __init__(self, store):

        self.store = store

    def find_path(self, start, target, depth=5):

        visited = set()

        return self._search(start, target, depth, visited)

    def _search(self, current, target, depth, visited):

        if depth == 0:
            return None

        if current == target:
            return [current]

        visited.add(current)

        edges = self.store.query_node(current)

        for edge in edges:
            source = edge[1]
            relation = edge[2]
            target_node = edge[3]

            if source == current:
                if target_node not in visited:
                    result = self._search(target_node, target, depth - 1, visited)

                    if result:
                        return [current, relation, *result]

        return None

    def explore(self, start, depth=2):

        visited = set()

        return self._explore(start, depth, visited)

    def _explore(self, node, depth, visited):

        if depth == 0:
            return []

        if node in visited:
            return []

        visited.add(node)

        results: ClassVar = []

        edges = self.store.query_node(node)

        for edge in edges:
            source = edge[1]
            relation = edge[2]
            target = edge[3]

            if source == node:
                results.append({"from": source, "relation": relation, "to": target})

                results.extend(self._explore(target, depth - 1, visited))

        return results

    def explore_until(self, start, targets, depth=5):

        paths: ClassVar = []

        def walk(node, path, level):

            if level == 0:
                return

            if node in targets:
                paths.append(path)
                return

            for edge in self.store.query_node(node):
                source = edge[1]
                relation = edge[2]
                target = edge[3]

                if source == node:
                    walk(
                        target,
                        path + [{"from": source, "relation": relation, "to": target}],
                        level - 1,
                    )

        walk(start, [], depth)

        return paths
