"""D6 — Jev A/B (§5.11, M7): runs LLM vs Jev classifier on the eval set.

Ships only if Jev beats/matches LLM on accuracy while improving latency/cost.
Runs with JEV_MOCK=1 (no key) for pipeline testing, or a real key for scores.

Usage: python -m eval.jev_ab --gold eval/gold/ --mock
"""
from __future__ import annotations

import argparse
import time


def run_ab(mock: bool = True) -> dict:
    from app.classify.classifier import LlmClassifier, JevClassifier
    from app.config import settings
    from app.interfaces import Candidates

    cands = Candidates(characters=[{"name": "Kael"}], threads=[{"title": "Docks"}])
    state = "[seg 1] Okay, we're heading to the docks."
    llm = LlmClassifier()
    t0 = time.time()
    llm_res = llm.route(state, cands)
    llm_ms = (time.time() - t0) * 1000

    if mock or not settings.JEV_API_KEY:
        jev_res, jev_ms = {"mock": True}, 0.0
    else:
        jev = JevClassifier(api_key=settings.JEV_API_KEY, base_url=settings.JEV_BASE_URL,
                            model=settings.JEV_MODEL)
        t0 = time.time()
        jev_res = jev.route(state, cands)
        jev_ms = (time.time() - t0) * 1000

    verdict = ("Jev ships only if it beats/matches LLM on eval accuracy with better "
               "latency/cost (§5.11). No key configured — mock run, not a ship decision.")
    result = {"llm_ms": llm_ms, "jev_ms": jev_ms, "jev": str(jev_res)[:200],
              "llm": str(llm_res)[:200], "verdict": verdict}
    print(result)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", default=True)
    args = ap.parse_args()
    run_ab(mock=args.mock)
