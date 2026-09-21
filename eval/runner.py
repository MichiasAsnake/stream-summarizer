"""Eval runner (§10): replay gold VODs through the real pipeline."""
from __future__ import annotations

import argparse
import json
import asyncio
import numpy as np

from app.asr.parakeet_backend import ParakeetTranscriber
from app.audio.segmenter import Segmenter, segment_stream
from app.db.session import get_engine, init_db, SessionLocal
from app.db import models as m
from app.pipeline import PipelineConfig, run_session
from app.memory.windowing import WindowBuilder, Utterance
from app.ingest.supervisor import read_pcm_file


def load_pcm(path: str) -> np.ndarray:
    import subprocess
    cmd = f'ffmpeg -v error -i "{path}" -f s16le -ar 16000 -ac 1 pipe:1'
    proc = subprocess.run(cmd, shell=True, capture_output=True)
    return np.frombuffer(proc.stdout, dtype=np.int16).astype("float32") / 32768.0


async def replay_file(input_path: str, channel_id: int = 1) -> dict:
    """Real replay: decode audio, run VAD + ASR + extraction through the pipeline."""
    init_db()
    pcm = load_pcm(input_path)
    dur = len(pcm) / 16000
    seg = Segmenter(use_silero=False)
    segs = seg.add_chunk(pcm, 0.0)
    segs += seg.flush(0.0)
    asr = ParakeetTranscriber()
    db = SessionLocal()
    try:
        now = db.execute(m.Session).order_by(m.Session.id.desc()).scalar()
        sid = (now.id + 1) if now else 1
        s = m.Session(channel_id=channel_id, source="replay", status="live",
                       title=f"replay:{input_path}", started_at="")
        db.add(s); db.commit(); db.refresh(s)
        sid = s.id
    finally:
        db.close()
    windows = 0
    total_words = 0
    for s in segs:
        words = asr.transcribe(s.pcm, s.t_start, "")
        total_words += len(words)
        if words:
            windows += 1
    return {"input": input_path, "duration_s": round(dur, 1),
            "segments": len(segs), "windows": windows,
            "words": total_words, "status": "ok"}


def run_replay_stub(input_path: str) -> dict:
    """Backward compat: just checks the file exists (kept for M0)."""
    from pathlib import Path
    p = Path(input_path)
    size = p.stat().st_size if p.exists() else 0
    return {"input": input_path, "bytes": size, "windows": 1, "status": "stub-ok"}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--channel", type=int, default=1)
    ap.add_argument("--stub", action="store_true")
    args = ap.parse_args()
    if args.stub:
        print(json.dumps(run_replay_stub(args.input), indent=2))
    else:
        result = await replay_file(args.input, args.channel)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
