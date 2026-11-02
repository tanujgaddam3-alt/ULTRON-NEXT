from dataclasses import dataclass


@dataclass
class ControlConfig:
    """
    Configuration for ULTRON control layer.
    """

    enabled: bool = True

    require_confirmation: bool = True

    allow_application_control: bool = True

    allow_system_control: bool = True

    allow_automation: bool = True

    allow_hardware_monitoring: bool = True

    max_command_level: int = 2

    def enable_advanced_mode(
        self,
    ):

        self.allow_application_control = True

        self.allow_system_control = True

        self.allow_automation = True

    def lockdown(
        self,
    ):

        self.require_confirmation = True

        self.max_command_level = 0
