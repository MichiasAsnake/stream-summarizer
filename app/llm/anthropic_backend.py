"""Anthropic backend (Claude Haiku default for extraction per §4)."""
from __future__ import annotations

import json
from typing import Any


class AnthropicLLM:
    def __init__(self, model: str = "claude-haiku-4-5", api_key: str = ""):
        from app.config import settings
        self.model = model
        self.api_key = api_key or settings.LLM_API_KEY

    def _client(self):
        import anthropic  # type: ignore
        return anthropic.Anthropic(api_key=self.api_key)

    def generate_json(self, schema: dict[str, Any], prompt: str, system: str = "") -> dict[str, Any]:
        c = self._client()
        resp = c.messages.create(model=self.model, max_tokens=1500, system=system,
                                 messages=[{"role": "user", "content": prompt}],
                                 tools=[{"name": "extract", "input_schema": schema}])
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use":
                return dict(block.input)
        return json.loads(resp.content[0].text)

    def generate_text(self, prompt: str, system: str = "", max_tokens: int = 500) -> str:
        c = self._client()
        resp = c.messages.create(model=self.model, max_tokens=max_tokens, system=system,
                                 messages=[{"role": "user", "content": prompt}])
        return "".join(getattr(b, "text", "") for b in resp.content)
