"""Stateful Silero VAD segmentation (§5.3). Tracks silence across chunk boundaries
so live audio is never split mid-phrase. Falls back to energy VAD when torch unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class VadSegment:
    t_start: float
    t_end: float
    pcm: np.ndarray


class Segmenter:
    """Cut utterance on >=500ms of silence; hard-cap 30s; drop <300ms (§5.3).

    Stateful across calls: accumulates frames until silence threshold is met,
    so a single 32ms chunk never emits a segment on its own.
    """

    def __init__(self, sample_rate: int = 16000, silence_ms: int = 500,
                 use_silero: bool = True):
        self.sr = sample_rate
        self.silence_ms = silence_ms
        self.use_silero = use_silero
        self._vad = None
        self._buf: list[np.ndarray] = []
        self._buf_start: float | None = None
        self._silence_samples = 0
        self._speech_seen = False
        try:
            if use_silero:
                import torch  # type: ignore
                self._vad = torch.hub.load(
                    "snakers4/silero-vad", "silero_vad",
                    force_reload=False, trust_repo=True)
        except Exception:
            self._vad = None  # energy fallback

    def _is_speech_energy(self, frame: np.ndarray) -> bool:
        return float(np.sqrt(np.mean(frame**2) + 1e-12)) > 0.02

    def _silero_speech(self, pcm: np.ndarray) -> bool:
        if self._vad is None:
            return self._is_speech_energy(pcm)
        import torch  # type: ignore
        with torch.no_grad():
            return bool(self._vad(torch.from_numpy(pcm.astype("float32")).unsqueeze(0)).item())

    def add_chunk(self, pcm: np.ndarray, t_offset: float) -> list[VadSegment]:
        """Accumulate a PCM chunk; returns completed segments when silence is found.

        State persists across calls. Call with each ingest frame/accumulation.
        """
        if len(pcm) == 0:
            return []
        if not self._buf:
            self._buf_start = t_offset
        self._buf.append(pcm)
        out: list[VadSegment] = []
        while self._buf:
            combined = np.concatenate(self._buf)
            dur = len(combined) / self.sr
            # Classify only the newly arrived samples. Reclassifying an
            # overlapping trailing window double-counts silence and shifts
            # boundaries earlier on every call.
            speech = self._silero_speech(pcm)
            if speech:
                self._speech_seen = True
                self._silence_samples = 0
            else:
                self._silence_samples += len(pcm)
            # Close on >=500ms silence and total dur >= 0.3s; hard-cap at 30s
            if (self._silence_samples >= self.silence_ms / 1000 * self.sr
                    and dur >= 0.3) or dur >= 30.0:
                # Cut at lowest-energy point in last 5s if over 30s
                cut = len(combined)
                if dur >= 30.0:
                    tail = combined[-int(self.sr * 5):]
                    frame_len = int(self.sr * 0.032)
                    best = 0
                    best_e = float("inf")
                    for i in range(0, len(tail) - frame_len, frame_len):
                        e = float(np.sqrt(np.mean(tail[i:i + frame_len]**2)))
                        if e < best_e:
                            best_e = e
                            best = i
                    cut = len(combined) - len(tail) + best
                seg_pcm = combined[:cut]
                seg_start = self._buf_start if self._buf_start is not None else t_offset
                t_end = seg_start + len(seg_pcm) / self.sr
                remaining = combined[cut:]
                if self._speech_seen and t_end - seg_start >= 0.3:
                    out.append(VadSegment(seg_start, t_end, seg_pcm))
                self._buf = [remaining] if len(remaining) > 0 else []
                self._buf_start = t_end if len(remaining) > 0 else None
                self._silence_samples = len(remaining)
                self._speech_seen = (len(remaining) > 0
                                     and self._is_speech_energy(remaining))
                if len(self._buf) == 0:
                    break
                continue
            # Not enough info yet; keep accumulating
            break
        return out

    def flush(self, t_offset: float) -> list[VadSegment]:
        """Return any remaining buffered audio as a final segment."""
        out: list[VadSegment] = []
        if self._buf:
            combined = np.concatenate(self._buf)
            start = self._buf_start if self._buf_start is not None else t_offset
            t_end = start + len(combined) / self.sr
            if self._speech_seen and t_end - start >= 0.3:
                out.append(VadSegment(start, t_end, combined))
            self._buf = []
            self._buf_start = None
            self._silence_samples = 0
            self._speech_seen = False
        return out


def segment_stream(pcm: np.ndarray, t_offset: float = 0.0) -> list[VadSegment]:
    """Stateless fallback for replay/tests: single-chunk segmentation."""
    sr = 16000
    frame_len = int(sr * 0.032)
    silence_need = int(500 / 32)
    out: list[VadSegment] = []
    cur: list[np.ndarray] = []
    sil = 0
    cur_start = t_offset
    for i in range(0, len(pcm), frame_len):
        frame = pcm[i : i + frame_len]
        speech = float(np.sqrt(np.mean(frame**2) + 1e-12)) > 0.02
        if not cur:
            cur_start = t_offset + i / sr
        cur.append(frame)
        sil = 0 if speech else sil + 1
        dur = sum(len(c) for c in cur) / sr
        if (sil >= silence_need and dur >= 0.3) or dur >= 30.0:
            seg_pcm = np.concatenate(cur)
            t_end = cur_start + len(seg_pcm) / sr
            if t_end - cur_start >= 0.3:
                out.append(VadSegment(cur_start, t_end, seg_pcm))
            cur, sil = [], 0
    if cur:
        seg_pcm = np.concatenate(cur)
        t_end = cur_start + len(seg_pcm) / sr
        if t_end - cur_start >= 0.3:
            out.append(VadSegment(cur_start, t_end, seg_pcm))
    return out
