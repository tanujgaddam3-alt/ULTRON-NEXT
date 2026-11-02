from dataclasses import dataclass


@dataclass
class LearningResult:
    subject: str

    documents: int

    results: list

    knowledge: list

    def __getitem__(self, key):

        return getattr(self, key)

    def get(self, key, default=None):

        return getattr(self, key, default)

    def to_dict(self):

        return {
            "subject": self.subject,
            "documents": self.documents,
            "results": self.results,
            "knowledge": self.knowledge,
        }
