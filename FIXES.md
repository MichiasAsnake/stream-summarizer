# Audit remediation

This copy addresses the September 21, 2026 code audit findings.

## Security and privacy

- Replaced user-controlled shell commands with argument-based process launches.
- Validated Twitch logins, replay sources, cross-channel references, and API targets.
- Removed hardcoded bearer tokens from both UIs; startup rejects missing/default secrets.
- Added constant-time bearer comparison and protected the metrics endpoint.
- Unknown voice clusters remain memory-only. Persisted voiceprints require active consent.
- Added consented WAV voiceprint enrollment; revocation removes embeddings immediately.
- Added consent-expiry and transcript-retention enforcement, including the public-mode 30-day cap.

## Pipeline correctness

- Replays now use ffmpeg directly, terminate, flush their queues, and mark sessions ended.
- Live child processes are terminated on cancellation and stop after the configured offline timeout.
- Corrected stateful VAD timestamps and silence accounting; silence-only buffers are discarded.
- Corrected window boundaries so post-silence/over-limit utterances start the next window.
- Linked transcript segments to their database windows and closed leaked database sessions.
- Connected session-scoped, bounded SSE publishing with keepalives.
- Removed hardcoded streamer/character names from extraction prompts.

## Providers, budgets, and deployment

- Added an OpenAI-compatible LLM backend and an OpenAI-compatible hosted-ASR backend.
- Unsupported providers now fail clearly instead of silently falling back to stubs.
- LLM budgets now include rolling summaries and recaps as well as extraction windows.
- Fixed setuptools discovery, Docker data mounts, the GPU Dockerfile selection, and Streamlink/ffmpeg dependencies.
- Updated Vite and its lockfile; `npm audit` reports zero vulnerabilities.

## Verification

- `pip install -e ".[dev]"`: passed
- `pytest -q`: 93 passed (latest run; see Auto-monitor below)
- `ruff check app eval tests`: passed
- Python bytecode compilation: passed
- Frontend production build: passed
- `npm audit`: 0 vulnerabilities
- Authenticated API smoke test: passed
- Finite replay integration smoke test: passed; session ended and segments linked to windows

Docker Compose YAML was parsed successfully. A Docker image build was not run because Docker is
not installed on the audit machine.

## Prompt hardening

- Extraction no longer forces an event or attribution merely because speech exists.
- Streamer perspective language is gender-neutral and unsupported involvement is `unknown`.
- Speaker confidence now reaches the extraction prompt instead of defaulting to `0.00`.
- Triage receives known people and open storylines before judging novelty.
- Recap inputs carry timestamps, honest newest-first ordering, importance, and explicit new markers.
- Twitch-style rolling and recap language is intentionally unchanged.

## Quality and operations hardening

- The evaluation runner now exercises the production pipeline through recap in an isolated DB.
- WER now uses true word-level Levenshtein distance; gold files also score memory and summaries.
- LLM providers use bounded timeouts/retries with Prometheus request and error metrics.
- Pipeline errors are logged, exposed through session status, and published over SSE where useful.
- Low-confidence entity, thread, and attribution updates remain auditable but cannot mutate memory.

## Crash recovery and monitor leases

- New `monitor_leases` table: live monitors hold `live:{channel_id}`, replays hold
  `session:{session_id}`. Acquisition is atomic, so concurrent requests or multiple
  workers cannot start duplicate monitors for a channel.
- The owning worker renews its lease every TTL/3. A stop request from any worker is
  recorded on the lease and honoured at the owner's next heartbeat.
- On startup, and every lease TTL afterwards, sessions still marked running with no
  valid lease are closed as `interrupted` with an explanatory `last_error`.
- Graceful shutdown stops local pipelines so sessions end cleanly and leases are released.
- Retention now treats `interrupted` sessions like `ended`/`failed` ones.
- The pipeline publishes a `session.status` SSE event when a session ends.

## Frontend live behavior

- API base is relative (`/api/v1`) with a Vite dev proxy; `VITE_API_BASE` overrides it.
- New `GET /sessions` endpoint for session discovery; the UI follows the newest running
  session unless the user picks one, and refreshes the list periodically.
- The live transcript is backfilled via `GET /sessions/{id}/transcript?limit=200` and then
  streamed over authenticated SSE (fetch-based, since EventSource cannot send headers),
  with automatic reconnect and backoff.
- `summary.updated` events refresh the rolling summary and the NEW badge; polling remains
  only as a slow fallback.
- HTTP errors are surfaced in the UI, a 401 re-prompts for the token, and pipeline errors
  and session `last_error` are shown.

## Auto-monitor and end-of-stream summaries

- Channels opt in with `PUT /channels/{id}/auto-monitor` (or `auto_monitor=true` on create).
- A watcher checks opted-in channels every `AUTO_MONITOR_POLL_SECONDS` and starts a monitor
  when one goes live. It uses the Twitch API (batched `GET /streams`, token refresh on 401)
  when credentials exist, otherwise `streamlink --json`; a failed check changes nothing.
- Only one worker polls (it holds a `watcher` lease); starts go through the per-channel lease,
  so auto and manual starts never duplicate.
- Stream id, title and category are stored on the session; changes append to `meta_history`.
- A channel whose last session ended without capturing speech is not restarted for
  `AUTO_MONITOR_RESTART_COOLDOWN_MINUTES`, preventing a restart loop when ingest is broken.
- Ended and interrupted sessions with events get a `final_summary` (also saved as a `final`
  summary row), generated off the shutdown path with up to 3 attempts and budget checks.
- The UI shows stream title/category and the wrap-up for finished sessions.

