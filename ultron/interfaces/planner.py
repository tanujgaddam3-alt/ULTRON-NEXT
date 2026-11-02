from abc import ABC, abstractmethod

from ultron.domain.goal import Goal
from ultron.domain.plan import Plan


class Planner(ABC):
    @abstractmethod
    def create_plan(
        self,
        goal: Goal,
    ) -> Plan:
        raise NotImplementedError
