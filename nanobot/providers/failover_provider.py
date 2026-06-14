"""Provider wrapper that tries configured LLM providers in priority order."""

from __future__ import annotations

from typing import Any

from loguru import logger

from nanobot.providers.base import LLMProvider, LLMResponse


class FailoverProvider(LLMProvider):
    """Try the primary provider first, then fall back to backup providers on errors."""

    def __init__(self, providers: list[tuple[str, LLMProvider]], default_model: str):
        super().__init__(providers[0][1].api_key if providers else None, providers[0][1].api_base if providers else None)
        self.providers = providers
        self.default_model = default_model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        last_response: LLMResponse | None = None
        for index, (name, provider) in enumerate(self.providers):
            response = await provider.chat(
                messages=messages,
                tools=tools,
                model=model if index == 0 else provider.get_default_model(),
                max_tokens=max_tokens,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
            )
            if not self._is_error(response):
                if index > 0:
                    logger.warning("Primary provider failed; using fallback provider {}", name)
                return response
            last_response = response
            logger.warning("Provider {} failed, trying next fallback: {}", name, response.content)

        return last_response or LLMResponse(content="Error: no providers configured", finish_reason="error")

    @staticmethod
    def _is_error(response: LLMResponse) -> bool:
        content = response.content or ""
        return response.finish_reason == "error" or content.startswith("Error:")

    def get_default_model(self) -> str:
        return self.default_model
