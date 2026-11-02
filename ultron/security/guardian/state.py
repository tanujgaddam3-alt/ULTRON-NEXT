from enum import Enum


class SecurityState(str, Enum):
    """
    Current ULTRON security state.
    """

    NORMAL = "normal"

    MONITORING = "monitoring"

    ALERT = "alert"

    DEFENDING = "defending"

    LOCKDOWN = "lockdown"
