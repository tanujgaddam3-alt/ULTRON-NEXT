from ultron.security.evidence import (
    EvidenceRecord,
)


class EvidenceCollector:
    """
    Collects context around security events.
    """

    def collect(
        self,
        event_id: str,
        identity=None,
        command=None,
        process=None,
        system=None,
        network=None,
    ):

        return EvidenceRecord(
            event_id=event_id,
            identity=(identity or {}),
            command_info=(command or {}),
            process_info=(process or {}),
            system_info=(system or {}),
            network_info=(network or {}),
        )
