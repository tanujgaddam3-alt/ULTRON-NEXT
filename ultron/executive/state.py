from enum import Enum


class ExecutiveState(str, Enum):
    IDLE = "idle"
    PERCEIVING = "perceiving"
    UNDERSTANDING = "understanding"
    REASONING = "reasoning"
    PLANNING = "planning"
    EXECUTING = "executing"
    LEARNING = "learning"
    ERROR = "error"
