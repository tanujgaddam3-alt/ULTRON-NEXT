from ultron.improvement.domain.models import ImprovementProposal


class ImprovementManager:
    """
    Coordinates the self-improvement pipeline.
    """


    def __init__(
        self,
        analyzer,
        planner,
        generator,
        test_runner,
        quality_checker,
        safety_validator,
        failure_analyzer=None,
        security_controller=None,
        security_bridge=None,
        repository=None,
        strategy_engine=None,
        patch_applier=None,
        context_builder=None,
        component_builder=None,
        capability_registry=None,
        experience_memory=None,

    ):

        self.analyzer = analyzer
        self.planner = planner
        self.generator = generator
        self.test_runner = test_runner
        self.quality_checker = quality_checker
        self.safety_validator = safety_validator

        self.failure_analyzer = failure_analyzer

        self.security_controller = security_controller
        self.security_bridge = security_bridge

        self.strategy_engine = strategy_engine

        self.patch_applier = patch_applier

        self.context_builder = context_builder

        self.component_builder = component_builder

        self.capability_registry = capability_registry

        self.experience_memory = experience_memory

        self.repository = repository


    def _get_context(
        self,
        component,
        error,
        proposal=None,
    ):

        context = None

        if self.context_builder:

            context = self.context_builder.build(
                component,
                error,
            )

        if context is None:

            context = {}


        if self.strategy_engine and proposal:

            context["strategy"] = (
                self.strategy_engine.evaluate(
                    proposal,
                    context,
                )
            )


        return context


    def _apply_pipeline(
        self,
        proposal,
        plan,
    ):

        patch = self.generator.generate(
            plan
        )


        quality = self.quality_checker.check(
            patch
        )

        if not quality["passed"]:

            return {
                "accepted": False,
                "reason": "Quality check failed",
                "details": quality,
            }


        safety = self.safety_validator.validate(
            patch
        )


        if patch.risk == "HIGH":

            return {
                "accepted": False,
                "reason": "Patch rejected due to high risk.",
                "patch": patch,
            }


        if not safety["safe"]:

            return {
                "accepted": False,
                "reason": "Safety validation failed",
                "details": safety,
            }

        if not self.patch_applier:

            return {
                "accepted": False,
                "reason": "Patch applier unavailable",
            }


        application = self.patch_applier.apply(
    patch
)

        if not application["success"]:

            return {
                "accepted": False,
                "reason": "Patch application failed",
                "details": application,
            }

        backup = application.get("backup")

        tests = self.test_runner.run()


        if not tests["success"]:

            return {
                "accepted": False,
                "reason": "Tests failed after patch",
                "tests": tests,
                "backup": backup,
            }
        if (
    patch.operation == "create"
    and self.capability_registry
):

         self.capability_registry.register(
        name=proposal.target,
        module=(
            f"ultron.skills."
            f"{proposal.target}.skill"
        ),
        description=(
            proposal.description
        ),
    )
         if self.experience_memory:

          self.experience_memory.record(
        {
            "capability": proposal.target,

            "reason": proposal.reason,

            "operation": patch.operation,

            "risk": patch.risk,

            "tests_passed": tests["success"],

            "result": "accepted",
        }
    )


        result = {

            "accepted": True,

            "proposal": proposal,

            "patch": patch,

            "tests": tests,

            "backup": backup,

        }


        if self.repository:

            self.repository.save(
                proposal
            )


        return result



    def improve(
        self,
        component: str,
        error: str,
    ):

        proposal = (
            self.analyzer.analyze_failure(
                component,
                error,
            )
        )


        context = self._get_context(
            component,
            error,
            proposal,
        )


        plan = self.planner.create_plan(
            proposal,
            context,
        )

        if (
    plan.get("operation") == "create"
    and self.component_builder
):

         blueprint = self.component_builder.build(
        capability=proposal.target,
        description=proposal.description,
    )

         plan["blueprint"] = blueprint


        return self._apply_pipeline(
            proposal,
            plan,
        )



    def create_improvement(
        self,
        capability: str,
        reason: str,
    ):

        proposal = ImprovementProposal(

            target=capability,

            description=(
                f"Add missing {capability} capability."
            ),

            reason=reason,

            suggestion=(
                f"Design and implement "
                f"{capability} capability."
            ),

            confidence=1.0,

            evidence={
                "source": "self_awareness",
                "type": "capability_gap",
            },
        )


        context = self._get_context(
            capability,
            reason,
            proposal,
        )

        


        plan = self.planner.create_plan(
            proposal,
            context,
        )

        if (
    plan.get("operation") == "create"
    and self.component_builder
):

         blueprint = self.component_builder.build(
        capability=proposal.target,
        description=proposal.description,
    )

         plan["blueprint"] = blueprint


        return self._apply_pipeline(
            proposal,
            plan,
        )



    def improve_from_test_failure(
        self,
        test_result,
    ):

        if not self.failure_analyzer:

            return {
                "accepted": False,
                "reason": "Failure analyzer unavailable",
            }


        failure = self.failure_analyzer.analyze(
            test_result
        )


        if not failure:

            return {
                "accepted": False,
                "reason": "No failure detected",
            }


        return self.improve(
            failure["component"],
            failure["failure"],
        )