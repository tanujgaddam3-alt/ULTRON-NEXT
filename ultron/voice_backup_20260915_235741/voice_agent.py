from ultron.agents.base import BaseAgent


class VoiceAgent(BaseAgent):
    """
    Handles voice interaction.
    """

    name = "voice"

    capabilities = [
        "speech_input",
        "speech_output",
        "voice_commands",
        "conversation",
    ]

    def __init__(
        self,
        recognizer=None,
        speaker=None,
        wake_detector=None,
        memory=None,
    ):

        self.recognizer = recognizer

        self.speaker = speaker

        self.wake = wake_detector

        self.memory = memory

    def process(
        self,
        audio,
    ):

        result = {
            "activated": False,
            "text": None,
        }

        if self.recognizer:
            speech = self.recognizer.recognize(audio)

            result["text"] = speech.text

        if self.wake and result["text"]:
            result["activated"] = self.wake.detect(result["text"]).detected

        return result

    def respond(
        self,
        text,
    ):

        if self.speaker:
            return self.speaker.speak(text)

        return text
