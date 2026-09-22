from app.config import settings
from app.llm.openai_backend import _reasoning_kwargs, _strict_schema
from app.llm.schemas import Extraction


def _objects(node, acc):
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            acc.append(node)
        for v in node.values():
            _objects(v, acc)
    elif isinstance(node, list):
        for v in node:
            _objects(v, acc)
    return acc


def test_strict_schema_marks_every_object_and_requires_every_key():
    schema = _strict_schema(Extraction.model_json_schema())
    objs = _objects(schema, [])
    assert objs, "expected object schemas in Extraction"
    for o in objs:
        assert o.get("additionalProperties") is False
        if isinstance(o.get("properties"), dict):
            assert sorted(o["required"]) == sorted(o["properties"].keys())


def test_strict_schema_leaves_scalars_alone():
    assert _strict_schema({"type": "string"}) == {"type": "string"}
    assert _strict_schema([{"type": "integer"}]) == [{"type": "integer"}]


def test_reasoning_kwargs_empty_by_default(monkeypatch):
    monkeypatch.setattr(settings, "LLM_REASONING_EFFORT", "")
    assert _reasoning_kwargs() == {}


def test_reasoning_kwargs_passes_configured_effort(monkeypatch):
    monkeypatch.setattr(settings, "LLM_REASONING_EFFORT", "low")
    assert _reasoning_kwargs() == {"extra_body": {"reasoning_effort": "low"}}
