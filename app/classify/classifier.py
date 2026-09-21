"""Classifier abstraction (§5.11): LLM default, Jev experimental behind flag."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.interfaces import Candidates, RoutingResult


IMPORTANCE_LEVELS = [
    "1 - Idle banter: filler words, reactions like yeah or mm-hmm, background chatter with no new information.",
    "2 - Minor: routine logistics, small talk naming places or people, restating known facts.",
    "3 - Notable: new plans, new places or items, meaningful interaction between characters.",
    "4 - Major: heists, arrests, chases, conflicts, big wins or losses.",
    "5 - Pivotal: turns that change what happens next, such as releases, betrayals, or deaths.",
]


@dataclass
class TriageVerdict:
    """Structured result of the Jev triage call (importance + atomic signals)."""
    importance: int | None = None
    importance_confidence: float = 0.0
    novelty: float | None = None
    new_character: bool = False
    has_storyline: bool = False
    model: str = ""
    raw: dict = field(default_factory=dict)


def build_routing_questions(characters: list[dict], threads: list[dict]) -> dict:
    """Docs-prescribed routing set: full records as criteria ('what is what'),
    an explicit none-of-the-above option per Choice (missing info is reported,
    not guessed), and a Noul alongside each Choice — the Choice picks
    relatively, the Noul decides absolutely whether to act at all.
    characters: [{name, desc, aliases}]; threads: [{title, summary}].
    """
    char_criteria = {}
    for c in characters:
        aka = ", ".join(c.get("aliases", []) or [])
        desc = c.get("desc") or "No description recorded yet."
        extra = f" Also known as: {aka}." if aka else ""
        char_criteria[c["name"]] = desc + extra
    char_criteria["none_of_the_above"] = (
        "No known person is the focus; casual banter or logistics with no clear subject.")
    thread_criteria = {}
    for t in threads:
        thread_criteria[t["title"]] = t.get("summary") or "No summary recorded yet."
    thread_criteria["none_of_the_above"] = (
        "The window advances no ongoing storyline; standalone chatter or logistics.")
    return {
        "character": {
            "type": "choice",
            "instructions": "Which known person is the focus of this transcript window — speaking, acting, or being discussed?",
            "criteria": char_criteria,
        },
        "character_in_focus": {
            "type": "noul",
            "instructions": "Some specific person is the focus of this window.",
            "criteria": {
                "true": "One person clearly stands out as speaking, acting, or being discussed.",
                "false": "Group banter or logistics with no single person standing out.",
            },
        },
        "thread": {
            "type": "choice",
            "instructions": "Which ongoing storyline does this window advance?",
            "criteria": thread_criteria,
        },
        "advances_storyline": {
            "type": "noul",
            "instructions": "This window advances an ongoing storyline rather than standalone chatter.",
            "criteria": {
                "true": "It continues, escalates, or resolves an ongoing plot such as a job, charges, a plan, or a conflict.",
                "false": "Standalone banter, routine logistics, or background noise with no plot thread.",
            },
        },
        "importance": {
            "type": "score",
            "instructions": "How important is this transcript window to the ongoing story?",
            "criteria": IMPORTANCE_LEVELS,
        },
    }


def build_triage_questions() -> dict:
    """Atomic triage question set: one importance score plus independent
    signals, each evaluated in parallel on the same state (fan-out is ~free).
    Criteria spell out exact conditions — jev-1.13 reads literally, so
    boundary cases are stated, not implied.
    """
    return {
        "importance": {
            "type": "score",
            "instructions": "How important is this transcript window to the ongoing story?",
            "criteria": IMPORTANCE_LEVELS,
        },
        "novelty": {
            "type": "score",
            "instructions": "Does this window introduce anything not already established?",
            "criteria": [
                "1 - Nothing new: rehash, filler, or repetition of known facts.",
                "2 - Mostly familiar with one small new detail.",
                "3 - Clear new development: a new person, plan, place, or event.",
            ],
        },
        "new_character": {
            "type": "noul",
            "instructions": "This window introduces a new person not in the known list.",
            "criteria": {
                "true": "A person is named, introduced, or speaks who was not known before.",
                "false": "Everyone mentioned or speaking is already known, or no person features at all.",
            },
        },
        "has_storyline": {
            "type": "noul",
            "instructions": "This window advances an ongoing storyline rather than standalone chatter.",
            "criteria": {
                "true": "It continues, escalates, or resolves an ongoing plot such as a job, charges, a plan, or a conflict.",
                "false": "Standalone banter, routine logistics, or background noise with no plot thread.",
            },
        },
    }


class LlmClassifier:
    """Uses §5.8 LLM output to classify characters/threads (§5.11)."""

    def __init__(self, llm=None):
        self.llm = llm

    def route(self, state: str, candidates: Candidates) -> RoutingResult:
        if not self.llm:
            return RoutingResult()
        try:
            text = state[:4000]
            char_names = [c.get("name", "?") for c in candidates.characters]
            thread_titles = [t.get("title", "?") for t in candidates.threads]
            prompt = (
                f"Given this transcript window:\n\n{text}\n\n"
                f"Known characters: {char_names}\n"
                f"Known threads: {thread_titles}\n\n"
                f"Which character is speaking and which thread does this belong to? "
                f"Answer with: character=<name or NEW> thread=<title or NONE> "
                f"importance=<1-5>"
            )
            resp = self.llm.generate_text(prompt, system="Classify the window.")
            import re
            char_m = re.search(r'character=(\S+)', resp)
            thread_m = re.search(r'thread=(\S+)', resp)
            imp_m = re.search(r'importance=(\d+)', resp)
            imp = int(imp_m.group(1)) if imp_m else 3
            return RoutingResult(
                character_choice=char_m.group(1) if char_m else None,
                thread_choice=thread_m.group(1) if thread_m and thread_m.group(1) != "NONE" else None,
                importance_score=max(1, min(5, imp)),
            )
        except Exception:
            return RoutingResult()


class JevClassifier:
    """Experimental: calls TypeSafe Jev POST /v1/systemone with Choice/Noul/Score (§5.11).

    Text-only, cannot generate text or invent entities. All questions in one parallel call.
    Ships only if it beats LLM path on eval (§10).
    """

    def __init__(self, api_key: str = "", base_url: str = "", model: str = "jev-1.13.0"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def _post(self, payload: dict) -> dict:
        """POST with one retry on 429 (honoring retry-after) and one on
        transport errors. Other errors propagate so callers can fail open."""
        import httpx
        import time
        last_exc = None
        for attempt in range(2):
            try:
                r = httpx.post(f"{self.base_url}/v1/systemone",
                               headers={"Authorization": f"Bearer {self.api_key}"},
                               json=payload, timeout=30)
                if r.status_code == 429 and attempt == 0:
                    try:
                        time.sleep(float(r.headers.get("retry-after", "1")))
                    except ValueError:
                        time.sleep(1)
                    continue
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                last_exc = e
                if attempt == 0 and e.response.status_code == 429:
                    continue
                raise
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_exc = e
                if attempt == 0:
                    time.sleep(1)
                    continue
                raise
        raise last_exc  # pragma: no cover - defensive

    def _require_key(self) -> None:
        from app.config import settings
        # D6: gated behind JEV_ENABLED + key; JEV_MOCK=1 allows eval without a key
        if getattr(settings, "JEV_MOCK", False) or not self.api_key:
            if not getattr(settings, "JEV_MOCK", False):
                raise RuntimeError("Jev not configured: set JEV_ENABLED=1 + JEV_API_KEY, "
                                   "or JEV_MOCK=1 for offline eval")

    def ask(self, state: str, questions: dict) -> dict:
        """Low-level fan-out call: any question set, full answers back
        (probabilities + confidence + model version included)."""
        from app.config import settings
        if getattr(settings, "JEV_MOCK", False):
            return {"model": self.model, "answers": {}, "mock": True}
        self._require_key()
        return self._post({"model": self.model, "state": state, "questions": questions})

    def triage(self, state: str) -> TriageVerdict:
        """Structured triage verdict for one window of transcript."""
        data = self.ask(state, build_triage_questions())
        answers = data.get("answers", {})

        def _score(key: str) -> tuple[float | None, float]:
            a = answers.get(key, {})
            try:
                return (float(a.get("score")), float(a.get("confidence", 0.0)))
            except (TypeError, ValueError):
                return None, 0.0

        def _noul(key: str) -> bool:
            try:
                return float(answers.get(key, {}).get("noul", 0)) >= 0.5
            except (TypeError, ValueError):
                return False

        imp, imp_conf = _score("importance")
        nov, _ = _score("novelty")
        # Scores are 0-based level indices (legend "0" = first criterion);
        # convert to 1-based importance to match the extraction schema.
        imp_i = None if imp is None else max(1, min(5, int(round(imp)) + 1))
        return TriageVerdict(
            importance=imp_i,
            importance_confidence=imp_conf,
            novelty=nov,
            new_character=_noul("new_character"),
            has_storyline=_noul("has_storyline"),
            model=data.get("model", ""),
            raw=data,
        )

    def route(self, state: str, candidates: Candidates) -> RoutingResult:
        from app.config import settings
        # D6: gated behind JEV_ENABLED + key; JEV_MOCK=1 allows eval without a key
        if getattr(settings, "JEV_MOCK", False) or not self.api_key:
            if not getattr(settings, "JEV_MOCK", False):
                raise RuntimeError("Jev not configured: set JEV_ENABLED=1 + JEV_API_KEY, "
                                   "or JEV_MOCK=1 for offline eval")
            return RoutingResult(raw={"mock": True, "model": self.model})
        # Real TypeSafe shape: questions is a MAP keyed by caller id, lowercase
        # types (choice/score/noul). Docs: POST {base}/v1/systemone.
        questions: dict = {}
        if candidates.characters:
            questions["character"] = {
                "type": "choice",
                "instructions": "Which known person is speaking or being portrayed in this transcript window?",
                "criteria": {c.get("name", "?"): c.get("desc", "") for c in candidates.characters},
            }
        questions["new_character"] = {
            "type": "noul",
            "instructions": "This window introduces a new person not in the known list.",
        }
        if candidates.threads:
            questions["thread"] = {
                "type": "choice",
                "instructions": "Which ongoing storyline does this window belong to?",
                "criteria": {t.get("title", "?"): t.get("summary", "") for t in candidates.threads},
            }
        questions["importance"] = {
            "type": "score",
            "instructions": "How important is this window to the overall story?",
            "criteria": ["1 - idle banter", "2 - minor", "3 - notable", "4 - major", "5 - pivotal"],
        }
        data = self._post({"model": self.model, "state": state, "questions": questions})
        answers = data.get("answers", {})
        out = RoutingResult(raw=data)
        try:
            if "character" in answers:
                out.character_choice = answers["character"].get("choice")
            if "thread" in answers:
                out.thread_choice = answers["thread"].get("choice")
            if "new_character" in answers:
                out.new_character_noul = float(answers["new_character"].get("noul", 0)) >= 0.5
            if "importance" in answers:
                try:
                    _imp = int(round(float(answers["importance"].get("score", 2))))
                except (TypeError, ValueError):
                    _imp = 2
                out.importance_score = max(1, min(5, _imp + 1))  # 0-based level -> 1-based
        except (TypeError, ValueError):
            pass  # conservative: keep defaults, raw holds the evidence
        return out


def get_classifier(kind: str = "llm", **kw):
    """NOTE: settings override the kind param — when CLASSIFIER=jev and
    JEV_ENABLED=1, even kind="llm" returns a keyed JevClassifier. Eval code
    needing a specific classifier must construct it explicitly."""
    from app.config import settings
    # D6: CLASSIFIER=jev only honored when JEV_ENABLED=1; else safe fallback to llm
    if kind == "jev":
        if not getattr(settings, "JEV_ENABLED", False):
            return LlmClassifier()
        return JevClassifier(**kw)
    if getattr(settings, "CLASSIFIER", "llm") == "jev" and getattr(settings, "JEV_ENABLED", False):
        return JevClassifier(api_key=settings.JEV_API_KEY, base_url=settings.JEV_BASE_URL,
                             model=settings.JEV_MODEL)
    from app.llm.base import get_llm as _get_llm
    return LlmClassifier(llm=_get_llm("extract"))
