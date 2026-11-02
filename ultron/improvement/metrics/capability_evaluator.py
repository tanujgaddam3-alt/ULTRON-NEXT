class CapabilityEvaluator:
    """
    Evaluates real capability quality.
    """


    def __init__(
        self,
        registry=None,
        history=None,
        test_runner=None,
    ):

        self.registry = registry
        self.history = history
        self.test_runner = test_runner



    def evaluate(
        self,
        capability,
    ):

        score = 0

        details = {}

        # Existence
        exists = False

        if self.registry:

            exists = (
                self.registry.exists(
                    capability
                )
            )


        if exists:

            score += 20

        details["exists"] = exists



        # History
        executions = 0

        if self.history:

          records = (
          self.history.history()
    )


        executions = sum(
        1
        for item in records
        if item.get("capability") == capability
    )


        if executions > 0:

            score += 20


        details["executions"] = executions



        # Testing
        tests = False

        if self.test_runner:

            result = (
                self.test_runner.run()
            )

            tests = result["success"]


        if tests:

            score += 30


        details["tests"] = tests



        return {

            "capability": capability,

            "score": score,

            "details": details,

        }
