"""LLM provider interface + stub. Gemini default per user choice (§4)."""
from __future__ import annotations

from typing import Any

from app.config import settings


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

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 stage: str = "extract"):
        self.api_key = api_key or settings.LLM_API_KEY
        self.model = model or settings.LLM_MODEL_EXTRACT
        self.stage = stage

    def _client(self):
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
        # google-genai expresses HTTP timeouts in milliseconds.
        return genai.Client(
            api_key=self.api_key,
            http_options=types.HttpOptions(
                timeout=max(1, int(settings.LLM_TIMEOUT_SECONDS * 1000))))

    def generate_json(self, schema: dict[str, Any], prompt: str, system: str = "") -> dict[str, Any]:
        import json

        from app.observability import call_llm
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
        resp = call_llm(
            "extract",
            lambda: c.models.generate_content(model=self.model, contents=contents))
        text = resp.text or ""
        # strip code fences if the model wraps JSON
        if "```" in text:
            parts = text.split("```")
            text = parts[1] if len(parts) > 1 else text
            text = text.strip().lstrip("json").strip()
        return json.loads(text)

    def generate_text(self, prompt: str, system: str = "", max_tokens: int = 500) -> str:
        from google.genai import types  # type: ignore

        from app.observability import call_llm
        c = self._client()
        stage = "recap" if self.stage == "recap" else "rolling"
        resp = call_llm(
            stage,
            lambda: c.models.generate_content(
                model=self.model,
                contents=f"{system}\n\n{prompt}",
                config=types.GenerateContentConfig(max_output_tokens=max_tokens)))
        return resp.text or ""


def get_llm(kind: str = "extract"):
    provider = settings.LLM_PROVIDER
    if provider == "gemini":
        model = settings.LLM_MODEL_EXTRACT if kind == "extract" else settings.LLM_MODEL_RECAP
        return GeminiLLM(model=model, stage=kind)
    if provider == "anthropic":
        from app.llm.anthropic_backend import AnthropicLLM  # lazy
        model = settings.LLM_MODEL_EXTRACT if kind == "extract" else settings.LLM_MODEL_RECAP
        return AnthropicLLM(model=model, stage=kind)
    if provider == "openai_compat":
        from app.llm.openai_backend import OpenAICompatibleLLM
        model = settings.LLM_MODEL_EXTRACT if kind == "extract" else settings.LLM_MODEL_RECAP
        return OpenAICompatibleLLM(model=model, stage=kind)
    if provider == "stub":
        return StubLLM()
    raise ValueError(f"unsupported LLM_PROVIDER: {provider}")
