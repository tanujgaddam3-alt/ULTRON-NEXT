import requests

from ultron.ai.llm.base import LLMProvider


class OllamaProvider(LLMProvider):
    """
    Local Ollama LLM provider.
    """

    def __init__(
        self,
        model="qwen2.5-coder:7b",
        url="http://localhost:11434/api/generate",
        timeout: float = 120.0,
    ):
        self.model = model
        self.url = url
        self.timeout = timeout

    def generate(
        self,
        prompt=str,
        context: dict | None = None,
    ) -> str:

        response = requests.post(
            self.url,
            json={
                "model": self.model,
                "prompt": f"""
You are ULTRON.
You are an advanced AI assistant system.
Do not identify yourself as Qwen.

User:
{prompt}

ULTRON:
""",
                "stream": False,
            },
            timeout=self.timeout,
        )

        response.raise_for_status()

        return response.json()["response"]
