"""LLM provider interface + stub. Gemini default per user choice (§4)."""
from __future__ import annotations

from typing import Any

from app.config import settings
from app.llm import prompts


class StubLLM:
    """Deterministic stub: echoes window into a minimal valid Extraction."""

    def generate_json(self, schema: dict[str, Any], prompt: str, system: str = "") -> dict[str, Any]:
        return {"utterance_attributions": [], "entity_updates": [], "thread_updates": [],
                "events": [], "window_summary": "Stub summary (no LLM configured).",
                "open_questions": []}

    def generate_text(self, prompt: str, system: str = "", max_tokens: int = 500) -> str:
        return "Stub summary (no LLM configured)."


class GeminiLLM:
    """Gemini via new google.genai SDK (old google.generativeai is sunset)."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or settings.LLM_API_KEY
        self.model = model or settings.LLM_MODEL_EXTRACT

    def _client(self):
        from google import genai  # type: ignore
        return genai.Client(api_key=self.api_key)

    def generate_json(self, schema: dict[str, Any], prompt: str, system: str = "") -> dict[str, Any]:
        import json
        c = self._client()
        # NOTE: the new SDK takes no response_schema dict here, so the schema
        # must be embedded in the prompt — previously it wasn't sent at all,
        # which is why windows came back with empty summaries.
        contents = (
            f"{system}\n\n{prompt}\n\n"
            "Respond with a single JSON object matching this JSON Schema "
            "(all keys required, use []/\"\" when nothing applies):\n"
            f"{json.dumps(schema)}\n\n"
            "Return ONLY the JSON object, no code fences, no commentary."
        )
        resp = c.models.generate_content(model=self.model, contents=contents)
        text = resp.text or ""
        # strip code fences if the model wraps JSON
        if "```" in text:
            parts = text.split("```")
            text = parts[1] if len(parts) > 1 else text
            text = text.strip().lstrip("json").strip()
        return json.loads(text)

    def generate_text(self, prompt: str, system: str = "", max_tokens: int = 500) -> str:
        c = self._client()
        resp = c.models.generate_content(
            model=self.model,
            contents=f"{system}\n\n{prompt}",
        )
        return resp.text or ""


def get_llm(kind: str = "extract"):
    provider = settings.LLM_PROVIDER
    if provider == "gemini":
        model = settings.LLM_MODEL_EXTRACT if kind == "extract" else settings.LLM_MODEL_RECAP
        return GeminiLLM(model=model)
    if provider == "anthropic":
        from app.llm.anthropic_backend import AnthropicLLM  # lazy
        model = settings.LLM_MODEL_EXTRACT if kind == "extract" else settings.LLM_MODEL_RECAP
        return AnthropicLLM(model=model)
    return StubLLM()
