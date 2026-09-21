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
- `pytest -q`: 78 passed
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
