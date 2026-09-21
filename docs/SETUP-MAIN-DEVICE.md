# Setup on the main device (handoff)

Instructions for getting the summarizer running on the owner's main Mac with
auto-monitor. Written for an agent picking this up cold. Do the steps in order and
**report back which verification checks passed** (bottom of this file).

Never commit `.env` or any key. `.env` is gitignored; keep it that way.

## 0. Before touching anything: is a monitor already running?

The owner is currently monitoring a channel. Older setups ran the pipeline from a
standalone script (see `scripts/restart_worker.sh`, which launches `/tmp/run_xqc.py`)
rather than through the API.

- Check: `pgrep -fl "run_xqc|uvicorn app.api.main"` and look for a live session in the
  existing database (`sqlite3 data/app.db "select id,status,started_at from sessions order by id desc limit 5"`).
- **The new server marks any session still `live` without a lease as `interrupted` on
  startup.** A script-driven session keeps writing, but the UI will show it ended.
- If a stream is in progress, wait for it to end (or ask the owner) before switching.
  After switching, start monitors only through the API / auto-monitor, not the script.
- If there is an existing `data/app.db`, back it up first: `cp data/app.db data/app.db.bak-$(date +%F)`.
  The schema migrates automatically on startup (`ensure_schema`), adding new tables/columns.

## 1. System prerequisites (macOS, Apple Silicon)

```bash
brew install ffmpeg streamlink node python@3.11   # skip any already installed
ffmpeg -version | head -1 && streamlink --version && node -v && python3.11 -V
```

`streamlink` pulls the Twitch audio and is also the auto-monitor's live check when no
Twitch API keys are configured. `ffmpeg` decodes audio. Both are required.

## 2. Code and Python environment

```bash
git clone https://github.com/MichiasAsnake/stream-summarizer.git   # or git pull in an existing checkout
cd stream-summarizer
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,asr-mac,vad,speakers,llm-anthropic]"
```

Extras: `asr-mac` = mlx-whisper transcription, `vad` = voice activity detection,
`speakers` = speaker identification, `llm-anthropic` = Claude provider. If a different
LLM provider is chosen, swap in `llm-gemini` or `llm-openai`.

## 3. Configure `.env`

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # use as API_BEARER_TOKEN
```

**Values that must change from `.env.example`** (the template defaults to stubs, which run
but produce no real transcript or summaries):

| Key | Set to | Notes |
|---|---|---|
| `API_BEARER_TOKEN` | the generated secret | Server refuses to start with the placeholder. Give it to the owner for the UI prompt. |
| `ASR_BACKEND` | `mlx_whisper` | Template says `stub`. |
| `ASR_MODEL` | `small` (default) | `medium`/`large-v3-turbo` = better accuracy, more CPU/RAM. |
| `LLM_PROVIDER` | `anthropic` (or `gemini` / `openai_compat`) | Template says `stub`. |
| `LLM_API_KEY` | provider key | Ask the owner; never guess or reuse unrelated keys. |
| `LLM_MODEL_EXTRACT` | e.g. `claude-haiku-4-5` | Template values are Gemini model names — must match the provider. |
| `LLM_MODEL_RECAP` | e.g. `claude-sonnet-5` | Used for recaps and end-of-stream summaries. |
| `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET` | from dev.twitch.tv app (optional) | Without them auto-monitor uses streamlink. With them it uses the Twitch API (more reliable, gives title/category). |

Optional to review: `LLM_SESSION_BUDGET_USD` / `LLM_MONTHLY_BUDGET_USD` (extraction pauses
when exceeded), `LLM_COST_PER_1K_IN/OUT` (set to the chosen model's pricing so budgets are
meaningful), `CONTENT_PROFILE` (`roleplay` | `gaming` | `just_chatting`).

Keep `CONSENT_REQUIRED=true`: speaker voiceprints are only stored for consented speakers.

## 4. Start the server

```bash
source .venv/bin/activate
mkdir -p data
python -m app.db.init_db
uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

Expected in the log: no traceback, and either nothing about auto-monitor (Twitch API or
streamlink found) or `Auto-monitor disabled: ...` (means neither keys nor streamlink —
fix step 1/3). Use one uvicorn worker unless there is a reason not to; multiple workers
are safe (leases prevent duplicates) but unnecessary on one machine.

To keep it running after the terminal closes, a `launchd` agent is the Mac-native option
(`~/Library/LaunchAgents/`, `RunAtLoad` + `KeepAlive`, `WorkingDirectory` = repo,
program = `.venv/bin/uvicorn app.api.main:app --host 127.0.0.1 --port 8000`, logs to
`data/server.log`). Ask the owner before installing a login item.

## 5. Channel + auto-monitor

```bash
export TOKEN=...   # API_BEARER_TOKEN
H="Authorization: Bearer $TOKEN"
curl -s -H "$H" http://127.0.0.1:8000/api/v1/channels                  # existing channels?
curl -s -X POST -H "$H" "http://127.0.0.1:8000/api/v1/channels?twitch_login=<login>&auto_monitor=true"
# or for an existing channel:
curl -s -X PUT -H "$H" -H "Content-Type: application/json" -d '{"enabled": true}' \
  http://127.0.0.1:8000/api/v1/channels/<id>/auto-monitor
```

Ask the owner which Twitch login(s) to monitor if it is not obvious from the existing DB.
Manual control is still available: `POST` / `DELETE /api/v1/channels/<id>/monitor`.

## 6. Web UI

```bash
cd web && npm ci && npm run dev    # http://localhost:5173, proxies /api to :8000
```

It prompts for the API token once per browser session. A simpler built-in page is also
served by the backend at `http://127.0.0.1:8000/ui`.

## Verification checklist (report each)

1. `pytest -q` passes (93+ tests) and `ruff check .` is clean.
2. Server starts with no traceback; `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/v1/sessions` returns `401` without the token and `200` with it.
3. `GET /api/v1/channels` shows the channel with `"auto_monitor": true`.
4. Live check works: `streamlink --json twitch.tv/<login> | head -c 300` returns `streams` when live
   (or, with Twitch keys, the server log shows no `live check ... failed` warnings).
5. When the channel is live: within ~60s `GET /api/v1/sessions` shows a new `live` session,
   the UI shows transcript lines streaming, and a rolling summary appears within a few minutes.
6. After the stream ends: the session becomes `ended` about 5 minutes after audio stops, and
   `GET /api/v1/sessions/<id>` has a non-empty `final_summary` a minute or so later.

If the channel is offline during setup, checks 5–6 can be exercised with a replay:
`curl -s -X POST -H "$H" "http://127.0.0.1:8000/api/v1/channels/<id>/replay?file=/path/to/audio.mp3"`.

## Troubleshooting

- **Sessions end after ~5 min with no transcript** — streamlink/ffmpeg can't pull audio.
  Run `streamlink --stdout twitch.tv/<login> audio_only | ffmpeg -i pipe:0 -t 5 -f null -`.
  Auto-monitor then waits `AUTO_MONITOR_RESTART_COOLDOWN_MINUTES` (10) before retrying.
- **Transcript but no summaries** — `LLM_PROVIDER` still `stub`, wrong model name for the
  provider, or budget exceeded. `GET /api/v1/sessions/<id>` shows `last_error`;
  `/api/v1/metrics` has `llm_errors_total`.
- **Session shows `interrupted`** — the server restarted/crashed mid-stream (see step 0).
  With auto-monitor on, a new session starts on the next check if the channel is still live.
- **`401` in the UI** — token mismatch; the UI re-prompts. Clear with a new browser tab.

Background on the design: `FIXES.md` (all changes since the audit) and `README.md`.
