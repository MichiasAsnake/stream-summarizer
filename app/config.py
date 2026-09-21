"""Central config (§7). Env-driven, per-channel overrides stored in channels.config_json."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    TWITCH_CLIENT_ID: str = ""
    TWITCH_CLIENT_SECRET: str = ""
    API_BEARER_TOKEN: str = ""

    ASR_BACKEND: str = "stub"  # stub|mlx_whisper|whisper_cpp|faster_whisper|hosted
    ASR_MODEL: str = "small"

    SPK_EMBED_MODEL: str = "speechbrain/spkrec-ecapa-voxceleb"
    SPK_MATCH_THRESHOLD: float = 0.65
    SPK_MARGIN: float = 0.10
    SPK_CLUSTER_THRESHOLD: float = 0.55
    SPK_MIN_SEG_SECONDS: float = 1.0
    SPK_PROMOTE_MIN_SEGMENTS: int = 3
    SPK_PROMOTE_MIN_SECONDS: float = 5.0
    SPK_MAX_PROVISIONAL: int = 50

    WINDOW_MIN_SECONDS: float = 60
    WINDOW_MAX_SECONDS: float = 90
    WINDOW_MAX_WORDS: int = 450
    WINDOW_MIN_SILENCE: float = 1.5

    LLM_PROVIDER: str = "stub"  # stub|gemini|anthropic|openai_compat
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = ""
    LLM_MODEL_EXTRACT: str = "gemini-3.6-flash"
    LLM_MODEL_RECAP: str = "gemini-3.6-flash"
    LLM_SESSION_BUDGET_USD: float = 5.0
    # D5: monthly cap (0 = unlimited) + cost model for estimation
    LLM_MONTHLY_BUDGET_USD: float = 20.0
    LLM_COST_PER_1K_IN: float = 0.0005
    LLM_COST_PER_1K_OUT: float = 0.0015
    LLM_TIMEOUT_SECONDS: float = 60.0
    LLM_MAX_RETRIES: int = 2
    LLM_RETRY_BACKOFF_SECONDS: float = 0.5

    # Pipeline ownership lease: renewed every TTL/3; a crashed worker's
    # sessions become recoverable once its lease expires.
    MONITOR_LEASE_TTL_SECONDS: float = 30.0

    # Only sufficiently supported model output may mutate canonical memory.
    # Lower-confidence output remains in windows.extraction_json for review.
    MEMORY_ENTITY_MIN_CONFIDENCE: float = 0.65
    MEMORY_THREAD_MIN_CONFIDENCE: float = 0.65
    MEMORY_EVENT_MIN_CONFIDENCE: float = 0.60
    MEMORY_ATTRIBUTION_MIN_CONFIDENCE: float = 0.60

    # D1: roleplay|gaming|just_chatting
    CONTENT_PROFILE: str = "roleplay"
    # D2: mac|local_gpu|cloud_gpu|hosted
    DEPLOY_TARGET: str = "mac"
    # D4: private|public
    APP_MODE: str = "private"
    # D3: consent gate + TTLs
    CONSENT_REQUIRED: bool = True
    CONSENT_TTL_DAYS: int = 365
    TRANSCRIPT_RETENTION_DAYS: int = 365
    # D6
    JEV_ENABLED: bool = False
    JEV_MOCK: bool = False

    CLASSIFIER: str = "llm"  # llm|jev
    JEV_API_KEY: str = ""
    JEV_MODEL: str = "jev-1.13.0"
    JEV_BASE_URL: str = "https://api.typesafe.ai"

    SESSION_END_OFFLINE_MINUTES: int = 5
    # Auto-monitor: channels opted in via PUT /channels/{id}/auto-monitor are
    # checked this often and a monitor starts when they go live. Uses the
    # Twitch API when TWITCH_CLIENT_ID/SECRET are set, else `streamlink --json`.
    AUTO_MONITOR_ENABLED: bool = True
    AUTO_MONITOR_POLL_SECONDS: float = 60.0
    # Don't auto-restart a channel whose last session ended without capturing
    # any speech within this many minutes (avoids a restart loop when ingest
    # is broken but Twitch still reports the channel live).
    AUTO_MONITOR_RESTART_COOLDOWN_MINUTES: float = 10.0
    STREAMLINK_BIN: str = "streamlink"
    # Write an end-of-stream summary for sessions that ended or were interrupted.
    FINAL_SUMMARY_ENABLED: bool = True
    DB_URL: str = "sqlite:///data/app.db"
    DATA_DIR: str = "./data"

    HOSTED_ASR_URL: str = ""
    HOSTED_ASR_API_KEY: str = ""


settings = Settings()
