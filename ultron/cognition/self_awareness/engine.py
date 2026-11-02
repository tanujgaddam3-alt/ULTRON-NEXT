from ultron.cognition.self_awareness.models import (
    SelfReport,
)
from ultron.cognition.self_awareness.capabilities import (
    DEFAULT_CAPABILITIES,
)

class SelfAwarenessEngine:
    """
    Inspects ULTRON internal architecture,
    capabilities, experience and limitations.
    """

    def __init__(
    self,
    skills,
    schema_repository,
    smith,
    system_registry=None,
):

     self.skills = skills
     self.schema_repository = schema_repository
     self.smith = smith
     self.system_registry = system_registry



    def analyze(self) -> SelfReport:

        # -----------------------------
        # Skills inspection
        # -----------------------------

        skill_data = []

        if self.skills:

            skill_data = (
                self.skills.available_skills()
            )


        skills = [

            item.get("name")

            for item in skill_data

        ]
        available_components = []


# -----------------------------
# Skills
# -----------------------------

        for skill in skills:

         available_components.append(
        skill.lower()
    )


# -----------------------------
# Registered systems
# -----------------------------

        if self.system_registry:

          available_components.extend(
        [
            name.lower()
            for name in self.system_registry.systems.keys()
        ]
    )


# -----------------------------
# Internal capabilities
# -----------------------------

        internal_components = [

    "memory",

    "reasoning",

    "planning",

    "execution",

    "reflection",

    "learning",

]


        available_components.extend(
    internal_components
)


# -----------------------------
# Skill components
# -----------------------------

        for skill in skills:

         available_components.append(
        skill.lower()
    )


# -----------------------------
# System components
# -----------------------------

        if self.system_registry:

          available_components.extend(
        [
            name.lower()
             for name in self.system_registry.systems.keys()
        ]
    )



        # -----------------------------
        # Schema inspection
        # -----------------------------

        schemas = (
            self.schema_repository.all()
        )


        schema_data = [

            {
                "name": schema.name,

                "confidence": schema.confidence,

            }

            for schema in schemas

        ]



        # -----------------------------
        # Experience analysis
        # -----------------------------

        history = (
            self.smith.history()
        )


        success_rate = 0.0


        if history:

            successful = sum(

                1

                for item in history

                if item.execution_success

            )


            success_rate = (
                successful /
                len(history)
            )



        weaknesses = []

        recommendations = []



        # -----------------------------
        # Capability analysis
        # -----------------------------


        capability_map = {

            "voice": {

                "description":
                "Audio input and speech interaction",

                "keywords": [
                    "voice",
                    "speech",
                    "audio",
                    "microphone",
                ],

            },


            "vision": {

                "description":
                "Visual perception capability",

                "keywords": [
                    "vision",
                    "camera",
                    "image",
                    "ocr",
                ],

            },


            "memory": {

                "description":
                "Long term memory capability",

                "keywords": [
                    "memory",
                ],

            },


            "learning": {

                "description":
                "Experience based improvement",

                "keywords": [
                    "learning",
                    "smith",
                    "improvement",
                ],

            },

        }



        installed_capabilities = " ".join(
    available_components
).lower()



        for capability, data in capability_map.items():

            available = any(

                keyword in installed_capabilities

                for keyword in data["keywords"]

            )


            if not available:

                weaknesses.append(
                    f"Missing {capability} capability."
                )


                recommendations.append(
                    {
                        "capability": capability,

                        "description":
                        data["description"],

                        "reason":
                        "Capability not detected in current skill architecture.",

                    }
                )



        # -----------------------------
        # Experience analysis
        # -----------------------------


        if not history:

            weaknesses.append(
                "No execution experience available."
            )


        elif success_rate < 0.5:

            weaknesses.append(
                "Low execution reliability."
            )


            recommendations.append(
                {
                    "capability":
                    "execution reliability",

                    "reason":
                    "Execution success rate below threshold.",

                }
            )



        # -----------------------------
        # Schema analysis
        # -----------------------------


        if len(schemas) < 5:

            weaknesses.append(
                "Limited cognitive schema coverage."
            )


            recommendations.append(
                {
                    "capability":
                    "cognitive schemas",

                    "reason":
                    "Few learned interaction patterns exist.",

                }
            )



        return SelfReport(

            skills=skills,

            schema_count=len(schemas),

            schemas=schema_data,

            experience_cycles=len(history),

            success_rate=success_rate,

            weaknesses=weaknesses,

            recommendations=recommendations,

        )
    def detect_missing_capabilities(
    self,
    skills,
):

     missing = []


     installed = [
        s.lower()
        for s in skills
    ]


     for capability in DEFAULT_CAPABILITIES:

        found = False


        for skill in capability.required_skills:

            if skill in installed:
                found = True


        if not found:

            missing.append(
                {
                    "capability":
                    capability.name,

                    "reason":
                    capability.description,

                    "required_skills":
                    capability.required_skills,

                }
            )


     return missing