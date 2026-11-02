from .base import DomainModel


class Fact(DomainModel):
    key: str

    value: str

    source: str

    confidence: float = 1.0
