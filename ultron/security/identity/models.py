from pydantic import BaseModel


class IdentityRecord(BaseModel):
    id: str
    name: str | None = None
    role: str | None = None
