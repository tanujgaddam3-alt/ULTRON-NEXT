class VoiceManager:
    """
    Controls ULTRON voice subsystem.
    """

    def __init__(
        self,
        microphone=None,
        agent=None,
    ):

        self.microphone = microphone

        self.agent = agent

    def start(
        self,
    ):

        if self.microphone:
            return self.microphone.start()

        return {"status": "voice_unavailable"}

    def listen(
        self,
    ):

        if not self.microphone:
            return None

        audio = self.microphone.capture()

        if self.agent:
            return self.agent.process(audio)

        return None

    def stop(
        self,
    ):

        if self.microphone:
            return self.microphone.stop()

        return {"status": "stopped"}
