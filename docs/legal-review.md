# D4 — Legal review checklist (§11). Not legal advice; lawyer review before public launch.

## Status: private-tool default (APP_MODE=private)
- [ ] Twitch ToS: streamlink audio capture is grey-area. Prototype privately OK; confirm sanctioned path before product launch.
- [ ] Voice prints = biometric data (BIPA/GDPR). Consent flow (app/consent.py + consents table) enforced; deletion endpoint real.
- [x] Unknown voices remain memory-only; no clips or searchable voiceprints are persisted without consent.
- [ ] Content rights: transcript retention policy (TRANSCRIPT_RETENTION_DAYS); decide display policy for game/music content.
- [ ] Attribution errors: confidence surfaced in UI; corrections logged.

## To flip to APP_MODE=public, all boxes above must be checked, plus:
- [x] Public-mode transcript retention is capped at 30 days; no unknown-speaker clips are stored.
- [ ] Auth upgrade scoped (V1 static bearer is NOT multi-tenant)
- [ ] Abuse/contact path + takedown for transcripts/clips
