from dataclasses import dataclass


@dataclass
class CapabilityRequirement:

    name: str

    description: str

    required_skills: list[str]

    required_actions: list[str]



DEFAULT_CAPABILITIES = [

    CapabilityRequirement(
        name="voice_interaction",
        description="Ability to hear and communicate through voice.",
        required_skills=[
            "voice",
            "speech",
            "audio",
        ],
        required_actions=[
            "listen",
            "speak",
        ],
    ),


    CapabilityRequirement(
        name="visual_perception",
        description="Ability to understand images and environment.",
        required_skills=[
            "vision",
            "camera",
            "ocr",
        ],
        required_actions=[
            "analyze_image",
        ],
    ),


    CapabilityRequirement(
        name="autonomous_learning",
        description="Ability to improve from experience.",
        required_skills=[
            "smith",
            "improvement",
            "memory",
        ],
        required_actions=[
            "learn",
            "reflect",
        ],
    ),


    CapabilityRequirement(
        name="device_control",
        description="Ability to control connected systems.",
        required_skills=[
            "system",
            "terminal",
        ],
        required_actions=[
            "open_application",
            "execute_shell",
        ],
    ),

]
