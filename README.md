# Twitch Stream Summarizer — V1
Implements `twitch-stream-summarizer-v1-design.md` (§1–§14).

## Owner decisions (§14) — all wired
| # | Decision | Default | How set |
|---|---|---|---|
| 1 | Target content type | `roleplay` | `CONTENT_PROFILE=roleplay\|gaming\|just_chatting`; stored per-channel in `channels.config_json`; shapes extraction hints (`app/content_profiles.py`) + eval focus |
| 2 | Deployment target | `mac` | `DEPLOY_TARGET=mac\|local_gpu\|cloud_gpu\|hosted`; presets in `deploy/*.env`, GPU overlay `deploy/docker-compose.gpu.yml`, `Dockerfile.gpu`; ASR map in `app/deploy.py` |
| 3 | Voice-enrollment consent | required | `CONSENT_REQUIRED=true`; `POST /speakers/enroll` rejects without `consent_note`; `POST/DELETE /speakers/{id}/consent`; records in `consents` table (`app/consent.py`); clips TTL `UNKNOWN_CLIP_TTL_HOURS` |
| 4 | Private vs public | `private` | `APP_MODE=private\|public`; public adds retention guardrails + startup warning (`app/mode.py`); launch checklist `docs/legal-review.md` |
| 5 | LLM provider + budget | stub / $5 sess / $20 mo | `LLM_PROVIDER`, `LLM_SESSION_BUDGET_USD`, `LLM_MONTHLY_BUDGET_USD`; enforced in `app/memory/extractor.py` via `app/llm/budget.py` (pause extraction, keep transcribing); per-window cost in `windows.cost_usd` |
| 6 | Jev access | disabled | `JEV_ENABLED=false`, `JEV_MOCK=false`; `CLASSIFIER=llm\|jev` honored only when enabled; A/B via `python -m eval.jev_ab` |

## Quickstart (Mac dev)

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # fill TWITCH_CLIENT_ID/SECRET, LLM keys
python -m app.db.init_db
uvicorn app.api.main:app --reload
```

GPU prod: `docker compose -f docker-compose.yml -f deploy/docker-compose.gpu.yml up --build`
Hosted ASR: `cp deploy/hosted.env .env` + set provider creds in `app/asr/hosted.py`.

Replay a file through the full pipeline (stub components, no GPU):

```bash
python -m eval.runner --input ./fixtures/sample.wav --channel test_channel --stub
python -m eval.jev_ab --mock   # Jev A/B without a key
```

## Layout
See §13 of the design doc. Key interfaces in `app/interfaces.py`:
`Transcriber`, `SpeakerEmbedder`, `LLM`, `Classifier`, `ContextSource`.

## Config
All settings via env — see `.env.example` (§7 + §14).

## Eval
`eval/runner.py` runs replay + metrics (§10). Gold set lives in `eval/gold/`
(empty until you add VODs + labels). Per-profile eval focus in `app/content_profiles.py`.
