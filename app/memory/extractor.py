"""LLM extraction runner (§5.8): one call per window, Pydantic validate, one retry."""
from __future__ import annotations

import json
import time
from datetime import UTC

from sqlalchemy.orm import Session as SASession

from app.db import models as m
from app.llm import prompts
from app.llm.schemas import Extraction
from app.memory.context import build_context
from app.memory.writer import write_extraction


def format_utterances(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        conf = r.get("conf")
        conf_text = f"{conf:.2f}" if isinstance(conf, (int, float)) else "unknown"
        lines.append(f'[seg {r["id"]} | {r["t_start"]:.1f}–{r["t_end"]:.1f} | speaker: {r.get("speaker","?")} '
                     f'(conf {conf_text})] "{r["text"]}"')
    return "\n".join(lines)


def run_extraction(db: SASession, channel_id: int, session_id: int, window_id: int,
                   utterances: list[dict], llm, model_name: str = "extract") -> Extraction:
    from app.config import settings
    from app.content_profiles import get_hint
    from app.llm.budget import budget_status, should_skip_extraction
    # D5: session/monthly cap — pause extraction, keep transcribing (§8 degradation ladder)
    try:
        month_prefix = (db.get(m.Window, window_id).created_at or "")[:7] if db.get(m.Window, window_id) else ""
        from datetime import datetime
        if not month_prefix:
            month_prefix = datetime.now(UTC).strftime("%Y-%m")
        status = budget_status(db, session_id, month_prefix,
                               settings.LLM_SESSION_BUDGET_USD, settings.LLM_MONTHLY_BUDGET_USD)
        skip, reason = should_skip_extraction(status)
        if skip:
            w = db.get(m.Window, window_id)
            if w:
                w.status = "failed"
                w.extraction_json = f'{{"skipped": "{reason}"}}'
                db.commit()
            return Extraction(window_summary="", open_questions=[reason])
    except Exception as exc:
        from app.observability import report_pipeline_error
        report_pipeline_error("budget-check", exc)
    # D1: content-profile hint shapes the character/thread model
    try:
        import json as _json
        ch = db.execute(__import__("sqlalchemy").select(m.Channel).where(
            m.Channel.id == db.get(m.Session, session_id).channel_id)).scalars().first() \
            if db.get(m.Session, session_id) else None
        profile = settings.CONTENT_PROFILE
        if ch and ch.config_json:
            try:
                profile = _json.loads(ch.config_json).get("content_profile", profile)
            except Exception:
                pass
        system = prompts.EXTRACTION_SYSTEM + "\n" + get_hint(profile)
    except Exception:
        system = prompts.EXTRACTION_SYSTEM
    ctx = build_context(db, channel_id, session_id,
                        [u.get("speaker", "?") for u in utterances],
                        window_text=" ".join(u.get("text", "") for u in utterances))
    prompt = f"<context>\n{ctx}\n</context>\n<transcript>\n{format_utterances(utterances)}\n</transcript>"
    schema = Extraction.model_json_schema()
    t0 = time.time()

    def _empty(e: Extraction) -> bool:
        return not (e.window_summary.strip() or e.events or e.entity_updates or e.thread_updates)

    try:
        raw = llm.generate_json(schema, prompt, system=system)
        ext = Extraction.model_validate(raw)
        # Schema-embedded prompt usually fixes empties, but if the model
        # returns a valid-but-empty object, nudge once more.
        if _empty(ext):
            raw = llm.generate_json(
                schema,
                prompt + "\nYou returned an empty result. The transcript above contains speech: "
                "write a grounded 1-2 sentence window_summary. Events, entity updates, thread "
                "updates, and attributions may remain empty when the window is pure banter, "
                "chat reading, ads, or technical chatter — but concrete in-world developments "
                "(transactions, items/money, plans, scouting, conflicts) are events, even "
                "routine ones. Do not invent content to fill an array.",
                system=system)
            ext = Extraction.model_validate(raw)
    except Exception as first_exc:
        from app.observability import report_pipeline_error, set_session_error
        report_pipeline_error("extraction-attempt", first_exc)
        set_session_error(db, session_id, "extraction", first_exc)
        try:
            raw = llm.generate_json(
                schema, prompt + "\nReturn ONLY valid JSON matching the schema.",
                system=system)
            ext = Extraction.model_validate(raw)
        except Exception as retry_exc:
            report_pipeline_error("extraction", retry_exc)
            set_session_error(db, session_id, "extraction", retry_exc)
            w = db.get(m.Window, window_id)
            if w:
                w.status = "failed"
                db.commit()
            return Extraction(window_summary="", open_questions=["extraction failed"])
    ms = int((time.time() - t0) * 1000)
    w = db.get(m.Window, window_id)
    if w:
        from app.llm.budget import estimate_cost_usd as _cost
        tok_in = max(1, len(prompt) // 4)
        tok_out = max(1, len(json.dumps(raw)) // 4)
        w.status = "done"
        w.extraction_json = json.dumps(raw)
        w.window_summary = ext.window_summary
        w.model = model_name
        w.latency_ms = ms
        w.tokens_in = tok_in
        w.tokens_out = tok_out
        try:
            w.cost_usd = _cost(tok_in, tok_out, settings.LLM_COST_PER_1K_IN,
                               settings.LLM_COST_PER_1K_OUT)
        except Exception:
            pass
        from app.observability import clear_session_error
        clear_session_error(db, session_id, "extraction")
        db.commit()
    write_extraction(db, channel_id, session_id, window_id, ext)
    return ext
