class CapabilityVerifier:
    """
    Verifies whether capabilities are actually usable.
    """


    def __init__(
        self,
        registry=None,
        evaluator=None,
    ):

        self.registry = registry

        self.evaluator = evaluator



    def verify(
    self,
    capability,
):

     if self.evaluator:

        result = (
            self.evaluator.evaluate(
                capability
            )
        )


        exists = result["details"]["exists"]

        return {

            "capability":
                capability,

            "exists":
                exists,

            "score":
                result["score"],

            "usable":
                exists and result["score"] >= 50,

            "details":
                result["details"],

        }


     return {

        "capability":
            capability,

        "exists":
            False,

        "score":
            0,

        "usable":
            False,

    }


    def verify_goal(
        self,
        capabilities,
    ):

        results = []


        for capability in capabilities:

            results.append(
                self.verify(
                    capability
                )
            )


        return {

            "complete":
                all(
                    item["usable"]
                    for item in results
                ),

            "capabilities":
                results,

        }
