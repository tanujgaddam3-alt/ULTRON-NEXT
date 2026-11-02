class SecurityFactory:
    """
    Creates ULTRON security components.
    """

    def __init__(
        self,
        config=None,
    ):

        self.config = config

    def create_guardian(
        self,
        guardian_class,
        **dependencies,
    ):

        return guardian_class(**dependencies)

    def create_component(
        self,
        component_class,
        **dependencies,
    ):

        return component_class(**dependencies)
