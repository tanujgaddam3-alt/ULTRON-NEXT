class GuardianLearningAdapter:
    """
    Connects security incidents
    with ULTRON learning.
    """

    def __init__(
        self,
        reflection=None,
        experience_store=None,
    ):

        self.reflection = reflection

        self.experience_store = experience_store

    def learn(
        self,
        incident,
    ):

        experience = {
            "type": "security_incident",
            "threat": incident.description,
            "severity": incident.severity,
            "response": incident.action_taken,
        }

        if self.experience_store:
            self.experience_store.save(experience)

        return experience
