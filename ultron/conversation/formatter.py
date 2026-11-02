class ResponseFormatter:
    """
    Converts raw system responses into
    user-friendly ULTRON responses.
    """

    def format(
        self,
        response,
    ):

        if isinstance(response, dict):

            # Execution results
            if (
                "results" in response
                and "completed" in response
            ):
                return {
                    "message": self._format_execution(
                        response
                    )
                }


            return response


        return {
            "message": str(response)
        }


    def _format_execution(
        self,
        result: dict,
    ) -> str:

        messages = []


        for item in result.get(
            "results",
            [],
        ):

            action = item.get(
                "action"
            )

            data = item.get(
                "result",
                {}
            )


            if action == "get_system_info":

                info = data.get(
                    "data",
                    {}
                )

                messages.append(
                    self._format_system_info(
                        info
                    )
                )


            elif action == "get_disk_usage":

                messages.append(
                    str(data)
                )


            else:

                messages.append(
                    str(data)
                )


        if not messages:

            return (
                f"Completed "
                f"{result.get('completed',0)} "
                "steps."
            )


        return "\n\n".join(
            messages
        )


    def _format_system_info(
        self,
        info: dict,
    ) -> str:

        return f"""
System Information:

Operating System: {info.get('os','Unknown')}
Hostname: {info.get('hostname','Unknown')}
Python Version: {info.get('python_version','Unknown')}
CPU Cores: {info.get('cpu_count','Unknown')}

Status: Online
""".strip()