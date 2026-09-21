"""SQLAlchemy models — §6 data model (SQLite; Postgres-portable)."""
from __future__ import annotations

from sqlalchemy import BLOB, REAL, TEXT, ForeignKey, Index, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Channel(Base):
    __tablename__ = "channels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    twitch_login: Mapped[str] = mapped_column(TEXT, unique=True, nullable=False)
    twitch_user_id: Mapped[str | None] = mapped_column(TEXT)
    display_name: Mapped[str | None] = mapped_column(TEXT)
    notes: Mapped[str | None] = mapped_column(TEXT)
    config_json: Mapped[str | None] = mapped_column(TEXT)
    created_at: Mapped[str] = mapped_column(TEXT, nullable=False)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False)
    twitch_stream_id: Mapped[str | None] = mapped_column(TEXT)
    source: Mapped[str] = mapped_column(TEXT, nullable=False)  # live|replay
    title: Mapped[str | None] = mapped_column(TEXT)
    category: Mapped[str | None] = mapped_column(TEXT)
    meta_history: Mapped[str | None] = mapped_column(TEXT)
    started_at: Mapped[str | None] = mapped_column(TEXT)
    ended_at: Mapped[str | None] = mapped_column(TEXT)
    status: Mapped[str] = mapped_column(TEXT, nullable=False)  # starting|live|degraded|ended|failed|interrupted
    final_summary: Mapped[str | None] = mapped_column(TEXT)
    last_error: Mapped[str | None] = mapped_column(TEXT)
    last_error_at: Mapped[str | None] = mapped_column(TEXT)


class MonitorLease(Base):
    """Cross-process ownership of a running pipeline.

    key is "live:{channel_id}" for live monitors (one per channel) and
    "session:{session_id}" for replays. A lease is valid while expires_at
    (unix seconds) is in the future; the owning worker renews it.
    """
    __tablename__ = "monitor_leases"
    key: Mapped[str] = mapped_column(TEXT, primary_key=True)
    session_id: Mapped[int | None] = mapped_column(Integer)
    owner: Mapped[str] = mapped_column(TEXT, nullable=False)
    expires_at: Mapped[float] = mapped_column(REAL, nullable=False)
    stop_requested: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Speaker(Base):
    __tablename__ = "speakers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False)
    name: Mapped[str | None] = mapped_column(TEXT)
    role: Mapped[str | None] = mapped_column(TEXT)  # streamer|guest|regular|unknown
    confirmed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    speech_seconds: Mapped[float] = mapped_column(REAL, default=0.0, nullable=False)
    consent_note: Mapped[str | None] = mapped_column(TEXT)
    created_at: Mapped[str] = mapped_column(TEXT, nullable=False)


class SpeakerEmbedding(Base):
    __tablename__ = "speaker_embeddings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    speaker_id: Mapped[int] = mapped_column(ForeignKey("speakers.id", ondelete="CASCADE"), nullable=False)
    embedding: Mapped[bytes] = mapped_column(BLOB, nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str] = mapped_column(TEXT, nullable=False)
    seconds: Mapped[float | None] = mapped_column(REAL)
    source: Mapped[str | None] = mapped_column(TEXT)
    created_at: Mapped[str] = mapped_column(TEXT, nullable=False)


class Window(Base):
    __tablename__ = "windows"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    t_start: Mapped[float] = mapped_column(REAL, nullable=False)
    t_end: Mapped[float] = mapped_column(REAL, nullable=False)
    status: Mapped[str] = mapped_column(TEXT, nullable=False)
    extraction_json: Mapped[str | None] = mapped_column(TEXT)
    window_summary: Mapped[str | None] = mapped_column(TEXT)
    model: Mapped[str | None] = mapped_column(TEXT)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(REAL)
    created_at: Mapped[str] = mapped_column(TEXT, nullable=False)
    # Worker-boot unix time: disambiguates overlapping stream-second
    # timestamps across restarts. NULL = pre-epoch era.
    # Added via ALTER TABLE on existing DBs (see app.db.session.ensure_schema).
    boot_epoch: Mapped[int | None] = mapped_column(Integer, default=None)


class Segment(Base):
    __tablename__ = "segments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    window_id: Mapped[int | None] = mapped_column(ForeignKey("windows.id"))
    t_start: Mapped[float] = mapped_column(REAL, nullable=False)
    t_end: Mapped[float] = mapped_column(REAL, nullable=False)
    wall_start: Mapped[str | None] = mapped_column(TEXT)
    text: Mapped[str] = mapped_column(TEXT, nullable=False, default="")
    words_json: Mapped[str | None] = mapped_column(TEXT)
    asr_model: Mapped[str | None] = mapped_column(TEXT)
    asr_conf: Mapped[float | None] = mapped_column(REAL)
    speaker_id: Mapped[int | None] = mapped_column(ForeignKey("speakers.id"))
    speaker_conf: Mapped[float | None] = mapped_column(REAL)
    speaker_flags: Mapped[str | None] = mapped_column(TEXT)


class SegmentEmbedding(Base):
    __tablename__ = "segment_embeddings"
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id", ondelete="CASCADE"), primary_key=True)
    embedding: Mapped[bytes] = mapped_column(BLOB, nullable=False)
    model: Mapped[str] = mapped_column(TEXT, nullable=False)


class Entity(Base):
    __tablename__ = "entities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False)
    type: Mapped[str] = mapped_column(TEXT, nullable=False)
    canonical_name: Mapped[str] = mapped_column(TEXT, nullable=False)
    description: Mapped[str | None] = mapped_column(TEXT)
    status: Mapped[str] = mapped_column(TEXT, default="provisional", nullable=False)
    merged_into: Mapped[int | None] = mapped_column(ForeignKey("entities.id"))
    needs_review: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    voiced_by_speaker_id: Mapped[int | None] = mapped_column(ForeignKey("speakers.id"))
    mention_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_seen_session: Mapped[int | None] = mapped_column(ForeignKey("sessions.id"))
    last_seen_at: Mapped[str | None] = mapped_column(TEXT)


class EntityAlias(Base):
    __tablename__ = "entity_aliases"
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True)
    alias: Mapped[str] = mapped_column(TEXT, primary_key=True)
    source: Mapped[str | None] = mapped_column(TEXT)


class Thread(Base):
    __tablename__ = "threads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False)
    title: Mapped[str] = mapped_column(TEXT, nullable=False)
    summary: Mapped[str | None] = mapped_column(TEXT)
    status: Mapped[str] = mapped_column(TEXT, default="open", nullable=False)
    importance: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    last_updated_at: Mapped[str | None] = mapped_column(TEXT)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    window_id: Mapped[int | None] = mapped_column(ForeignKey("windows.id"))
    t_start: Mapped[float | None] = mapped_column(REAL)
    t_end: Mapped[float | None] = mapped_column(REAL)
    type: Mapped[str | None] = mapped_column(TEXT)
    description: Mapped[str] = mapped_column(TEXT, nullable=False)
    importance: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    thread_id: Mapped[int | None] = mapped_column(ForeignKey("threads.id"))
    # Per-event streamer involvement (2.5b, log-only): actor|target|witness|
    # informed|ambient|offscreen|unknown. NULL = pre-role era (see gold set, no backfill).
    # Added via ALTER TABLE on existing DBs (see app.db.session.ensure_schema).
    streamer_role: Mapped[str | None] = mapped_column(TEXT, default=None)


class EventParticipant(Base):
    __tablename__ = "event_participants"
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), primary_key=True)


class Attribution(Base):
    __tablename__ = "attributions"
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(TEXT, nullable=False)
    entity_id: Mapped[int | None] = mapped_column(ForeignKey("entities.id"))
    confidence: Mapped[float | None] = mapped_column(REAL)
    evidence: Mapped[str | None] = mapped_column(TEXT)
    method: Mapped[str] = mapped_column(TEXT, nullable=False)


class Summary(Base):
    __tablename__ = "summaries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    kind: Mapped[str] = mapped_column(TEXT, nullable=False)
    covers_t_start: Mapped[float | None] = mapped_column(REAL)
    covers_t_end: Mapped[float | None] = mapped_column(REAL)
    text: Mapped[str] = mapped_column(TEXT, nullable=False)
    model: Mapped[str | None] = mapped_column(TEXT)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(REAL)
    created_at: Mapped[str] = mapped_column(TEXT, nullable=False)
    # Recap cache: "max_event_id:max_window_id:max_thread_updated_at" snapshot.
    # Added via ALTER TABLE on existing DBs (see routes_sessions.get_recap).
    cache_key: Mapped[str | None] = mapped_column(TEXT, default=None)


class SummaryFeedback(Base):
    """Confidence votes on displayed summaries; negative runs simplify recaps."""
    __tablename__ = "summary_feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    summary_id: Mapped[int | None] = mapped_column(ForeignKey("summaries.id", ondelete="SET NULL"))
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    kind: Mapped[str] = mapped_column(TEXT, nullable=False)  # rolling|recap
    vote: Mapped[str] = mapped_column(TEXT, nullable=False)  # yes|no|not_sure
    created_at: Mapped[str] = mapped_column(TEXT, nullable=False)


class Correction(Base):
    __tablename__ = "corrections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    before_json: Mapped[str | None] = mapped_column(TEXT)
    after_json: Mapped[str | None] = mapped_column(TEXT)
    created_at: Mapped[str] = mapped_column(TEXT, nullable=False)


class IngestGap(Base):
    __tablename__ = "ingest_gaps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    t_start: Mapped[float] = mapped_column(REAL, nullable=False)
    t_end: Mapped[float] = mapped_column(REAL, nullable=False)
    reason: Mapped[str | None] = mapped_column(TEXT)
    # Worker-boot unix time (stream seconds are boot-relative).
    boot_epoch: Mapped[int | None] = mapped_column(Integer, default=None)


class PlotBeat(Base):
    """Hourly plot beats (§two-pass): immutable timeline entries.
    wall_start/wall_end are primary display time; t_start/t_end are
    secondary boot-relative stream seconds."""
    __tablename__ = "plot_beats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    hour_index: Mapped[int] = mapped_column(Integer, nullable=False)
    run_label: Mapped[str] = mapped_column(TEXT, nullable=False, default="live")
    wall_start: Mapped[str | None] = mapped_column(TEXT)
    wall_end: Mapped[str | None] = mapped_column(TEXT)
    t_start: Mapped[float | None] = mapped_column(REAL)
    t_end: Mapped[float | None] = mapped_column(REAL)
    headline: Mapped[str] = mapped_column(TEXT, nullable=False)
    context: Mapped[str | None] = mapped_column(TEXT)
    significance: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    status: Mapped[str] = mapped_column(TEXT, default="final", nullable=False)
    continues_beat_id: Mapped[int | None] = mapped_column(ForeignKey("plot_beats.id"))
    prompt_version: Mapped[str | None] = mapped_column(TEXT)
    model: Mapped[str | None] = mapped_column(TEXT)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(REAL)
    created_at: Mapped[str] = mapped_column(TEXT, nullable=False)


class BeatRun(Base):
    """Idempotency watermark: one row per closed hour per run label."""
    __tablename__ = "beat_runs"
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    hour_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_label: Mapped[str] = mapped_column(TEXT, primary_key=True, default="live")
    status: Mapped[str] = mapped_column(TEXT, nullable=False)  # done|skipped
    reason: Mapped[str | None] = mapped_column(TEXT)
    created_at: Mapped[str] = mapped_column(TEXT, nullable=False)


class BeatEvent(Base):
    __tablename__ = "beat_events"
    beat_id: Mapped[int] = mapped_column(ForeignKey("plot_beats.id", ondelete="CASCADE"), primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), primary_key=True)


class Consent(Base):
    """D3: voice-print consent record (§11/BIPA). One row per grant; revocation stamps revoked_at."""
    __tablename__ = "consents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False)
    speaker_id: Mapped[int] = mapped_column(ForeignKey("speakers.id", ondelete="CASCADE"), nullable=False)
    note: Mapped[str] = mapped_column(TEXT, nullable=False, default="")
    granted_by: Mapped[str] = mapped_column(TEXT, nullable=False, default="owner")
    granted_at: Mapped[str] = mapped_column(TEXT, nullable=False)
    revoked_at: Mapped[str | None] = mapped_column(TEXT)


Index("idx_segments_session_t", Segment.session_id, Segment.t_start)
Index("idx_beats_session_label_hour", PlotBeat.session_id, PlotBeat.run_label,
      PlotBeat.hour_index)
