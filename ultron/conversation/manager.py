import asyncio

from ultron.conversation.router import (
    ConversationRouter,
    ConversationType,
)

from ultron.conversation.context import (
    ConversationContext,
)

from ultron.conversation.formatter import (
    ResponseFormatter,
)

from ultron.conversation.prompts.system import (
    ULTRON_SYSTEM_PROMPT,
)

from ultron.conversation.history.store import (
    ConversationHistory,
)

from ultron.planning.command_planner import (
    CommandPlanner,
)

from ultron.application.runtime.events import (
    RuntimeEvent,
)

from ultron.conversation.cognitive.formatter import (
    CognitiveResponseFormatter,
)


class ConversationManager:
    """
    Main conversation orchestration layer.

    Responsibilities:
    - route conversations
    - build context
    - access memory
    - communicate with LLM
    - execute commands
    - run cognitive tasks
    """

    def __init__(
        self,
        llm=None,
        memory=None,
        knowledge=None,
        executor=None,
        runtime=None,
    ):

        self.llm = llm

        self.router = ConversationRouter()

        self.context = ConversationContext(
            memory,
            knowledge,
        )

        self.formatter = ResponseFormatter()

        self.executor = executor

        self.runtime = runtime

        self.memory = memory

        self.history = ConversationHistory()

        self.command_planner = CommandPlanner()

        self.cognitive_formatter = (
            CognitiveResponseFormatter()
        )


    def process(
        self,
        message: str,
        session_id: str,
    ):

        message_type = self.router.route(
            message
        )

        context = self.context.build(
            message
        )


        if message_type == ConversationType.COMMAND:

            response = self.handle_command(
                message,
                context,
            )


        elif message_type == ConversationType.TASK:

            response = self.handle_task(
                message,
                context,
            )


        else:

            response = self.handle_chat(
                message,
                context,
                session_id,
            )


        return self.formatter.format(
            response
        )


    def handle_task(
        self,
        message,
        context,
    ):

        if not self.runtime:

            return {
                "message": "Runtime unavailable"
            }


        event = RuntimeEvent(
            source="conversation",
            payload=message,
        )


        cycle = self.runtime.run_cycle(
            event
        )


        return {
            "message":
                self.cognitive_formatter.format(
                    cycle
                )
        }


    def handle_chat(
        self,
        message: str,
        context,
        session_id: str,
    ):

        if not self.llm:
            return {
                "message": "LLM unavailable"
            }

        prompt = self._prepare_chat_prompt(message, context, session_id)
        response = self.llm.generate(
            prompt,
            context=context,
        )
        return self._finish_chat(message, response, session_id)

    async def handle_chat_async(
        self,
        message: str,
        context,
        session_id: str,
    ):
        if not self.llm:
            return {
                "message": "LLM unavailable"
            }

        prompt = self._prepare_chat_prompt(message, context, session_id)
        response = await asyncio.to_thread(
            self.llm.generate,
            prompt,
            context=context,
        )
        return self._finish_chat(message, response, session_id)

    def _prepare_chat_prompt(self, message, context, session_id):
        memory_context = {}
        if self.memory:
            memory_context = self.memory.recall(
                message
            )

        conversation_history = self.history.get(session_id)
        self.history.add(
            session_id,
            "user",
            message,
        )
        return f"""
{ULTRON_SYSTEM_PROMPT}

Memory:
{memory_context}

Conversation History:
{conversation_history}

Context:
{context}

User:
{message}

ULTRON:
"""

    def _finish_chat(self, message, response, session_id):
        self.history.add(
            session_id,
            "assistant",
            response,
        )
        self._learn_from_message(message)
        return {
            "message": response
        }


    def handle_command(
        self,
        message,
        context,
    ):

        if not self.executor:

            return {
                "message":
                "Command executor unavailable"
            }


        plan = self.command_planner.create_plan(
            message
        )


        print(
            "DEBUG PLAN:",
            plan
        )


        result = self.executor.execute(
            plan
        )


        return result


    def _learn_from_message(
        self,
        message: str,
    ):
        """
        Extract simple user facts
        and store them in semantic memory.
        """

        if not self.memory:

            return


        text = message.lower().strip()


        if text.startswith(
            "my name is"
        ):

            name = (
                message[len("my name is"):]
                .strip()
            )


            self.memory.remember(
                "semantic",
                {
                    "name": name,
                },
            )
