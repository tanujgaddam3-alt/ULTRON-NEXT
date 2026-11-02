from typing import ClassVar

from ultron.learning.education.result import LearningResult


class TeacherOrchestrator:
    """
    Coordinates ULTRON's educational learning process.
    """

    def __init__(
        self,
        processor,
        curriculum,
        progress,
    ):

        self.processor = processor

        self.curriculum = curriculum

        self.progress = progress

    def teach_subject(
        self,
        subject: str,
        pdfs: list[str],
    ):

        results: ClassVar = []

        for pdf in pdfs:
            result = self.processor.process_pdf(
                pdf,
                subject,
            )

            results.append(result)

        self.progress.update(
            subject=subject,
            topic="document_completed",
            confidence=1.0,
        )

        return LearningResult(
            subject=subject, documents=len(pdfs), results=results, knowledge=[]
        )
