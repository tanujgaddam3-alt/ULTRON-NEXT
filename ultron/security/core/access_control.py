from enum import Enum


class AccessLevel(str, Enum):
    NONE = "none"

    USER = "user"

    ADMIN = "admin"

    SYSTEM = "system"


class AccessControl:
    """
    Controls resource access.
    """

    def __init__(self):

        self.permissions = {}

    def grant(
        self,
        user_id: str,
        resource: str,
        level: AccessLevel,
    ):

        self.permissions[(user_id, resource)] = level

    def check(
        self,
        user_id: str,
        resource: str,
    ):

        return self.permissions.get(
            (
                user_id,
                resource,
            ),
            AccessLevel.NONE,
        )
