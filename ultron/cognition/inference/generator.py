from typing import Any

from ultron.cognition.context.models import CognitiveContext
from ultron.cognition.reasoning.hypothesis import Hypothesis
from ultron.domain.action_types import ActionType


class HypothesisGenerator:
    """
    Generates executable hypotheses from cognitive context.

    Responsibilities:
    - Resolve authoritative canonical intent.
    - Preserve structured entities extracted by perception.
    - Normalize entity names into stable execution parameters.
    - Preserve the original user request for downstream agents.
    - Produce hypotheses that can be consumed directly by planning
      and execution without re-parsing natural language.

    The generator does not execute actions.
    """

    _INTENT_ACTIONS = {
        "conversation": (
            ActionType.RESPOND,
            0.95,
        ),
        "system_information": (
            ActionType.GET_SYSTEM_INFO,
            0.98,
        ),
        "get_disk_usage": (
            ActionType.GET_DISK_USAGE,
            0.98,
        ),
        "open_application": (
            ActionType.OPEN_APPLICATION,
            0.95,
        ),
        "build_project": (
            ActionType.BUILD_PROJECT,
            0.98,
        ),
        "create_file": (
            ActionType.WRITE_FILE,
            0.97,
        ),
        "write_file": (
            ActionType.WRITE_FILE,
            0.97,
        ),
        "read_file": (
            ActionType.READ_FILE,
            0.95,
        ),
        "memory_store": (
            ActionType.LEARN,
            0.95,
        ),
        "query_time": (
            ActionType.RESPOND,
            0.95,
        ),
        "query_weather": (
            ActionType.SEARCH_WEB,
            0.95,
        ),
        "search": (
            ActionType.SEARCH_WEB,
            0.90,
        ),
        "question": (
            ActionType.RESPOND,
            0.80,
        ),
        "unknown": (
            ActionType.ASK_CLARIFICATION,
            1.00,
        ),
    }

    # Canonical parameter names used by downstream execution.
    _PARAMETER_ALIASES = {
        "filename": "file_name",
        "file": "file_name",
        "filepath": "path",
        "file_path": "path",
        "dir": "directory",
        "folder": "directory",
        "project": "project_name",
        "name": "project_name",
    }

    def generate(
        self,
        context: CognitiveContext,
    ) -> list[Hypothesis]:
        """
        Generate hypotheses from the current cognitive context.
        """

        intent = self._resolve_intent(context)

        if intent in self._INTENT_ACTIONS:
            action, confidence = self._INTENT_ACTIONS[intent]

            arguments = self._build_arguments(
                context=context,
                intent=intent,
            )

            metadata: dict[str, Any] = {
                "source": "intent",
                "intent": intent,
                "query": context.query,
                "explicit_intent": True,
                "entities": self._extract_context_data(context),
                "arguments_preserved": bool(arguments),
            }

            return [
                Hypothesis(
                    action=action,
                    arguments=arguments,
                    reasoning=(
                        f"Explicit canonical intent "
                        f"'{intent}' resolved to action "
                        f"'{action.value}'. "
                        f"Preserved {len(arguments)} execution "
                        f"parameter(s)."
                    ),
                    confidence=confidence,
                    priority=confidence,
                    cost=0.1,
                    metadata=metadata,
                )
            ]

        return self._generate_schema_hypotheses(
            context=context,
            intent=intent,
        )

    # ------------------------------------------------------------------
    # Intent resolution
    # ------------------------------------------------------------------

    @classmethod
    def _resolve_intent(
        cls,
        context: CognitiveContext,
    ) -> str:
        metadata = getattr(context, "metadata", {}) or {}

        understanding = getattr(
            context,
            "understanding",
            None,
        )

        understanding_context = getattr(
            understanding,
            "context",
            {},
        ) or {}

        intent = (
            metadata.get("intent")
            or understanding_context.get("intent")
            or "unknown"
        )

        return str(intent).strip().lower() or "unknown"

    # ------------------------------------------------------------------
    # Structured parameter extraction
    # ------------------------------------------------------------------

    @classmethod
    def _extract_context_data(
        cls,
        context: CognitiveContext,
    ) -> dict[str, Any]:
        """
        Collect structured data from all known perception locations.

        Current architecture primarily stores entities in
        context.understanding.context, but some perception paths may
        expose them through an 'entities' mapping. Both are supported.
        """

        understanding = getattr(
            context,
            "understanding",
            None,
        )

        raw_context = getattr(
            understanding,
            "context",
            {},
        ) or {}

        if not isinstance(raw_context, dict):
            raw_context = {}

        data: dict[str, Any] = dict(raw_context)

        # Some perception implementations put the extracted entities
        # under a nested "entities" dictionary.
        nested_entities = raw_context.get("entities")

        if isinstance(nested_entities, dict):
            for key, value in nested_entities.items():
                if value is not None:
                    data.setdefault(key, value)

        # Some context implementations may expose entities directly.
        direct_entities = getattr(
            context,
            "entities",
            None,
        )

        if isinstance(direct_entities, dict):
            for key, value in direct_entities.items():
                if value is not None:
                    data.setdefault(key, value)

        return data

    @classmethod
    def _build_arguments(
        cls,
        context: CognitiveContext,
        intent: str,
    ) -> dict[str, Any]:
        """
        Convert perception entities into stable downstream arguments.
        """

        raw = cls._extract_context_data(context)

        arguments: dict[str, Any] = {}

        # --------------------------------------------------------------
        # Normalize all known aliases first.
        # --------------------------------------------------------------

        for key, value in raw.items():
            if value is None:
                continue

            if key in {
                "intent",
                "entities",
            }:
                continue

            canonical_key = cls._PARAMETER_ALIASES.get(
                key,
                key,
            )

            if canonical_key not in arguments:
                arguments[canonical_key] = value

        # --------------------------------------------------------------
        # Preserve original request.
        #
        # This is critical for CodingAgent. Structured entities tell us
        # WHAT file is involved, while the request tells us WHAT the
        # user wants the file to contain.
        # --------------------------------------------------------------

        query = getattr(
            context,
            "query",
            None,
        )

        if query:
            arguments.setdefault(
                "request",
                str(query),
            )

        # --------------------------------------------------------------
        # File operations
        # --------------------------------------------------------------

        if intent in {
            "create_file",
            "write_file",
            "read_file",
        }:
            file_name = (
                arguments.get("file_name")
                or arguments.get("filename")
            )

            path = arguments.get("path")

            directory = arguments.get(
                "directory"
            )

            if file_name and not path:
                if directory:
                    arguments["path"] = (
                        f"{str(directory).rstrip('/')}/"
                        f"{str(file_name).lstrip('/')}"
                    )
                else:
                    arguments["path"] = str(file_name)

            # Keep filename explicitly available even when path exists.
            if file_name:
                arguments["file_name"] = str(file_name)

        # --------------------------------------------------------------
        # Project operations
        # --------------------------------------------------------------

        if intent == "build_project":
            project_name = arguments.get(
                "project_name"
            )

            if project_name:
                arguments["project_name"] = str(
                    project_name
                )

            project_type = arguments.get(
                "project_type"
            )

            if project_type:
                arguments["project_type"] = str(
                    project_type
                )

            description = arguments.get(
                "description"
            )

            if description:
                arguments["description"] = str(
                    description
                )

        return arguments

    # ------------------------------------------------------------------
    # Schema-driven reasoning
    # ------------------------------------------------------------------

    def _generate_schema_hypotheses(
        self,
        context: CognitiveContext,
        intent: str,
    ) -> list[Hypothesis]:
        hypotheses: list[Hypothesis] = []

        for activated in context.schemas:
            schema = activated.schema

            for action in activated.predicted_actions:
                try:
                    action_type = ActionType(action)

                except ValueError:
                    continue

                confidence = min(
                    activated.activation
                    + schema.confidence * 0.5,
                    1.0,
                )

                hypotheses.append(
                    Hypothesis(
                        action=action_type,
                        arguments=self._extract_context_data(
                            context
                        ),
                        reasoning=(
                            f"Activated schema "
                            f"'{schema.name}'. "
                            f"Predicted action "
                            f"'{action}'. "
                            f"Schema confidence: "
                            f"{schema.confidence}."
                        ),
                        confidence=confidence,
                        priority=confidence,
                        cost=0.1,
                        metadata={
                            "source": "schema",
                            "schema": schema.id,
                            "intent": intent,
                            "explicit_intent": False,
                            "query": context.query,
                        },
                    )
                )

        # --------------------------------------------------------------
        # No usable schema
        # --------------------------------------------------------------

        if not hypotheses:
            action, confidence = self._INTENT_ACTIONS.get(
                intent,
                (
                    ActionType.ASK_CLARIFICATION,
                    1.0,
                ),
            )

            hypotheses.append(
                Hypothesis(
                    action=action,
                    arguments=self._extract_context_data(
                        context
                    ),
                    reasoning=(
                        f"No schema matched. "
                        f"Using intent '{intent}'."
                    ),
                    confidence=confidence,
                    priority=confidence,
                    cost=0.1,
                    metadata={
                        "source": "intent_fallback",
                        "intent": intent,
                        "query": context.query,
                        "explicit_intent": False,
                    },
                )
            )

        return hypotheses
