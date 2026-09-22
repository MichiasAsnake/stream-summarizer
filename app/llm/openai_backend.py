"""OpenAI-compatible LLM backend."""
from __future__ import annotations

import json
from typing import Any

from app.config import settings


def _reasoning_kwargs() -> dict[str, Any]:
    """extra_body for reasoning-effort, only when explicitly configured."""
    effort = (settings.LLM_REASONING_EFFORT or "").strip()
    if not effort:
        return {}
    return {"extra_body": {"reasoning_effort": effort}}


def _strict_schema(node: Any) -> Any:
    """Normalize a schema for strict structured output.

    Groq (and OpenAI strict mode) require additionalProperties:false on
    every object and required[] to list every key in properties.
    Standard JSON Schema — harmless for servers that don't enforce it.
    Missing keys are backfilled from pydantic defaults on validate.
    """
    if isinstance(node, dict):
        out = {k: _strict_schema(v) for k, v in node.items()}
        if out.get("type") == "object" or "properties" in out:
            out["additionalProperties"] = False
            if isinstance(out.get("properties"), dict):
                out["required"] = sorted(out["properties"].keys())
        return out
    if isinstance(node, list):
        return [_strict_schema(v) for v in node]
    return node


class OpenAICompatibleLLM:
    def __init__(self, model: str, api_key: str = "", base_url: str = "",
                 stage: str = "extract"):
        self.model = model
        self.stage = stage
        self.api_key = api_key or settings.LLM_API_KEY
        self.base_url = base_url or settings.LLM_BASE_URL
        if not self.api_key:
            raise RuntimeError("LLM_API_KEY is required for openai_compat")

    def _client(self):
        from openai import OpenAI  # type: ignore
        kwargs = {"api_key": self.api_key,
                  "timeout": settings.LLM_TIMEOUT_SECONDS,
                  "max_retries": 0}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return OpenAI(**kwargs)

    def generate_json(self, schema: dict[str, Any], prompt: str,
                      system: str = "") -> dict[str, Any]:
        from app.observability import call_llm
        client = self._client()
        response = call_llm("extract", lambda: client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}],
            response_format={"type": "json_schema", "json_schema": {
                "name": "stream_extraction", "strict": True,
                "schema": _strict_schema(schema)}},
            **_reasoning_kwargs(),
        ))
        content = response.choices[0].message.content or "{}"
        return json.loads(content)

    def generate_text(self, prompt: str, system: str = "",
                      max_tokens: int = 500) -> str:
        from app.observability import call_llm
        client = self._client()
        stage = "recap" if self.stage == "recap" else "rolling"
        response = call_llm(stage, lambda: client.chat.completions.create(
            model=self.model, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}],
            **_reasoning_kwargs(),
        ))
        return response.choices[0].message.content or ""
