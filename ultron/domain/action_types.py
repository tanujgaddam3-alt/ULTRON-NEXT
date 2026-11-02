from enum import Enum


class ActionType(str, Enum):
    """
    Canonical actions understood by ULTRON.

    Every cognitive component must use these values instead
    of arbitrary strings.
    """

    RESPOND = "respond"

    ASK_CLARIFICATION = "ask_clarification"

    OPEN_APPLICATION = "open_application"
    GET_SYSTEM_INFO = "get_system_info"
    GET_DISK_USAGE = "get_disk_usage"

    BUILD_PROJECT = "build_project"

    READ_FILE = "read_file"

    WRITE_FILE = "write_file"

    SEARCH_WEB = "search_web"

    EXECUTE_SHELL = "execute_shell"

    EXECUTE_PYTHON = "execute_python"

    LEARN = "learn"

    REFLECT = "reflect"

    SELF_ANALYZE = "self_analyze"

    IMPROVE_SELF = "improve_self"

    IDLE = "idle"
