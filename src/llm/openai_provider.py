from __future__ import annotations

import openai

from src.llm.models import LLMResponse
from src.llm.provider import LLMProvider


class OpenAIProvider(LLMProvider):
    # Pricing per million tokens: (input, output)
    _PRICING = {
        "gpt-4o": (2.5, 10.0),
        "gpt-4o-mini": (0.15, 0.60),
    }

    def __init__(self, api_key: str, model: str = "gpt-4o") -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key)
        self._model = model

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> LLMResponse:
        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        usage = response.usage
        return LLMResponse(
            content=response.choices[0].message.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            model=self._model,
            provider="openai",
        )

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        input_cost, output_cost = self._PRICING.get(self._model, (2.5, 10.0))
        return (input_tokens / 1_000_000 * input_cost
                + output_tokens / 1_000_000 * output_cost)

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self._model
