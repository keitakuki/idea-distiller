from __future__ import annotations

import anthropic

from src.llm.models import LLMResponse
from src.llm.provider import LLMProvider


class AnthropicProvider(LLMProvider):
    # Pricing per million tokens – default rates, overridden per model below
    _INPUT_COST_PER_M = 1.0
    _OUTPUT_COST_PER_M = 5.0

    # Per-model pricing
    _PRICING = {
        "claude-haiku-4-5-20251001": (1.0, 5.0),
        "claude-sonnet-4-5-20250929": (3.0, 15.0),
    }

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001") -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> LLMResponse:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return LLMResponse(
            content=response.content[0].text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=self._model,
            provider="anthropic",
        )

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        in_cost, out_cost = self._PRICING.get(
            self._model, (self._INPUT_COST_PER_M, self._OUTPUT_COST_PER_M)
        )
        return (input_tokens / 1_000_000 * in_cost
                + output_tokens / 1_000_000 * out_cost)

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def model_name(self) -> str:
        return self._model
