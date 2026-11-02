class VoiceIntegrationTest:
    """
    Tests voice subsystem integration.
    """

    def __init__(
        self,
        microphone=None,
        recognizer=None,
        speaker=None,
        agent=None,
    ):

        self.microphone = microphone

        self.recognizer = recognizer

        self.speaker = speaker

        self.agent = agent

    def run(
        self,
    ):

        results = {
            "microphone": self.microphone is not None,
            "recognizer": self.recognizer is not None,
            "speaker": self.speaker is not None,
            "agent": self.agent is not None,
        }

        results["passed"] = all(results.values())

        return results
