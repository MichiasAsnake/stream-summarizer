# Twitch Stream Summarizer — V1
Implements `twitch-stream-summarizer-v1-design.md` (§1–§14).

## Owner decisions (§14) — all wired
| # | Decision | Default | How set |
|---|---|---|---|
| 1 | Target content type | `roleplay` | `CONTENT_PROFILE=roleplay\|gaming\|just_chatting`; stored per-channel in `channels.config_json`; shapes extraction hints (`app/content_profiles.py`) + eval focus |
| 2 | Deployment target | `mac` | `DEPLOY_TARGET=mac\|local_gpu\|cloud_gpu\|hosted`; presets in `deploy/*.env`, GPU overlay `deploy/docker-compose.gpu.yml`, `Dockerfile.gpu`; ASR map in `app/deploy.py` |
| 3 | Voice-enrollment consent | required | Unknown clusters remain memory-only. Persisted voiceprints require active consent plus `POST /speakers/{id}/voiceprint`; revocation deletes embeddings immediately. |
| 4 | Private vs public | `private` | `APP_MODE=private\|public`; public adds retention guardrails + startup warning (`app/mode.py`); launch checklist `docs/legal-review.md` |
| 5 | LLM provider + budget | stub / $5 sess / $20 mo | `LLM_PROVIDER`, `LLM_SESSION_BUDGET_USD`, `LLM_MONTHLY_BUDGET_USD`; enforced in `app/memory/extractor.py` via `app/llm/budget.py` (pause extraction, keep transcribing); per-window cost in `windows.cost_usd` |
| 6 | Jev access | disabled | `JEV_ENABLED=false`, `JEV_MOCK=false`; `CLASSIFIER=llm\|jev` honored only when enabled; A/B via `python -m eval.jev_ab` |

## Quickstart (Mac dev)

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # fill TWITCH_CLIENT_ID/SECRET, LLM keys
# Set API_BEARER_TOKEN in .env to a long random value before startup.
# ffmpeg is also required locally (`brew install ffmpeg` on macOS).
python -m app.db.init_db
uvicorn app.api.main:app --reload
```

Web UI (dev): `cd web && npm ci && npm run dev`, then open http://localhost:5173.
The UI calls a relative `/api/v1`, which Vite proxies to `http://localhost:8000`
(override with `VITE_API_PROXY`, or build with `VITE_API_BASE` for a separate API host).
It lists sessions, follows the newest live one, and streams the transcript live.

Running pipelines hold a lease in the database (`MONITOR_LEASE_TTL_SECONDS`), so
multiple uvicorn workers never start duplicate monitors for one channel, a stop
request reaches whichever worker owns the monitor, and sessions left `live` by a
crashed process are closed as `interrupted` on startup or once the lease expires.

GPU prod: `docker compose -f docker-compose.yml -f deploy/docker-compose.gpu.yml up --build`
Hosted ASR: `cp deploy/hosted.env .env`, then set `HOSTED_ASR_URL` and
`HOSTED_ASR_API_KEY` for an OpenAI-compatible transcription endpoint.

Replay a file through the full production pipeline in an isolated database:

```bash
python -m eval.runner --input ./fixtures/sample.wav --channel 1
python -m eval.runner --input ./fixtures/sample.wav --gold ./eval/gold/my-stream.json
python -m eval.runner --input ./fixtures/sample.wav --stub  # file-only smoke check
python -m eval.jev_ab --mock   # Jev A/B without a key
```

## Layout
See §13 of the design doc. Key interfaces in `app/interfaces.py`:
`Transcriber`, `SpeakerEmbedder`, `LLM`, `Classifier`, `ContextSource`.

## Config
All settings via env — see `.env.example` (§7 + §14).

## Eval
`eval/runner.py` runs ingest through recap using the application pipeline, then optionally
scores transcript WER, events, entities, threads, summary facts, and entity duplication.
Gold-file format is documented in `eval/gold/README.md`. Evaluations use a temporary database
unless `--db-url` is supplied. Per-profile eval focus lives in `app/content_profiles.py`.
