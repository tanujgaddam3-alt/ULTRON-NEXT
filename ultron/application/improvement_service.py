from ultron.improvement.domain.models import (
    ImprovementProposal,
)
class ImprovementService:
    """
    Application interface for self-improvement.
    """

    def __init__(
    self,
    manager,
    advisor=None,
    skills=None,
    schema_repository=None,
    system_registry=None,
):

      self.manager = manager
      self.advisor = advisor

      self.skills = skills
      self.schema_repository = schema_repository
      self.system_registry = system_registry

        


    def analyze_failure(
        self,
        component: str,
        error: str,
    ):

        return self.manager.improve(
            component,
            error,
        )


    def analyze_capability_gap(
        self,
        capability: str,
        reason: str,
    ):

        if not self.advisor:

            return {
                "error":
                "Improvement advisor unavailable"
            }


        recommendation = self.advisor.analyze(
    component=capability,
    problem=reason,
    context={
        "source": "self_awareness",
        "type": "capability_gap",
        "architecture": self._get_architecture_context(),
    },
)


        return {
            "capability": capability,
            "reason": reason,
            "recommendation": recommendation,
        }
    def _get_architecture_context(self):

     return {
        "improvement_system": True,
        "llm_connected": self.advisor is not None,
        "manager_connected": self.manager is not None,

        "capabilities": [
            "self_awareness",
            "improvement_analysis",
            "proposal_generation",
        ],
    }
    def _build_context(self):

     context = {}


     if self.skills:

        context["skills"] = (
            self.skills.available_skills()
        )


     if self.schema_repository:

        context["schemas"] = [

            schema.name

            for schema in
            self.schema_repository.all()

        ]


     if self.system_registry:

        context["systems"] = [

            name

            for name in
            self.system_registry.systems.keys()

        ]


     return context
    def create_capability_plan(
    self,
    capability,
    reason,
):

     recommendation = (
        self.analyze_capability_gap(
            capability,
            reason,
        )
    )


     if not self.manager:

        return recommendation


     proposal = {

        "target": capability,

        "reason": reason,

        "suggestion":
        recommendation.get(
            "recommendation"
        ),

        "type":
        "capability_improvement",

    }


     return self.manager.planner.create_plan(
        proposal
    )
    def plan_capability_improvement(
    self,
    capability: str,
    reason: str,
):
     """
    Converts a capability gap into
    an engineering improvement plan.
    """

     analysis = self.analyze_capability_gap(
        capability,
        reason,
    )


     if not self.manager:

        return {
            "success": False,
            "reason": "Improvement manager unavailable",
            "analysis": analysis,
        }


     proposal = ImprovementProposal(

    target=capability,

    reason=reason,

    suggestion=analysis.get(
        "recommendation",
        "",
    ),

    confidence=0.8,

)


     
     context = None

     if self.manager.context_builder:

      context = self.manager.context_builder.build(
        capability,
        reason,
    )


     plan = self.manager.planner.create_plan(
    proposal,
    context,
)


     patch = None


     if self.manager.generator:

      patch = self.manager.generator.generate(
        plan
    )


     return {

    "success": True,

    "analysis": analysis,

    "plan": plan,

    "patch": patch,

}


    def health_check(self):

        return {
            "manager": self.manager is not None,
            "advisor": self.advisor is not None,
        }