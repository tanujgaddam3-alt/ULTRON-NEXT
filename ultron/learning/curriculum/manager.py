from .model import Subject


class CurriculumManager:
    """
    Manages ULTRON learning programs.
    """

    def __init__(self):

        self.subjects = {}

    def add_subject(
        self,
        subject: Subject,
    ):

        self.subjects[subject.name] = subject

    def get_subject(
        self,
        name: str,
    ):

        return self.subjects.get(name)

    def all_subjects(
        self,
    ):

        return list(self.subjects.values())
