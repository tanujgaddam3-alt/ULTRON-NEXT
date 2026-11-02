from dataclasses import dataclass


@dataclass
class VisionConfig:
    """
    Configuration for ULTRON vision system.
    """

    camera_enabled: bool = True

    object_detection_enabled: bool = True

    ocr_enabled: bool = True

    visual_memory_enabled: bool = True

    default_model: str = "none"

    max_memory_entries: int = 1000

    def disable_camera(
        self,
    ):

        self.camera_enabled = False

    def enable_high_accuracy(
        self,
    ):

        self.object_detection_enabled = True

        self.ocr_enabled = True

        self.visual_memory_enabled = True
