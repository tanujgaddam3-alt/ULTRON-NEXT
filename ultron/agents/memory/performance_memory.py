from datetime import UTC, datetime


class AgentPerformanceMemory:
    """
    Stores agent execution history.
    """

    def __init__(
        self,
    ):

        self.records = []

    def record(
        self,
        agent_name: str,
        task: str,
        success: bool,
    ):

        record = {
            "agent": agent_name,
            "task": task,
            "success": success,
            "timestamp": datetime.now(UTC),
        }

        self.records.append(record)

        return record

    def success_rate(
        self,
        agent_name: str,
    ):

        agent_records = [
            record for record in self.records if record["agent"] == agent_name
        ]

        if not agent_records:
            return 0.0

        successful = sum(1 for record in agent_records if record["success"])

        return successful / len(agent_records)
