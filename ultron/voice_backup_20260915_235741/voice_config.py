from dataclasses import dataclass


@dataclass
class VoiceConfig:
    """
    Voice system configuration.
    """

    enabled: bool = True

    wake_word: str = "ultron"

    language: str = "en"

    speech_rate: int = 150

    voice_enabled: bool = True

    memory_enabled: bool = True

    def disable_voice(
        self,
    ):

        self.voice_enabled = False

    def set_wake_word(
        self,
        word: str,
    ):

        self.wake_word = word
