import json

from ultron.ai.llm.provider import ProviderManager


class ImprovementAdvisor:
    """
    Uses an LLM to analyze ULTRON
    improvement opportunities.
    """

    def __init__(
        self,
        llm: ProviderManager,
    ):
        self.llm = llm

    def analyze(
        self,
        component: str,
        problem: str,
        context: dict | None = None,
    ):

        prompt = f"""
You are ULTRON's software improvement advisor.

Analyze this component:

Component:
{component}

Problem:
{problem}

Additional context:
{context}

Tasks:
1. Identify the root cause.
2. Suggest architectural improvements.
3. Explain risks.
4. Suggest a safe implementation approach.

Return a structured improvement recommendation.
"""

        return self.llm.generate(
            prompt,
            context,
        )

    def generate_patch(
        self,
        plan: dict,
    ):

        prompt = f"""
You are ULTRON's code generation engine.

Generate a safe Python implementation.

If operation is "modify":
generate a replacement patch.

If operation is "create":
generate new files based on the blueprint.

Operation:
{plan.get("operation")}

Target component:
{plan.get("target")}

Problem:
{plan.get("problem")}


Source Context:
{plan.get("context")}

Strategy:
{plan.get("strategy")}

Blueprint:
{plan.get("blueprint")}

Return ONLY valid JSON.

For operation="modify":

{{
    "file": "python file path",
    "description": "change explanation",
    "original": "existing code",
    "replacement": "new code",
    "changes": "summary"
}}


For operation="create":

{{
    "description": "component creation explanation",
    "files": [
        {{
            "path": "python file path",
            "content": "complete python code"
        }}
    ],
    "changes": "summary"
}}
"""

        response = self.llm.generate(
            prompt,
            plan,
        )

        try:
            cleaned = response.strip()

            if cleaned.startswith("```"):
                cleaned = cleaned.replace("```json", "").replace("```", "").strip()

            return json.loads(cleaned)

        except Exception:
         return {
        "file": plan.get(
            "target",
            "unknown",
        ),
        "files": [],
        "description": "LLM returned invalid JSON",
        "original": "",
        "replacement": "",
        "changes": response,
    }

        
