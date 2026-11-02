from ultron.cognition.schema.models import (
    Schema,
    Concept,
    Relation,
)


def load_default_schemas(repository):

    open_application = Schema(
        id="schema_open_application",
        name="open_application",
        description="User wants to open or launch an application.",

        concepts={

            "intent": Concept(
                id="intent_open",
                name="open",
                kind="intent",
                metadata={
                    "action": "open_application"
                },
            ),

            "application": Concept(
                id="entity_application",
                name="application",
                kind="entity",
                metadata={
                    "skill": "system"
                },
            ),

        },

        relations=[

            Relation(
                source="intent",
                target="application",
                relation="requires",
            )

        ],

        confidence=1.0,

        metadata={
            "action": "open_application"
        },
    )


    conversation = Schema(
        id="schema_conversation",
        name="conversation",
        description="Normal user conversation and dialogue.",

        concepts={

            "intent": Concept(
                id="intent_conversation",
                name="conversation",
                kind="intent",
            )

        },

        confidence=1.0,

        metadata={
            "action": "respond"
        },
    )


    system_information = Schema(
        id="schema_system_information",
        name="system_information",
        description="User requests information about system status.",

        concepts={

            "intent": Concept(
                id="intent_system",
                name="system info",
                kind="intent",
            )

        },

        confidence=1.0,

        metadata={
            "action": "get_system_info"
        },
    )


    self_analysis = Schema(
        id="schema_self_analysis",
        name="self_analysis",
        description=(
            "User wants information about ULTRON "
            "internal state and capabilities."
        ),

        concepts={

            "intent": Concept(
                id="intent_self_analysis",
                name="analyze",
                kind="intent",
                metadata={
                    "action": "self_analyze"
                },
            )

        },

        confidence=1.0,

        metadata={
            "action": "self_analyze"
        },
    )


    improve_self = Schema(
        id="schema_improve_self",
        name="improve_self",
        description=(
            "User wants ULTRON to identify "
            "weaknesses and plan improvements."
        ),

        concepts={

            "intent": Concept(
                id="intent_improve",
                name="improve",
                kind="intent",
                metadata={
                    "action": "improve_self"
                },
            )

        },

        confidence=1.0,

        metadata={
            "action": "improve_self"
        },
    )
    improve_system = Schema(
    id="schema_improve_system",
    name="improve_system",
    description=(
        "User wants ULTRON to implement "
        "missing capabilities."
    ),

    concepts={
        "intent": Concept(
            id="intent_improve_system",
            name="build",
            kind="intent",
            metadata={
                "action":"improve_system"
            },
        )
    },

    confidence=1.0,

    metadata={
        "action":"improve_system"
    },
)


    defaults = [

        open_application,

        conversation,

        system_information,

        self_analysis,

        improve_self,

    ]


    for schema in defaults:

        if not repository.exists(
            schema.id
        ):

            repository.add(
                schema
            )