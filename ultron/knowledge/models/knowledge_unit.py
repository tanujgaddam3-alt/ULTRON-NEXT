from pydantic import BaseModel


class KnowledgeUnit(BaseModel):
    concept: str

    definitions: list[str] = []

    relationships: list[str] = []

    examples: list[str] = []

    rules: list[str] = []

    source: str | None = None
