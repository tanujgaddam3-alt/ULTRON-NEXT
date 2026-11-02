class CognitiveResponseFormatter:
    """
    Converts cognitive cycle results into user-facing responses.

    Responsibilities:
    - expose meaningful execution results
    - format self awareness reports
    - format improvement reports
    - handle generic cognitive responses
    """

    def format(
        self,
        cycle,
    ):

        execution = cycle.execution


        if not execution:

            return (
                "No execution result was generated."
            )


        if not execution.record.success:

            return (
                "I was unable to complete the task. "
                f"{execution.record.message}"
            )


        # ---------------------------------
        # Skill generated outputs
        # ---------------------------------

        if execution.data:

            response = self._extract_skill_output(
                execution.data
            )

            if response:

                return response



        # ---------------------------------
        # Normal task completion
        # ---------------------------------

        if cycle.plan.plan.steps:

            return (
                "Completed task. "
                f"{execution.record.message}"
            )



        return self._generate_response(
            cycle
        )



    def _extract_skill_output(
        self,
        data,
    ):
        """
        Extract meaningful information from
        skill execution results.
        """

        for item in data:

            if not isinstance(
                item,
                dict,
            ):
                continue


            result = item.get(
                "result"
            )


            if not isinstance(
                result,
                dict,
            ):
                continue



            # -----------------------------
            # Self awareness
            # -----------------------------

            if "report" in result:

                return self._format_self_report(
                    result["report"]
                )



            # -----------------------------
            # Improvement system
            # -----------------------------

            if (
                result.get("type")
                ==
                "improvement_analysis"
            ):

                return self._format_improvement(
                    result
                )
            # -----------------------------
# Improvement proposals
# -----------------------------

            if (
    result.get("type")
    ==
    "improvement_proposal"
):

             return self._format_improvement_proposals(
        result
    )



            # -----------------------------
            # Generic skill response
            # -----------------------------

            if "message" in result:

                return str(
                    result["message"]
                )



            if "output" in result:

                return str(
                    result["output"]
                )



        return None



    def _format_self_report(
        self,
        report,
    ):

        output = [

            "ULTRON SELF ANALYSIS",

            "",

            "Capabilities:",

        ]


        for skill in report.skills:

            output.append(
                f"- {skill}"
            )



        output.append(
            ""
        )

        output.append(
            "Schemas:"
        )


        for schema in report.schemas:

            output.append(
                f"- {schema['name']} "
                f"(confidence: "
                f"{schema['confidence']})"
            )



        output.append(
            ""
        )

        output.append(
            "Experience:"
        )


        output.append(
            f"- Cycles: "
            f"{report.experience_cycles}"
        )


        output.append(
            f"- Success rate: "
            f"{report.success_rate}"
        )



        if report.weaknesses:

            output.append(
                ""
            )

            output.append(
                "Weaknesses:"
            )


            for weakness in report.weaknesses:

                output.append(
                    f"- {weakness}"
                )



        if report.recommendations:

            output.append(
                ""
            )

            output.append(
                "Recommendations:"
            )


            for recommendation in report.recommendations:

                output.append(
                    f"- {recommendation}"
                )



        return "\n".join(output)



    def _format_improvement(
        self,
        result,
    ):

        output = [

            "ULTRON IMPROVEMENT ANALYSIS",

            "",

        ]


        recommendations = (
            result.get(
                "recommendations",
                []
            )
        )


        if recommendations:

            output.append(
                "Recommended improvements:"
            )


            for item in recommendations:

                output.append(
                    f"- {item}"
                )

        else:

            output.append(
                "No improvement opportunities detected."
            )



        return "\n".join(output)

    def _format_improvement_proposals(
    self,
    result,
):

     output = [
        "ULTRON IMPROVEMENT PROPOSALS",
        "",
    ]


     proposals = result.get(
        "proposals",
        []
    )


     for proposal in proposals:

        output.append(
            f"Capability: {proposal.get('capability')}"
        )

        output.append(
            f"Reason: {proposal.get('reason')}"
        )


        recommendation = proposal.get(
            "recommendation"
        )


        if recommendation:

            output.append(
                ""
            )

            output.append(
                "Advisor Analysis:"
            )

            output.append(
                recommendation
            )


        output.append(
            ""
        )


     return "\n".join(output)
    def _generate_response(
        self,
        cycle,
    ):

        try:

            action = (
                cycle.decision
                .decision
                .action
                .value
            )

        except Exception:

            return (
                "Task completed."
            )



        if action == "respond":

            return (
                "Hello. How can I assist you?"
            )


        if action == "ask_clarification":

            return (
                "Could you provide more details?"
            )


        if action == "improve_self":

            return (
                "Improvement analysis completed."
            )


        return (
            f"Decision: {action}"
        )