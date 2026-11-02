from datetime import UTC, datetime
from uuid import uuid4


class Note:
    """
    Represents a stored note.
    """

    def __init__(
        self,
        title: str,
        content: str,
    ):

        self.id = str(uuid4())

        self.title = title

        self.content = content

        self.created_at = datetime.now(UTC)


class NotesManager:
    """
    Manages personal notes.
    """

    def __init__(
        self,
    ):

        self.notes = []

    def create(
        self,
        title,
        content,
    ):

        note = Note(
            title,
            content,
        )

        self.notes.append(note)

        return note

    def search(
        self,
        keyword,
    ):

        return [note for note in self.notes if keyword.lower() in note.content.lower()]

    def all(
        self,
    ):

        return self.notes
