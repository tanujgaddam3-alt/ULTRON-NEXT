class SecurityRegistry:
    """
    Stores ULTRON security components.
    """

    def __init__(
        self,
    ):

        self.components = {}

    def register(
        self,
        name: str,
        component,
    ):

        self.components[name] = component

    def get(
        self,
        name: str,
    ):

        return self.components.get(name)

    def list_components(
        self,
    ):

        return list(self.components.keys())
