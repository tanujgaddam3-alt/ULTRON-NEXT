from typing import ClassVar


class ContradictionDetector:
    """
    Detects conflicting knowledge statements.
    """

    def detect(self, knowledge_a, knowledge_b):

        conflicts: ClassVar = []

        if knowledge_a["concept"] == knowledge_b["concept"]:
            if knowledge_a["statement"].lower() != knowledge_b["statement"].lower():
                conflicts.append(
                    {
                        "concept": knowledge_a["concept"],
                        "first": knowledge_a["statement"],
                        "second": knowledge_b["statement"],
                    }
                )

        return conflicts
