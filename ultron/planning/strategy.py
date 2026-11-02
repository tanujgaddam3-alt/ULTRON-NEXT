from enum import Enum


class PlanningStrategy(str, Enum):
    SEQUENTIAL = "sequential"
    HIERARCHICAL = "hierarchical"
    REACTIVE = "reactive"
