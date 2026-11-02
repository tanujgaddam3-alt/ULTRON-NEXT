from ultron.agents.team.model import AgentTeam


class TeamManager:
    """
    Creates and manages agent teams.
    """

    def __init__(self):

        self.teams = []

    def create_team(
        self,
        objective: str,
        agents: list[str],
    ) -> AgentTeam:

        team = AgentTeam(
            name="dynamic_team",
            objective=objective,
            agents=agents,
        )

        self.teams.append(team)

        return team

    def list_teams(self):

        return self.teams
