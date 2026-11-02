from abc import ABC, abstractmethod

from ultron.domain.action import Action
from ultron.domain.observation import Observation


class Skill(ABC):
    @abstractmethod
    def execute(
        self,
        action: Action,
    ) -> Observation:
        raise NotImplementedError
