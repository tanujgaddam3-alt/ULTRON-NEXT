from ultron.learning.teacher.pdf_loader import (
    PDFLoader,
)


class TeacherService:
    """
    Interface for teaching ULTRON.
    """

    def __init__(
        self,
        knowledge_manager,
    ):

        self.knowledge = knowledge_manager

        self.pdf_loader = PDFLoader()

    def teach(
        self,
        content: str,
        source: str,
        subject: str,
    ):

        document = {
            "content": content,
            "source": {
                "type": "education",
                "subject": subject,
                "reference": source,
            },
        }

        return self.knowledge.learn(document)

    def teach_pdf(
        self,
        path: str,
        subject: str,
    ):

        document = self.pdf_loader.load(
            path,
            subject,
        )

        return self.knowledge.learn(document)
