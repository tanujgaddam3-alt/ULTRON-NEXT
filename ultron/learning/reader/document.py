from dataclasses import dataclass, field


@dataclass
class Document:
    """
    Universal document representation.

    Every input type:
    PDF, DOCX, MD, TXT, PPTX, etc.

    gets converted into this format.
    """

    title: str = ""

    content: str = ""

    sections: list = field(default_factory=list)

    metadata: dict = field(default_factory=dict)
