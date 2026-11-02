import os

from openai import OpenAI

from ultron.ai.llm.base import LLMProvider


class OpenAIProvider(LLMProvider):
    """
    OpenAI based language model provider.
    """

    def __init__(
        self,
        model: str = "gpt-5-mini",
    ):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        self.model = model

    def generate(
        self,
        prompt: str,
        context: dict | None = None,
    ) -> str:

        response = self.client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": "You are ULTRON's improvement advisor. Analyze software architecture and suggest safe improvements.",
                },
                {"role": "user", "content": prompt},
            ],
        )

        return response.output_text
