from typing import ClassVar


class ExplanationBuilder:
    def build(self, path):

        if not path:
            return "No reasoning found."

        sentences: ClassVar = []

        if isinstance(path, list):
            for i in range(0, len(path) - 2, 2):
                source = path[i]
                relation = path[i + 1]
                target = path[i + 2]

                sentence = self.convert(source, relation, target)

                sentences.append(sentence)

        return " ".join(sentences)

    def convert(self, source, relation, target):

        templates = {
            "causes": f"{source} produces {target}",
            "changes": f"{source} changes {target}",
            "affects": f"{source} affects {target}",
            "depends on": f"{source} depends on {target}",
            "proportional to": f"{source} is proportional to {target}",
        }

        return templates.get(relation, f"{source} is related to {target}") + "."
