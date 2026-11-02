from abc import ABC, abstractmethod


class BaseObserver(ABC):
    """
    Base interface for world observers.
    """

    @abstractmethod
    def observe(self):
        """
        Collect world information.
        """
        raise NotImplementedError
