class VisionModelManager:
    """
    Manages vision AI models.
    """

    def __init__(
        self,
    ):

        self.models = {}

    def register(
        self,
        name,
        model,
    ):

        self.models[name] = model

    def get(
        self,
        name,
    ):

        return self.models.get(name)

    def available_models(
        self,
    ):

        return list(self.models.keys())
