from datetime import UTC, datetime


class SecurityPolicyManager:
    """
    Manages ULTRON security policies.
    """

    def __init__(self):

        self.policies = {}

    def add_policy(
        self,
        name: str,
        value,
    ):

        self.policies[name] = {
            "value": value,
            "created_at": (datetime.now(UTC)),
        }

        return self.policies[name]

    def get_policy(
        self,
        name: str,
    ):

        return self.policies.get(name)

    def check(
        self,
        name: str,
        default=None,
    ):

        policy = self.get_policy(name)

        if policy:
            return policy["value"]

        return default
