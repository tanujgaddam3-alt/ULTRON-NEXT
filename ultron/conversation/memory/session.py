from collections import defaultdict


class SessionMemory:
    """
    Temporary conversation memory.

    Stores messages during active sessions.
    """

    def __init__(self):
        self.sessions = defaultdict(list)


    def add(
        self,
        session_id: str,
        role: str,
        content: str,
    ):

        self.sessions[session_id].append(
            {
                "role": role,
                "content": content,
            }
        )


    def get(
        self,
        session_id: str,
    ) -> list:

        return self.sessions.get(
            session_id,
            []
        )


    def clear(
        self,
        session_id: str,
    ):

        self.sessions.pop(
            session_id,
            None
        )
