"""Olama provider fallback for a locally hosted Olama agent API."""

from __future__ import annotations

from typing import Any

import httpx

from nanobot.providers.base import LLMProvider, LLMResponse


class OlamaProvider(LLMProvider):
    """Call a local Olama `/v1/agent/run` endpoint.

    This is intentionally a simple text fallback for the user's local service at
    http://127.0.0.1:19074. Tool execution remains handled by nanobot itself; the
    prompt sent to Olama includes the conversation text.
    """

    def __init__(self, api_key: str = "no-key", api_base: str = "http://127.0.0.1:19074", default_model: str = "broken"):
        super().__init__(api_key, api_base.rstrip("/"))
        self.default_model = default_model

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                   model: str | None = None, max_tokens: int = 4096, temperature: float = 0.7,
                   reasoning_effort: str | None = None) -> LLMResponse:
        prompt = self._messages_to_prompt(messages)
        payload: dict[str, Any] = {
            "prompt": prompt,
            "chat_id": model or self.default_model,
            "max_history": 16,
        }
        if tools:
            payload["tools"] = tools
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(f"{self.api_base}/v1/agent/run", json=payload)
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            return LLMResponse(content=f"Error: Olama fallback unavailable at {self.api_base}: {e}", finish_reason="error")

        return LLMResponse(content=self._extract_content(data), finish_reason="stop")

    def _messages_to_prompt(self, messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for message in self._sanitize_empty_content(messages):
            role = message.get("role", "user")
            content = message.get("content", "")
            if isinstance(content, list):
                content = "\n".join(str(part.get("text", part)) if isinstance(part, dict) else str(part) for part in content)
            lines.append(f"{role}: {content}")
        return "\n\n".join(lines)

    @staticmethod
    def _extract_content(data: Any) -> str:
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            for key in ("response", "content", "message", "text", "output"):
                value = data.get(key)
                if isinstance(value, str):
                    return value
                if isinstance(value, dict):
                    nested = OlamaProvider._extract_content(value)
                    if nested:
                        return nested
            return str(data)
        return str(data)

    def get_default_model(self) -> str:
        return self.default_model
